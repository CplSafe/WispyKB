# 聊天和会话管理路由
# /api/v1/applications/{app_id}/chat, /api/v1/sessions/* 相关接口

import logging
from core import config, audit_log, audit_log_with_changes

import uuid
import json
from datetime import datetime
from typing import Dict, Optional, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from psycopg.rows import dict_row

from .auth import get_current_user

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(tags=["聊天管理"])


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None
    context: Optional[List[Dict[str, str]]] = None
    stream: Optional[bool] = False
    retrieval_method: Optional[str] = "semantic_search"
    top_k: Optional[int] = 5  # 增加到 5 以确保包含图片流程图等辅助内容


class FeedbackRequest(BaseModel):
    """反馈请求"""
    message_id: str
    feedback_type: str  # 'like' or 'dislike'
    comment: Optional[str] = None


# ==================== 聊天 API ====================

@router.post("/{app_id}/chat")
async def chat(app_id: str, request: ChatRequest, user: Dict = Depends(get_current_user)):
    """
    聊天接口 - 基于知识库的RAG问答

    增强：
    - 支持多轮对话会话管理
    - 支持多种检索策略（语义搜索、全文搜索、混合搜索）
    - 自动保存聊天历史
    - 支持上下文记忆
    """
    pool = config.pool

    user_id = user.get('user_id') if user else None
    session_id = request.session_id

    # 获取应用配置
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM applications WHERE id = %s", (app_id,))
            app = await cur.fetchone()

            if not app:
                raise HTTPException(status_code=404, detail="应用不存在")

            # 如果没有提供 session_id，创建新会话
            if not session_id:
                await cur.execute("""
                    INSERT INTO chat_sessions (application_id, user_id, title, message_count)
                    VALUES (%s, %s, %s, 1)
                    RETURNING id
                """, (app_id, user_id, request.message[:50] + "..." if len(request.message) > 50 else request.message))
                session = await cur.fetchone()
                session_id = session['id']
            else:
                # 检查会话是否存在
                await cur.execute("SELECT id FROM chat_sessions WHERE id = %s", (session_id,))
                existing_session = await cur.fetchone()
                if not existing_session:
                    raise HTTPException(status_code=404, detail="会话不存在")

                # 更新会话消息计数
                await cur.execute("""
                    UPDATE chat_sessions
                    SET message_count = message_count + 1, updated_at = NOW()
                    WHERE id = %s
                """, (session_id,))

    # 构建上下文
    messages = []

    # 基础系统提示词（稍后会添加知识库内容）
    # 如果应用没有配置 system_prompt，使用默认的简单提示词
    custom_prompt = app.get('system_prompt') or ""
    default_prompt = """你是一个专业的AI客服助手。请基于【参考知识库内容】回答问题，不要编造信息。"""
    base_system_prompt = f"{custom_prompt}\n\n{default_prompt}" if custom_prompt else default_prompt

    # 加载历史对话上下文（最近5轮）
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT user_message, ai_response
                FROM chat_messages
                WHERE session_id = %s
                ORDER BY created_at DESC
                LIMIT 5
            """, (session_id,))
            history = await cur.fetchall()

            # 按时间正序添加历史
            for msg in reversed(history):
                messages.append({"role": "user", "content": msg['user_message']})
                messages.append({"role": "assistant", "content": msg['ai_response']})

    # 从知识库检索相关内容 - 使用新的检索策略
    retrieved_context = None
    sources_list = []

    if app.get('knowledge_base_ids'):
        query_embedding = await main_pgvector.embedding_service.generate(request.message)
        if query_embedding:
            # 根据请求的检索方法选择策略
            retrieval_method = request.retrieval_method or "semantic_search"

            # 验证检索方法是否有效
            valid_methods = ["semantic_search", "full_text_search", "hybrid_search", "hybrid_rerank"]
            method = retrieval_method if retrieval_method in valid_methods else "semantic_search"

            # 使用统一搜索接口
            search_result = await main_pgvector.unified_search(
                pool_ref=pool,
                query=request.message,
                query_embedding=query_embedding,
                kb_ids=app['knowledge_base_ids'],
                method=method,
                top_k=request.top_k or 5
            )

            results = search_result.get('results', [])

            if results:
                context_parts = []
                has_image_ref = False

                for i, r in enumerate(results, 1):
                    kb_name = r.get('kb_name', r.get('doc_name', '文档'))
                    content = r['content']

                    # 将相对路径的图片链接转换为完整 URL
                    # 处理 ![图片](/static/files/images/...) -> ![图片](http://localhost:8888/static/files/images/...)
                    import re
                    def convert_image_urls(text):
                        # 匹配 Markdown 图片格式中的相对路径
                        pattern = r'!\[([^\]]*)\]\(/static/files/images/([^)]+)\)'
                        replacement = fr'![\1]({config.STATIC_URL}/images/\2)'
                        return re.sub(pattern, replacement, text)

                    content = convert_image_urls(content)

                    # 检测是否包含图片占位符或 Markdown 图片链接
                    if '[图片]' in content or '![' in content or '/static/files/images/' in content:
                        has_image_ref = True
                    context_parts.append(f"[参考内容{i}] {kb_name}\n{content}\n")
                    sources_list.append({
                        "kb_name": kb_name,
                        "doc_name": r.get('doc_name', ''),
                        "content": content[:200] + "..." if len(content) > 200 else content,
                        "similarity": r.get('similarity', 0)
                    })

                retrieved_context = "\n".join(context_parts)

                # 直接使用应用配置的提示词，不需要任何额外说明
                # 将知识库内容拼接到系统提示词
                knowledge_section = f"【参考知识库内容】\n\n{retrieved_context}"

                # 合并：应用提示词 + 知识库内容
                final_system_prompt = f"{base_system_prompt}\n\n{knowledge_section}"

                messages.append({
                    "role": "system",
                    "content": final_system_prompt
                })
            else:
                # 没有检索到知识库内容，只使用应用配置的提示词
                messages.append({"role": "system", "content": base_system_prompt})

    # 用户消息
    messages.append({"role": "user", "content": request.message})

    # 从应用配置获取 LLM 参数
    llm_params = {
        "temperature": app.get('temperature', 0.1),
        "top_p": app.get('top_p', 0.9),
        "num_predict": app.get('max_tokens', 2048),
    }

    # 调用 LLM
    response = await main_pgvector.call_ollama(app['model'], messages, **llm_params)

    # 保存聊天消息
    message_id = str(uuid.uuid4())
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                INSERT INTO chat_messages (
                    session_id, application_id, message_id, user_message, ai_response,
                    sources, model_used
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                session_id, app_id, message_id, request.message, response,
                json.dumps(sources_list, ensure_ascii=False), app['model']
            ))

    return {
        "response": response,
        "message": request.message,
        "message_id": message_id,
        "session_id": session_id,
        "sources": sources_list
    }


# ==================== 应用反馈 API ====================

@router.post("/{app_id}/feedback")
async def submit_app_feedback(app_id: str, request: FeedbackRequest, user: Dict = Depends(get_current_user)):
    """提交应用消息反馈 - 点赞/差评/评论"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 验证应用存在
            await cur.execute("""
                SELECT id FROM applications WHERE id = %s
            """, (app_id,))
            app = await cur.fetchone()

            if not app:
                raise HTTPException(status_code=404, detail="应用不存在")

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


