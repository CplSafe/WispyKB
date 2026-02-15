# 审计日志装饰器
# 独立模块避免循环导入

import functools
import inspect
import json
import logging

logger = logging.getLogger(__name__)


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
                for name in ['doc_id', 'document_id', 'app_id', 'application_id', 'kb_id', 'knowledge_base_id', 'user_id', 'id', 'username']:
                    if name in param_names:
                        _id_param = name
                        break

            # 获取用户信息
            user = kwargs.get('user')
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


def audit_log_with_changes(entity_type: str = None, action: str = None, id_param: str = None):
    """
    带变更记录的审计日志装饰器

    使用示例:
        @audit_log_with_changes()
        async def update_knowledge_base(kb_id: str, request=..., user=Depends(...)):
            return {"message": "success", "changes": {...}}
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)

            # 获取用户信息
            user = kwargs.get('user')
            if not user:
                for arg in args:
                    if isinstance(arg, dict) and 'user_id' in arg:
                        user = arg
                        break

            if user and isinstance(result, dict):
                user_id = user.get('user_id')
                username = user.get('username')
                changes = result.get('changes')

                # 自动推断参数
                _entity_type = entity_type
                _action = action
                _id_param = id_param

                func_name = func.__name__
                if _entity_type is None:
                    if '_' in func_name:
                        parts = func_name.split('_')
                        if len(parts) >= 2:
                            _entity_type = '_'.join(parts[1:])

                if _action is None:
                    _action = 'update'

                if _id_param is None:
                    sig = inspect.signature(func)
                    param_names = list(sig.parameters.keys())
                    for name in ['doc_id', 'app_id', 'kb_id', 'user_id', 'id']:
                        if name in param_names:
                            _id_param = name
                            break

                # 获取 entity_id
                entity_id = None
                if _id_param and _id_param in kwargs:
                    entity_id = kwargs[_id_param]
                elif isinstance(result, dict) and 'id' in result:
                    entity_id = result['id']
                else:
                    sig = inspect.signature(func)
                    param_names = list(sig.parameters.keys())
                    if _id_param and _id_param in param_names:
                        idx = param_names.index(_id_param)
                        if idx < len(args):
                            entity_id = args[idx]

                if entity_id and changes:
                    from . import config
                    pool = config.pool
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
