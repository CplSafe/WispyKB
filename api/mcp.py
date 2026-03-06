"""
MCP (Model Context Protocol) 相关路由
从 main_pgvector.py 拆分出来
"""
import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Union

from core import audit_log, audit_log_with_changes
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/mcp", tags=["mcp"])

# ==================== 全局变量访问函数 ====================
def _get_globals():
    """获取 main_pgvector 模块中的全局变量"""
    # 延迟导入避免循环依赖
    import main_pgvector as mp
    return {
        'mcp_server': mp.mcp_server,
        'get_current_user': mp.get_current_user,
    }


# ==================== 数据模型 ====================
class MCPRequest(BaseModel):
    """MCP JSON-RPC 请求模型"""
    jsonrpc: str = "2.0"
    id: Optional[Union[str, int]] = None
    method: str
    params: Optional[Dict[str, Any]] = None


class MCPConfigRequest(BaseModel):
    """MCP 服务器配置请求"""
    name: str
    connection_type: str = "http"  # 'http', 'ws', 'sse', 'stdio'
    url: Optional[str] = None      # HTTP/SSE/WS 模式需要
    command: Optional[str] = None  # stdio 模式需要
    args: Optional[List[str]] = None  # stdio 模式的命令参数
    headers: Optional[Dict[str, str]] = None
    auth_token: Optional[str] = None
    api_key: Optional[str] = None


class MCPTestRequest(BaseModel):
    """MCP 连接测试请求"""
    url: Optional[str] = None      # HTTP 测试
    command: Optional[str] = None  # stdio 测试
    args: Optional[List[str]] = None
    auth_token: Optional[str] = None
    api_key: Optional[str] = None


# ==================== MCP 工具 API ====================
@router.get("/tools")
async def list_mcp_tools(
    include_remote: bool = True,
    user: Dict = Depends(lambda: _get_globals()['get_current_user'])
):
    """列出所有 MCP 工具（本地 + 远程）"""
    mcp_server = _get_globals()['mcp_server']
    if not mcp_server:
        return {'tools': [], 'count': 0}

    tools = await mcp_server.get_all_tools()
    return {'tools': tools, 'count': len(tools)}


@router.post("/tools/{tool_name}/invoke")
async def invoke_mcp_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    source: Optional[str] = None,
    user: Dict = Depends(lambda: _get_globals()['get_current_user'])
):
    """调用 MCP 工具"""
    mcp_server = _get_globals()['mcp_server']
    if not mcp_server:
        raise HTTPException(status_code=500, detail="MCP 服务未初始化")

    result = await mcp_server.call_tool(tool_name, arguments, source)

    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])

    return result


# ==================== MCP 配置 API ====================
@router.get("/configs")
async def list_mcp_configs(user: Dict = Depends(lambda: _get_globals()['get_current_user'])):
    """获取所有 MCP 服务器配置"""
    mcp_server = _get_globals()['mcp_server']
    if not mcp_server:
        return {'configs': [], 'count': 0}

    try:
        configs = await mcp_server.get_remote_configs()
    except Exception as e:
        logger.error(f"获取 MCP 配置失败: {e}")
        return {'configs': [], 'count': 0}
    return {'configs': configs, 'count': len(configs)}


@router.post("/configs")
@audit_log(entity_type="mcp_config")
async def create_mcp_config(
    config: MCPConfigRequest,
    user: Dict = Depends(lambda: _get_globals()['get_current_user'])
):
    """添加新的 MCP 服务器配置（支持本地和远程）"""
    mcp_server = _get_globals()['mcp_server']
    if not mcp_server:
        raise HTTPException(status_code=500, detail="MCP 服务未初始化")

    # 验证参数
    if config.connection_type != 'stdio' and not config.url:
        raise HTTPException(status_code=400, detail="HTTP/SSE/WS 模式需要提供 URL")

    if config.connection_type == 'stdio' and not config.command:
        raise HTTPException(status_code=400, detail="stdio 模式需要提供 command")

    user_id = user.get('user_id') if user else None
    config_id = str(uuid.uuid4())

    try:
        result = await mcp_server.add_remote_server(
            config_id=config_id,
            name=config.name,
            connection_type=config.connection_type,
            url=config.url or "",
            command=config.command,
            args=config.args,
            headers=config.headers,
            auth_token=config.auth_token,
            api_key=config.api_key
        )

        return {
            'id': config_id,
            'message': 'MCP 服务器添加成功',
            **result
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"添加失败: {str(e)}")


