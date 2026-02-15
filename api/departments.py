# 部门管理路由
# /api/v1/system/departments/* 相关接口

import uuid
from core import config, audit_log, audit_log_with_changes

import logging
from typing import Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from .models import CreateDepartmentRequest, UpdateDepartmentRequest
from .auth import get_current_user

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/api/v1/system/departments", tags=["部门管理"])


@router.get("")
async def list_departments(
    name: Optional[str] = None,
    status: Optional[int] = None,
    user: Dict = Depends(get_current_user)
):
    """获取部门列表（树形结构）"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    from main_pgvector import has_permission

    if not await has_permission(user['user_id'], 'system:dept:manage'):
        raise HTTPException(status_code=403, detail="无权限访问")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 构建查询条件
            conditions = ["d.deleted_at IS NULL"]
            params = []

            if name:
                conditions.append("d.name LIKE %s")
                params.append(f"%{name}%")
            if status is not None:
                conditions.append("d.status = %s")
                params.append(status)

            where_clause = " AND ".join(conditions)

            query = f"""
                SELECT
                  d.*,
                  (SELECT COUNT(*) FROM system_user_dept ud WHERE ud.dept_id = d.id) as user_count
                FROM departments d
                WHERE {where_clause}
                ORDER BY d.created_at
            """
            await cur.execute(query, params)
            departments = await cur.fetchall()

    # 构建树形结构
    def build_tree(parent_id=None):
        result = []
        for dept in departments:
            if dept.get('parent_id') == parent_id:
                children = build_tree(dept['id'])
                dept_copy = dict(dept)
                if children:
                    dept_copy['children'] = children
                result.append(dept_copy)
        return result

    return {"departments": build_tree()}


@router.get("/{dept_id}")
async def get_department(dept_id: str, user: Dict = Depends(get_current_user)):
    """获取部门详情"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    from main_pgvector import has_permission

    if not await has_permission(user['user_id'], 'system:dept:manage'):
        raise HTTPException(status_code=403, detail="无权限访问")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT d.*, parent.name as parent_name
                FROM departments d
                LEFT JOIN departments parent ON d.parent_id = parent.id
                WHERE d.id = %s AND d.deleted_at IS NULL
            """, (dept_id,))
            dept = await cur.fetchone()

            if not dept:
                raise HTTPException(status_code=404, detail="部门不存在")

            # 获取部门用户数量
            await cur.execute("""
                SELECT COUNT(*) as user_count
                FROM system_user_dept
                WHERE dept_id = %s
            """, (dept_id,))
            user_count = await cur.fetchone()
            dept['user_count'] = user_count['user_count'] if user_count else 0

    return dept


@router.get("/{dept_id}/users")
async def get_department_users(dept_id: str, user: Dict = Depends(get_current_user)):
    """获取部门用户列表"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    from main_pgvector import has_permission

    if not await has_permission(user['user_id'], 'system:dept:manage'):
        raise HTTPException(status_code=403, detail="无权限访问")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 验证部门存在
            await cur.execute(
                "SELECT id FROM departments WHERE id = %s AND deleted_at IS NULL",
                (dept_id,)
            )
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="部门不存在")

            # 获取部门用户
            await cur.execute("""
                SELECT u.id, u.username, u.email, u.avatar, u.role
                FROM users u
                INNER JOIN system_user_dept ud ON u.id = ud.user_id
                WHERE ud.dept_id = %s AND u.deleted_at IS NULL
                ORDER BY u.username
            """, (dept_id,))
            users = await cur.fetchall()

    return {"users": users}


