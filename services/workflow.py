# 工作流执行引擎
# 基于 MaxKB 的工作流架构实现

import asyncio
import logging
from typing import Dict, List, Any, Optional, Callable
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
    """节点类型"""
    START = "start-node"
    END = "end-node"
    AI_CHAT = "ai-chat-node"
    CONDITION = "condition-node"
    LOOP = "loop-node"
    SEARCH_KNOWLEDGE = "search-knowledge-node"
    KNOWLEDGE_WRITE = "knowledge-write-node"
    DATA_SOURCE_LOCAL = "data-source-local-node"
    DATA_SOURCE_WEB = "data-source-web-node"
    DOCUMENT_EXTRACT = "document-extract-node"
    DOCUMENT_SPLIT = "document-split-node"
    FORM = "form-node"
    QUESTION = "question-node"
    REPLY = "reply-node"
    VARIABLE_ASSIGN = "variable-assign-node"
    VARIABLE_SPLIT = "variable-splitting"
    VARIABLE_AGGREGATION = "variable-aggregation-node"
    PARAMETER_EXTRACTION = "parameter-extraction-node"
    TOOL = "tool-node"
    MCP = "mcp-node"
    INTENT_CLASSIFY = "intent-classify-node"
    RERANKER = "reranker-node"
    IMAGE_GENERATE = "image-generate-node"
    IMAGE_UNDERSTAND = "image-understand-node"
    SPEECH_TO_TEXT = "speech-to-text-node"
    TEXT_TO_SPEECH = "text-to-speech-node"
    APPLICATION = "application-node"
    LOOP_BREAK = "loop-break-node"
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

    def set_variable(self, key: str, value: Any):
        """设置变量"""
        self.variables[key] = value
        logger.debug(f"Variable set: {key} = {value}")

    def get_variable(self, key: str, default: Any = None) -> Any:
        """获取变量"""
        # 支持嵌套访问，如 user.name
        keys = key.split('.')
        value = self.variables
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

    def resolve_value(self, value: Any) -> Any:
        """解析值中的变量引用，如 {{variable_name}}"""
        if isinstance(value, str):
            # 简单的变量替换
            if value.startswith('{{') and value.endswith('}}'):
                var_name = value[2:-2].strip()
                return self.get_variable(var_name)
            return value
        return value


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


class StartNode(BaseNode):
    """开始节点"""

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        # 初始化变量
        return {"message": "Workflow started"}


class AIChatNode(BaseNode):
    """AI对话节点"""

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        from core.config import get_ollama_base_url

        prompt = context.resolve_value(self.properties.get('prompt', ''))
        model = self.properties.get('model', 'deepseek-r1:8b')
        temperature = self.properties.get('temperature', 0.7)
        max_tokens = self.properties.get('max_tokens', 2048)

        # 替换提示词中的变量
        if isinstance(prompt, str):
            for key, value in context.variables.items():
                prompt = prompt.replace(f'{{{{{key}}}}}', str(value))

        # 调用 Ollama API
        base_url = get_ollama_base_url()
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {
                        "num_ctx": 8192,
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    }
                }
            )
            response.raise_for_status()
            data = response.json()
            ai_response = data.get("message", {}).get("content", "")

        output_variable = self.properties.get('output_variable', 'ai_response')
        context.set_variable(output_variable, ai_response)

        return {"response": ai_response}


class SearchKnowledgeNode(BaseNode):
    """知识库检索节点"""

    async def _execute(self, context: WorkflowContext) -> Dict[str, Any]:
        from core.utils import vector_search_multi
        from services.embedding import EmbeddingService
        from core.config import get_ollama_base_url

        query = context.resolve_value(self.properties.get('query', ''))
        knowledge_base_id = self.properties.get('knowledge_base_id')
        top_k = self.properties.get('top_k', 3)
        similarity_threshold = self.properties.get('similarity_threshold', 0.5)
        output_variable = self.properties.get('output_variable', 'search_results')

        # 生成查询向量
        embedding_model = 'nomic-embed-text'
        embedding_service = EmbeddingService(embedding_model, get_ollama_base_url())
        query_embedding = await embedding_service.generate(query)

        if not query_embedding:
            raise ValueError("Failed to generate query embedding")

        # 检索
        from core import config
        results = await vector_search_multi(
            pool_ref=config.pool,
            query_embedding=query_embedding,
            kb_ids=[knowledge_base_id] if knowledge_base_id else [],
            top_k=top_k,
            threshold=similarity_threshold
        )

        context.set_variable(output_variable, results)

        return {"results": results, "count": len(results)}


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
            NodeType.START.value: StartNode,
            NodeType.AI_CHAT.value: AIChatNode,
            NodeType.SEARCH_KNOWLEDGE.value: SearchKnowledgeNode,
            NodeType.CONDITION.value: ConditionNode,
            NodeType.LOOP.value: LoopNode,
            NodeType.VARIABLE_ASSIGN.value: VariableAssignNode,
            NodeType.REPLY.value: ReplyNode,
            NodeType.TOOL.value: ToolNode,
        }

    def register_node(self, node_type: str, node_class: type[BaseNode]):
        """注册自定义节点类型"""
        self.node_classes[node_type] = node_class

    async def execute(
        self,
        workflow_definition: Dict[str, Any],
        input_data: Dict[str, Any] = None
    ) -> WorkflowContext:
        """
        执行工作流

        Args:
            workflow_definition: 工作流定义
            input_data: 输入数据

        Returns:
            工作流执行上下文（包含所有变量和结果）
        """
        context = WorkflowContext()

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
            node_class = self.node_classes.get(
                node_data['type'],
                lambda nid, nt, props: BaseNode(nid, nt, props)
            )
            node_map[node_data['id']] = node_class(
                node_data['id'],
                node_data['type'],
                node_data.get('properties', {})
            )

        # 构建邻接表
        adjacency = {node_id: [] for node_id in node_map}
        edge_map = {}
        for edge in edges:
            source = edge['sourceNodeId']
            target = edge['targetNodeId']
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
    input_data: Dict[str, Any] = None
) -> WorkflowContext:
    """
    执行工作流（便捷函数）

    Args:
        workflow_definition: 工作流定义
        input_data: 输入数据

    Returns:
        工作流执行上下文
    """
    return await workflow_engine.execute(workflow_definition, input_data)
