# 应用管理路由
# /api/v1/applications/* 相关接口

import uuid
import secrets
import logging
import asyncio
import json
import re
import httpx
from datetime import datetime
from typing import Dict, Optional, List

from core import audit_log, audit_log_with_changes
from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from psycopg.rows import dict_row


from .auth import get_current_user

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/api/v1/applications", tags=["应用管理"])

# 创建公开分享路由
share_router = APIRouter(prefix="/api/v1/share", tags=["公开分享"])


class CreateApplicationRequest(BaseModel):
    """创建应用请求"""
    name: str
    description: Optional[str] = None
    model: Optional[str] = "llama3.1"
    knowledge_base_ids: Optional[List[str]] = []
    is_public: Optional[bool] = False
    system_prompt: Optional[str] = None
    welcome_message: Optional[str] = None
    share_password: Optional[str] = None
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 2048


class UpdateApplicationRequest(BaseModel):
    """更新应用请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    model: Optional[str] = None
    knowledge_base_ids: Optional[List[str]] = None
    is_public: Optional[bool] = None
    system_prompt: Optional[str] = None
    welcome_message: Optional[str] = None
    share_password: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    auto_image_prompt: Optional[bool] = None
    image_prompt_template: Optional[str] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    retrieval_method: Optional[str] = None
    similarity_threshold: Optional[float] = None


@router.get("")
async def list_applications(user: Dict = Depends(get_current_user)):
    """获取应用列表（包含反馈统计）

    权限规则：
    - 超级管理员：查看所有应用
    - 普通用户：查看自己的应用 + 公开应用
    """
    from core import config
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 获取用户角色
            await cur.execute("SELECT role FROM users WHERE id = %s", (user['user_id'],))
            user_row = await cur.fetchone()
            user_role = user_row['role'] if user_row else 'user'

            # 超级管理员可以查看所有应用
            # 普通用户只能查看自己的应用或公开的应用
            if user_role == 'super_admin':
                where_clause = ""
                params = ()
            else:
                where_clause = "WHERE a.owner_id = %s OR a.is_public = true"
                params = (user['user_id'],)

            await cur.execute(f"""
                SELECT a.*,
                       u.username as owner_name,
                       u.avatar as owner_avatar,
                       COALESCE(c.conv_count, 0) as conversation_count,
                       COALESCE(c.msg_count, 0) as message_count,
                       COALESCE(f.like_count, 0) as like_count,
                       COALESCE(f.dislike_count, 0) as dislike_count,
                       COALESCE(f.feedback_count, 0) as feedback_count
                FROM applications a
                LEFT JOIN users u ON a.owner_id = u.id
                LEFT JOIN (
                    SELECT c.app_id,
                           COUNT(DISTINCT c.id) as conv_count,
                           COALESCE(SUM(m.msg_count), 0) as msg_count
                    FROM conversations c
                    LEFT JOIN (
                        SELECT conversation_id, COUNT(*) as msg_count
                        FROM messages
                        GROUP BY conversation_id
                    ) m ON m.conversation_id = c.id
                    GROUP BY c.app_id
                ) c ON a.id = c.app_id
                LEFT JOIN (
                    SELECT application_id,
                           SUM(CASE WHEN feedback_type = 'like' THEN 1 ELSE 0 END) as like_count,
                           SUM(CASE WHEN feedback_type = 'dislike' THEN 1 ELSE 0 END) as dislike_count,
                           COUNT(*) as feedback_count
                    FROM message_feedback
                    GROUP BY application_id
                ) f ON f.application_id = a.id
                {where_clause}
                ORDER BY a.created_at DESC
            """, params)
            rows = await cur.fetchall()

    return {"applications": rows}


@router.post("")
@audit_log()
async def create_application(request: CreateApplicationRequest, user: Dict = Depends(get_current_user)):
    """创建应用"""
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")

    app_id = str(uuid.uuid4())
    # 生成分享ID（8位随机字符串）
    share_id = secrets.token_urlsafe(8)

    from core import config
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO applications (id, name, description, model, knowledge_base_ids, is_public, owner_id, system_prompt, welcome_message, share_password, share_id, temperature, max_tokens, image_prompt_template, auto_image_prompt, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id
            """, (app_id, request.name, request.description, request.model,
                  request.knowledge_base_ids, request.is_public, user['user_id'],
                  request.system_prompt, request.welcome_message, request.share_password, share_id,
                  request.temperature, request.max_tokens, request.image_prompt_template,
                  request.auto_image_prompt))
            await conn.commit()

    return {
        "id": app_id,
        "share_id": share_id,
        "name": request.name,
        "message": "应用创建成功"
    }