@router.post("")
@audit_log()
async def create_department(request: CreateDepartmentRequest, user: Dict = Depends(get_current_user)):
    """创建部门"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    from main_pgvector import has_permission

    if not await has_permission(user['user_id'], 'system:dept:create'):
        raise HTTPException(status_code=403, detail="无权限创建部门")

    pool = config.pool

    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # 验证父部门是否存在
                if request.parent_id:
                    await cur.execute(
                        "SELECT id FROM departments WHERE id = %s AND deleted_at IS NULL",
                        (request.parent_id,)
                    )
                    if not await cur.fetchone():
                        raise HTTPException(status_code=400, detail="父部门不存在")

                # 创建部门
                dept_id = str(uuid.uuid4())
                await cur.execute("""
                    INSERT INTO departments (id, name, parent_id, sort, status, remark, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                """, (dept_id, request.name, request.parent_id, request.sort,
                      request.status, request.remark))
                await conn.commit()

        return {"id": dept_id, "message": "部门创建成功"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建部门失败: {str(e)}")
        raise HTTPException(status_code=500, detail="创建部门失败，请稍后重试")


@router.put("/{dept_id}")
@audit_log_with_changes()
async def update_department(
    dept_id: str,
    request: UpdateDepartmentRequest,
    current_user: Dict = Depends(get_current_user)
):
    """更新部门"""
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")

    from main_pgvector import has_permission

    if not await has_permission(current_user['user_id'], 'system:dept:update'):
        raise HTTPException(status_code=403, detail="无权限更新部门")

    pool = config.pool

    # 用于跟踪变更
    changes = {}

    try:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM departments WHERE id = %s AND deleted_at IS NULL",
                    (dept_id,)
                )
                old_dept = await cur.fetchone()
                if not old_dept:
                    raise HTTPException(status_code=404, detail="部门不存在")

                # 验证父部门（不能设置自己为父部门）
                if request.parent_id:
                    if request.parent_id == dept_id:
                        raise HTTPException(status_code=400, detail="不能将部门设置为自己的父部门")

                    await cur.execute(
                        "SELECT id FROM departments WHERE id = %s AND deleted_at IS NULL",
                        (request.parent_id,)
                    )
                    if not await cur.fetchone():
                        raise HTTPException(status_code=400, detail="父部门不存在")

                updates = []
                values = []

                if request.name is not None:
                    changes['name'] = {'old': old_dept['name'], 'new': request.name}
                    updates.append("name = %s")
                    values.append(request.name)
                if request.parent_id is not None:
                    changes['parent_id'] = {'old': old_dept['parent_id'], 'new': request.parent_id}
                    updates.append("parent_id = %s")
                    values.append(request.parent_id)
                if request.sort is not None:
                    changes['sort'] = {'old': old_dept['sort'], 'new': request.sort}
                    updates.append("sort = %s")
                    values.append(request.sort)
                if request.status is not None:
                    changes['status'] = {'old': old_dept['status'], 'new': request.status}
                    updates.append("status = %s")
                    values.append(request.status)
                if request.remark is not None:
                    changes['remark'] = {'old': old_dept['remark'], 'new': request.remark}
                    updates.append("remark = %s")
                    values.append(request.remark)

                if updates:
                    values.append(dept_id)
                    await cur.execute(f"""
                        UPDATE departments SET {', '.join(updates)}, updated_at = NOW()
                        WHERE id = %s
                    """, values)
                    await conn.commit()

        return {"message": "部门更新成功", "changes": changes}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新部门失败: {str(e)}")
        raise HTTPException(status_code=500, detail="更新部门失败，请稍后重试")


@router.delete("/{dept_id}")
@audit_log()
async def delete_department(dept_id: str, current_user: Dict = Depends(get_current_user)):
    """删除部门（软删除）"""
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")

    from main_pgvector import has_permission

    if not await has_permission(current_user['user_id'], 'system:dept:delete'):
        raise HTTPException(status_code=403, detail="无权限删除部门")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 检查是否有子部门
            await cur.execute(
                "SELECT id FROM departments WHERE parent_id = %s AND deleted_at IS NULL",
                (dept_id,)
            )
            if await cur.fetchone():
                raise HTTPException(status_code=400, detail="该部门下有子部门，无法删除")

            # 检查是否有用户
            await cur.execute(
                "SELECT user_id FROM system_user_dept WHERE dept_id = %s",
                (dept_id,)
            )
            if await cur.fetchone():
                raise HTTPException(status_code=400, detail="该部门下有用户，无法删除")

            await cur.execute("""
                UPDATE departments SET deleted_at = NOW()
                WHERE id = %s
            """, (dept_id,))
            await conn.commit()

    return {"message": "部门删除成功"}


@router.post("/assign-user")
@audit_log(entity_type="department_member", action="assign")
async def assign_user_to_department(
    dept_id: str,
    user_id: str,
    current_user: Dict = Depends(get_current_user)
):
    """将用户分配到部门"""
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")

    from main_pgvector import has_permission

    if not await has_permission(current_user['user_id'], 'system:dept:update'):
        raise HTTPException(status_code=403, detail="无权限分配用户到部门")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 验证部门和用户存在
            await cur.execute(
                "SELECT id FROM departments WHERE id = %s AND deleted_at IS NULL",
                (dept_id,)
            )
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="部门不存在")

            await cur.execute(
                "SELECT id FROM users WHERE id = %s AND deleted_at IS NULL",
                (user_id,)
            )
            if not await cur.fetchone():
                raise HTTPException(status_code=404, detail="用户不存在")

            # 分配用户到部门
            await cur.execute("""
                INSERT INTO system_user_dept (user_id, dept_id)
                VALUES (%s, %s)
                ON CONFLICT (user_id) DO UPDATE SET dept_id = %s
            """, (user_id, dept_id, dept_id))
            await conn.commit()

    return {"message": "用户分配到部门成功"}


@router.delete("/{dept_id}/users/{user_id}")
@audit_log(entity_type="department_member", action="remove")
async def remove_user_from_department(
    dept_id: str,
    user_id: str,
    current_user: Dict = Depends(get_current_user)
):
    """将用户从部门移除"""
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")

    from main_pgvector import has_permission

    if not await has_permission(current_user['user_id'], 'system:dept:update'):
        raise HTTPException(status_code=403, detail="无权限移除用户")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                DELETE FROM system_user_dept
                WHERE user_id = %s AND dept_id = %s
            """, (user_id, dept_id))
            await conn.commit()

    return {"message": "用户从部门移除成功"}


@router.get("/users/{user_id}/departments")
async def get_user_departments(user_id: str, current_user: Dict = Depends(get_current_user)):
    """获取用户的部门列表"""
    if not current_user:
        raise HTTPException(status_code=401, detail="未登录")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT d.*
                FROM system_user_dept ud
                JOIN departments d ON ud.dept_id = d.id
                WHERE ud.user_id = %s AND d.deleted_at IS NULL
            """, (user_id,))
            departments = await cur.fetchall()

    return {"departments": departments}
