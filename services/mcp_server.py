# MCPServer - MCP 服务器管理器服务
# 从 main_pgvector.py 拆分

import json
import logging
from typing import Dict, List, Any, Optional, Callable

import httpx
from psycopg.rows import dict_row

from .cache import CacheManager
from .mcp_client import MCPClient

logger = logging.getLogger(__name__)


class MCPServer:
    """
    MCP 服务器管理器 - 管理本地工具和远程 MCP 连接

    功能：
    1. 内置本地工具（知识库搜索、文档获取等）
    2. 动态添加远程 MCP 服务器
    3. 统一的工具调用接口
    4. 工具发现和聚合
    """

    def __init__(self, pool_ref, cache_manager: CacheManager):
        self.pool_ref = pool_ref
        self.cache = cache_manager
        self._local_tools: Dict[str, Any] = {}
        self._remote_clients: Dict[str, MCPClient] = {}
        self._register_local_tools()

    def _register_local_tools(self):
        """注册本地内置工具"""
        import core.config as config
        from core.utils import vector_search_multi, call_ollama
        from services.embedding import EmbeddingService
        from services.workflow import WorkflowEngine

        # 获取服务实例
        embedding_service = config.embedding_service
        if not embedding_service:
            embedding_service = EmbeddingService(config.OLLAMA_EMBEDDING_MODEL, config.OLLAMA_BASE_URL)
            config.embedding_service = embedding_service

        workflow_engine = config.workflow_engine
        OLLAMA_CHAT_MODEL = config.OLLAMA_CHAT_MODEL

        # 知识库搜索工具
        async def search_knowledgebase(args: Dict[str, Any]) -> Dict[str, Any]:
            kb_ids = args.get('kb_ids', [])
            query = args.get('query', '')
            top_k = args.get('top_k', 5)

            query_embedding = await embedding_service.generate(query)
            if not query_embedding:
                return {'results': [], 'error': '生成嵌入向量失败'}

            results = await vector_search_multi(
                self.pool_ref, query_embedding, kb_ids, top_k=top_k
            )
            return {'results': results, 'query': query}

        self._local_tools['knowledge:search'] = {
            'name': 'knowledge:search',
            'description': '在知识库中搜索相关信息',
            'category': 'knowledge',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'kb_ids': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': '知识库ID列表'
                    },
                    'query': {'type': 'string', 'description': '搜索查询'},
                    'top_k': {'type': 'integer', 'description': '返回结果数量', 'default': 5}
                },
                'required': ['kb_ids', 'query']
            },
            'handler': search_knowledgebase
        }

        # 获取文档内容工具
        async def get_document(args: Dict[str, Any]) -> Dict[str, Any]:
            doc_id = args.get('doc_id')
            kb_id = args.get('kb_id')

            async with self.pool_ref.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("""
                        SELECT id, name, content, chunk_count
                        FROM documents
                        WHERE id = %s AND kb_id = %s
                    """, (doc_id, kb_id))
                    doc = await cur.fetchone()

            if not doc:
                return {'error': '文档不存在'}

            return {
                'id': doc['id'],
                'name': doc['name'],
                'content': doc.get('content', ''),
                'chunk_count': doc.get('chunk_count', 0)
            }

        self._local_tools['knowledge:get_document'] = {
            'name': 'knowledge:get_document',
            'description': '获取文档的详细内容',
            'category': 'knowledge',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'doc_id': {'type': 'string', 'description': '文档ID'},
                    'kb_id': {'type': 'string', 'description': '知识库ID'}
                },
                'required': ['doc_id', 'kb_id']
            },
            'handler': get_document
        }

        # 执行工作流工具
        async def execute_workflow(args: Dict[str, Any]) -> Dict[str, Any]:
            workflow_id = args.get('workflow_id')
            inputs = args.get('inputs', {})
            result = await workflow_engine.execute(workflow_id, inputs, None)
            return result

        self._local_tools['workflow:execute'] = {
            'name': 'workflow:execute',
            'description': '执行工作流',
            'category': 'workflow',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'workflow_id': {'type': 'string', 'description': '工作流ID'},
                    'inputs': {'type': 'object', 'description': '工作流输入参数'}
                },
                'required': ['workflow_id']
            },
            'handler': execute_workflow
        }

        # LLM 聊天工具
        async def chat_with_llm(args: Dict[str, Any]) -> Dict[str, Any]:
            model = args.get('model', OLLAMA_CHAT_MODEL)
            messages = args.get('messages', [])
            response = await call_ollama(model, messages)
            return {'response': response, 'model': model}

        self._local_tools['llm:chat'] = {
            'name': 'llm:chat',
            'description': '使用 LLM 进行对话',
            'category': 'llm',
            'inputSchema': {
                'type': 'object',
                'properties': {
                    'model': {'type': 'string', 'description': '模型名称'},
                    'messages': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'role': {'type': 'string'},
                                'content': {'type': 'string'}
                            }
                        },
                        'description': '对话消息'
                    }
                },
                'required': ['messages']
            },
            'handler': chat_with_llm
        }

        # 列出知识库工具
        async def list_knowledgebases(args: Dict[str, Any]) -> Dict[str, Any]:
            async with self.pool_ref.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("""
                        SELECT id, name, description, doc_count, token_count
                        FROM knowledge_bases
                        ORDER BY created_at DESC
                    """)
                    kbs = await cur.fetchall()
            return {'knowledge_bases': kbs}

        self._local_tools['knowledge:list'] = {
            'name': 'knowledge:list',
            'description': '列出所有可用的知识库',
            'category': 'knowledge',
            'inputSchema': {
                'type': 'object',
                'properties': {},
                'required': []
            },
            'handler': list_knowledgebases
        }

    async def add_remote_server(
        self,
        config_id: str,
        name: str,
        connection_type: str,
        url: str = "",
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        headers: Optional[Dict[str, str]] = None,
        auth_token: Optional[str] = None,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        添加 MCP 服务器配置（支持远程和本地）

        Args:
            config_id: 配置ID
            name: 服务器名称
            connection_type: 连接类型 (http, ws, sse, stdio)
            url: 服务器URL (stdio 模式不需要)
            command: stdio 模式下的命令
            args: stdio 模式下的命令参数
            headers: 自定义请求头
            auth_token: Bearer Token
            api_key: API Key

        本地 MCP 服务器示例：
        - HTTP: connection_type="http", url="http://localhost:3000/mcp"
        - SSE: connection_type="sse", url="http://localhost:3000/sse"
        - stdio: connection_type="stdio", command="npx", args=["-y", "@modelcontextprotocol/server-filesystem", "/path"]
        """
        client = MCPClient(
            config_id=config_id,
            name=name,
            connection_type=connection_type,
            url=url,
            command=command,
            args=args,
            headers=headers,
            auth_token=auth_token,
            api_key=api_key
        )

        await client.initialize()
        tools = await client.tools_list()

        # stdio 模式暂不支持 resources 和 prompts
        if connection_type == 'stdio':
            resources = []
            prompts = []
        else:
            resources = await client.resources_list()
            prompts = await client.prompts_list()

        self._remote_clients[config_id] = client

        # 保存到数据库
        async with self.pool_ref.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO mcp_configs
                    (id, name, connection_type, url, command, args, headers, auth_token, api_key, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        connection_type = EXCLUDED.connection_type,
                        url = EXCLUDED.url,
                        command = EXCLUDED.command,
                        args = EXCLUDED.args,
                        headers = EXCLUDED.headers,
                        auth_token = EXCLUDED.auth_token,
                        api_key = EXCLUDED.api_key,
                        is_active = EXCLUDED.is_active
                """, (
                    config_id, name, connection_type, url, command,
                    json.dumps(args) if args else None,
                    json.dumps(headers) if headers else None,
                    auth_token, api_key, True
                ))
                await conn.commit()

        return {
            'config_id': config_id,
            'name': name,
            'connection_type': connection_type,
            'tools_count': len(tools),
            'resources_count': len(resources),
            'prompts_count': len(prompts),
            'tools': tools,
            'resources': resources,
            'prompts': prompts
        }

    async def remove_remote_server(self, config_id: str) -> bool:
        """移除 MCP 服务器"""
        if config_id in self._remote_clients:
            client = self._remote_clients[config_id]
            # 清理 stdio 进程
            if client._process:
                try:
                    client._process.terminate()
                    await client._process.wait()
                except:
                    pass
            del self._remote_clients[config_id]

        async with self.pool_ref.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM mcp_configs WHERE id = %s", (config_id,))
                await conn.commit()

        return True

    async def get_all_tools(self) -> List[Dict[str, Any]]:
        """获取所有可用工具（本地 + 远程）"""
        all_tools = []

        # 本地工具
        for tool_name, tool_def in self._local_tools.items():
            all_tools.append({
                'name': tool_def['name'],
                'description': tool_def['description'],
                'category': tool_def.get('category', 'local'),
                'source': 'local',
                'inputSchema': tool_def['inputSchema']
            })

        # 远程工具
        for config_id, client in self._remote_clients.items():
            for tool in client._tools:
                all_tools.append({
                    'name': tool.get('name', ''),
                    'description': tool.get('description', ''),
                    'source': f'remote:{config_id}',
                    'source_name': client.name,
                    'inputSchema': tool.get('inputSchema', {})
                })

        return all_tools

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        source: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        调用工具

        Args:
            tool_name: 工具名称（可以包含前缀如 "remote:config_id:tool_name"）
            arguments: 工具参数
            source: 工具来源（可选）
        """
        # 解析工具名称
        parts = tool_name.split(':')

        if parts[0] == 'remote' and len(parts) >= 2:
            # 远程工具: remote:config_id:tool_name
            config_id = parts[1]
            remote_tool_name = ':'.join(parts[2:]) if len(parts) > 2 else parts[2]

            if config_id in self._remote_clients:
                return await self._remote_clients[config_id].tools_call(remote_tool_name, arguments)
            else:
                return {'error': f'远程服务器未找到: {config_id}'}

        # 本地工具
        if tool_name in self._local_tools:
            tool = self._local_tools[tool_name]
            try:
                result = await tool['handler'](arguments)
                return {
                    'content': [
                        {
                            'type': 'text',
                            'text': json.dumps(result, ensure_ascii=False)
                        }
                    ],
                    'isError': False
                }
            except Exception as e:
                return {
                    'content': [{'type': 'text', 'text': str(e)}],
                    'isError': True
                }

        # 尝试在所有远程客户端中查找
        for config_id, client in self._remote_clients.items():
            for tool in client._tools:
                if tool.get('name') == tool_name:
                    return await client.tools_call(tool_name, arguments)

        return {'error': f'工具未找到: {tool_name}'}

    async def get_remote_configs(self) -> List[Dict[str, Any]]:
        """获取所有远程 MCP 配置"""
        configs = []
        async with self.pool_ref.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT id, name, connection_type, url, command, args, headers,
                           auth_token, api_key, is_active, created_at
                    FROM mcp_configs
                    ORDER BY created_at DESC
                """)
                rows = await cur.fetchall()
                configs = [dict(row) for row in rows]

        # 添加工具数量
        for config in configs:
            config_id = config['id']
            if config_id in self._remote_clients:
                config['tools_count'] = len(self._remote_clients[config_id]._tools)
                config['resources_count'] = len(self._remote_clients[config_id]._resources)
            else:
                config['tools_count'] = 0
                config['resources_count'] = 0

        return configs

    async def test_connection(
        self,
        url: str,
        auth_token: Optional[str] = None,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """测试远程 MCP 服务器连接"""
        headers = {}
        if auth_token:
            headers['Authorization'] = f'Bearer {auth_token}'
        if api_key:
            headers['x-api-key'] = api_key

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    url,
                    json={
                        'jsonrpc': '2.0',
                        'id': 1,
                        'method': 'tools/list',
                        'params': {}
                    },
                    headers=headers
                )

                if response.status_code == 200:
                    data = response.json()
                    if 'result' in data:
                        tools = data['result'].get('tools', [])
                        return {
                            'success': True,
                            'message': '连接成功',
                            'tools_count': len(tools),
                            'tools': tools[:5]  # 返回前5个工具作为预览
                        }

                return {
                    'success': False,
                    'message': f'连接失败: {response.text}'
                }
        except Exception as e:
            return {
                'success': False,
                'message': f'连接错误: {str(e)}'
            }
