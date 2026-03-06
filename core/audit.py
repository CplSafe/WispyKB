# 审计日志装饰器 + 中间件
# 独立模块避免循环导入

import asyncio
import functools
import inspect
import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


# ==================== 辅助函数 ====================

def _sanitize_body(body_bytes: bytes, max_len: int = 2000) -> Optional[str]:
    """解析请求体，过滤 password 字段，截断至 max_len"""
    if not body_bytes:
        return None
    try:
        data = json.loads(body_bytes)
        if isinstance(data, dict):
            for k in list(data.keys()):
                if 'password' in k.lower():
                    data[k] = '***'
        text = json.dumps(data, ensure_ascii=False)
    except Exception:
        text = body_bytes.decode('utf-8', errors='replace')
    return text[:max_len] if len(text) > max_len else text


def _infer_module(path: str) -> str:
    """从 URL 路径推断模块名，例如 /api/v1/system/roles → system"""
    parts = [p for p in path.split('/') if p]
    # parts: ['api', 'v1', 'system', 'roles'] → index 2 = 'system'
    if len(parts) >= 3:
        return parts[2]
    return 'unknown'


def _infer_operation(method: str, path: str) -> str:
    """从 method + path 推断操作描述"""
    mapping = {
        'POST': '创建',
        'PUT': '更新',
        'PATCH': '更新',
        'DELETE': '删除',
    }
    verb = mapping.get(method.upper(), method)
    # 取路径最后一段作为资源名
    parts = [p for p in path.split('/') if p and not p.startswith('{')]
    resource = parts[-1] if parts else path
    return f"{verb} {resource}"


async def _decode_user_from_token(authorization: Optional[str]):
    """从 Bearer token 解析 user_id 和 username，失败返回 None, None"""
    if not authorization or not authorization.startswith('Bearer '):
        return None, None
    try:
        import jwt
        from core.config import JWT_SECRET, JWT_ALGORITHM
        token = authorization[7:]
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get('user_id'), payload.get('username')
    except Exception:
        return None, None


async def _write_operate_log(pool, user_id, username, method, url, ip,
                              ua, request_params, status, error_msg, execute_time):
    """异步写入 system_operate_log，失败不影响主流程"""
    try:
        module = _infer_module(url)
        operation = _infer_operation(method, url)
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO system_operate_log
                        (user_id, username, module, operation, request_method,
                         request_url, request_ip, user_agent, request_params,
                         status, error_msg, execute_time, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (user_id, username, module, operation, method,
                      url, ip, ua, request_params,
                      status, error_msg, execute_time))
                await conn.commit()
    except Exception as e:
        logger.error(f"写入操作日志失败: {e}")


async def write_login_log(pool, username: str, status: int,
                           ip: str = None, ua: str = None, error: str = None):
    """写入登录日志到 system_login_log"""
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO system_login_log
                        (username, status, ip_address, user_agent, error_msg, login_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, (username, status, ip, ua, error))
                await conn.commit()
    except Exception as e:
        logger.error(f"写入登录日志失败: {e}")


# ==================== 操作日志中间件 ====================

# 跳过记录的路径前缀
_SKIP_PREFIXES = (
    '/api/v1/system/audit',
    '/api/v1/auth/login',
    '/api/v1/share',       # 公开分享接口，无用户认证，跳过
    '/health',
    '/docs',
    '/openapi.json',
    '/redoc',
    '/static',
)
_SKIP_METHODS = {'GET', 'OPTIONS', 'HEAD'}


