# 工作流执行引擎
# 基于 MaxKB 的工作流架构实现

import asyncio
import logging
from typing import Dict, List, Any, Optional, Callable, AsyncGenerator
from dataclasses import dataclass, field
from enum import Enum
import json
import httpx
from datetime import datetime

logger = logging.getLogger(__name__)


class NodeStatus(Enum):
    """节点执行状态"""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class NodeType(Enum):
    """节点类型 (与 FlowGram 前端保持一致)"""
    START = "start"
    END = "end"
    LLM = "llm"
    HTTP = "http"
    CODE = "code"
    VARIABLE = "variable"
    CONDITION = "condition"
    LOOP = "loop"
    BLOCK_START = "block-start"
    BLOCK_END = "block-end"
    KNOWLEDGE_BASE = "knowledge-base"
    MCP_SERVICE = "mcp-service"
    WORKFLOW_APP = "workflow-app"
    CONTINUE = "continue"
    BREAK = "break"
    # 兼容旧版节点类型
    AI_CHAT = "ai-chat-node"
    SEARCH_KNOWLEDGE = "search-knowledge-node"
    KNOWLEDGE_WRITE = "knowledge-write-node"
    REPLY = "reply-node"
    VARIABLE_ASSIGN = "variable-assign-node"
    TOOL = "tool-node"
    MCP = "mcp-node"
    LOOP_CONTINUE = "loop-continue-node"


@dataclass
class NodeResult:
    """节点执行结果"""
    node_id: str
    status: NodeStatus
    output: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    execution_time: float = 0.0


@dataclass
class WorkflowContext:
    """工作流上下文"""
    variables: Dict[str, Any] = field(default_factory=dict)
    node_results: Dict[str, NodeResult] = field(default_factory=dict)
    current_node_id: Optional[str] = None
    execution_path: List[str] = field(default_factory=list)
    loop_stack: List[Dict[str, Any]] = field(default_factory=list)
    # LLM 节点流式 token 回调：async (token: str) -> None
    stream_callback: Optional[Callable] = field(default=None, compare=False)

    def set_variable(self, key: str, value: Any):
        """设置变量"""
        self.variables[key] = value
        logger.debug(f"Variable set: {key} = {value}")

    def get_variable(self, key: str, default: Any = None) -> Any:
        """获取变量"""
        # 优先查找完整 key（如 start_0.query）
        if key in self.variables:
            return self.variables[key]

        # 否则尝试嵌套访问（如 user.name）
        keys = key.split('.')
        value = self.variables
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

    def resolve_value(self, value: Any) -> Any:
        """解析值中的变量引用，如 {{variable_name}} 或 {{nodeId.field}}"""
        if not isinstance(value, str):
            return value
        import re
        # 整体是一个变量引用，直接返回原始类型
        single = re.fullmatch(r'\{\{(.+?)\}\}', value.strip())
        if single:
            return self.get_variable(single.group(1).strip())
        # 含多个或嵌入文本的模板，做字符串替换
        def replace_var(m):
            v = self.get_variable(m.group(1).strip())
            return str(v) if v is not None else m.group(0)
        return re.sub(r'\{\{(.+?)\}\}', replace_var, value)

    def resolve_inputs_values(self, inputs_values: Dict[str, Any]) -> Dict[str, Any]:
        """解析 FlowGram inputsValues 结构，返回实际值字典"""
        result = {}
        for field_name, field_val in inputs_values.items():
            if not isinstance(field_val, dict):
                result[field_name] = field_val
                continue
            val_type = field_val.get('type', 'constant')
            content = field_val.get('content', '')
            if val_type == 'constant':
                result[field_name] = content
            elif val_type in ('template', 'expression'):
                result[field_name] = self.resolve_value(content)
            elif val_type == 'ref':
                # ref: content 是 [nodeId, fieldName] 列表
                if isinstance(content, list) and len(content) == 2:
                    result[field_name] = self.get_variable(f"{content[0]}.{content[1]}")
                else:
                    result[field_name] = self.resolve_value(str(content))
            else:
                result[field_name] = content
        return result


