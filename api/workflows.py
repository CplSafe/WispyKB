# 工作流和任务管理路由
# /api/v1/tasks/* 和 /api/v1/workflows/* 相关接口

import uuid
import os
import base64
from datetime import datetime
from core import config, audit_log, audit_log_with_changes
from core.config import UPLOAD_DIR

import json
import logging
from typing import Dict, List, Optional, Any, Union
from enum import Enum
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from psycopg.rows import dict_row

from .auth import get_current_user

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/api/v1", tags=["工作流和任务管理"])

# 图标上传目录 - 使用统一的 UPLOAD_DIR
ICON_UPLOAD_DIR = UPLOAD_DIR / "workflow_icons"
ICON_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ==================== 数据模型 ====================

class NodeType(str, Enum):
    """工作流节点类型"""
    START = "start"           # 开始节点
    END = "end"             # 结束节点
    LLM = "llm"             # LLM 调用节点
    INPUT = "input"         # 输入节点
    OUTPUT = "output"       # 输出节点
    HUMAN = "human"         # 人工介入节点
    KNOWLEDGE = "knowledge"    # 知识库检索节点
    CODE = "code"             # 代码执行节点
    CONDITION = "condition"    # 条件判断节点
    TEMPLATE = "template"      # 模板转换节点
    HTTP = "http"             # HTTP 请求节点


class WorkflowNode(BaseModel):
    """工作流节点"""
    id: str
    type: NodeType
    name: str
    config: Dict[str, Any] = {}
    position: Dict[str, float] = {"x": 0, "y": 0}
    inputs: List[str] = []
    outputs: List[str] = []


class WorkflowEdge(BaseModel):
    """工作流连线"""
    id: str
    source: str    # 源节点ID
    target: str    # 目标节点ID
    condition: Optional[str] = None  # 条件表达式


class WorkflowDefinition(BaseModel):
    """工作流定义"""
    nodes: List[WorkflowNode]
    edges: List[WorkflowEdge]
    variables: Dict[str, Any] = {}  # 工作流变量


class CreateWorkflowRequest(BaseModel):
    """创建工作流请求"""
    name: str
    description: Optional[str] = None
    icon: Optional[str] = None
    definition: Optional[Dict[str, Any]] = None  # 接受任意 JSON 结构


class UpdateWorkflowRequest(BaseModel):
    """更新工作流请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    icon: Optional[str] = None
    definition: Optional[Dict[str, Any]] = None


class PublishWorkflowRequest(BaseModel):
    """发布工作流请求"""
    description: Optional[str] = None


# ==================== 辅助函数 ====================

def get_main_module():
    """延迟获取主模块，避免循环导入"""
    import sys
    _main_pgvector = sys.modules.get('main_pgvector')
    if _main_pgvector is None:
        import importlib
        _main_pgvector = importlib.import_module('main_pgvector')
    return _main_pgvector


# ==================== 任务管理 API ====================

@router.get("/tasks/{task_id}")
async def get_task_status(task_id: str, user: Dict = Depends(get_current_user)):
    """获取任务状态"""
    task_queue = main_pgvector.task_queue

    task = await task_queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    user: Dict = Depends(get_current_user)
):
    """列出任务"""
    task_queue = main_pgvector.task_queue

    user_id = user.get('user_id') if user else None
    tasks = await task_queue.list_tasks(
        status=status,
        task_type=task_type,
        created_by=user_id,
        limit=limit,
        offset=offset
    )
    return {"tasks": tasks, "count": len(tasks)}


@router.delete("/tasks/{task_id}")
@audit_log(entity_type="task", action="cancel")
async def cancel_task(task_id: str, user: Dict = Depends(get_current_user)):
    """取消/删除任务"""
    pool = config.pool
    task_queue = main_pgvector.task_queue

    task = await task_queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    # 只有任务创建者可以取消
    if task.get('created_by') != user.get('user_id'):
        raise HTTPException(status_code=403, detail="无权操作此任务")

    # 更新状态为已取消
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                UPDATE async_tasks
                SET status = 'cancelled', updated_at = NOW(), completed_at = NOW()
                WHERE id = %s AND status IN ('pending', 'processing')
            """, (task_id,))
            await conn.commit()

    return {"success": True, "message": "任务已取消"}


# ==================== 工作流管理 API ====================

