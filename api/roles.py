# 角色管理路由
# /api/v1/system/roles/* 相关接口

import uuid
from core import config, audit_log, audit_log_with_changes

import logging
from typing import Dict, Optional, List
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from .models import CreateRoleRequest, UpdateRoleRequest, AssignRoleRequest
from .dependencies import validate_pagination
from .auth import get_current_user
from .permission import PermissionChecker, AdminOrAbove

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/api/v1/system/roles", tags=["角色管理"])


@router.get("")
async def list_roles(
    page: int = 1,
    page_size: int = 20,
    name: Optional[str] = None,
    status: Optional[int] = None,
    user: Dict = Depends(AdminOrAbove)
):
    """获取角色列表 - 需要管理员权限"""
    page, page_size = validate_pagination(page, page_size)
    pool = config.pool

    async with pool.connection() as conn:
        # 构建查询条件
        conditions = ["r.deleted_at IS NULL"]
        params = []

        if name:
            conditions.append("r.name LIKE %s")
            params.append(f"%{name}%")
        if status is not None:
            conditions.append("r.status = %s")
            params.append(status)

        where_clause = " AND ".join(conditions)

        # 获取总数
        async with conn.cursor(row_factory=dict_row) as cur_count:
            await cur_count.execute(f"""
                SELECT COUNT(*) as total
                FROM system_role r
                WHERE {where_clause}
            """, params)
            total_result = await cur_count.fetchone()
            total = total_result['total'] if total_result else 0

        # 获取分页数据
        offset = (page - 1) * page_size

        async with conn.cursor(row_factory=dict_row) as cur:
            main_query = f"""
                SELECT
                    r.*,
                    COUNT(DISTINCT ur.user_id) as user_count
                FROM system_role r
                LEFT JOIN system_user_role ur ON r.id = ur.role_id
                WHERE {where_clause}
                GROUP BY r.id
                ORDER BY r.sort
                LIMIT {page_size} OFFSET {offset}
            """
            await cur.execute(main_query, params)
            roles = await cur.fetchall()

    return {
        "roles": roles,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@router.get("/{role_id}")
async def get_role(role_id: str, user: Dict = Depends(AdminOrAbove)):
    """获取角色详情 - 需要管理员权限"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT r.*,
                       COUNT(DISTINCT ur.user_id) as user_count
                FROM system_role r
                LEFT JOIN system_user_role ur ON r.id = ur.role_id
                WHERE r.id = %s AND r.deleted_at IS NULL
                GROUP BY r.id
            """, (role_id,))
            role = await cur.fetchone()

            if not role:
                raise HTTPException(status_code=404, detail="角色不存在")

            # 获取角色的权限
            await cur.execute("""
                SELECT p.id, p.name, p.code
                FROM system_permission p
                JOIN system_role_permission rp ON p.id = rp.permission_id
                WHERE rp.role_id = %s
            """, (role_id,))
            role['permissions'] = await cur.fetchall()

            # 获取角色的菜单
            await cur.execute("""
                SELECT m.id, m.name, m.type
                FROM system_menu m
                JOIN system_role_menu rm ON m.id = rm.menu_id
                WHERE rm.role_id = %s
                ORDER BY m.sort
            """, (role_id,))
            role['menus'] = await cur.fetchall()

    return role