class BaseNode:
    """工作流节点基类"""

    def __init__(self, node_id: str, node_type: str, properties: Dict[str, Any]):
        self.node_id = node_id
        self.node_type = node_type
        self.properties = properties

    async def execute(self, context: WorkflowContext) -> NodeResult:
        """
        执行节点

        Args:
            context: 工作流上下文

        Returns:
            节点执行结果
        """
        start_time = datetime.now()

        try:
            # 添加到执行路径
            context.execution_path.append(self.node_id)
            context.current_node_id = self.node_id

            logger.info(f"Executing node: {self.node_id} (type: {self.node_type})")

            # 执行具体逻辑
            output = await self._execute(context)

            execution_time = (datetime.now() - start_time).total_seconds()

            result = NodeResult(
                node_id=self.node_id,
                status=NodeStatus.SUCCESS,
                output=output,
                execution_time=execution_time
            )

            context.node_results[self.node_id] = result
            return result

        except Exception as e:
            logger.error(f"Node execution failed: {self.node_id}, error: {e}")
            execution_time = (datetime.now() - start_time).total_seconds()

            result = NodeResult(
                node_id=self.node_id,
                status=NodeStatus.FAILED,
                error=str(e),
                execution_time=execution_time
            )

            context.node_results[self.node_id] = result
            return result

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        """子类实现的具体执行逻辑"""
        raise NotImplementedError(f"_execute not implemented for {self.node_type}")


class EndNode(BaseNode):
    """结束节点"""

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        # 解析 end 节点的 inputsValues，把最终输出写入 context 供外部读取
        inputs_values = self.properties.get('inputsValues', {})
        resolved = context.resolve_inputs_values(inputs_values)
        for field_name, val in resolved.items():
            context.set_variable(f"{self.node_id}.{field_name}", val)
        return resolved


class StartNode(BaseNode):
    """开始节点"""

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        # 把 start 节点的 outputs 字段从 context.variables 映射到 nodeId.field 格式
        # 这样下游节点可以用 {{start_0.query}} 引用
        outputs_schema = self.properties.get('outputs', {})
        output_fields = list(outputs_schema.get('properties', {}).keys())
        for field_name in output_fields:
            # 从 context 里取同名变量（由调用方写入，如 query/message/input）
            val = context.get_variable(field_name)
            if val is not None:
                context.set_variable(f"{self.node_id}.{field_name}", val)
        return {"message": "Workflow started"}


class AIChatNode(BaseNode):
    """AI对话节点（兼容 FlowGram inputsValues 结构）"""

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        from core.config import OLLAMA_BASE_URL

        # 解析 FlowGram inputsValues 结构
        inputs_values = self.properties.get('inputsValues', {})
        resolved = context.resolve_inputs_values(inputs_values)

        prompt = resolved.get('prompt', self.properties.get('prompt', ''))
        system_prompt = resolved.get('systemPrompt', self.properties.get('systemPrompt', ''))
        model = resolved.get('modelName', self.properties.get('model', 'deepseek-r1:8b'))
        temperature = resolved.get('temperature', self.properties.get('temperature', 0.7))
        max_tokens = self.properties.get('max_tokens', 2048)
        api_host = resolved.get('apiHost', '')

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": str(system_prompt)})
        messages.append({"role": "user", "content": str(prompt)})

        # 优先用节点配置的 apiHost，否则用全局 Ollama 地址
        base_url = OLLAMA_BASE_URL
        if api_host:
            # apiHost 可能是 OpenAI 兼容地址，去掉 /v1 后缀用原生 Ollama
            base_url = api_host.rstrip('/').removesuffix('/v1') or base_url

        ai_response = ""
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream(
                "POST",
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "options": {
                        "num_ctx": 8192,
                        "temperature": float(temperature),
                        "num_predict": max_tokens,
                    }
                }
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        token = data.get("message", {}).get("content", "")
                        if token:
                            import re as _re
                            # 移除 <think> 标签，但保留换行符（不使用 strip）
                            token = _re.sub(r'<think>.*?</think>', '', token, flags=_re.DOTALL)
                            if token:
                                ai_response += token
                                if context.stream_callback:
                                    await context.stream_callback(token)
                        if data.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue

        # 按 FlowGram outputs schema 定义的字段名存变量（通常是 result）
        outputs_schema = self.properties.get('outputs', {})
        output_fields = list(outputs_schema.get('properties', {}).keys())
        primary_field = output_fields[0] if output_fields else 'result'
        context.set_variable(f"{self.node_id}.{primary_field}", ai_response)
        # 兼容旧逻辑
        context.set_variable('ai_response', ai_response)

        return {primary_field: ai_response}


