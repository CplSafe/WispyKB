# API 依赖项
# 从 main_pgvector.py 提取的共享依赖和工具函数

import os
import hashlib
import secrets
import logging
from typing import Optional, Dict
from fastapi import Header, HTTPException
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

# 这些需要从主模块导入
# pool = ...
# JWT_SECRET = ...

# ==================== JWT 工具 ====================

def create_access_token(payload: dict, secret: str) -> str:
    """创建 JWT access token"""
    import jwt
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict:
    """解码 JWT access token"""
    import jwt
    return jwt.decode(token, secret, algorithms=["HS256"])


# ==================== 密码工具 ====================

BCRYPT_AVAILABLE = False
try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    pass


def hash_password(password: str) -> str:
    """对密码进行安全哈希"""
    if BCRYPT_AVAILABLE:
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    else:
        # 备用方案：SHA256 + 随机 salt
        salt = secrets.token_hex(32)
        return hashlib.sha256(f"{salt}{password}".encode()).hexdigest() + f"${salt}"


def verify_password(password: str, hashed: str) -> bool:
    """验证密码是否正确

    支持多种密码格式的向后兼容：
    1. bcrypt 格式 (推荐)
    2. SHA256+salt 格式 (hash$salt)
    3. MD5 格式 (32位十六进制，仅用于兼容旧数据)
    """
    if not hashed or not password:
        return False

    # 1. 尝试 bcrypt 格式
    if BCRYPT_AVAILABLE:
        try:
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
        except (ValueError, TypeError):
            pass  # 不是 bcrypt 格式，继续尝试其他格式

    # 2. 尝试 SHA256+salt 格式 (hash$salt)
    if '$' in hashed and len(hashed) > 65:  # SHA256(64) + $(1) + salt(至少32)
        try:
            hash_part, salt = hashed.rsplit('$', 1)
            computed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
            if hash_part == computed:
                return True
        except (ValueError, IndexError):
            pass

    # 3. 尝试纯 MD5 格式 (32位十六进制) - 兼容旧数据
    if len(hashed) == 32:
        try:
            int(hashed, 16)  # 验证是否为有效的十六进制字符串
            computed_md5 = hashlib.md5(password.encode()).hexdigest()
            if computed_md5 == hashed:
                logger.warning("检测到使用MD5格式的密码，建议用户重新设置密码以升级到bcrypt")
                return True
        except ValueError:
            pass

    # 4. 尝试纯 SHA256 格式 (64位十六进制) - 兼容旧数据
    if len(hashed) == 64:
        try:
            int(hashed, 16)  # 验证是否为有效的十六进制字符串
            computed_sha256 = hashlib.sha256(password.encode()).hexdigest()
            if computed_sha256 == hashed:
                logger.warning("检测到使用无盐SHA256格式的密码，建议用户重新设置密码以升级到bcrypt")
                return True
        except ValueError:
            pass

    return False


# ==================== 分页工具 ====================

def validate_pagination(page: int, page_size: int, max_page_size: int = 100) -> tuple:
    """验证分页参数，返回 或抛出异常"""
    if page < 1:
        raise HTTPException(status_code=400, detail="页码必须大于0")
    if page_size < 1:
        raise HTTPException(status_code=400, detail="每页数量必须大于0")
    if page_size > max_page_size:
        raise HTTPException(status_code=400, detail=f"每页数量不能超过{max_page_size}")
    return page, page_size


# ==================== 依赖工厂 ====================

def create_get_current_user(pool, JWT_SECRET):
    """创建获取当前用户的依赖函数"""

    async def get_current_user(authorization: Optional[str] = Header(None)) -> Optional[Dict]:
        """从请求头获取当前登录用户"""
        if not authorization:
            return None

        if not authorization.startswith("Bearer "):
            return None

        token = authorization[7:]  # 移除 "Bearer " 前缀

        try:
            payload = decode_access_token(token, JWT_SECRET)
            user_id = payload.get("user_id")

            if not user_id:
                return None

            # 从数据库获取用户信息
            async with pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(
                        """SELECT id, username, email, role, avatar
                           FROM users WHERE id = %s AND deleted_at IS NULL""",
                        (user_id,)
                    )
                    user = await cur.fetchone()

                    if user:
                        return {
                            "user_id": user["id"],
                            "username": user["username"],
                            "email": user.get("email"),
                            "role": user.get("role"),
                            "avatar": user.get("avatar")
                        }

            return None

        except Exception as e:
            logger.error(f"获取用户失败: {e}")
            return None

    return get_current_user


def create_has_permission(pool):
    """创建权限检查函数"""

    async def get_user_roles(user_id: str):
        """获取用户的所有角色"""
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT r.id, r.name, r.code, r.sort, r.status, r.type, r.data_scope
                    FROM system_role r
                    JOIN system_user_role ur ON r.id = ur.role_id
                    WHERE ur.user_id = %s AND r.deleted_at IS NULL AND r.status = 0
                    ORDER BY r.sort
                """, (user_id,))
                return await cur.fetchall()

    async def get_user_permissions(user_id: str):
        """获取用户的所有权限"""
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT DISTINCT p.code
                    FROM system_permission p
                    JOIN system_role_permission rp ON p.id = rp.permission_id
                    JOIN system_user_role ur ON rp.role_id = ur.role_id
                    WHERE ur.user_id = %s
                    UNION
                    SELECT DISTINCT p.code
                    FROM system_permission p
                    JOIN system_role_permission rp ON p.id = rp.permission_id
                    JOIN system_user_role ur ON rp.role_id = ur.role_id
                    JOIN system_role r ON ur.role_id = r.id
                    WHERE ur.user_id = %s
                """, (user_id, user_id))
                permissions = await cur.fetchall()
                return [p[0] for p in permissions] if permissions else []

    async def has_permission(user_id: str, permission: str) -> bool:
        """检查用户是否有指定权限"""
        # 首先检查 users 表中的 role 字段（兼容老系统）
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
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

        # 检查 RBAC 系统中的超级管理员角色
        roles = await get_user_roles(user_id)
        if any(r['code'] == 'super_admin' for r in roles):
            return True

        permissions = await get_user_permissions(user_id)
        return permission in permissions

    return has_permission