@router.delete("/{app_id}")
@audit_log()
async def delete_application(app_id: str, user: Dict = Depends(get_current_user)):
    """删除应用"""
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")

    from core import config
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 检查权限
            await cur.execute(
                "SELECT owner_id FROM applications WHERE id = %s",
                (app_id,)
            )
            app = await cur.fetchone()
            if not app:
                raise HTTPException(status_code=404, detail="应用不存在")

            if app['owner_id'] != user['user_id'] and user.get('role') != 'super_admin':
                raise HTTPException(status_code=403, detail="无权限删除此应用")

            await cur.execute("DELETE FROM applications WHERE id = %s", (app_id,))
            await conn.commit()

    return {"message": "应用删除成功"}


@router.get("/{app_id}")
async def get_application(app_id: str, user: Dict = Depends(get_current_user)):
    """获取应用详情"""
    from core import config
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT a.*,
                       u.username as owner_name,
                       u.avatar as owner_avatar
                FROM applications a
                LEFT JOIN users u ON a.owner_id = u.id
                WHERE a.id = %s
            """, (app_id,))
            app = await cur.fetchone()

            if not app:
                raise HTTPException(status_code=404, detail="应用不存在")

    return app


@router.put("/{app_id}")
@audit_log_with_changes()
async def update_application(
    app_id: str,
    request: UpdateApplicationRequest,
    user: Dict = Depends(get_current_user)
):
    """更新应用"""
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")

    from core import config
    pool = config.pool

    # 用于跟踪变更
    changes = {}

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 检查权限并获取当前值
            await cur.execute(
                "SELECT owner_id, name, description, model, knowledge_base_ids, is_public, system_prompt, welcome_message, share_password, temperature, max_tokens, image_prompt_template, auto_image_prompt, top_p, top_k, retrieval_method, similarity_threshold FROM applications WHERE id = %s",
                (app_id,)
            )
            app = await cur.fetchone()
            if not app:
                raise HTTPException(status_code=404, detail="应用不存在")

            if app['owner_id'] != user['user_id'] and user.get('role') != 'super_admin':
                raise HTTPException(status_code=403, detail="无权限更新此应用")

            updates = []
            values = []

            if request.name is not None:
                changes['name'] = {'old': app['name'], 'new': request.name}
                updates.append("name = %s")
                values.append(request.name)
            if request.description is not None:
                changes['description'] = {'old': app['description'], 'new': request.description}
                updates.append("description = %s")
                values.append(request.description)
            if request.model is not None:
                changes['model'] = {'old': app['model'], 'new': request.model}
                updates.append("model = %s")
                values.append(request.model)
            if request.knowledge_base_ids is not None:
                changes['knowledge_base_ids'] = {'old': app['knowledge_base_ids'], 'new': request.knowledge_base_ids}
                updates.append("knowledge_base_ids = %s")
                values.append(request.knowledge_base_ids)
            if request.is_public is not None:
                changes['is_public'] = {'old': app['is_public'], 'new': request.is_public}
                updates.append("is_public = %s")
                values.append(request.is_public)
            if request.system_prompt is not None:
                changes['system_prompt'] = {'old': app['system_prompt'], 'new': request.system_prompt}
                updates.append("system_prompt = %s")
                values.append(request.system_prompt)
            if request.welcome_message is not None:
                changes['welcome_message'] = {'old': app['welcome_message'], 'new': request.welcome_message}
                updates.append("welcome_message = %s")
                values.append(request.welcome_message)
            if request.share_password is not None:
                changes['share_password'] = {'old': app['share_password'], 'new': request.share_password}
                updates.append("share_password = %s")
                values.append(request.share_password)
            if request.temperature is not None:
                changes['temperature'] = {'old': app['temperature'], 'new': request.temperature}
                updates.append("temperature = %s")
                values.append(request.temperature)
            if request.max_tokens is not None:
                changes['max_tokens'] = {'old': app['max_tokens'], 'new': request.max_tokens}
                updates.append("max_tokens = %s")
                values.append(request.max_tokens)
            if request.image_prompt_template is not None:
                changes['image_prompt_template'] = {'old': app.get('image_prompt_template'), 'new': request.image_prompt_template}
                updates.append("image_prompt_template = %s")
                values.append(request.image_prompt_template)
            if request.auto_image_prompt is not None:
                changes['auto_image_prompt'] = {'old': app.get('auto_image_prompt'), 'new': request.auto_image_prompt}
                updates.append("auto_image_prompt = %s")
                values.append(request.auto_image_prompt)
            if request.top_p is not None:
                changes['top_p'] = {'old': app.get('top_p'), 'new': request.top_p}
                updates.append("top_p = %s")
                values.append(request.top_p)
            if request.top_k is not None:
                changes['top_k'] = {'old': app.get('top_k'), 'new': request.top_k}
                updates.append("top_k = %s")
                values.append(request.top_k)
            if request.retrieval_method is not None:
                changes['retrieval_method'] = {'old': app.get('retrieval_method'), 'new': request.retrieval_method}
                updates.append("retrieval_method = %s")
                values.append(request.retrieval_method)
            if request.similarity_threshold is not None:
                changes['similarity_threshold'] = {'old': app.get('similarity_threshold'), 'new': request.similarity_threshold}
                updates.append("similarity_threshold = %s")
                values.append(request.similarity_threshold)

            if updates:
                updates.append("updated_at = NOW()")
                values.append(app_id)

                await cur.execute(f"""
                    UPDATE applications
                    SET {', '.join(updates)}
                    WHERE id = %s
                """, values)
                await conn.commit()

    return {"message": "应用更新成功", "changes": changes}


@router.get("/{app_id}/analytics")
async def get_application_analytics(app_id: str, user: Dict = Depends(get_current_user)):
    """获取应用统计数据"""
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")

    from core import config
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 会话统计（基于 chat_messages 的 session_id）
            await cur.execute("""
                SELECT
                    COUNT(DISTINCT session_id) as total_conversations,
                    COUNT(DISTINCT CASE WHEN created_at > NOW() - INTERVAL '7 days' THEN session_id END) as recent_conversations
                FROM chat_messages
                WHERE application_id = %s
            """, (app_id,))
            conv_stats = await cur.fetchone()

            # 消息统计
            await cur.execute("""
                SELECT
                    COUNT(*) as total_messages,
                    COUNT(CASE WHEN created_at > NOW() - INTERVAL '7 days' THEN 1 END) as recent_messages
                FROM chat_messages
                WHERE application_id = %s
            """, (app_id,))
            msg_stats = await cur.fetchone()

            # 反馈统计
            await cur.execute("""
                SELECT
                    COUNT(*) as total_feedback,
                    SUM(CASE WHEN feedback_type = 'like' THEN 1 ELSE 0 END) as likes,
                    SUM(CASE WHEN feedback_type = 'dislike' THEN 1 ELSE 0 END) as dislikes
                FROM message_feedback
                WHERE application_id = %s
            """, (app_id,))
            feedback_stats = await cur.fetchone()

            # 每日统计（最近30天）
            await cur.execute("""
                SELECT
                    DATE(created_at) as date,
                    COUNT(*) as count
                FROM chat_messages
                WHERE application_id = %s
                    AND created_at > NOW() - INTERVAL '30 days'
                GROUP BY DATE(created_at)
                ORDER BY date
            """, (app_id,))
            daily_stats_result = await cur.fetchall()
            daily_stats = [
                {"date": str(row["date"]), "count": row["count"]}
                for row in daily_stats_result
            ]

    return {
        "conversations": {
            "total": conv_stats['total_conversations'] if conv_stats else 0,
            "recent": conv_stats['recent_conversations'] if conv_stats else 0
        },
        "messages": {
            "total": msg_stats['total_messages'] if msg_stats else 0,
            "recent": msg_stats['recent_messages'] if msg_stats else 0
        },
        "feedback": {
            "total": feedback_stats['total_feedback'] if feedback_stats else 0,
            "likes": feedback_stats['likes'] if feedback_stats else 0,
            "dislikes": feedback_stats['dislikes'] if feedback_stats else 0
        },
        "daily_stats": daily_stats
    }


# ==================== 公开分享 API ====================

class ChatRequest(BaseModel):
    """聊天请求"""
    message: str
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None
    context: Optional[List[Dict[str, str]]] = None
    stream: Optional[bool] = True
    retrieval_method: Optional[str] = "semantic_search"
    top_k: Optional[int] = 5  # 增加到 5 以确保包含图片流程图等辅助内容


class FeedbackRequest(BaseModel):
    """反馈请求"""
    message_id: str
    feedback_type: str  # 'like' or 'dislike'
    comment: Optional[str] = None


@share_router.get("/{share_id}")
async def get_shared_app(
    share_id: str,
    x_share_password: Optional[str] = Header(None, alias="X-Share-Password")
):
    """获取公开分享的应用信息"""
    from core import config
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT id, name, description, welcome_message, model, is_public, share_password
                FROM applications WHERE share_id = %s AND is_public = true
            """, (share_id,))
            app = await cur.fetchone()

            if not app:
                raise HTTPException(status_code=404, detail="分享不存在或已失效")

            # 如果有密码，验证
            if app.get("share_password") and app.get("share_password") != x_share_password:
                raise HTTPException(status_code=401, detail="密码错误")

    return {
        "id": app["id"],
        "name": app["name"],
        "description": app.get("description"),
        "welcome_message": app.get("welcome_message", "你好，我是AI助手，请问有什么可以帮你的？"),
        "has_password": bool(app.get("share_password"))
    }


