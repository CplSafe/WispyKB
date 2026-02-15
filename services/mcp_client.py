# MCPClient - MCP 客户端服务
# 从 main_pgvector.py 拆分

import asyncio
import json
import logging
from typing import Dict, List, Any, Optional

import httpx

logger = logging.getLogger(__name__)


class MCPClient:
    """
    MCP 客户端 - 连接到外部/本地 MCP 服务器

    支持的连接类型：
    - http: HTTP/HTTPS 端点（远程服务器）
    - ws: WebSocket 端点
    - sse: Server-Sent Events（本地服务器常用）
    - stdio: 本地进程通过标准输入输出通信

    本地 MCP 服务器示例：
    - SSE: http://localhost:3000/sse
    - HTTP: http://localhost:3000/mcp
    """

    def __init__(
        self,
        config_id: str,
        name: str,
        connection_type: str,
        url: str,
        command: Optional[str] = None,  # stdio 模式下的命令
        args: Optional[List[str]] = None,   # stdio 模式下的参数
        headers: Optional[Dict[str, str]] = None,
        auth_token: Optional[str] = None,
        api_key: Optional[str] = None
    ):
        self.config_id = config_id
        self.name = name
        self.connection_type = connection_type  # 'http', 'ws', 'sse', 'stdio'
        self.url = url
        self.command = command
        self.args = args or []
        self.headers = headers or {}
        self.auth_token = auth_token
        self.api_key = api_key
        self._tools: List[Dict[str, Any]] = []
        self._resources: List[Dict[str, Any]] = []
        self._prompts: List[Dict[str, Any]] = []
        self._process = None  # stdio 模式的子进程

    async def initialize(self):
        """初始化连接，获取服务器能力"""
        if self.auth_token:
            self.headers['Authorization'] = f'Bearer {self.auth_token}'
        if self.api_key:
            self.headers['x-api-key'] = self.api_key

        # 如果是 stdio 模式，启动子进程
        if self.connection_type == 'stdio' and self.command:
            await self._start_stdio_process()

    async def _start_stdio_process(self):
        """启动 stdio 模式的 MCP 进程"""
        try:
            self._process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            logger.info(f"MCP stdio 进程已启动: {self.command}")

            # 初始化握手
            await self._send_stdio_request({
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {
                    'protocolVersion': '2024-11-05',
                    'capabilities': {},
                    'clientInfo': {
                        'name': 'ai-kb-service',
                        'version': '2.0.0'
                    }
                }
            })

        except Exception as e:
            logger.error(f"MCP stdio 进程启动失败: {e}")

    async def _send_stdio_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """向 stdio 进程发送请求"""
        if not self._process or self._process.stdin.is_closing():
            return {'error': {'code': -1, 'message': '进程未运行'}}

        try:
            # 发送请求
            request_json = json.dumps(request) + '\n'
            self._process.stdin.write(request_json.encode())
            await self._process.stdin.drain()

            # 读取响应（一行一个 JSON-RPC 响应）
            response_line = await self._process.stdout.readline()
            if not response_line:
                return {'error': {'code': -1, 'message': '无响应'}}

            return json.loads(response_line.decode())
        except Exception as e:
            return {'error': {'code': -1, 'message': str(e)}}

    async def tools_list(self) -> List[Dict[str, Any]]:
        """列出服务器提供的所有工具"""
        try:
            if self.connection_type == 'stdio':
                # stdio 模式
                response = await self._send_stdio_request({
                    'jsonrpc': '2.0',
                    'id': 2,
                    'method': 'tools/list',
                    'params': {}
                })
                if 'result' in response:
                    self._tools = response['result'].get('tools', [])
                    return self._tools
            else:
                # HTTP/SSE/WebSocket 模式
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(
                        self.url,
                        json={
                            'jsonrpc': '2.0',
                            'id': 1,
                            'method': 'tools/list',
                            'params': {}
                        },
                        headers=self.headers
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if 'result' in data:
                            self._tools = data['result'].get('tools', [])
                            return self._tools
        except Exception as e:
            logger.warning(f"MCP 客户端 {self.name} 工具列表获取失败: {e}")
        return []

    async def tools_call(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """调用工具"""
        try:
            if self.connection_type == 'stdio':
                # stdio 模式
                response = await self._send_stdio_request({
                    'jsonrpc': '2.0',
                    'id': 3,
                    'method': 'tools/call',
                    'params': {
                        'name': name,
                        'arguments': arguments
                    }
                })
                return response
            else:
                # HTTP/SSE/WebSocket 模式
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(
                        self.url,
                        json={
                            'jsonrpc': '2.0',
                            'id': 1,
                            'method': 'tools/call',
                            'params': {
                                'name': name,
                                'arguments': arguments
                            }
                        },
                        headers=self.headers
                    )
                    if response.status_code == 200:
                        return response.json()
                return {
                    'error': {
                        'code': response.status_code,
                        'message': response.text
                    }
                }
        except Exception as e:
            return {
                'error': {
                    'code': -1,
                    'message': str(e)
                }
            }

    async def resources_list(self) -> List[Dict[str, Any]]:
        """列出服务器提供的所有资源"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self.url,
                    json={
                        'jsonrpc': '2.0',
                        'id': 1,
                        'method': 'resources/list',
                        'params': {}
                    },
                    headers=self.headers
                )
                if response.status_code == 200:
                    data = response.json()
                    if 'result' in data:
                        self._resources = data['result'].get('resources', [])
                        return self._resources
        except Exception as e:
            logger.warning(f"MCP 客户端 {self.name} 资源列表获取失败: {e}")
        return []

    async def prompts_list(self) -> List[Dict[str, Any]]:
        """列出服务器提供的所有提示模板"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    self.url,
                    json={
                        'jsonrpc': '2.0',
                        'id': 1,
                        'method': 'prompts/list',
                        'params': {}
                    },
                    headers=self.headers
                )
                if response.status_code == 200:
                    data = response.json()
                    if 'result' in data:
                        self._prompts = data['result'].get('prompts', [])
                        return self._prompts
        except Exception as e:
            logger.warning(f"MCP 客户端 {self.name} 提示模板列表获取失败: {e}")
        return []
