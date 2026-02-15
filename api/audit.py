# 审计日志路由
# /api/v1/system/audit/* 相关接口

import logging
from core import config
from typing import Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from .auth import get_current_user

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/api/v1/system/audit", tags=["审计日志"])


async def has_permission(user_id: str, permission: str) -> bool:
    """检查用户是否拥有指定权限"""
    pool = config.pool
    try:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 首先检查 users 表中的 role 字段（兼容老系统）
                await cur.execute("""
                    SELECT role
                    FROM users
                    WHERE id = %s AND deleted_at IS NULL
                    LIMIT 1
                """, (user_id,))
                user = await cur.fetchone()
                if user and user.get('role') == 'super_admin':
                    # 超级管理员拥有所有权限
                    return True

                # 检查 system_role 表中的超级管理员角色（RBAC系统）
                await cur.execute("""
                    SELECT r.code
                    FROM system_role r
                    JOIN system_user_role ur ON r.id = ur.role_id
                    WHERE ur.user_id = %s AND r.code = 'super_admin' AND r.deleted_at IS NULL
                    LIMIT 1
                """, (user_id,))
                super_admin = await cur.fetchone()
                if super_admin:
                    return True

                # 检查用户角色的菜单权限
                await cur.execute("""
                    SELECT DISTINCT m.permission
                    FROM system_menu m
                    JOIN system_role_menu rm ON m.id = rm.menu_id
                    JOIN system_user_role ur ON rm.role_id = ur.role_id
                    WHERE ur.user_id = %s AND m.permission = %s AND m.status = 0
                    LIMIT 1
                """, (user_id, permission))
                result = await cur.fetchone()
                return result is not None

    except Exception as e:
        logger.error(f"权限检查失败: {e}")
        return False


@router.get("")
async def list_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: Dict = Depends(get_current_user)
):
    """获取审计日志列表"""
    # 检查权限
    if not await has_permission(current_user['user_id'], 'system:audit:view'):
        raise HTTPException(status_code=403, detail="无权限访问")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 构建查询条件
            conditions = ["1=1"]
            params = []

            if user_id:
                conditions.append("user_id = %s")
                params.append(user_id)
            if action:
                conditions.append("action = %s")
                params.append(action)
            if entity_type:
                conditions.append("entity_type = %s")
                params.append(entity_type)
            if start_date:
                conditions.append("created_at >= %s")
                params.append(start_date)
            if end_date:
                conditions.append("created_at <= %s")
                params.append(end_date)

            where_clause = " AND ".join(conditions)

            # 获取总数
            await cur.execute(f"""
                SELECT COUNT(*) as total
                FROM audit_logs
                WHERE {where_clause}
            """, params)
            total_result = await cur.fetchone()
            total = total_result['total'] if total_result else 0

            # 获取分页数据
            offset = (page - 1) * page_size
            params.extend([page_size, offset])

            await cur.execute(f"""
                SELECT
                    id,
                    entity_type,
                    entity_id,
                    action,
                    user_id,
                    username,
                    changes,
                    ip_address,
                    user_agent,
                    created_at
                FROM audit_logs
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, params)
            logs = await cur.fetchall()

    return {
        "logs": logs,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@router.get("/stats")
async def get_audit_stats(
    current_user: Dict = Depends(get_current_user)
):
    """获取审计日志统计信息"""
    # 检查权限
    if not await has_permission(current_user['user_id'], 'system:audit:view'):
        raise HTTPException(status_code=403, detail="无权限访问")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 今日操作次数
            await cur.execute("""
                SELECT COUNT(*) as count
                FROM audit_logs
                WHERE DATE(created_at) = CURRENT_DATE
            """)
            today_count = await cur.fetchone()

            # 本周操作次数
            await cur.execute("""
                SELECT COUNT(*) as count
                FROM audit_logs
                WHERE created_at >= DATE_TRUNC('week', CURRENT_DATE)
            """)
            week_count = await cur.fetchone()

            # 按操作类型统计
            await cur.execute("""
                SELECT action, COUNT(*) as count
                FROM audit_logs
                WHERE created_at >= DATE_TRUNC('week', CURRENT_DATE)
                GROUP BY action
                ORDER BY count DESC
                LIMIT 10
            """)
            action_stats = await cur.fetchall()

            # 按用户统计
            await cur.execute("""
                SELECT username, COUNT(*) as count
                FROM audit_logs
                WHERE created_at >= DATE_TRUNC('week', CURRENT_DATE)
                GROUP BY username
                ORDER BY count DESC
                LIMIT 10
            """)
            user_stats = await cur.fetchall()

    return {
        "today_count": today_count['count'] if today_count else 0,
        "week_count": week_count['count'] if week_count else 0,
        "action_stats": action_stats or [],
        "user_stats": user_stats or []
    }


@router.get("/operate-logs")
async def list_operate_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    module: Optional[str] = None,
    status: Optional[int] = None,
    current_user: Dict = Depends(get_current_user)
):
    """获取操作日志列表（system_operate_log表）"""
    # 检查权限
    if not await has_permission(current_user['user_id'], 'system:audit:view'):
        raise HTTPException(status_code=403, detail="无权限访问")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 构建查询条件
            conditions = ["1=1"]
            params = []

            if module:
                conditions.append("module = %s")
                params.append(module)
            if status is not None:
                conditions.append("status = %s")
                params.append(status)

            where_clause = " AND ".join(conditions)

            # 获取总数
            await cur.execute(f"""
                SELECT COUNT(*) as total
                FROM system_operate_log
                WHERE {where_clause}
            """, params)
            total_result = await cur.fetchone()
            total = total_result['total'] if total_result else 0

            # 获取分页数据
            offset = (page - 1) * page_size
            params.extend([page_size, offset])

            await cur.execute(f"""
                SELECT
                    id,
                    user_id,
                    username,
                    module,
                    operation,
                    request_method,
                    request_url,
                    request_ip,
                    user_agent,
                    request_params,
                    response_data,
                    status,
                    error_msg,
                    execute_time,
                    created_at
                FROM system_operate_log
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, params)
            logs = await cur.fetchall()

    return {
        "logs": logs,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@router.get("/login-logs")
async def list_login_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[int] = None,
    current_user: Dict = Depends(get_current_user)
):
    """获取登录日志列表"""
    # 检查权限
    if not await has_permission(current_user['user_id'], 'system:audit:view'):
        raise HTTPException(status_code=403, detail="无权限访问")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 构建查询条件
            conditions = []
            params = []

            if status is not None:
                conditions.append(f"status = {status}")

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            # 获取总数
            await cur.execute(f"""
                SELECT COUNT(*) as total
                FROM system_login_log
                WHERE {where_clause}
            """, params)
            total_result = await cur.fetchone()
            total = total_result['total'] if total_result else 0

            # 获取分页数据
            offset = (page - 1) * page_size
            params.extend([page_size, offset])

            await cur.execute(f"""
                SELECT
                    id,
                    username,
                    status,
                    ip_address,
                    user_agent,
                    error_msg,
                    login_at
                FROM system_login_log
                WHERE {where_clause}
                ORDER BY login_at DESC
                LIMIT %s OFFSET %s
            """, params)
            logs = await cur.fetchall()

    return {
        "logs": logs,
        "total": total,
        "page": page,
        "page_size": page_size
    }
