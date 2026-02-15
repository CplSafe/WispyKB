# 认证路由
# /api/v1/auth/* 相关接口

import logging
from core import config, audit_log, audit_log_with_changes

from datetime import datetime, timedelta
from typing import Optional, Dict
from enum import Enum
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from psycopg.rows import dict_row
import jwt

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/api/v1/auth", tags=["认证"])

# 全局配置（使用延迟导入避免循环依赖）
_main_pgvector = None


def get_main_module():
    """延迟获取主模块，避免循环导入"""
    global _main_pgvector
    if _main_pgvector is None:
        import sys
        import importlib
        # 获取主模块引用
        _main_pgvector = sys.modules.get('main_pgvector')
        if _main_pgvector is None:
            # 如果还没加载，先导入
            _main_pgvector = importlib.import_module('main_pgvector')
    return _main_pgvector


class UserRole(str, Enum):
    """用户角色枚举"""
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class LoginRequest(BaseModel):
    """登录请求"""
    username: str
    password: str


async def get_db_pool():
    """获取数据库连接池"""
    main = get_main_module()
    return getattr(main, 'pool', None)


async def get_jwt_config():
    """获取JWT配置"""
    main = get_main_module()
    return {
        'secret': getattr(main, 'JWT_SECRET', 'default-secret'),
        'algorithm': getattr(main, 'JWT_ALGORITHM', 'HS256'),
        'expiration_hours': getattr(main, 'JWT_EXPIRATION_HOURS', 24)
    }


async def get_current_user(authorization: Optional[str] = Header(None)) -> Dict:
    """从请求头获取当前登录用户"""
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from fastapi import Request

    # 导入 JWT 配置
    import core.config

    # 获取数据库连接池（直接从 core.config 获取实时值）
    pool = core.config.pool

    if not pool:
        raise HTTPException(status_code=401, detail="数据库未初始化")

    if not authorization:
        raise HTTPException(status_code=401, detail="未提供认证令牌")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="无效的认证格式")

    token = authorization[7:]  # 移除 "Bearer " 前缀

    try:
        payload = jwt.decode(token, core.config.JWT_SECRET, algorithms=[core.config.JWT_ALGORITHM])
        user_id = payload.get("user_id")

        if not user_id:
            raise HTTPException(status_code=401, detail="Token 中无用户信息")

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

        raise HTTPException(status_code=401, detail="用户不存在")

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的 Token")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取用户失败: {e}")
        raise HTTPException(status_code=401, detail="获取用户信息失败")


def create_token(user_id: str, username: str, role: UserRole) -> str:
    """创建 JWT Token"""
    # 从 core.config 导入 JWT 配置
    import core.config as config

    payload = {
        "user_id": user_id,
        "username": username,
        "role": role.value,
        "exp": datetime.utcnow() + timedelta(hours=config.JWT_EXPIRATION_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def verify_token(token: str) -> Dict:
    """验证 JWT Token"""
    import core.config as config
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的 Token")


# ==================== 认证 API ====================

@router.post("/login")
@audit_log(entity_type="auth", action="login", id_param="username")
async def login(request: LoginRequest):
    """用户登录"""
    from api.dependencies import verify_password

    # 直接从 core.config 获取 pool（运行时导入，确保已初始化）
    import core.config
    pool = core.config.pool

    if not pool:
        raise HTTPException(status_code=503, detail="数据库未初始化")

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM users WHERE username = %s AND deleted_at IS NULL",
                (request.username,)
            )
            user = await cur.fetchone()

    if not user or not verify_password(request.password, user['password_hash']):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    token = create_token(user['id'], user['username'], UserRole(user['role']))

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user['id'],
            "username": user['username'],
            "email": user['email'],
            "role": user['role'],
            "avatar": user.get('avatar'),
        }
    }


@router.get("/me")
async def get_me(user: Dict = Depends(get_current_user)):
    """获取当前用户信息"""
    # get_current_user 已经处理了认证，失败会抛出 401
    pool = config.pool
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM users WHERE id = %s", (user['user_id'],))
            user_data = await cur.fetchone()

    return user_data


# 导出依赖函数
def init_auth_router():
    """初始化认证路由（不需要参数）"""
    return router, get_current_user