class OperateLogMiddleware:
    """
    操作日志中间件：自动记录所有写操作到 system_operate_log
    使用方式（在 main_pgvector.py 中）：
        app.add_middleware(OperateLogMiddleware)
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        method = scope.get('method', '')
        path = scope.get('path', '')

        # 跳过不需要记录的请求
        if method in _SKIP_METHODS:
            await self.app(scope, receive, send)
            return
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        # 先把完整 body 读出来，再通过 receive_replay 回放给下游
        body_bytes = b''
        raw_message = await receive()
        if raw_message['type'] == 'http.request':
            body_bytes = raw_message.get('body', b'')
            # 如果 more_body=True，继续读直到全部收完
            while raw_message.get('more_body', False):
                raw_message = await receive()
                body_bytes += raw_message.get('body', b'')

        replayed = False

        async def receive_replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {'type': 'http.request', 'body': body_bytes, 'more_body': False}
            # 后续调用（正常不会发生）返回空
            return {'type': 'http.request', 'body': b'', 'more_body': False}

        # 提取请求信息
        headers = dict(scope.get('headers', []))
        authorization = headers.get(b'authorization', b'').decode('utf-8', errors='replace')
        ua = headers.get(b'user-agent', b'').decode('utf-8', errors='replace')
        # 优先从代理头取真实 IP
        forwarded_for = headers.get(b'x-forwarded-for', b'').decode('utf-8', errors='replace')
        real_ip = headers.get(b'x-real-ip', b'').decode('utf-8', errors='replace')
        client = scope.get('client')
        if forwarded_for:
            ip = forwarded_for.split(',')[0].strip()
        elif real_ip:
            ip = real_ip.strip()
        elif client:
            ip = client[0]
        else:
            ip = None

        start_time = time.time()
        status_code = 500
        error_msg = None

        async def send_wrapper(message):
            nonlocal status_code
            if message['type'] == 'http.response.start':
                status_code = message.get('status', 500)
            await send(message)

        try:
            await self.app(scope, receive_replay, send_wrapper)
        except Exception as e:
            error_msg = str(e)
            raise
        finally:
            execute_time = int((time.time() - start_time) * 1000)
            request_params = _sanitize_body(body_bytes)
            status = 0 if status_code < 400 else 1

            # 解析 JWT 用户信息
            user_id, username = await _decode_user_from_token(authorization)

            # 异步写日志，不阻塞响应
            from core import config as _cfg
            _pool = _cfg.pool
            if _pool:
                asyncio.create_task(_write_operate_log(
                    pool=_pool,
                    user_id=user_id,
                    username=username,
                    method=method,
                    url=path,
                    ip=ip,
                    ua=ua,
                    request_params=request_params,
                    status=status,
                    error_msg=error_msg,
                    execute_time=execute_time,
                ))


async def log_audit(
    pool,
    entity_type: str,
    entity_id: str,
    action: str,
    user_id: str = None,
    username: str = None,
    changes: dict = None,
    ip_address: str = None,
    user_agent: str = None
):
    """
    记录审计日志

    Args:
        pool: 数据库连接池
        entity_type: 实体类型 (knowledge_base, document, chunk, application, etc.)
        entity_id: 实体ID
        action: 操作类型 (create, update, delete)
        user_id: 用户ID
        username: 用户名（快照，防止用户被删除后无法追溯）
        changes: 变更内容 JSONB，格式: {"field_name": {"old": value, "new": value}}
        ip_address: IP地址
        user_agent: 用户代理
    """
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO audit_logs (entity_type, entity_id, action, user_id, username, changes, ip_address, user_agent, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (entity_type, entity_id, action, user_id, username, json.dumps(changes) if changes else None, ip_address, user_agent))
                await conn.commit()
    except Exception as e:
        # 审计日志记录失败不应影响主流程
        logger.error(f"Failed to log audit: {e}")


def audit_log(entity_type: str = None, action: str = None, id_param: str = None):
    """
    统一审计日志装饰器 - 类似 Spring Boot 的 @AuditLog 注解

    使用示例:
        @audit_log()
        async def delete_document(kb_id: str, doc_id: str, user=Depends(...)):
            pass
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 执行原函数
            result = await func(*args, **kwargs)

            # 自动推断参数
            _entity_type = entity_type
            _action = action
            _id_param = id_param

            # 从函数名推断
            func_name = func.__name__
            if _entity_type is None:
                if '_' in func_name:
                    parts = func_name.split('_')
                    if len(parts) >= 2:
                        _entity_type = '_'.join(parts[1:])

            if _action is None:
                if 'create' in func_name or 'add' in func_name:
                    _action = 'create'
                elif 'update' in func_name or 'edit' in func_name or 'modify' in func_name:
                    _action = 'update'
                elif 'delete' in func_name or 'remove' in func_name:
                    _action = 'delete'
                elif 'get' in func_name or 'list' in func_name or 'query' in func_name:
                    _action = 'view'
                else:
                    _action = 'unknown'

            # 自动选择 ID 参数
            if _id_param is None:
                sig = inspect.signature(func)
                param_names = list(sig.parameters.keys())
                for name in ['workflow_id', 'doc_id', 'document_id', 'app_id', 'application_id',
                              'kb_id', 'knowledge_base_id', 'role_id', 'dept_id',
                              'user_id', 'id', 'username']:
                    if name in param_names:
                        _id_param = name
                        break

            # 获取用户信息（兼容 user / current_user 两种参数名）
            user = kwargs.get('user') or kwargs.get('current_user')
            if not user:
                for arg in args:
                    if isinstance(arg, dict) and 'user_id' in arg:
                        user = arg
                        break

            if user:
                user_id = user.get('user_id')
                username = user.get('username')

                # 获取 entity_id
                entity_id = None
                if isinstance(result, dict) and 'id' in result:
                    entity_id = result['id']
                elif _id_param and _id_param in kwargs:
                    entity_id = kwargs[_id_param]
                elif _id_param:
                    sig = inspect.signature(func)
                    param_names = list(sig.parameters.keys())
                    if _id_param in param_names:
                        idx = param_names.index(_id_param)
                        if idx < len(args):
                            entity_id = args[idx]
                    # 如果 id_param 不是函数参数，直接使用它作为值（如 id_param="1"）
                    elif _id_param and not _id_param.isidentifier():
                        entity_id = _id_param

                # 如果没有 entity_id，使用默认值
                if not entity_id:
                    entity_id = "unknown"

                # 获取 IP 和 User-Agent
                ip_address = None
                user_agent = None
                for arg in args:
                    if hasattr(arg, 'client') and hasattr(arg, 'headers'):
                        ip_address = arg.client.host
                        user_agent = arg.headers.get('user-agent')
                        break

                # 获取 pool
                from . import config
                pool = config.pool

                # 异步记录审计日志
                try:
                    await log_audit(
                        pool=pool,
                        entity_type=_entity_type or func_name,
                        entity_id=str(entity_id),
                        action=_action,
                        user_id=user_id,
                        username=username,
                        ip_address=ip_address,
                        user_agent=user_agent
                    )
                except Exception as e:
                    logger.error(f"审计日志记录失败: {e}")

            return result
        return wrapper
    return decorator