@router.post("/workflows")
@audit_log()
async def create_workflow(
    request: CreateWorkflowRequest,
    user: Dict = Depends(get_current_user)
):
    """创建工作流"""
    pool = config.pool

    user_id = user.get('user_id') if user else None
    workflow_id = str(uuid.uuid4())

    # 处理 definition，支持任意 JSON 结构
    definition_data = request.definition if request.definition else {}

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 检查是否有 icon 列
            await cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'workflows' AND column_name = 'icon'
            """)
            has_icon_column = await cur.fetchone()

            if has_icon_column:
                await cur.execute("""
                    INSERT INTO workflows (id, name, description, icon, definition, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    workflow_id,
                    request.name,
                    request.description,
                    request.icon,
                    json.dumps(definition_data, ensure_ascii=False),
                    user_id
                ))
            else:
                await cur.execute("""
                    INSERT INTO workflows (id, name, description, definition, created_by)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    workflow_id,
                    request.name,
                    request.description,
                    json.dumps(definition_data, ensure_ascii=False),
                    user_id
                ))
            await conn.commit()

    return {
        "success": True,
        "data": {
            "id": workflow_id,
            "name": request.name,
            "icon": request.icon,
            "description": request.description
        }
    }


@router.get("/workflows")
async def list_workflows(
    limit: int = 50,
    offset: int = 0,
    user: Dict = Depends(get_current_user)
):
    """列出工作流"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT * FROM workflows
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, (limit, offset))
            workflows = await cur.fetchall()

    return {"workflows": workflows, "count": len(workflows)}


@router.get("/workflows/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    user: Dict = Depends(get_current_user)
):
    """获取工作流详情"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM workflows WHERE id = %s", (workflow_id,))
            workflow = await cur.fetchone()

            if not workflow:
                raise HTTPException(status_code=404, detail="工作流不存在")

    return {
        "success": True,
        "data": workflow
    }


@router.put("/workflows/{workflow_id}")
@audit_log_with_changes()
async def update_workflow(
    workflow_id: str,
    request: UpdateWorkflowRequest,
    user: Dict = Depends(get_current_user)
):
    """更新工作流"""
    pool = config.pool

    # 用于跟踪变更
    changes = {}

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM workflows WHERE id = %s", (workflow_id,))
            workflow = await cur.fetchone()

            if not workflow:
                raise HTTPException(status_code=404, detail="工作流不存在")

            # 构建更新字段
            update_fields = []
            update_values = []

            if request.name is not None:
                changes['name'] = {'old': workflow['name'], 'new': request.name}
                update_fields.append("name = %s")
                update_values.append(request.name)

            if request.description is not None:
                changes['description'] = {'old': workflow['description'], 'new': request.description}
                update_fields.append("description = %s")
                update_values.append(request.description)

            if request.icon is not None:
                changes['icon'] = {'old': workflow.get('icon'), 'new': request.icon}
                update_fields.append("icon = %s")
                update_values.append(request.icon)

            if request.definition is not None:
                changes['definition'] = {'old': 'updated', 'new': 'updated'}
                update_fields.append("definition = %s")
                update_values.append(json.dumps(request.definition, ensure_ascii=False))

            if update_fields:
                update_fields.append("version = version + 1")
                update_fields.append("updated_at = NOW()")
                update_values.append(workflow_id)

                await cur.execute(f"""
                    UPDATE workflows
                    SET {', '.join(update_fields)}
                    WHERE id = %s
                """, update_values)
                await conn.commit()

    return {
        "success": True,
        "data": {
            "id": workflow_id,
            "name": request.name or workflow['name'],
            "description": request.description,
            "icon": request.icon,
            "updatedAt": datetime.now().isoformat()
        }
    }


@router.delete("/workflows/{workflow_id}")
@audit_log()
async def delete_workflow(
    workflow_id: str,
    user: Dict = Depends(get_current_user)
):
    """删除工作流"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM workflows WHERE id = %s", (workflow_id,))
            await conn.commit()

    return {"success": True, "message": "工作流删除成功"}


