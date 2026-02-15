"""
SSO 单点登录相关路由
从 main_pgvector.py 拆分出来
"""
import hashlib
import json
import logging
import os
import uuid
from typing import Dict, Optional
from urllib.parse import urlencode

import httpx
from core import audit_log, audit_log_with_changes
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sso", tags=["sso"])

# ==================== 全局变量访问函数 ====================
def _get_globals():
    """获取 main_pgvector 模块中的全局变量"""
    # 延迟导入避免循环依赖
    import main_pgvector as mp
    from api.auth import create_token
    from api.models import UserRole
    return {
        'pool': mp.pool,
        'get_current_user': get_current_user,
        'create_token': create_token,
        'UserRole': UserRole,
    }


# 导入 get_current_user（从 auth 模块）
from api.auth import get_current_user


# SSO 提供商配置
SSO_PROVIDERS = {
    'feishu': {
        'auth_url': 'https://open.feishu.cn/open-apis/authen/v1/authorize',
        'token_url': 'https://open.feishu.cn/open-apis/authen/v1/oidc/access_token',
        'userinfo_url': 'https://open.feishu.cn/open-apis/authen/v1/user_info',
        'scope': 'openid email profile'
    },
    'dingtalk': {
        'auth_url': 'https://login.dingtalk.com/oauth2/auth',
        'token_url': 'https://api.dingtalk.com/v1.0/oauth2/userAccessToken',
        'userinfo_url': 'https://api.dingtalk.com/v1.0/contact/users/me',
        'scope': 'openid corpid contact'
    },
    'wechat': {
        'auth_url': 'https://open.work.weixin.qq.com/wwopen/sso/qrConnect',
        'token_url': 'https://qyapi.weixin.qq.com/cgi-bin/gettoken',
        'userinfo_url': 'https://qyapi.weixin.qq.com/cgi-bin/auth/getuserinfo',
        'scope': 'snsapi_base'
    }
}


# ==================== 数据模型 ====================
class SSOConfigRequest(BaseModel):
    provider: str  # 'feishu', 'dingtalk', 'wechat', 'oidc', 或自定义名称
    enabled: bool = True
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    redirect_uri: Optional[str] = None
    app_id: Optional[str] = None  # For Feishu/DingTalk
    app_secret: Optional[str] = None  # For Feishu/DingTalk

    # 通用 OIDC 配置
    display_name: Optional[str] = None  # 显示名称，如"广东省政务云登录"
    auth_url: Optional[str] = None  # 授权地址，如 https://xxx.com/authorize
    token_url: Optional[str] = None  # Token 地址，如 https://xxx.com/token
    userinfo_url: Optional[str] = None  # 用户信息地址，如 https://xxx.com/userinfo
    scope: Optional[str] = 'openid email profile'  # 授权范围
    issuer: Optional[str] = None  # Issuer，用于验证
    jwks_url: Optional[str] = None  # JWKS 地址，用于验证 id_token（可选）

    # 响应类型和模式
    response_type: Optional[str] = 'code'
    response_mode: Optional[str] = None  # query, form_post, fragment
    grant_type: Optional[str] = 'authorization_code'

    # 额外的认证参数
    extra_auth_params: Optional[Dict[str, str]] = None  # 额外的授权参数
    extra_token_params: Optional[Dict[str, str]] = None  # 额外的 token 参数


class OAuthCallbackRequest(BaseModel):
    code: str
    state: Optional[str] = None
    provider: str


# ==================== SSO API ====================
@router.get("/config")
async def get_sso_config():
    """获取 SSO 配置状态（只返回是否启用和提供商名称，不返回敏感信息）"""
    g = _get_globals()
    async with g['pool'].connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT provider, enabled, config FROM sso_configs")
            configs = await cur.fetchall()

            default_names = {
                'feishu': '飞书',
                'dingtalk': '钉钉',
                'wechat': '企业微信'
            }

            return {
                "providers": [
                    {
                        "provider": config['provider'],
                        "enabled": config['enabled'],
                        "name": default_names.get(config['provider'], config['provider']),
                        "display_name": config['config'].get('display_name') if config.get('config') else None
                    }
                    for config in configs
                ]
            }


