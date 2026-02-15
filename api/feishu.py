"""
飞书集成相关路由
从 main_pgvector.py 拆分出来
"""
import logging
import uuid
from typing import Dict, Optional

from core import audit_log, audit_log_with_changes
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/integrations/feishu", tags=["feishu"])

# ==================== 全局变量访问函数 ====================
def _get_globals():
    """获取 main_pgvector 模块中的全局变量"""
    # 延迟导入避免循环依赖
    import main_pgvector as mp
    return {
        'pool': mp.pool,
    }


# 导入 get_current_user（从 auth 模块）
from api.auth import get_current_user


# ==================== 数据模型 ====================
class FeishuConfigRequest(BaseModel):
    app_id: str
    app_secret: str


# ==================== 飞书配置 API ====================
@router.get("/config")
async def get_feishu_config():
    """获取飞书配置（只返回 app_id，不返回敏感信息）"""
    g = _get_globals()
    async with g['pool'].connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT app_id FROM feishu_configs LIMIT 1")
            config = await cur.fetchone()

            if config:
                return {
                    "feishu_app_id": config['app_id'],
                    "configured": True
                }
            return {
                "feishu_app_id": "",
                "configured": False
            }


@router.post("/config")
@audit_log(entity_type="system_config", action="update")
async def save_feishu_config(
    request: FeishuConfigRequest,
    user: Dict = Depends(get_current_user)
):
    """保存飞书配置（仅管理员）"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if user.get('role') != 'super_admin':
        raise HTTPException(status_code=403, detail="只有管理员可以配置飞书集成")

    g = _get_globals()
    async with g['pool'].connection() as conn:
        async with conn.cursor() as cur:
            # 删除旧配置并插入新配置
            await cur.execute("DELETE FROM feishu_configs")
            config_id = str(uuid.uuid4())
            await cur.execute("""
                INSERT INTO feishu_configs (id, app_id, app_secret)
                VALUES (%s, %s, %s)
            """, (config_id, request.app_id, request.app_secret))
            await conn.commit()

            return {"message": "飞书配置保存成功"}


@router.delete("/config")
@audit_log(entity_type="system_config", action="delete")
async def delete_feishu_config(
    user: Dict = Depends(get_current_user)
):
    """删除飞书配置（仅管理员）"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if user.get('role') != 'super_admin':
        raise HTTPException(status_code=403, detail="只有管理员可以删除飞书配置")

    g = _get_globals()
    async with g['pool'].connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM feishu_configs")
            await conn.commit()

            return {"message": "飞书配置已删除"}