@router.post("/workflows/{workflow_id}/publish")
@audit_log()
async def publish_workflow(
    workflow_id: str,
    request: PublishWorkflowRequest = None,
    user: Dict = Depends(get_current_user)
):
    """发布工作流"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 获取当前工作流
            await cur.execute("SELECT * FROM workflows WHERE id = %s", (workflow_id,))
            workflow = await cur.fetchone()

            if not workflow:
                raise HTTPException(status_code=404, detail="工作流不存在")

            # 更新发布状态
            await cur.execute("""
                UPDATE workflows
                SET is_published = true, updated_at = NOW()
                WHERE id = %s
            """, (workflow_id,))

            # 创建版本快照（保存到 workflow_versions 表，如果存在的话）
            version_id = str(uuid.uuid4())
            try:
                await cur.execute("""
                    INSERT INTO workflow_versions (id, workflow_id, version, definition, description, is_published, created_by, created_at)
                    VALUES (%s, %s, %s, %s, %s, true, %s, NOW())
                """, (
                    version_id,
                    workflow_id,
                    workflow['version'],
                    json.dumps(workflow['definition'], ensure_ascii=False),
                    request.description if request else None,
                    user.get('user_id') if user else None
                ))
            except Exception as e:
                # 如果 workflow_versions 表不存在，忽略错误
                logger.warning(f"Failed to create version snapshot: {e}")

            await conn.commit()

    return {
        "success": True,
        "data": {
            "id": workflow_id,
            "version": workflow['version'],
            "publishedAt": datetime.now().isoformat()
        }
    }


@router.get("/workflows/{workflow_id}/versions")
async def get_workflow_versions(
    workflow_id: str,
    limit: int = 20,
    offset: int = 0,
    user: Dict = Depends(get_current_user)
):
    """获取工作流版本历史"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 首先检查工作流是否存在
            await cur.execute("SELECT id FROM workflows WHERE id = %s", (workflow_id,))
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="工作流不存在")

            # 尝试从 workflow_versions 表获取版本历史
            versions = []
            try:
                await cur.execute("""
                    SELECT id, version, description, is_published, created_at
                    FROM workflow_versions
                    WHERE workflow_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s OFFSET %s
                """, (workflow_id, limit, offset))
                versions = await cur.fetchall()
            except Exception as e:
                logger.warning(f"workflow_versions table not found: {e}")

            # 如果没有版本表或没有数据，返回当前版本作为唯一版本
            if not versions:
                await cur.execute("""
                    SELECT id, version, is_published, updated_at as created_at
                    FROM workflows WHERE id = %s
                """, (workflow_id,))
                current = await cur.fetchone()
                if current:
                    versions = [{
                        "id": current['id'],
                        "version": str(current['version']),
                        "description": "当前版本",
                        "isPublished": current['is_published'],
                        "createdAt": current['created_at'].isoformat() if current['created_at'] else None
                    }]

    return {
        "success": True,
        "data": versions
    }