class SearchKnowledgeNode(BaseNode):
    """知识库检索节点（兼容 FlowGram inputsValues 结构）"""

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        from core.utils import vector_search_multi
        from services.embedding import EmbeddingService
        from core.config import OLLAMA_BASE_URL, OLLAMA_EMBEDDING_MODEL

        # 解析 FlowGram inputsValues 结构
        inputs_values = self.properties.get('inputsValues', {})
        logger.info(f"[KB Node] inputsValues: {inputs_values}")
        logger.info(f"[KB Node] context.variables: {context.variables}")

        resolved = context.resolve_inputs_values(inputs_values)
        logger.info(f"[KB Node] resolved: {resolved}")

        query = resolved.get('query', '')
        knowledge_base_id = resolved.get('knowledgeBaseId', '')
        top_k = resolved.get('topK', 5)

        if not query:
            raise ValueError(f"query 字段不能为空 (resolved={resolved})")
        if not knowledge_base_id:
            raise ValueError("knowledgeBaseId 字段不能为空")

        # 生成查询向量
        embedding_service = EmbeddingService(OLLAMA_EMBEDDING_MODEL, OLLAMA_BASE_URL)
        query_embedding = await embedding_service.generate(query)

        if not query_embedding:
            raise ValueError("Failed to generate query embedding")

        # 检索
        from core import config
        results = await vector_search_multi(
            pool_ref=config.pool,
            query_embedding=query_embedding,
            kb_ids=[knowledge_base_id],
            top_k=top_k,
            threshold=0.5
        )

        # 按 FlowGram outputs schema 定义的字段名存变量
        outputs_schema = self.properties.get('outputs', {})
        output_fields = list(outputs_schema.get('properties', {}).keys())
        primary_field = output_fields[0] if output_fields else 'results'
        context.set_variable(f"{self.node_id}.{primary_field}", results)

        return {primary_field: results, "count": len(results)}


class ConditionNode(BaseNode):
    """条件分支节点"""

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        branches = self.properties.get('branches', [])
        matched_branch = None

        for branch in branches:
            conditions = branch.get('conditions', [])
            all_match = True

            for condition in conditions:
                field = condition.get('field')
                operator = condition.get('operator', 'equals')
                value = context.resolve_value(condition.get('value'))

                actual_value = context.get_variable(field)

                # 评估条件
                if not self._evaluate_condition(actual_value, operator, value):
                    all_match = False
                    break

            if all_match:
                matched_branch = branch
                break

        return {
            "matched_branch": matched_branch,
            "condition_met": matched_branch is not None
        }

    def _evaluate_condition(self, actual: Any, operator: str, expected: Any) -> bool:
        """评估单个条件"""
        if operator == 'equals':
            return actual == expected
        elif operator == 'not_equals':
            return actual != expected
        elif operator == 'contains':
            return expected in str(actual) if actual else False
        elif operator == 'not_contains':
            return expected not in str(actual) if actual else True
        elif operator == 'greater_than':
            return float(actual) > float(expected) if actual else False
        elif operator == 'less_than':
            return float(actual) < float(expected) if actual else False
        elif operator == 'is_empty':
            return not actual
        elif operator == 'is_not_empty':
            return bool(actual)
        else:
            return True


class LoopNode(BaseNode):
    """循环节点"""

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        loop_type = self.properties.get('loop_type', 'array')
        loop_variable = self.properties.get('loop_variable', '')
        loop_count = self.properties.get('loop_count', 10)

        if loop_type == 'array':
            # 数组遍历
            array = context.get_variable(loop_variable, [])
            if not isinstance(array, list):
                array = [array]

            loop_info = {
                'type': 'array',
                'items': array,
                'index': 0,
                'current_item': array[0] if array else None,
                'total': len(array)
            }

            context.loop_stack.append(loop_info)

        elif loop_type == 'number':
            # 数值循环
            loop_info = {
                'type': 'number',
                'count': loop_count,
                'index': 0
            }

            context.loop_stack.append(loop_info)

        elif loop_type == 'infinite':
            # 无限循环
            loop_info = {
                'type': 'infinite',
                'index': 0
            }

            context.loop_stack.append(loop_info)

        return {"loop_started": True}