@share_router.post("/{share_id}/chat")
async def public_chat(
    share_id: str,
    request: ChatRequest,
    password: Optional[str] = None,
    x_share_password: Optional[str] = Header(None, alias="X-Share-Password"),
    x_forwarded_for: Optional[str] = Header(None),
    x_real_ip: Optional[str] = Header(None),
    user_agent: Optional[str] = Header(None)
):
    """公开聊天接口 - 无需登录，支持对话上下文（永久记忆）

    Args:
        share_id: 分享ID
        request: 聊天请求
        password: 访问密码（如果需要，已弃用，请使用 X-Share-Password header）
        x_share_password: 通过 X-Share-Password header 传递的密码
        stream: 是否使用流式输出，默认True
        x_forwarded_for: 代理转发的客户端IP
        x_real_ip: 真实客户端IP
        user_agent: 用户代理
    """
    from core import config
    from core.utils import vector_search_multi, call_ollama
    from services.embedding import EmbeddingService
    pool = config.pool
    import main_pgvector
    OLLAMA_BASE_URL = main_pgvector.OLLAMA_BASE_URL
    OLLAMA_EMBEDDING_MODEL = getattr(main_pgvector, 'OLLAMA_EMBEDDING_MODEL', 'nomic-embed-text')

    # 创建 embedding service
    embedding_service = EmbeddingService(OLLAMA_EMBEDDING_MODEL, OLLAMA_BASE_URL)

    # 获取应用配置
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT id, name, model, knowledge_base_ids, system_prompt, temperature, top_p, max_tokens, embedding_model, embedding_dimension, retrieval_method, top_k, similarity_threshold
                FROM applications WHERE share_id = %s AND is_public = true
            """, (share_id,))
            app = await cur.fetchone()

            if not app:
                raise HTTPException(status_code=404, detail="分享不存在或已失效")

            # 转换 Decimal 类型为 float（PostgreSQL 返回的数值类型）
            import decimal
            def convert_decimal(obj):
                if isinstance(obj, decimal.Decimal):
                    return float(obj)
                return obj
            app = {k: convert_decimal(v) for k, v in app.items()}

            # 检查密码 - 支持 header 和 form 参数
            actual_password = x_share_password or password
            if app.get('share_password') and app.get('share_password') != actual_password:
                raise HTTPException(status_code=401, detail="密码错误")

    # 构建上下文
    messages = []

    # 使用应用配置的提示词，如果没有配置则使用默认提示词
    custom_prompt = app.get('system_prompt') or ""
    default_prompt = """你是一个专业的AI客服助手。请基于【参考知识库内容】回答问题，不要编造信息。"""
    base_system_prompt = f"{custom_prompt}\n\n{default_prompt}" if custom_prompt else default_prompt

    # 添加对话历史上下文（永久记忆功能）
    if request.context:
        for ctx_msg in request.context[-10:]:  # 最多保留最近10条消息作为上下文
            if ctx_msg.get('role') in ['user', 'assistant']:
                messages.append({
                    "role": ctx_msg['role'],
                    "content": ctx_msg['content']
                })

    # 从知识库检索相关内容 - 使用多策略检索并合并结果
    retrieved_context = None
    if app.get('knowledge_base_ids'):
        logger.info(f"Searching in knowledge bases: {len(app['knowledge_base_ids'])} KBs")
        query_embedding = await embedding_service.generate(request.message)
        if not query_embedding:
            logger.warning(f"Failed to generate embedding for query: {request.message}")
        else:
            # 使用应用配置的 top_k 和 threshold
            top_k = app.get('top_k', 3)
            threshold = app.get('similarity_threshold', 0.5)
            logger.info(f"使用多策略检索 top_k={top_k}, threshold={threshold}")

            # 多策略检索：同时执行语义搜索、全文搜索、混合搜索
            from core.utils import semantic_search, full_text_search, hybrid_search

            all_results = []
            seen_doc_ids = set()  # 用于去重

            # 1. 语义搜索
            semantic_results = await semantic_search(
                pool_ref=pool,
                query_embedding=query_embedding,
                kb_ids=app['knowledge_base_ids'],
                top_k=top_k,
                threshold=threshold
            )
            for r in semantic_results:
                doc_id = r.get('chunk_id') or r.get('id')
                if doc_id not in seen_doc_ids:
                    seen_doc_ids.add(doc_id)
                    r['search_method'] = 'semantic'
                    all_results.append(r)
            logger.info(f"语义搜索返回 {len(semantic_results)} 条，去重后 {sum(1 for r in all_results if r.get('search_method') == 'semantic')} 条")

            # 2. 全文搜索
            fts_results = await full_text_search(
                pool_ref=pool,
                query=request.message,
                kb_ids=app['knowledge_base_ids'],
                top_k=top_k
            )
            for r in fts_results:
                doc_id = r.get('chunk_id') or r.get('id')
                if doc_id not in seen_doc_ids:
                    seen_doc_ids.add(doc_id)
                    r['search_method'] = 'fulltext'
                    all_results.append(r)
            logger.info(f"全文搜索返回 {len(fts_results)} 条，去重后新增 {sum(1 for r in all_results if r.get('search_method') == 'fulltext')} 条")

            # 3. 混合搜索（向量 + 全文）
            hybrid_results = await hybrid_search(
                pool_ref=pool,
                query=request.message,
                query_embedding=query_embedding,
                kb_ids=app['knowledge_base_ids'],
                top_k=top_k,
                alpha=0.7
            )
            for r in hybrid_results:
                doc_id = r.get('chunk_id') or r.get('id')
                if doc_id not in seen_doc_ids:
                    seen_doc_ids.add(doc_id)
                    r['search_method'] = 'hybrid'
                    all_results.append(r)
            logger.info(f"混合搜索返回 {len(hybrid_results)} 条，去重后新增 {sum(1 for r in all_results if r.get('search_method') == 'hybrid')} 条")

            # 按相似度/得分排序，取前 top_k 条
            all_results.sort(key=lambda x: x.get('score', x.get('similarity', x.get('distance', 0))), reverse=True)
            results = all_results[:top_k]
            logger.info(f"多策略合并后最终返回 {len(results)} 条结果")

            if results:
                context_parts = []
                has_image_ref = False

                for i, r in enumerate(results, 1):
                    kb_name = r.get('kb_name', r.get('doc_name', '文档'))
                    content = r['content']

                    # 转换图片 URL：使用外网地址而不是 localhost
                    import os
                    server_host = os.getenv("SERVER_HOST", "192.168.1.61")
                    server_port = os.getenv("SERVER_PORT", "8888")
                    base_url = f"http://{server_host}:{server_port}/static/files/images"

                    def convert_image_urls(text):
                        # 匹配 Markdown 图片格式中的相对路径或绝对路径（localhost）
                        # 先匹配完整URL（http://localhost:8888/static/files/images/...）
                        text = re.sub(
                            r'!\[([^\]]*)\]\(http://localhost:\d+/static/files/images/([^)]+)\)',
                            fr'[流程图链接: {base_url}/\2]',
                            text
                        )
                        # 再匹配相对路径（/static/files/images/...）
                        text = re.sub(
                            r'!\[([^\]]*)\]\(/static/files/images/([^)]+)\)',
                            fr'[流程图链接: {base_url}/\2]',
                            text
                        )
                        return text

                    content = convert_image_urls(content)

                    # 记录转换后的内容（用于调试）
                    if '流程图链接' in content or '/static/files/images' in content:
                        logger.info(f"Chunk {i} 包含图片链接，转换后内容（前200字符）: {content[:200]}")

                    # 检测是否包含图片占位符或流程图链接
                    if '[流程图链接' in content or '[图片]' in content:
                        has_image_ref = True
                    context_parts.append(f"[参考内容{i}] {kb_name}\n{content}\n")

                retrieved_context = "\n".join(context_parts)

                # 获取应用的图片提示词配置
                auto_image_prompt = app.get('auto_image_prompt', True)
                image_template = app.get('image_prompt_template')

                # 将知识库内容拼接到系统提示词
                knowledge_section = f"【参考知识库内容】\n\n{retrieved_context}"


                # 合并：应用提示词 + 知识库内容
                final_system_prompt = f"{base_system_prompt}\n\n{knowledge_section}"

                messages.append({
                    "role": "system",
                    "content": final_system_prompt
                })
                logger.info(f"系统提示（前500字符）: {final_system_prompt[:500]}")
                logger.info(f"has_image_ref: {has_image_ref}")
            else:
                # 没有检索到知识库内容，只使用应用配置的提示词
                messages.append({"role": "system", "content": base_system_prompt})
                logger.warning(f"No results found for query: {request.message}")

    # 当前用户消息
    messages.append({"role": "user", "content": request.message})

    # 提取客户端 IP
    def get_client_ip():
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        elif x_real_ip:
            return x_real_ip
        return None

    client_ip = get_client_ip()

    # 使用请求体中的 stream 参数，默认为 True
    use_stream = request.stream if request.stream is not None else True

    if use_stream:
        # 流式输出 - 支持 DeepSeek-R1 等推理模型
        async def generate_chat_stream():
            full_response = ""  # 记录完整响应用于日志
            # 推理模型可能需要更长时间思考，增加超时到10分钟
            async with httpx.AsyncClient(timeout=600.0) as client:
                payload = {
                    "model": app['model'],
                    "messages": messages,
                    "stream": True,
                    "options": {
                        "num_ctx": 8192,
                        "temperature": app.get('temperature', 0.1),
                        "top_p": app.get('top_p', 0.9),
                        "num_predict": app.get('max_tokens', 2048),
                    }
                }

                async with client.stream("POST", f"{OLLAMA_BASE_URL}/api/chat", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.strip():
                            try:
                                data = json.loads(line)
                                message_obj = data.get("message", {})
                                content = message_obj.get("content", "")
                                
                                # DeepSeek-R1 等推理模型的思考过程在 thinking 字段
                                # 我们只输出最终的 content，跳过 thinking 阶段
                                if content:
                                    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
                                    content = content.strip()
                                    if content:
                                        full_response += content
                                        yield f"data: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
                                elif data.get("done", False):
                                    # 流结束标记
                                    break
                            except json.JSONDecodeError:
                                continue

                # 记录流式输出的完整响应（调试用）
                logger.info(f"流式输出完整响应（{len(full_response)}字符）: {full_response}")

                # 如果知识库中包含图片链接，在回答结束后自动追加
                if has_image_ref:
                    logger.info(f"检测到图片引用，开始提取图片链接...")
                    # 提取所有图片链接
                    image_links = re.findall(r'\[流程图链接: ([^\]]+)\]', retrieved_context)
                    logger.info(f"提取到 {len(image_links)} 个图片链接: {image_links}")
                    if image_links:
                        # 换行后输出图片链接
                        newline_content = '\n\n**相关流程图/图片：**'
                        yield f"data: {json.dumps({'content': newline_content}, ensure_ascii=False)}\n\n"
                        for link in image_links:
                            # 输出可点击的链接格式
                            link_content = f'\n- [查看流程图]({link})'
                            yield f"data: {json.dumps({'content': link_content}, ensure_ascii=False)}\n\n"
                    else:
                        logger.warning("has_image_ref=True 但未能提取到图片链接")
                else:
                    logger.info("没有检测到图片引用，跳过图片链接输出")

                yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate_chat_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    else:
        # 非流式输出 - 使用应用配置的 LLM 参数
        llm_params = {
            "temperature": app.get('temperature', 0.1),
            "top_p": app.get('top_p', 0.9),
            "num_predict": app.get('max_tokens', 2048),
        }
        # 显式使用导入的 call_ollama 函数
        from core.utils import call_ollama as call_llm
        response = await call_llm(app['model'], messages, stream=False, **llm_params)

        # 保存聊天记录
        message_id = str(uuid.uuid4())
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    INSERT INTO chat_messages (application_id, message_id, user_message, ai_response, ip_address, user_agent)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (app['id'], message_id, request.message, response, client_ip, user_agent))

                # 更新应用的对话计数
                await cur.execute("""
                    INSERT INTO application_conversations (application_id, message_count, date)
                    VALUES (%s, 1, %s)
                    ON CONFLICT (application_id, date)
                    DO UPDATE SET message_count = application_conversations.message_count + 1
                """, (app['id'], datetime.now().date()))

        return {
            "response": response
        }


@share_router.post("/{share_id}/feedback")
async def submit_feedback(share_id: str, request: FeedbackRequest):
    """提交消息反馈 - 点赞/差评/评论"""
    from core import config
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 验证应用存在
            await cur.execute("""
                SELECT id FROM applications WHERE share_id = %s AND is_public = true
            """, (share_id,))
            app = await cur.fetchone()

            if not app:
                raise HTTPException(status_code=404, detail="分享不存在或已失效")

            # 保存反馈
            await cur.execute("""
                INSERT INTO message_feedback (id, application_id, message_id, feedback_type, comment, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (message_id)
                DO UPDATE SET feedback_type = EXCLUDED.feedback_type, comment = EXCLUDED.comment, created_at = EXCLUDED.created_at
            """, (
                str(uuid.uuid4()),
                app['id'],
                request.message_id,
                request.feedback_type,
                request.comment,
                datetime.now()
            ))

            await conn.commit()

    return {"success": True, "message": "反馈已提交"}
