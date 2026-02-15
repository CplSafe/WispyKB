# 权限装饰器和依赖
# 使用方式：
#   @permit_all - 允许所有人访问（包括未登录）
#   @require_login - 需要登录
#   @require_permission("system:role:manage") - 需要特定权限
#   @require_roles("admin", "super_admin") - 需要特定角色

import functools
import logging
from typing import Optional, Dict, List, Callable
from fastapi import HTTPException, Depends

logger = logging.getLogger(__name__)

# 全局变量，由主模块初始化
_pool = None
_has_permission_func = None


def init_permission(pool, has_permission_func):
    """初始化权限模块"""
    global _pool, _has_permission_func
    _pool = pool
    _has_permission_func = has_permission_func


# ==================== 装饰器 ====================

def permit_all(func: Callable):
    """
    允许所有人访问（包括未登录用户）
    使用方式: @permit_all
    """
    func._permit_all = True
    func._require_login = False
    func._required_permission = None
    func._required_roles = None
    return func


def require_login(func: Callable):
    """
    需要登录才能访问
    使用方式: @require_login
    """
    func._permit_all = False
    func._require_login = True
    func._required_permission = None
    func._required_roles = None
    return func


def require_permission(permission: str):
    """
    需要特定权限才能访问
    使用方式: @require_permission("system:role:manage")
    """
    def decorator(func: Callable):
        func._permit_all = False
        func._require_login = True
        func._required_permission = permission
        func._required_roles = None
        return func
    return decorator


def require_roles(*roles: str):
    """
    需要特定角色才能访问
    使用方式: @require_roles("admin", "super_admin")
    """
    def decorator(func: Callable):
        func._permit_all = False
        func._require_login = True
        func._required_permission = None
        func._required_roles = list(roles)
        return func
    return decorator


# ==================== 依赖项 ====================

async def get_current_user_optional(authorization: Optional[str] = None) -> Optional[Dict]:
    """
    获取当前用户（可选，不强制登录）
    用于 @permit_all 的接口
    """
    from .dependencies import create_get_current_user
    from . import config
    from main_pgvector import JWT_SECRET

    if not authorization:
        return None

    get_user = create_get_current_user(_pool, JWT_SECRET)
    return await get_user(authorization=authorization)


async def get_current_user_required(user: Optional[Dict] = Depends(get_current_user_optional)) -> Dict:
    """
    获取当前用户（必须登录）
    用于 @require_login 的接口
    """
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


class PermissionChecker:
    """
    权限检查依赖
    配合路由使用
    """

    def __init__(self, permission: str = None, roles: List[str] = None, login_required: bool = True):
        self.permission = permission
        self.roles = roles
        self.login_required = login_required

    async def __call__(self, user: Optional[Dict] = Depends(get_current_user_optional)) -> Optional[Dict]:
        # 不需要登录
        if not self.login_required:
            return user

        # 需要登录
        if not user:
            raise HTTPException(status_code=401, detail="请先登录")

        # 检查角色
        if self.roles:
            user_role = user.get('role', '')
            # 首先检查 users 表中的 role 字段
            if user_role in self.roles:
                return user
            # 超级管理员拥有所有权限
            if user_role == 'super_admin':
                return user
            # 然后检查 system_user_role 表
            if _pool:
                from psycopg.rows import dict_row
                async with _pool.connection() as conn:
                    async with conn.cursor(row_factory=dict_row) as cur:
                        await cur.execute("""
                            SELECT r.code FROM system_role r
                            JOIN system_user_role ur ON r.id = ur.role_id
                            WHERE ur.user_id = %s AND r.deleted_at IS NULL
                        """, (user['user_id'],))
                        user_roles = [row['code'] for row in await cur.fetchall()]
                        # 超级管理员拥有所有权限
                        if 'super_admin' in user_roles:
                            return user
                        if not any(role in self.roles for role in user_roles):
                            raise HTTPException(status_code=403, detail="无权限访问")

        # 检查权限
        if self.permission and _has_permission_func:
            if not await _has_permission_func(user['user_id'], self.permission):
                raise HTTPException(status_code=403, detail="无权限访问")

        return user


# ==================== 快捷依赖 ====================

# 公开访问
Public = PermissionChecker(login_required=False)

# 需要登录
LoginRequired = PermissionChecker(login_required=True)

# 角色管理权限
RoleManageRequired = PermissionChecker(permission="system:role:manage", login_required=True)

# 用户管理权限
UserManageRequired = PermissionChecker(permission="system:user:manage", login_required=True)

# 知识库管理权限
KnowledgeManageRequired = PermissionChecker(permission="knowledge:manage", login_required=True)

# 仅超级管理员
SuperAdminOnly = PermissionChecker(roles=["super_admin"], login_required=True)

# 管理员及以上
AdminOrAbove = PermissionChecker(roles=["admin", "super_admin"], login_required=True)