class VariableAssignNode(BaseNode):
    """变量赋值节点"""

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        variables = self.properties.get('variables', [])

        for var in variables:
            key = var.get('key')
            value = context.resolve_value(var.get('value'))
            if key:
                context.set_variable(key, value)

        return {"assigned": len(variables)}


class ReplyNode(BaseNode):
    """回复节点"""

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        reply_content = context.resolve_value(self.properties.get('reply_content', ''))
        reply_type = self.properties.get('reply_type', 'text')

        # 将回复存储到上下文中
        context.set_variable('__reply__', {
            'content': reply_content,
            'type': reply_type
        })

        return {"reply": reply_content}


class ToolNode(BaseNode):
    """工具调用节点"""

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        tool_name = self.properties.get('tool_name', '')
        tool_params = self.properties.get('tool_params', {})
        output_variable = self.properties.get('output_variable', 'tool_result')

        # 解析参数中的变量
        resolved_params = {}
        for key, value in tool_params.items():
            resolved_params[key] = context.resolve_value(value)

        # 这里可以集成具体的工具调用逻辑
        logger.info(f"Calling tool: {tool_name} with params: {resolved_params}")

        result = {"tool": tool_name, "params": resolved_params, "status": "executed"}
        context.set_variable(output_variable, result)

        return result