# ==================== 飞书知识库同步 API ====================
@router.get("/wiki/nodes")
async def get_feishu_wiki_nodes(
    app_id: str,
    app_secret: str,
    parent_node_token: Optional[str] = None,
    user: Dict = Depends(get_current_user)
):
    """获取飞书知识库节点列表"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if user.get('role') != 'super_admin':
        raise HTTPException(status_code=403, detail="只有管理员可以同步飞书知识库")

    async with httpx.AsyncClient() as http_client:
        # 获取 tenant_access_token
        token_response = await http_client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": app_id,
                "app_secret": app_secret
            }
        )
        token_data = token_response.json()

        if token_data.get('code') != 0:
            raise HTTPException(status_code=400, detail=f"获取飞书 token 失败: {token_data}")

        tenant_access_token = token_data.get('tenant_access_token')

        # 获取知识库列表
        if parent_node_token:
            url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{parent_node_token}/blocks"
        else:
            url = "https://open.feishu.cn/open-apis/wiki/v2/spaces"

        response = await http_client.get(
            url,
            headers={"Authorization": f"Bearer {tenant_access_token}"}
        )
        data = response.json()

        if data.get('code') != 0:
            raise HTTPException(status_code=400, detail=f"获取飞书知识库失败: {data}")

        return {
            "raw_response": data,  # 返回完整响应用于调试
            "nodes": data.get('data', {}).get('items', [])
        }


@router.get("/debug")
async def debug_feishu_api(
    app_id: str,
    app_secret: str,
    space_id: Optional[str] = None,
    user: Dict = Depends(get_current_user)
):
    """调试飞书 API - 查看实际返回数据"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if user.get('role') != 'super_admin':
        raise HTTPException(status_code=403, detail="只有管理员可以调试")

    async with httpx.AsyncClient() as http_client:
        # 获取 tenant_access_token
        token_response = await http_client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": app_id,
                "app_secret": app_secret
            }
        )
        token_data = token_response.json()

        if token_data.get('code') != 0:
            return {"error": "获取 token 失败", "token_response": token_data}

        tenant_access_token = token_data.get('tenant_access_token')

        # 测试多个 API 端点
        results = {}

        # 1. 获取知识库空间列表（v2）
        try:
            resp1 = await http_client.get(
                "https://open.feishu.cn/open-apis/wiki/v2/spaces",
                headers={"Authorization": f"Bearer {tenant_access_token}"}
            )
            results['spaces_v2'] = {
                "status_code": resp1.status_code,
                "json": resp1.json()
            }
        except Exception as e:
            results['spaces_v2'] = {"error": str(e)}

        # 2. 如果提供了 space_id，获取该空间的节点
        if space_id:
            try:
                resp_nodes = await http_client.get(
                    f"https://open.feishu.cn/open-apis/wiki/v2/spaces/{space_id}/nodes",
                    headers={"Authorization": f"Bearer {tenant_access_token}"}
                )
                results['space_nodes'] = {
                    "status_code": resp_nodes.status_code,
                    "json": resp_nodes.json()
                }
            except Exception as e:
                results['space_nodes'] = {"error": str(e)}

        # 3. 尝试获取文档列表（用不同的方式）
        try:
            resp_list = await http_client.get(
                "https://open.feishu.cn/open-apis/docx/v1/documents/lists",
                headers={"Authorization": f"Bearer {tenant_access_token}"}
            )
            results['doc_list'] = {
                "status_code": resp_list.status_code,
                "json": resp_list.json() if resp_list.status_code == 200 else resp_list.text
            }
        except Exception as e:
            results['doc_list'] = {"error": str(e)}

        return results


@router.get("/sync/{task_id}")
async def get_sync_status(task_id: str, user: Dict = Depends(get_current_user)):
    """获取同步任务状态"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    g = _get_globals()
    async with g['pool'].connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
            task = await cur.fetchone()

            if not task:
                raise HTTPException(status_code=404, detail="任务不存在")

            return {
                "task_id": task['id'],
                "type": task['type'],
                "status": task['status'],
                "result": task.get('result'),
                "error_message": task.get('error_message'),
                "completed_at": task['completed_at'].isoformat() if task['completed_at'] else None
            }


@router.get("/doc/{node_token}")
async def get_feishu_doc_content(
    node_token: str,
    app_id: str,
    app_secret: str,
    user: Dict = Depends(get_current_user)
):
    """获取飞书文档内容"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if user.get('role') != 'super_admin':
        raise HTTPException(status_code=403, detail="只有管理员可以访问飞书内容")

    async with httpx.AsyncClient() as http_client:
        # 获取 tenant_access_token
        token_response = await http_client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": app_id,
                "app_secret": app_secret
            }
        )
        token_data = token_response.json()

        if token_data.get('code') != 0:
            raise HTTPException(status_code=400, detail=f"获取飞书 token 失败: {token_data}")

        tenant_access_token = token_data.get('tenant_access_token')

        # 获取文档内容
        response = await http_client.get(
            f"https://open.feishu.cn/open-apis/docx/v1/documents/{node_token}/raw_content",
            headers={"Authorization": f"Bearer {tenant_access_token}"}
        )
        data = response.json()

        if data.get('code') != 0:
            raise HTTPException(status_code=400, detail=f"获取文档内容失败: {data}")

        return {
            "content": data.get('data', {}).get('content', ''),
            "node_token": node_token
        }