@router.post("")
@audit_log()
async def create_role(request: CreateRoleRequest, user: Dict = Depends(get_current_user)):
    """创建角色"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    from api.permission import has_permission

    if not await has_permission(user['user_id'], 'system:role:create'):
        raise HTTPException(status_code=403, detail="无权限创建角色")

    pool = config.pool

    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # 检查角色代码是否已存在
                await cur.execute("SELECT id FROM system_role WHERE code = %s AND deleted_at IS NULL", (request.code,))
                if await cur.fetchone():
                    raise HTTPException(status_code=400, detail="角色代码已存在")

                # 创建角色
                role_id = f"role_{request.code}"
                await cur.execute("""
                    INSERT INTO system_role (id, name, code, sort, status, type, data_scope, remark, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """, (role_id, request.name, request.code, request.sort, request.status, 1,
                      request.data_scope, request.remark))

                # 分配菜单权限
                if request.menu_ids:
                    for menu_id in request.menu_ids:
                        await cur.execute("""
                            INSERT INTO system_role_menu (role_id, menu_id)
                            VALUES (%s, %s)
                            ON CONFLICT (role_id, menu_id) DO NOTHING
                        """, (role_id, menu_id))

                await conn.commit()

        return {"id": role_id, "message": "角色创建成功"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建角色失败: {str(e)}")
        raise HTTPException(status_code=500, detail="创建角色失败，请稍后重试")


@router.put("/{role_id}")
@audit_log_with_changes()
async def update_role(
    role_id: str,
    request: UpdateRoleRequest,
    current_user: Dict = Depends(get_current_user)
):
    """更新角色"""
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")

    from api.permission import has_permission

    if not await has_permission(current_user['user_id'], 'system:role:update'):
        raise HTTPException(status_code=403, detail="无权限更新角色")

    pool = config.pool

    # 用于跟踪变更
    changes = {}

    try:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM system_role WHERE id = %s AND deleted_at IS NULL", (role_id,))
                old_role = await cur.fetchone()
                if not old_role:
                    raise HTTPException(status_code=404, detail="角色不存在")

                updates = []
                values = []

                if request.name is not None:
                    changes['name'] = {'old': old_role['name'], 'new': request.name}
                    updates.append("name = %s")
                    values.append(request.name)
                if request.sort is not None:
                    changes['sort'] = {'old': old_role['sort'], 'new': request.sort}
                    updates.append("sort = %s")
                    values.append(request.sort)
                if request.status is not None:
                    changes['status'] = {'old': old_role['status'], 'new': request.status}
                    updates.append("status = %s")
                    values.append(request.status)
                if request.data_scope is not None:
                    changes['data_scope'] = {'old': old_role['data_scope'], 'new': request.data_scope}
                    updates.append("data_scope = %s")
                    values.append(request.data_scope)
                if request.remark is not None:
                    changes['remark'] = {'old': old_role['remark'], 'new': request.remark}
                    updates.append("remark = %s")
                    values.append(request.remark)

                if updates:
                    values.append(role_id)
                    await cur.execute(f"""
                        UPDATE system_role SET {', '.join(updates)}, updated_at = NOW()
                        WHERE id = %s
                    """, values)

                # 更新菜单权限
                if request.menu_ids is not None:
                    changes['menu_ids'] = {'old': 'N/A', 'new': request.menu_ids}
                    await cur.execute("DELETE FROM system_role_menu WHERE role_id = %s", (role_id,))
                    for menu_id in request.menu_ids:
                        await cur.execute("""
                            INSERT INTO system_role_menu (role_id, menu_id)
                            VALUES (%s, %s)
                        """, (role_id, menu_id))

                await conn.commit()

        return {"message": "角色更新成功", "id": role_id, "changes": changes}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新角色失败: {str(e)}")
        raise HTTPException(status_code=500, detail="更新角色失败，请稍后重试")


@router.delete("/{role_id}")
@audit_log()
async def delete_role(role_id: str, current_user: Dict = Depends(get_current_user)):
    """删除角色（软删除）"""
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")

    from api.permission import has_permission

    if not await has_permission(current_user['user_id'], 'system:role:delete'):
        raise HTTPException(status_code=403, detail="无权限删除角色")

    # 不能删除超级管理员角色
    if role_id == "role_super_admin":
        raise HTTPException(status_code=400, detail="不能删除超级管理员角色")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                UPDATE system_role SET deleted_at = NOW()
                WHERE id = %s
            """, (role_id,))
            await conn.commit()

    return {"message": "角色删除成功"}


@router.get("/{role_id}/users")
async def get_role_users(role_id: str, user: Dict = Depends(get_current_user)):
    """获取角色下的用户列表"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    from api.permission import has_permission

    if not await has_permission(user['user_id'], 'system:role:manage'):
        raise HTTPException(status_code=403, detail="无权限访问")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT u.id, u.username, u.email, u.nickname, u.avatar, u.status
                FROM users u
                JOIN system_user_role ur ON u.id = ur.user_id
                WHERE ur.role_id = %s AND u.deleted_at IS NULL
                ORDER BY u.created_at DESC
            """, (role_id,))
            users = await cur.fetchall()

    return {"users": users}
