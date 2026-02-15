# 用户个人资料路由
# /api/v1/user/* 相关接口

import logging
from typing import Optional, Dict

from core import audit_log, audit_log_with_changes
from fastapi import APIRouter, Depends, HTTPException, Form
from psycopg.rows import dict_row

from .auth import get_current_user

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/api/v1/user", tags=["用户个人"])


@router.get("/me")
async def get_current_user_info(user: Dict = Depends(get_current_user)):
    """获取当前用户信息"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    import core.config
    pool = core.config.pool
    if not pool:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, username, email, role, avatar, created_at FROM users WHERE id = %s",
                (user['user_id'],)
            )
            user_info = await cur.fetchone()

    if not user_info:
        raise HTTPException(status_code=404, detail="用户不存在")

    return {
        "id": user_info['id'],
        "username": user_info['username'],
        "email": user_info['email'],
        "role": user_info['role'],
        "avatar": user_info.get('avatar'),
        "created_at": user_info['created_at'].isoformat() if user_info['created_at'] else None
    }


@router.put("/profile")
@audit_log_with_changes()
async def update_user_profile(
    username: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    avatar: Optional[str] = Form(None),
    user: Dict = Depends(get_current_user)
):
    """更新用户资料"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    updates = []
    values = []
    changes = {}

    import core.config
    pool = core.config.pool
    if not pool:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    # 获取当前用户信息用于审计
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT username, email, avatar FROM users WHERE id = %s",
                (user['user_id'],)
            )
            current_user = await cur.fetchone()

    # 检查用户名是否被其他用户占用
    if username:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id FROM users WHERE username = %s AND id != %s",
                    (username, user['user_id'])
                )
                if await cur.fetchone():
                    raise HTTPException(status_code=400, detail="用户名已被使用")

        changes['username'] = {'old': current_user['username'], 'new': username}
        updates.append("username = %s")
        values.append(username)

    # 检查邮箱是否被其他用户占用
    if email:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id FROM users WHERE email = %s AND id != %s",
                    (email, user['user_id'])
                )
                if await cur.fetchone():
                    raise HTTPException(status_code=400, detail="邮箱已被使用")

        changes['email'] = {'old': current_user['email'], 'new': email}
        updates.append("email = %s")
        values.append(email)

    if avatar is not None:
        changes['avatar'] = {'old': current_user['avatar'], 'new': avatar}
        updates.append("avatar = %s")
        values.append(avatar)

    if updates:
        values.append(user['user_id'])
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE id = %s",
                    values
                )
                await conn.commit()

    return {"message": "资料更新成功", "changes": changes}


@router.get("/accessible-resources")
async def get_user_accessible_resources(user: Dict = Depends(get_current_user)):
    """获取当前用户可访问的资源列表"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    import core.config
    pool = core.config.pool
    if not pool:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 获取知识库资源
            await cur.execute("""
                SELECT kb.id, kb.name, 'knowledge_base' as resource_type,
                       COALESCE(rp.permissions, ARRAY[]::TEXT[]) as permissions
                FROM knowledge_bases kb
                LEFT JOIN resource_permissions rp ON rp.resource_id = kb.id
                    AND rp.resource_type = 'knowledge_base'
                    AND (rp.user_id = %s OR rp.role_id IN (
                        SELECT role_id FROM system_user_role WHERE user_id = %s
                    ))
                WHERE kb.deleted_at IS NULL
                GROUP BY kb.id, kb.name, rp.permissions
            """, (user['user_id'], user['user_id']))
            knowledge_bases = await cur.fetchall()

            # 获取应用资源
            await cur.execute("""
                SELECT app.id, app.name, 'application' as resource_type,
                       COALESCE(rp.permissions, ARRAY[]::TEXT[]) as permissions
                FROM chat_applications app
                LEFT JOIN resource_permissions rp ON rp.resource_id = app.id
                    AND rp.resource_type = 'application'
                    AND (rp.user_id = %s OR rp.role_id IN (
                        SELECT role_id FROM system_user_role WHERE user_id = %s
                    ))
                WHERE app.deleted_at IS NULL
                GROUP BY app.id, app.name, rp.permissions
            """, (user['user_id'], user['user_id']))
            applications = await cur.fetchall()

    return {
        "knowledge_bases": knowledge_bases,
        "applications": applications
    }


@router.get("/departments")
async def get_user_departments_info(user: Dict = Depends(get_current_user)):
    """获取当前用户的部门信息"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    import core.config
    pool = core.config.pool
    if not pool:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT d.*, parent.name as parent_name
                FROM system_user_dept ud
                JOIN departments d ON ud.dept_id = d.id
                LEFT JOIN departments parent ON d.parent_id = parent.id
                WHERE ud.user_id = %s AND d.deleted_at IS NULL
            """, (user['user_id'],))
            user_depts = await cur.fetchall()

    return {"departments": user_depts}