class WorkflowEngine:
    """
    工作流执行引擎

    负责：
    1. 解析工作流定义
    2. 构建执行图（DAG）
    3. 按拓扑序执行节点
    4. 处理条件分支和循环
    5. 管理执行上下文
    """

    def __init__(self, pool=None, cache=None):
        """初始化工作流引擎

        Args:
            pool: 数据库连接池
            cache: 缓存实例
        """
        self.pool = pool
        self.cache = cache
        self.node_classes: Dict[str, type[BaseNode]] = {
            # FlowGram 前端节点类型
            NodeType.START.value: StartNode,
            NodeType.END.value: EndNode,
            NodeType.LLM.value: AIChatNode,
            NodeType.KNOWLEDGE_BASE.value: SearchKnowledgeNode,
            NodeType.CONDITION.value: ConditionNode,
            NodeType.LOOP.value: LoopNode,
            NodeType.VARIABLE.value: VariableAssignNode,
            NodeType.MCP_SERVICE.value: ToolNode,
            # 兼容旧版节点类型
            NodeType.AI_CHAT.value: AIChatNode,
            NodeType.SEARCH_KNOWLEDGE.value: SearchKnowledgeNode,
            NodeType.VARIABLE_ASSIGN.value: VariableAssignNode,
            NodeType.REPLY.value: ReplyNode,
            NodeType.TOOL.value: ToolNode,
        }

    def register_node(self, node_type: str, node_class: type[BaseNode]):
        """注册自定义节点类型"""
        self.node_classes[node_type] = node_class

    async def execute(
        self,
        workflow_id_or_definition,  # Union[str, Dict[str, Any]]
        input_data: Dict[str, Any] = None,
        user_id: Optional[str] = None,
        stream_callback: Optional[Callable] = None,
    ) -> WorkflowContext:
        """
        执行工作流

        Args:
            workflow_id_or_definition: 工作流 ID（字符串）或工作流定义（字典）
            input_data: 输入数据
            user_id: 用户 ID（未使用，保留参数兼容性）
            stream_callback: LLM 节点流式 token 回调 async (token: str) -> None

        Returns:
            工作流执行上下文（包含所有变量和结果）
        """
        # 如果传入的是 workflow_id，从数据库加载定义
        if isinstance(workflow_id_or_definition, str):
            if not self.pool:
                raise ValueError("Database pool not initialized")

            from psycopg.rows import dict_row
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        "SELECT definition FROM workflows WHERE id = %s",
                        (workflow_id_or_definition,)
                    )
                    row = await cur.fetchone()

                    if not row:
                        raise ValueError(f"Workflow {workflow_id_or_definition} not found")

                    workflow_definition = row['definition']
        else:
            workflow_definition = workflow_id_or_definition

        context = WorkflowContext()
        context.stream_callback = stream_callback

        # 设置输入数据
        if input_data:
            for key, value in input_data.items():
                context.set_variable(key, value)

        # 解析节点和边
        nodes = workflow_definition.get('nodes', [])
        edges = workflow_definition.get('edges', [])

        # 构建节点图
        node_map = {}
        for node_data in nodes:
            node_type = node_data['type']
            node_class = self.node_classes.get(node_type)

            if node_class is None:
                logger.error(f"No executor found for node type: {node_type}")
                logger.error(f"Available node types: {list(self.node_classes.keys())}")
                raise ValueError(f"No executor found for node type: {node_type}")

            node_map[node_data['id']] = node_class(
                node_data['id'],
                node_type,
                node_data.get('data') or node_data.get('properties', {})
            )

        # 构建邻接表
        adjacency = {node_id: [] for node_id in node_map}
        edge_map = {}
        for edge in edges:
            source = edge.get('sourceNodeID') or edge.get('sourceNodeId')
            target = edge.get('targetNodeID') or edge.get('targetNodeId')
            adjacency[source].append(target)
            edge_map[(source, target)] = edge

        # 找到开始节点
        start_nodes = [
            node_id for node_id, node in node_map.items()
            if node.node_type == NodeType.START.value
        ]

        if not start_nodes:
            raise ValueError("No start node found in workflow")

        # 拓扑排序执行
        executed = set()
        execution_queue = start_nodes.copy()

        while execution_queue:
            current_id = execution_queue.pop(0)

            if current_id in executed:
                continue

            # 检查前驱节点是否已执行
            predecessors = [
                src for src, tgt in edge_map.keys() if tgt == current_id
            ]
            if not all(pred in executed for pred in predecessors):
                # 等待前驱节点执行完成
                continue

            # 执行节点
            node = node_map[current_id]
            result = await node.execute(context)

            # 标记已执行
            executed.add(current_id)

            # 根据执行结果决定后续流程
            if result.status == NodeStatus.FAILED:
                logger.error(f"Node {current_id} failed, stopping workflow")
                break

            if node.node_type == NodeType.CONDITION.value:
                # 条件节点：根据结果选择分支
                matched_branch = result.output.get('matched_branch')
                if matched_branch:
                    # 这里需要根据分支选择下一个节点
                    # 简化处理：添加所有后继节点
                    pass
            elif node.node_type == NodeType.LOOP.value:
                # 循环节点：处理循环逻辑
                loop_info = context.loop_stack[-1] if context.loop_stack else None
                if loop_info and loop_info['type'] == 'array':
                    # 检查是否还有更多元素
                    if loop_info['index'] < loop_info['total'] - 1:
                        # 继续循环，重新添加循环节点到队列
                        loop_info['index'] += 1
                        if loop_info['index'] < len(loop_info['items']):
                            loop_info['current_item'] = loop_info['items'][loop_info['index']]
                        execution_queue.insert(0, current_id)
                        executed.remove(current_id)

            # 添加后继节点到执行队列
            for successor in adjacency[current_id]:
                if successor not in executed:
                    execution_queue.append(successor)

        logger.info(f"Workflow execution completed. Executed {len(executed)} nodes.")
        return context


# 全局工作流引擎实例
workflow_engine = WorkflowEngine()


async def execute_workflow(
    workflow_definition: Dict[str, Any],
    input_data: Dict[str, Any] = None,
    stream_callback: Optional[Callable] = None,
) -> WorkflowContext:
    """
    执行工作流（便捷函数）

    Args:
        workflow_definition: 工作流定义
        input_data: 输入数据
        stream_callback: LLM 节点流式 token 回调

    Returns:
        工作流执行上下文
    """
    return await workflow_engine.execute(workflow_definition, input_data, stream_callback)