@router.post("/workflows/{workflow_id}/icon")
async def upload_workflow_icon(
    workflow_id: str,
    file: UploadFile = File(...),
    user: Dict = Depends(get_current_user)
):
    """上传工作流图标"""
    pool = config.pool

    # 验证文件类型
    allowed_types = ["image/jpeg", "image/png", "image/gif", "image/webp"]
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="不支持的图片格式，仅支持 JPEG、PNG、GIF、WebP")

    # 验证文件大小（最大 2MB）
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="图片大小不能超过 2MB")

    # 生成文件名
    file_ext = file.filename.split('.')[-1] if '.' in file.filename else 'png'
    filename = f"{workflow_id}_{uuid.uuid4().hex[:8]}.{file_ext}"
    filepath = os.path.join(ICON_UPLOAD_DIR, filename)

    # 保存文件
    with open(filepath, 'wb') as f:
        f.write(content)

    # 生成 URL - 使用静态文件服务的路径
    icon_url = f"/static/files/workflow_icons/{filename}"

    # 更新数据库
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 检查工作流是否存在
            await cur.execute("SELECT id FROM workflows WHERE id = %s", (workflow_id,))
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="工作流不存在")

            # 检查是否有 icon 列
            await cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'workflows' AND column_name = 'icon'
            """)
            has_icon_column = await cur.fetchone()

            if has_icon_column:
                await cur.execute("""
                    UPDATE workflows SET icon = %s, updated_at = NOW() WHERE id = %s
                """, (icon_url, workflow_id))
                await conn.commit()
            else:
                # 如果没有 icon 列，尝试添加
                try:
                    await cur.execute("ALTER TABLE workflows ADD COLUMN icon TEXT")
                    await cur.execute("""
                        UPDATE workflows SET icon = %s, updated_at = NOW() WHERE id = %s
                    """, (icon_url, workflow_id))
                    await conn.commit()
                except Exception as e:
                    logger.warning(f"Failed to add icon column: {e}")

    return {
        "success": True,
        "data": {
            "icon": icon_url
        }
    }


@router.post("/workflows/{workflow_id}/execute")
async def execute_workflow(
    workflow_id: str,
    inputs: Dict[str, Any],
    user: Dict = Depends(get_current_user)
):
    """执行工作流"""
    workflow_engine = main_pgvector.workflow_engine

    user_id = user.get('user_id') if user else None

    if not workflow_engine:
        raise HTTPException(status_code=500, detail="工作流引擎未初始化")

    result = await workflow_engine.execute(workflow_id, inputs, user_id)
    return result


@router.get("/workflows/{workflow_id}/executions")
async def list_workflow_executions(
    workflow_id: str,
    limit: int = 50,
    offset: int = 0,
    user: Dict = Depends(get_current_user)
):
    """列出工作流执行记录"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT * FROM workflow_executions
                WHERE workflow_id = %s
                ORDER BY started_at DESC
                LIMIT %s OFFSET %s
            """, (workflow_id, limit, offset))
            executions = await cur.fetchall()

    return {"executions": executions, "count": len(executions)}


@router.get("/workflows/executions/{execution_id}")
async def get_workflow_execution(
    execution_id: str,
    user: Dict = Depends(get_current_user)
):
    """获取工作流执行详情"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM workflow_executions WHERE id = %s", (execution_id,))
            execution = await cur.fetchone()

            if not execution:
                raise HTTPException(status_code=404, detail="执行记录不存在")

    return execution


@router.post("/workflows/executions/{execution_id}/pause")
async def pause_workflow_execution(
    execution_id: str,
    user: Dict = Depends(get_current_user)
):
    """暂停工作流执行"""
    workflow_engine = main_pgvector.workflow_engine

    if not workflow_engine:
        raise HTTPException(status_code=500, detail="工作流引擎未初始化")

    user_id = user.get('user_id') if user else None
    result = await workflow_engine.pause(execution_id, user_id)
    return result


@router.post("/workflows/executions/{execution_id}/resume")
async def resume_workflow_execution(
    execution_id: str,
    human_input: Optional[Dict[str, Any]] = None,
    user: Dict = Depends(get_current_user)
):
    """恢复工作流执行"""
    workflow_engine = main_pgvector.workflow_engine

    if not workflow_engine:
        raise HTTPException(status_code=500, detail="工作流引擎未初始化")

    user_id = user.get('user_id') if user else None
    result = await workflow_engine.resume(execution_id, user_id, human_input)
    return result


@router.post("/workflows/executions/{execution_id}/input")
async def submit_workflow_human_input(
    execution_id: str,
    input_data: Dict[str, Any],
    user: Dict = Depends(get_current_user)
):
    """提交工作流人工输入"""
    workflow_engine = main_pgvector.workflow_engine

    if not workflow_engine:
        raise HTTPException(status_code=500, detail="工作流引擎未初始化")

    user_id = user.get('user_id') if user else None
    result = await workflow_engine.submit_human_input(execution_id, input_data, user_id)
    return result


@router.get("/workflows/executions/{execution_id}/status")
async def get_workflow_execution_status(
    execution_id: str,
    user: Dict = Depends(get_current_user)
):
    """获取工作流执行状态（轮询用）"""
    workflow_engine = main_pgvector.workflow_engine

    if not workflow_engine:
        raise HTTPException(status_code=500, detail="工作流引擎未初始化")

    execution = await workflow_engine.get_execution_status(execution_id)

    # 返回精简的状态信息
    return {
        "execution_id": execution['id'],
        "workflow_id": execution['workflow_id'],
        "status": execution['status'],
        "started_at": execution['started_at'].isoformat() if execution['started_at'] else None,
        "completed_at": execution['completed_at'].isoformat() if execution['completed_at'] else None,
        "pending_human_input_node_id": execution.get('pending_human_input_node_id'),
        "current_node_id": execution.get('current_node_id'),
        "outputs": execution.get('outputs'),
        "error": execution.get('error')
    }