def audit_log_with_changes(entity_type: str = None, action: str = None,
                           id_param: str = None, fetch_old=None):
    """
    带变更记录的审计日志装饰器

    参数:
        fetch_old: 可选的异步函数 async(entity_id, pool) -> dict
                   传入时，装饰器执行前先查旧值，执行后自动 diff 新旧值，
                   无需业务代码手动返回 changes。
                   不传时沿用原有行为（从返回值的 changes 字段读取）。

    使用示例（自动 diff 模式）:
        async def _fetch_old(entity_id, pool):
            async with pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("SELECT name, description FROM workflows WHERE id=%s", (entity_id,))
                    return await cur.fetchone() or {}

        @audit_log_with_changes(fetch_old=_fetch_old)
        async def update_workflow(workflow_id: str, request: UpdateRequest, current_user=Depends(...)):
            ...  # 正常业务逻辑，无需手动返回 changes

    使用示例（原有模式）:
        @audit_log_with_changes()
        async def update_knowledge_base(kb_id: str, request=..., user=Depends(...)):
            return {"message": "success", "changes": {"name": {"old": "A", "new": "B"}}}
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 自动推断参数
            _entity_type = entity_type
            _action = action or 'update'
            _id_param = id_param

            func_name = func.__name__
            if _entity_type is None:
                if '_' in func_name:
                    parts = func_name.split('_')
                    if len(parts) >= 2:
                        _entity_type = '_'.join(parts[1:])

            if _id_param is None:
                sig = inspect.signature(func)
                param_names = list(sig.parameters.keys())
                for name in ['workflow_id', 'doc_id', 'document_id', 'app_id',
                              'application_id', 'kb_id', 'knowledge_base_id',
                              'role_id', 'dept_id', 'user_id', 'id']:
                    if name in param_names:
                        _id_param = name
                        break

            # 获取 entity_id
            entity_id = None
            if _id_param and _id_param in kwargs:
                entity_id = kwargs[_id_param]
            if entity_id is None and _id_param:
                sig = inspect.signature(func)
                param_names = list(sig.parameters.keys())
                if _id_param in param_names:
                    idx = param_names.index(_id_param)
                    if idx < len(args):
                        entity_id = args[idx]

            # fetch_old 模式：执行前查旧值
            from . import config
            pool = config.pool
            old_data = {}
            if fetch_old and entity_id and pool:
                try:
                    old_data = await fetch_old(str(entity_id), pool) or {}
                except Exception as e:
                    logger.error(f"fetch_old 失败: {e}")

            # 执行原函数
            result = await func(*args, **kwargs)

            # 获取用户信息
            user = kwargs.get('user') or kwargs.get('current_user')
            if not user:
                for arg in args:
                    if isinstance(arg, dict) and 'user_id' in arg:
                        user = arg
                        break
            if not user:
                return result

            user_id = user.get('user_id')
            username = user.get('username')

            # 确定 entity_id（也可从返回值取）
            if not entity_id and isinstance(result, dict) and 'id' in result:
                entity_id = result['id']
            if not entity_id:
                return result

            # 确定 changes
            changes = None
            if fetch_old:
                # 自动 diff 模式：从 kwargs 中找 Pydantic request model 提取新值
                new_data = {}
                for v in kwargs.values():
                    if hasattr(v, 'model_dump'):
                        new_data = {k: val for k, val in v.model_dump().items()
                                    if val is not None}
                        break
                    elif hasattr(v, 'dict'):
                        new_data = {k: val for k, val in v.dict().items()
                                    if val is not None}
                        break

                if old_data and new_data:
                    changes = {
                        field: {'old': old_data.get(field), 'new': new_val}
                        for field, new_val in new_data.items()
                        if field in old_data and old_data.get(field) != new_val
                    }
            elif isinstance(result, dict):
                # 原有模式：从返回值的 changes 字段读取
                changes = result.get('changes')

            if changes and pool:
                try:
                    await log_audit(
                        pool=pool,
                        entity_type=_entity_type or func_name,
                        entity_id=str(entity_id),
                        action=_action,
                        user_id=user_id,
                        username=username,
                        changes=changes
                    )
                except Exception as e:
                    logger.error(f"审计日志记录失败: {e}")

            return result
        return wrapper
    return decorator