@router.post("/config")
@audit_log(entity_type="system_config", action="update")
async def save_sso_config(request: SSOConfigRequest, user: Dict = Depends(get_current_user)):
    """保存 SSO 配置（仅管理员）"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    if user.get('role') != 'super_admin':
        raise HTTPException(status_code=403, detail="只有管理员可以配置 SSO")

    g = _get_globals()
    async with g['pool'].connection() as conn:
        async with conn.cursor() as cur:
            # 构建配置数据，兼容旧版本和新的通用 OIDC
            config_data = {
                'client_id': request.client_id,
                'client_secret': request.client_secret,
                'redirect_uri': request.redirect_uri,
                'app_id': request.app_id,
                'app_secret': request.app_secret,
            }

            # 通用 OIDC 配置
            if request.display_name:
                config_data['display_name'] = request.display_name
            if request.auth_url:
                config_data['auth_url'] = request.auth_url
            if request.token_url:
                config_data['token_url'] = request.token_url
            if request.userinfo_url:
                config_data['userinfo_url'] = request.userinfo_url
            if request.scope:
                config_data['scope'] = request.scope
            if request.issuer:
                config_data['issuer'] = request.issuer
            if request.jwks_url:
                config_data['jwks_url'] = request.jwks_url
            if request.response_type:
                config_data['response_type'] = request.response_type
            if request.response_mode:
                config_data['response_mode'] = request.response_mode
            if request.grant_type:
                config_data['grant_type'] = request.grant_type
            if request.extra_auth_params:
                config_data['extra_auth_params'] = request.extra_auth_params
            if request.extra_token_params:
                config_data['extra_token_params'] = request.extra_token_params

            await cur.execute("""
                INSERT INTO sso_configs (id, provider, enabled, config)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (provider)
                DO UPDATE SET enabled = EXCLUDED.enabled, config = EXCLUDED.config, updated_at = NOW()
            """, (request.provider, request.provider, request.enabled, json.dumps(config_data)))
            await conn.commit()

            return {"message": "SSO 配置保存成功"}


@router.get("/login/{provider}")
async def sso_login(provider: str, redirect_uri: Optional[str] = None):
    """获取 SSO 登录链接 - 支持通用 OIDC"""
    g = _get_globals()
    async with g['pool'].connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT enabled, config FROM sso_configs WHERE provider = %s",
                (provider,)
            )
            sso_config = await cur.fetchone()

            if not sso_config or not sso_config['enabled']:
                raise HTTPException(status_code=400, detail="该 SSO 提供商未启用")

            config = sso_config['config']

            # 判断是预定义提供商还是自定义 OIDC
            if provider in SSO_PROVIDERS:
                # 预定义提供商（飞书、钉钉、企业微信）
                provider_config = SSO_PROVIDERS[provider]
                auth_url_base = provider_config['auth_url']
                scope = config.get('scope', provider_config['scope'])

                # 不同提供商的参数名不同
                if provider == 'feishu':
                    params = {
                        'app_id': config.get('app_id', config.get('client_id', '')),
                        'redirect_uri': redirect_uri or config.get('redirect_uri', ''),
                        'response_type': 'code',
                        'scope': scope,
                        'state': str(uuid.uuid4()),
                    }
                elif provider == 'dingtalk':
                    params = {
                        'client_id': config.get('client_id', ''),
                        'redirect_uri': redirect_uri or config.get('redirect_uri', ''),
                        'response_type': 'code',
                        'scope': scope,
                        'state': str(uuid.uuid4()),
                        'prompt': 'consent',
                    }
                else:  # wechat
                    params = {
                        'appid': config.get('app_id', config.get('client_id', '')),
                        'redirect_uri': redirect_uri or config.get('redirect_uri', ''),
                        'response_type': 'code',
                        'scope': scope,
                        'state': str(uuid.uuid4()),
                    }
            else:
                # 自定义 OIDC 提供商
                auth_url_base = config.get('auth_url')
                if not auth_url_base:
                    raise HTTPException(status_code=400, detail="缺少授权地址配置")

                scope = config.get('scope', 'openid email profile')
                client_id = config.get('client_id', '')

                # 标准 OIDC 参数
                params = {
                    'client_id': client_id,
                    'redirect_uri': redirect_uri or config.get('redirect_uri', ''),
                    'response_type': config.get('response_type', 'code'),
                    'scope': scope,
                    'state': str(uuid.uuid4()),
                }

                # 添加 response_mode（可选）
                if config.get('response_mode'):
                    params['response_mode'] = config['response_mode']

                # 添加额外的认证参数
                extra_params = config.get('extra_auth_params', {})
                if extra_params:
                    params.update(extra_params)

            auth_url = f"{auth_url_base}?{urlencode(params)}"

            return {
                "auth_url": auth_url,
                "provider": provider,
                "state": params.get('state', ''),
                "display_name": config.get('display_name', provider)
            }


@router.post("/callback/{provider}")
@audit_log(entity_type="auth", action="sso_login")
async def sso_callback(
    provider: str,
    request: OAuthCallbackRequest,
):
    """SSO 回调处理 - 支持通用 OIDC"""
    g = _get_globals()
    create_token = g['create_token']
    UserRole = g['UserRole']

    async with g['pool'].connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT config FROM sso_configs WHERE provider = %s AND enabled = TRUE",
                (provider,)
            )
            sso_config = await cur.fetchone()

            if not sso_config:
                raise HTTPException(status_code=400, detail="该 SSO 提供商未启用")

            config = sso_config['config']

            # 获取 access_token 和用户信息
            async with httpx.AsyncClient() as http_client:
                if provider in SSO_PROVIDERS:
                    # 预定义提供商
                    provider_config = SSO_PROVIDERS[provider]

                    if provider == 'feishu':
                        # 飞书获取用户信息
                        token_response = await http_client.post(
                            provider_config['token_url'],
                            headers={'Authorization': f"Bearer {request.code}"}
                        )
                        token_data = token_response.json()

                        if token_data.get('code') != 0:
                            raise HTTPException(status_code=400, detail=f"获取 token 失败: {token_data}")

                        access_token = token_data.get('data', {}).get('access_token')

                        # 获取用户信息
                        user_response = await http_client.get(
                            provider_config['userinfo_url'],
                            headers={'Authorization': f"Bearer {access_token}"}
                        )
                        user_data = user_response.json()

                        if user_data.get('code') != 0:
                            raise HTTPException(status_code=400, detail=f"获取用户信息失败: {user_data}")

                        user_info = user_data.get('data', {})
                        provider_user_id = user_info.get('user_id', '')
                        email = user_info.get('email', '')
                        username = user_info.get('name', '')
                        avatar = user_info.get('avatar_url', '')

                    elif provider == 'dingtalk':
                        # 钉钉获取用户信息
                        token_response = await http_client.post(
                            provider_config['token_url'],
                            json={
                                'clientId': config.get('client_id'),
                                'code': request.code,
                                'grantType': 'authorization_code'
                            }
                        )
                        token_data = token_response.json()

                        access_token = token_data.get('accessToken')

                        # 获取用户信息
                        user_response = await http_client.get(
                            provider_config['userinfo_url'],
                            headers={'x-acs-dingtalk-access-token': access_token}
                        )
                        user_data = user_response.json()

                        provider_user_id = user_data.get('unionId', '')
                        email = user_data.get('email', '')
                        username = user_data.get('nick', '')
                        avatar = user_data.get('avatarUrl', '')

                    elif provider == 'wechat':
                        # 企业微信
                        token_response = await http_client.get(
                            f"{provider_config['token_url']}?corpid={config.get('app_id')}&corpsecret={config.get('app_secret')}"
                        )
                        token_data = token_response.json()

                        if token_data.get('errcode') != 0:
                            raise HTTPException(status_code=400, detail=f"获取 token 失败: {token_data}")

                        access_token = token_data.get('access_token')

                        user_response = await http_client.get(
                            f"{provider_config['userinfo_url']}?access_token={access_token}&code={request.code}"
                        )
                        user_data = user_response.json()

                        if user_data.get('errcode') != 0:
                            raise HTTPException(status_code=400, detail=f"获取用户信息失败: {user_data}")

                        provider_user_id = user_data.get('UserId', '')
                        username = ''
                        email = ''
                        avatar = ''
                else:
                    # 自定义 OIDC 提供商
                    token_url = config.get('token_url')
                    if not token_url:
                        raise HTTPException(status_code=400, detail="缺少 token_url 配置")

                    # 标准 OIDC Token 请求
                    token_params = {
                        'grant_type': config.get('grant_type', 'authorization_code'),
                        'code': request.code,
                        'client_id': config.get('client_id', ''),
                        'client_secret': config.get('client_secret', ''),
                        'redirect_uri': config.get('redirect_uri', ''),
                    }

                    # 添加额外的 token 参数
                    extra_params = config.get('extra_token_params', {})
                    if extra_params:
                        token_params.update(extra_params)

                    token_response = await http_client.post(
                        token_url,
                        data=token_params,
                        headers={'Content-Type': 'application/x-www-form-urlencoded'}
                    )
                    token_data = token_response.json()

                    # 检查错误
                    if 'error' in token_data:
                        raise HTTPException(status_code=400, detail=f"获取 token 失败: {token_data.get('error_description', token_data.get('error'))}")

                    access_token = token_data.get('access_token')

                    # 获取用户信息
                    # 优先从 id_token 解析，否则调用 userinfo endpoint
                    user_info = {}
                    if 'id_token' in token_data:
                        # 简单解析 JWT (不验证签名，生产环境建议验证)
                        try:
                            import base64
                            id_token = token_data['id_token']
                            # JWT 格式: header.payload.signature
                            parts = id_token.split('.')
                            if len(parts) >= 2:
                                # 添加 padding
                                payload = parts[1]
                                payload += '=' * (4 - len(payload) % 4)
                                user_info = json.loads(base64.b64decode(payload))
                        except Exception as e:
                            logger.warning(f"解析 id_token 失败: {e}")

                    # 如果 id_token 没有足够信息，调用 userinfo
                    userinfo_url = config.get('userinfo_url')
                    if userinfo_url and (not user_info or not user_info.get('email')):
                        user_response = await http_client.get(
                            userinfo_url,
                            headers={'Authorization': f"Bearer {access_token}"}
                        )
                        user_info = user_response.json()

                        # 检查错误
                    if 'error' in user_info:
                        raise HTTPException(status_code=400, detail=f"获取用户信息失败: {user_info.get('error_description', user_info.get('error'))}")

                    # 提取用户信息（兼容多种字段名）
                    provider_user_id = (
                        user_info.get('sub') or
                        user_info.get('user_id') or
                        user_info.get('id') or
                        user_info.get('userId') or
                        str(uuid.uuid4())
                    )
                    email = user_info.get('email') or user_info.get('email_address') or ''
                    username = (
                        user_info.get('name') or
                        user_info.get('username') or
                        user_info.get('preferred_username') or
                        user_info.get('nickname') or
                        email.split('@')[0] if email else provider_user_id
                    )
                    avatar = user_info.get('picture') or user_info.get('avatar') or user_info.get('avatar_url') or ''

            # 查找或创建用户
            await cur.execute("""
                SELECT user_id FROM user_bindings
                WHERE provider = %s AND provider_user_id = %s
            """, (provider, provider_user_id))
            binding = await cur.fetchone()

            if binding:
                # 已绑定，直接登录
                await cur.execute("""
                    SELECT id, username, email, role, avatar FROM users WHERE id = %s
                """, (binding['user_id'],))
                user = await cur.fetchone()

                # 更新绑定信息
                await cur.execute("""
                    UPDATE user_bindings
                    SET provider_email = %s, provider_username = %s, avatar_url = %s, updated_at = NOW()
                    WHERE provider = %s AND provider_user_id = %s
                """, (email, username, avatar, provider, provider_user_id))
            else:
                # 未绑定，自动创建新用户
                new_user_id = str(uuid.uuid4())
                new_username = username or f"{provider}_{provider_user_id[:8]}"
                new_email = email or f"{provider_user_id}@{provider}.local"

                await cur.execute("""
                    INSERT INTO users (id, username, email, password_hash, role, avatar)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (new_user_id, new_username, new_email, hashlib.md5(str(uuid.uuid4()).encode()).hexdigest(), 'member', avatar))

                # 创建绑定
                await cur.execute("""
                    INSERT INTO user_bindings (id, user_id, provider, provider_user_id, provider_email, provider_username, avatar_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (str(uuid.uuid4()), new_user_id, provider, provider_user_id, email, username, avatar))

                await cur.execute("""
                    SELECT id, username, email, role, avatar FROM users WHERE id = %s
                """, (new_user_id,))
                user = await cur.fetchone()

            await conn.commit()

            # 生成 token
            token = create_token(user['id'], user['username'], UserRole(user['role']))

            return {
                "access_token": token,
                "token_type": "bearer",
                "user": {
                    "id": user['id'],
                    "username": user['username'],
                    "email": user['email'],
                    "role": user['role'],
                    "avatar": user['avatar'],
                },
                "is_new_user": not bool(binding)
            }