# ==================== 聊天会话管理 ====================

@router.get("/api/v1/applications/{app_id}/sessions")
async def get_chat_sessions(
    app_id: str,
    limit: int = 20,
    offset: int = 0,
    user: Dict = Depends(get_current_user)
):
    """获取应用的所有聊天会话"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT * FROM chat_sessions
                WHERE application_id = %s
                ORDER BY updated_at DESC
                LIMIT %s OFFSET %s
            """, (app_id, limit, offset))
            sessions = await cur.fetchall()

            # 获取每个会话的最后一条消息作为预览
            for session in sessions:
                await cur.execute("""
                    SELECT user_message FROM chat_messages
                    WHERE session_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (session['id'],))
                last_msg = await cur.fetchone()
                session['last_message'] = last_msg['user_message'] if last_msg else ''

    return {"sessions": sessions, "count": len(sessions)}


@router.get("/api/v1/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    user: Dict = Depends(get_current_user)
):
    """获取会话的所有消息"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT * FROM chat_messages
                WHERE session_id = %s
                ORDER BY created_at ASC
            """, (session_id,))
            messages = await cur.fetchall()

    return {"messages": messages, "count": len(messages)}


@router.delete("/api/v1/sessions/{session_id}")
@audit_log()
async def delete_session(session_id: str, user: Dict = Depends(get_current_user)):
    """删除聊天会话"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("DELETE FROM chat_sessions WHERE id = %s", (session_id,))
            await conn.commit()

    return {"success": True, "message": "会话已删除"}


@router.put("/api/v1/sessions/{session_id}/title")
async def update_session_title(
    session_id: str,
    title: str,
    user: Dict = Depends(get_current_user)
):
    """更新会话标题"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                UPDATE chat_sessions
                SET title = %s, updated_at = NOW()
                WHERE id = %s
            """, (title, session_id))
            await conn.commit()

    return {"success": True, "message": "标题已更新"}