@router.delete("/configs/{config_id}")
@audit_log(entity_type="mcp_config", action="delete")
async def delete_mcp_config(
    config_id: str,
    user: Dict = Depends(lambda: _get_globals()['get_current_user'])
):
    """删除 MCP 服务器配置"""
    mcp_server = _get_globals()['mcp_server']
    if not mcp_server:
        raise HTTPException(status_code=500, detail="MCP 服务未初始化")

    await mcp_server.remove_remote_server(config_id)
    return {'message': 'MCP 服务器已删除'}


@router.get("/configs/{config_id}/tools")
async def get_mcp_config_tools(
    config_id: str,
    user: Dict = Depends(lambda: _get_globals()['get_current_user'])
):
    """获取指定 MCP 配置的工具列表"""
    mcp_server = _get_globals()['mcp_server']
    if not mcp_server:
        raise HTTPException(status_code=500, detail="MCP 服务未初始化")

    if config_id not in mcp_server._remote_clients:
        raise HTTPException(status_code=404, detail="配置不存在")

    client = mcp_server._remote_clients[config_id]
    return {
        'config_id': config_id,
        'name': client.name,
        'tools': client._tools,
        'count': len(client._tools)
    }


@router.post("/test-connection")
async def test_mcp_connection(
    request: MCPTestRequest,
    user: Dict = Depends(lambda: _get_globals()['get_current_user'])
):
    """测试 MCP 服务器连接"""
    mcp_server = _get_globals()['mcp_server']
    if not mcp_server:
        raise HTTPException(status_code=500, detail="MCP 服务未初始化")

    result = await mcp_server.test_connection(
        url=request.url,
        auth_token=request.auth_token,
        api_key=request.api_key
    )

    return result


@router.get("/agent-capabilities")
async def get_agent_capabilities(user: Dict = Depends(lambda: _get_globals()['get_current_user'])):
    """
    获取 Agent 能力列表

    返回系统支持的所有能力，供 Agent 使用
    """
    mcp_server = _get_globals()['mcp_server']
    if not mcp_server:
        raise HTTPException(status_code=500, detail="MCP 服务未初始化")

    tools = await mcp_server.get_all_tools()

    # 按分类组织工具
    tools_by_category: Dict[str, List[Dict]] = {}
    for tool in tools:
        category = tool.get('category', 'other')
        if category not in tools_by_category:
            tools_by_category[category] = []
        tools_by_category[category].append({
            'name': tool['name'],
            'description': tool['description'],
            'source': tool.get('source', 'local')
        })

    return {
        "version": "1.0.0",
        "capabilities": {
            "knowledge_base": {
                "search": True,
                "retrieve": True,
                "multi_kb": True
            },
            "llm": {
                "chat": True,
                "streaming": True,
                "models": ["llama3", "llama2", "qwen", "mistral"]
            },
            "workflow": {
                "execute": True,
                "pause_resume": True,
                "human_input": True
            },
            "retrieval": {
                "vector": True,
                "keyword": True,
                "hybrid": True,
                "rerank": True
            },
            "mcp": {
                "dynamic_config": True,
                "remote_servers": True,
                "tool_count": len(tools)
            }
        },
        "tools_by_category": tools_by_category,
        "total_tools": len(tools)
    }


# ==================== 工作流编辑器专用 MCP 服务 API ====================
# 为工作流编辑器提供简化的 MCP 服务列表接口

# 创建单独的路由用于工作流编辑器
mcp_services_router = APIRouter(prefix="/api/v1/mcp-services", tags=["MCP服务列表"])


@mcp_services_router.get("")
async def list_mcp_services_for_workflow(user: Dict = Depends(lambda: _get_globals()['get_current_user'])):
    """
    获取 MCP 服务列表（供工作流编辑器使用）

    返回格式: {"success": true, "data": [{"id": "...", "name": "...", "methods": [...]}]}
    """
    mcp_server = _get_globals()['mcp_server']
    if not mcp_server:
        return {"success": False, "error": "MCP 服务未初始化"}

    try:
        # 获取所有 MCP 配置
        configs = await mcp_server.get_remote_configs()

        # 转换为工作流编辑器需要的格式
        services = []
        for config in configs:
            # 获取该服务的工具列表
            config_id = config.get('id', '')
            tools = []

            if config_id in mcp_server._remote_clients:
                client = mcp_server._remote_clients[config_id]
                # 提取工具名称作为方法列表
                tools = [tool.get('name', '') for tool in client._tools]

            services.append({
                'id': config_id,
                'name': config.get('name', 'Unknown'),
                'description': config.get('description', ''),
                'methods': tools,
                'connection_type': config.get('connection_type', 'http')
            })

        return {
            "success": True,
            "data": services
        }
    except Exception as e:
        logger.error(f"Error listing MCP services: {e}")
        return {
            "success": False,
            "error": str(e)
        }

