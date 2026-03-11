# 知识库路由
# /api/v1/knowledge-bases/* 相关接口

import uuid
import logging
import asyncio
import httpx
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Response
from pydantic import BaseModel
from psycopg.rows import dict_row
from core import config, audit_log, audit_log_with_changes

from .auth import get_current_user

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["知识库管理"])


class CreateKnowledgeBaseRequest(BaseModel):
    """创建知识库请求"""
    name: str
    description: Optional[str] = None
    embedding_model: Optional[str] = None
    chunk_size: Optional[int] = 512
    chunk_overlap: Optional[int] = 50


class UpdateKnowledgeBaseRequest(BaseModel):
    """更新知识库请求"""
    name: Optional[str] = None
    description: Optional[str] = None
    embedding_model: Optional[str] = None
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None


class SearchRequest(BaseModel):
    """搜索请求"""
    query: str
    top_k: Optional[int] = 5
    threshold: Optional[float] = 0.0


class HybridSearchRequest(BaseModel):
    """混合搜索请求 - 支持三段式搜索：向量 + 关键词 + Rerank"""
    query: str
    top_k: Optional[int] = 5
    alpha: Optional[float] = 0.7  # 向量/关键词混合权重，默认0.7表示70%向量+30%关键词
    enable_rerank: Optional[bool] = False  # 是否启用Rerank重排序
    rerank_alpha: Optional[float] = 0.3  # Rerank分数权重，默认0.3表示30%rerank+70%原分数
    rerank_model: Optional[str] = "bge-reranker-v2-m3"  # Rerank模型名称


class WebSyncRequest(BaseModel):
    """网页同步请求"""
    url: str
    use_mcp: bool = False  # 是否使用 MCP Chrome DevTools 工具抓取（用于动态页面）


class FeishuWikiSyncRequest(BaseModel):
    """飞书 Wiki 知识库同步请求"""
    space_id: Optional[str] = None  # 飞书知识库空间 ID（数字格式），用于同步指定的知识库
    node_token: Optional[str] = None  # 飞书知识库节点 Token，可直接同步某个节点及其子节点
    url: Optional[str] = None  # 飞书文档分享链接（自动解析）


class FeishuConfigRequest(BaseModel):
    """飞书配置请求"""
    app_id: str
    app_secret: str


# ==================== 知识库 CRUD API ====================

@router.get("")
async def list_knowledge_bases(user: Dict = Depends(get_current_user)):
    """获取知识库列表

    权限规则：
    - 超级管理员：查看所有知识库
    - 工作区管理员：查看自己的知识库
    - 普通用户：查看自己的知识库 + 公开的知识库

    添加 Cache-Control 头避免前端缓存
    """
    from fastapi import Response

    user_id = user.get('user_id') if user else None
    user_role = user.get('role') if user else 'member'

    # import main_pgvector
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 构建查询条件
            if user_role == 'super_admin':
                where_clause = ""
                params = ()
            else:
                # 自己的 + 公开的
                where_clause = "WHERE kb.owner_id = %s OR kb.is_public = true"
                params = (user_id,)

            await cur.execute(f"""
                SELECT kb.id,
                       kb.name,
                       kb.description,
                       kb.embedding_model,
                       kb.chunk_size,
                       kb.chunk_overlap,
                       kb.owner_id,
                       kb.is_public,
                       kb.allow_public_upload,
                       u.username as owner_name,
                       u.avatar as owner_avatar,
                       kb.created_at,
                       kb.updated_at,
                       COALESCE(doc_counts.doc_count, 0) as doc_count,
                       COALESCE(doc_counts.token_count, 0) as token_count,
                       COALESCE(proc_counts.processing_count, 0) as processing_count
                FROM knowledge_bases kb
                LEFT JOIN users u ON kb.owner_id = u.id
                LEFT JOIN (
                    SELECT kb_id,
                           COUNT(*) as doc_count,
                           SUM(chunk_count) as token_count
                    FROM documents
                    WHERE status = 'completed'
                    GROUP BY kb_id
                ) doc_counts ON kb.id = doc_counts.kb_id
                LEFT JOIN (
                    SELECT kb_id,
                           COUNT(*) as processing_count
                    FROM documents
                    WHERE status = 'processing'
                    GROUP BY kb_id
                ) proc_counts ON kb.id = proc_counts.kb_id
                {where_clause}
                ORDER BY kb.created_at DESC
            """, params)
            rows = await cur.fetchall()

    import json
    from datetime import datetime, date
    from fastapi import Response

    def _default(obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    return Response(
        content=json.dumps({"knowledge_bases": rows}, default=_default, ensure_ascii=False),
        media_type="application/json",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


@router.get("/{kb_id}")
async def get_knowledge_base(kb_id: str, user: Dict = Depends(get_current_user)):
    """获取知识库详情"""
    # import main_pgvector
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM knowledge_bases WHERE id = %s", (kb_id,))
            row = await cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="知识库不存在")

    return row


@router.get("/{kb_id}/processing-progress")
async def get_kb_processing_progress(kb_id: str, user: Dict = Depends(get_current_user)):
    """
    获取知识库文档处理进度

    返回该知识库下所有正在处理中的文档的进度信息
    """
    pool = config.pool

    # 获取处理中的文档列表及其关联的任务进度
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 查询处理中的文档及其任务进度
            await cur.execute("""
                SELECT
                    d.id as doc_id,
                    d.filename,
                    d.status as doc_status,
                    d.chunk_count,
                    t.id as task_id,
                    t.status as task_status,
                    t.progress,
                    t.message,
                    t.updated_at
                FROM documents d
                LEFT JOIN task_queue t ON t.payload->>'doc_id' = d.id
                WHERE d.kb_id = %s AND d.status = 'processing'
                ORDER BY d.created_at DESC
            """, (kb_id,))
            rows = await cur.fetchall()

    if not rows:
        return {
            "kb_id": kb_id,
            "processing_count": 0,
            "documents": [],
            "overall_progress": 0
        }

    documents = []
    total_progress = 0
    valid_progress_count = 0

    for row in rows:
        doc_info = {
            "doc_id": row['doc_id'],
            "filename": row['filename'],
            "status": row['doc_status'],
            "chunk_count": row['chunk_count'],
            "task_id": row['task_id'],
            "task_status": row['task_status'],
            "progress": row['progress'] or 0,
            "message": row['message'] or '准备处理...'
        }
        documents.append(doc_info)

        # 计算平均进度（只计算有进度的文档）
        if row['progress'] is not None:
            total_progress += row['progress']
            valid_progress_count += 1

    overall = round(total_progress / valid_progress_count, 1) if valid_progress_count > 0 else 0

    return {
        "kb_id": kb_id,
        "processing_count": len(documents),
        "documents": documents,
        "overall_progress": overall
    }


@router.post("")
@audit_log()
async def create_knowledge_base(request: CreateKnowledgeBaseRequest, user: Dict = Depends(get_current_user)):
    """创建知识库"""
    kb_id = str(uuid.uuid4())

    pool = config.pool

    # embedding_model 默认从 config 读取实际部署的模型名
    embedding_model = request.embedding_model or getattr(config, 'VLLM_EMBEDDING_MODEL', 'embedding')

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                INSERT INTO knowledge_bases (id, name, description, embedding_model, chunk_size, chunk_overlap, owner_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING *
            """, (kb_id, request.name, request.description, embedding_model,
                  request.chunk_size, request.chunk_overlap, user.get('user_id')))
            new_kb = await cur.fetchone()
            await conn.commit()

    return {
        "id": new_kb['id'],
        "name": new_kb['name'],
        "description": new_kb.get('description'),
        "embedding_model": new_kb.get('embedding_model'),
        "chunk_size": new_kb.get('chunk_size'),
        "chunk_overlap": new_kb.get('chunk_overlap'),
        "owner_id": new_kb.get('owner_id'),
        "doc_count": 0,
        "token_count": 0,
        "created_at": new_kb.get('created_at'),
        "updated_at": new_kb.get('updated_at'),
        "message": "知识库创建成功"
    }


@router.delete("/{kb_id}")
@audit_log()
async def delete_knowledge_base(kb_id: str, user: Dict = Depends(get_current_user)):
    """删除知识库"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM knowledge_bases WHERE id = %s", (kb_id,))
            kb = await cur.fetchone()

            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")

            await cur.execute("DELETE FROM knowledge_bases WHERE id = %s", (kb_id,))
            await conn.commit()

    return {"message": "知识库删除成功"}


@router.put("/{kb_id}")
@audit_log_with_changes()
async def update_knowledge_base(kb_id: str, request: UpdateKnowledgeBaseRequest, user: Dict = Depends(get_current_user)):
    """更新知识库"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM knowledge_bases WHERE id = %s", (kb_id,))
            kb = await cur.fetchone()

            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")

            updates = []
            values = []
            changes = {}

            if request.name is not None:
                updates.append("name = %s")
                values.append(request.name)
                changes["name"] = {"old": kb.get('name'), "new": request.name}
            if request.description is not None:
                updates.append("description = %s")
                values.append(request.description)
                changes["description"] = {"old": kb.get('description'), "new": request.description}
            if request.embedding_model is not None:
                updates.append("embedding_model = %s")
                values.append(request.embedding_model)
                changes["embedding_model"] = {"old": kb.get('embedding_model'), "new": request.embedding_model}
            if request.chunk_size is not None:
                updates.append("chunk_size = %s")
                values.append(request.chunk_size)
                changes["chunk_size"] = {"old": kb.get('chunk_size'), "new": request.chunk_size}
            if request.chunk_overlap is not None:
                updates.append("chunk_overlap = %s")
                values.append(request.chunk_overlap)
                changes["chunk_overlap"] = {"old": kb.get('chunk_overlap'), "new": request.chunk_overlap}

            if updates:
                updates.append("updated_at = NOW()")
                values.append(kb_id)

                await cur.execute(f"""
                    UPDATE knowledge_bases
                    SET {', '.join(updates)}
                    WHERE id = %s
                """, values)
                await conn.commit()

    # 返回包含 changes 的结果，供装饰器使用
    return {"message": "知识库更新成功", "id": kb_id, "changes": changes}


# ==================== 搜索 API ====================

@router.post("/{kb_id}/search")
async def search_knowledge_base(kb_id: str, request: SearchRequest, user: Dict = Depends(get_current_user)):
    """在知识库中搜索（纯向量搜索）"""
    from core import config

    pool = config.pool
    embedding_service = config.embedding_service

    if not embedding_service:
        raise HTTPException(status_code=500, detail="向量服务未初始化")

    # 验证知识库
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM knowledge_bases WHERE id = %s", (kb_id,))
            kb = await cur.fetchone()

            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")

    # 生成查询向量
    query_embedding = await embedding_service.generate(request.query)

    if not query_embedding:
        raise HTTPException(status_code=500, detail="向量生成失败")

    # 执行向量搜索 - 优先使用 Milvus，否则使用 pgvector，最后回退到全文搜索
    from core import config
    from core.utils import semantic_search, full_text_search

    vector_store_instance = config.vector_store_instance
    rerank_service = config.rerank_service

    if vector_store_instance:
        # 使用 Milvus 向量存储
        results = await vector_store_instance.search(
            embedding=query_embedding,
            kb_ids=[kb_id],
            top_k=request.top_k
        )
    else:
        # 回退到 pgvector 语义搜索
        logger.warning("Milvus 未初始化，尝试使用 pgvector 作为回退方案")
        results = await semantic_search(
            pool_ref=pool,
            query_embedding=query_embedding,
            kb_ids=[kb_id],
            top_k=request.top_k
        )
        # 如果 pgvector 也没有数据（没有 embedding 列），回退到全文搜索
        if not results:
            logger.warning("pgvector 搜索无结果，回退到全文搜索")
            # 全文搜索获取更多候选结果，用于 Rerank
            results = await full_text_search(
                pool_ref=pool,
                query=request.query,
                kb_ids=[kb_id],
                top_k=request.top_k * 5  # 获取更多候选用于 Rerank
            )

    # 使用 Rerank 模型重新排序（如果可用）
    if results and rerank_service and rerank_service.enabled:
        try:
            results = await rerank_service.rerank(
                query=request.query,
                documents=results,
                top_k=request.top_k
            )
            logger.info(f"Rerank 完成: 返回 {len(results)} 个结果")
        except Exception as e:
            logger.warning(f"Rerank 失败，使用原始排序: {e}")

    return {
        "query": request.query,
        "results": results,
        "count": len(results),
        "search_type": "vector" if vector_store_instance else "full_text"
    }


@router.post("/{kb_id}/search/hybrid")
async def search_knowledge_base_hybrid(kb_id: str, request: HybridSearchRequest, user: Dict = Depends(get_current_user)):
    """
    混合搜索：向量搜索 + 关键词搜索 + Rerank 重排序

    参考 Dify 和 RAGFlow 的三段式搜索实现：
    1. 向量搜索 - 获取语义相似的候选结果
    2. 关键词搜索 - 获取关键词匹配的结果
    3. Rerank - 用专门模型重新排序，提高准确率

    Args:
        kb_id: 知识库ID
        request: 搜索请求，包含查询文本、返回数量、混合权重等参数

    Returns:
        搜索结果，包含融合后的相似度分数
    """
    from core import config

    pool = config.pool
    embedding_service = config.embedding_service
    rerank_service = config.rerank_service

    if not embedding_service:
        raise HTTPException(status_code=500, detail="向量服务未初始化")

    # 验证知识库
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM knowledge_bases WHERE id = %s", (kb_id,))
            kb = await cur.fetchone()

            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")

    # 生成查询向量
    query_embedding = await embedding_service.generate(request.query)

    if not query_embedding:
        raise HTTPException(status_code=500, detail="向量生成失败")

    # 执行混合搜索（获取更多候选用于 Rerank）
    from core import utils
    results = await utils.hybrid_search(
        pool_ref=pool,
        query=request.query,
        query_embedding=query_embedding,
        kb_ids=[kb_id],
        top_k=request.top_k * 3 if request.enable_rerank else request.top_k,
        alpha=request.alpha
    )

    # 如果启用 Rerank，进行重排序
    if request.enable_rerank and results:
        if not rerank_service:
            raise HTTPException(status_code=500, detail="Rerank 服务未初始化")
        results = await rerank_service.rerank_hybrid(
            query=request.query,
            documents=results,
            top_k=request.top_k,
            alpha=request.rerank_alpha
        )

    return {
        "query": request.query,
        "results": results,
        "count": len(results),
        "search_type": "hybrid_rerank" if request.enable_rerank else "hybrid",
        "alpha": request.alpha,
        "rerank_enabled": request.enable_rerank
    }


# ==================== 同步 API ====================

@router.post("/{kb_id}/sync/web")
async def sync_web_content(
    kb_id: str,
    request: WebSyncRequest,
    user: Dict = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    """同步网页内容到知识库"""
    from core import config

    pool = config.pool
    mcp_server = config.mcp_server

    # 验证知识库
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM knowledge_bases WHERE id = %s", (kb_id,))
            kb = await cur.fetchone()

            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")

    try:
        # 检查是否使用 MCP Chrome DevTools
        if request.use_mcp:
            # 检查 MCP 服务是否可用
            if not mcp_server or not mcp_server.initialized:
                raise HTTPException(
                    status_code=400,
                    detail="MCP 服务未初始化，请先配置 Chrome DevTools MCP 服务器"
                )

            # 调用 MCP Chrome 工具获取页面内容
            try:
                # 导航到页面
                await mcp_server.call_tool(
                    "puppeteer_navigate",
                    {"url": request.url}
                )

                # 等待页面加载
                await asyncio.sleep(3)

                # 获取页面文本内容
                result = await mcp_server.call_tool(
                    "puppeteer_page_content",
                    {}
                )

                # 解析返回的内容
                content_text = result.get("text", "") if isinstance(result, dict) else str(result)

                # 获取页面标题
                title_result = await mcp_server.call_tool(
                    "puppeteer_evaluate",
                    {"script": "document.title"}
                )
                title_text = title_result.get("result", "网页内容") if isinstance(title_result, dict) else "网页内容"

            except Exception as mcp_error:
                logger.error(f"MCP Chrome 调用失败: {mcp_error}")
                raise HTTPException(
                    status_code=400,
                    detail=f"MCP Chrome 调用失败: {str(mcp_error)}，请确保 Chrome DevTools MCP 服务器已配置"
                )

        else:
            # 检查是否是飞书文档链接
            is_feishu = 'feishu.cn' in request.url or 'feishu.com' in request.url or 'larksuite.com' in request.url

            if is_feishu:
                # 飞书文档需要使用 MCP 或飞书 API
                raise HTTPException(
                    status_code=400,
                    detail="飞书文档是动态页面，请启用 use_mcp 参数使用 Chrome DevTools 抓取，或使用飞书集成功能"
                )

            # 抓取网页内容
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                response = await client.get(request.url, headers=headers)
                response.raise_for_status()

            # 解析HTML
            try:
                from bs4 import BeautifulSoup
            except ImportError:
                raise HTTPException(status_code=500, detail="缺少 HTML 解析库，请安装 beautifulsoup4")

            soup = BeautifulSoup(response.text, 'html.parser')

            # 移除script和style标签
            for script in soup(['script', 'style', 'nav', 'footer', 'header']):
                script.decompose()

            # 提取主要内容
            title = soup.find('title')
            title_text = title.get_text() if title else "网页内容"

            # 尝试找到主要内容区域
            main_content = (
                soup.find('main') or
                soup.find('article') or
                soup.find('div', class_=lambda x: x and ('content' in str(x).lower() or 'article' in str(x).lower())) or
                soup.body
            )

            if main_content:
                # 获取段落文本
                paragraphs = main_content.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li'])
                content_text = '\n\n'.join([p.get_text().strip() for p in paragraphs if p.get_text().strip()])
            else:
                content_text = soup.get_text()

        # 清理内容
        content_text = '\n'.join([line.strip() for line in content_text.split('\n') if line.strip()])
        word_count = len(content_text)

        if word_count < 100:
            raise HTTPException(status_code=400, detail="网页内容太少，无法有效提取（可能需要启用 use_mcp 参数）")

        # 创建文档记录
        doc_id = str(uuid.uuid4())
        doc_name = f"{title_text[:50]} - {request.url}"

        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    INSERT INTO documents (id, kb_id, name, file_path, status, word_count, chunk_count, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (doc_id, kb_id, doc_name, f"web:{request.url}", 'pending', word_count, 0, datetime.now()))

        # 后台处理文档
        if background_tasks:
            background_tasks.add_task(_process_web_document, doc_id, kb_id, content_text)

        return {
            "document_id": doc_id,
            "document_name": doc_name,
            "word_count": word_count,
            "message": "网页内容同步成功，正在处理中"
        }

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=400, detail=f"无法访问网页: {e.response.status_code}")
    except Exception as e:
        logger.error(f"网页同步失败: {e}")
        raise HTTPException(status_code=500, detail=f"网页同步失败: {str(e)}")


async def _process_web_document(doc_id: str, kb_id: str, content: str):
    """后台处理网页文档"""
    from core import config
    from core.document_processor import DocumentProcessor

    pool = config.pool
    embedding_service = config.embedding_service
    vector_store_instance = config.vector_store_instance
    VECTOR_STORE_TYPE = config.VECTOR_STORE_TYPE

    try:
        # 分块
        processor = DocumentProcessor()
        chunks = processor.chunk_text(content)

        # 生成嵌入向量
        embeddings = await embedding_service.generate_batch(chunks)

        # 存储到数据库
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                # 先插入 chunks 记录（不含向量）
                chunk_ids = []
                for i, chunk in enumerate(chunks):
                    chunk_id = str(uuid.uuid4())
                    chunk_ids.append(chunk_id)
                    await cur.execute("""
                        INSERT INTO chunks (id, doc_id, chunk_index, content)
                        VALUES (%s, %s, %s, %s)
                    """, (chunk_id, doc_id, i, chunk))

                # 根据向量存储类型存储向量
                if VECTOR_STORE_TYPE == 'milvus' and vector_store_instance:
                    # 使用 Milvus 存储向量
                    valid_items = []
                    for chunk_id, embedding in zip(chunk_ids, embeddings):
                        if embedding and len(embedding) > 0:
                            # 获取 chunk 内容
                            chunk_index = chunk_ids.index(chunk_id)
                            valid_items.append({
                                'chunk_id': chunk_id,
                                'document_id': doc_id,
                                'content': chunks[chunk_index],
                                'embedding': embedding,
                                'chunk_index': chunk_index,
                            })

                    if valid_items:
                        await vector_store_instance.insert_batch(valid_items)
                        logger.info(f"网页文档 {doc_id} 向量存储到 Milvus 完成: {len(valid_items)} 个chunk")

                else:
                    # 使用 pgvector 存储向量（PostgreSQL）
                    for chunk_id, embedding in zip(chunk_ids, embeddings):
                        if embedding and len(embedding) > 0:
                            embedding_str = str(embedding).replace('[', '[').replace(']', ']')
                            await cur.execute("""
                                UPDATE chunks SET embedding = %s::vector WHERE id = %s
                            """, (embedding_str, chunk_id))

                # 更新文档状态
                await cur.execute("""
                    UPDATE documents
                    SET status = 'completed', chunk_count = %s, processed_at = %s
                    WHERE id = %s
                """, (len(chunks), datetime.now(), doc_id))

                await conn.commit()

        logger.info(f"网页文档 {doc_id} 处理完成，共 {len(chunks)} 个chunk")

    except Exception as e:
        logger.error(f"处理网页文档 {doc_id} 失败: {e}")
        # 更新状态为失败
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE documents SET status = 'failed' WHERE id = %s", (doc_id,))
                await conn.commit()


# ==================== 飞书集成 API ====================

@router.get("/integrations/feishu/config")
async def get_feishu_config(user: Dict = Depends(get_current_user)):
    """获取飞书配置（敏感信息脱敏）"""
    # import main_pgvector
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT feishu_app_id FROM system_config WHERE id = '1'")
            row = await cur.fetchone()

            # 脱敏处理
            config = {"feishu_app_id": row['feishu_app_id'] if row and row['feishu_app_id'] else None}
            return config


@router.post("/integrations/feishu/config")
async def save_feishu_config(
    request: FeishuConfigRequest,
    user: Dict = Depends(get_current_user)
):
    """保存飞书配置"""
    # import main_pgvector
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 检查是否是管理员
            if user.get('role') not in ['admin', 'super_admin']:
                raise HTTPException(status_code=403, detail="只有管理员可以配置飞书集成")

            await cur.execute("""
                INSERT INTO system_config (id, feishu_app_id, feishu_app_secret, updated_at)
                VALUES ('1', %s, %s, NOW())
                ON CONFLICT (id) DO UPDATE SET
                    feishu_app_id = EXCLUDED.feishu_app_id,
                    feishu_app_secret = EXCLUDED.feishu_app_secret,
                    updated_at = NOW()
            """, (request.app_id, request.app_secret))
            await conn.commit()

            return {"message": "飞书配置已保存"}


@router.delete("/integrations/feishu/config")
async def delete_feishu_config(user: Dict = Depends(get_current_user)):
    """删除飞书配置"""
    # import main_pgvector
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 检查是否是管理员
            if user.get('role') not in ['admin', 'super_admin']:
                raise HTTPException(status_code=403, detail="只有管理员可以删除飞书配置")

            await cur.execute("UPDATE system_config SET feishu_app_id = NULL, feishu_app_secret = NULL WHERE id = '1'")
            await conn.commit()

            return {"message": "飞书配置已删除"}


@router.post("/{kb_id}/sync/feishu")
async def sync_feishu_document(
    kb_id: str,
    request: FeishuWikiSyncRequest,
    user: Dict = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    """同步飞书文档到知识库"""
    # import main_pgvector
    pool = config.pool

    # 验证知识库
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM knowledge_bases WHERE id = %s", (kb_id,))
            kb = await cur.fetchone()

            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")

    # 获取飞书配置
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT feishu_app_id, feishu_app_secret FROM system_config WHERE id = '1'")
            config = await cur.fetchone()

    if not config or not config.get('feishu_app_id') or not config.get('feishu_app_secret'):
        raise HTTPException(
            status_code=400,
            detail="请先在系统设置中配置飞书 App ID 和 App Secret"
        )

    app_id = config['feishu_app_id']
    app_secret = config['feishu_app_secret']

    try:
        import json
        import hashlib

        # 解析 URL 中的 node_token
        node_token = request.node_token
        if request.url:
            # 从 URL 中解析 node_token
            # 格式: https://xxx.feishu.cn/wiki/xxxxx 或 https://xxx.feishu.cn/docx/xxxxx
            if '/wiki/' in request.url:
                node_token = request.url.split('/wiki/')[1].split('?')[0]
            elif '/docx/' in request.url:
                node_token = request.url.split('/docx/')[1].split('?')[0]

        if not node_token:
            raise HTTPException(
                status_code=400,
                detail="请提供飞书文档 node_token 或分享链接"
            )

        # 1. 获取 tenant_access_token
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": app_id,
                    "app_secret": app_secret
                }
            )
            token_data = token_response.json()
            if token_data.get('code') != 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"获取飞书访问令牌失败: {token_data.get('msg')}"
                )
            tenant_access_token = token_data.get('tenant_access_token')

        # 2. 获取文档内容
        async with httpx.AsyncClient() as client:
            # 获取文档内容
            doc_response = await client.get(
                f"https://open.feishu.cn/open-apis/docx/v1/documents/{node_token}/raw_content",
                headers={"Authorization": f"Bearer {tenant_access_token}"}
            )

            doc_data = doc_response.json()
            if doc_data.get('code') != 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"获取飞书文档失败: {doc_data.get('msg')}"
                )

            # 获取文档信息（标题等）
            info_response = await client.get(
                f"https://open.feishu.cn/open-apis/docx/v1/documents/{node_token}",
                headers={"Authorization": f"Bearer {tenant_access_token}"}
            )
            info_data = info_response.json()
            doc_title = f"飞书文档_{node_token[:8]}"
            if info_data.get('code') == 0 and info_data.get('data', {}).get('document'):
                doc_title = info_data['data']['document'].get('title', doc_title)

            # 处理文档内容
            content_data = doc_data.get('data', {})
            content_text = content_data.get('content', '')

            if not content_text:
                raise HTTPException(
                    status_code=400,
                    detail="飞书文档内容为空"
                )

            # 处理 markdown 内容
            if isinstance(content_text, dict):
                # 处理富文本格式
                text_content = _parse_feishu_content(content_text)
            else:
                text_content = content_text

            word_count = len(text_content)

            # 3. 创建文档记录
            doc_id = str(uuid.uuid4())
            doc_name = f"{doc_title[:50]} - 飞书"

            async with pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("""
                        INSERT INTO documents (id, kb_id, name, file_path, status, word_count, chunk_count, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, (doc_id, kb_id, doc_name, f"feishu:{node_token}", 'pending', word_count, 0, datetime.now()))

            # 4. 后台处理文档
            if background_tasks:
                background_tasks.add_task(_process_web_document, doc_id, kb_id, text_content)

            return {
                "document_id": doc_id,
                "document_name": doc_name,
                "word_count": word_count,
                "message": "飞书文档同步成功"
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"飞书文档同步失败: {e}")
        raise HTTPException(status_code=500, detail=f"飞书文档同步失败: {str(e)}")


def _parse_feishu_content(content: dict) -> str:
    """解析飞书富文本内容为纯文本"""
    text_parts = []

    def parse_node(node):
        if isinstance(node, dict):
            node_type = node.get('type', '')

            if node_type == 'paragraph':
                # 处理段落
                text_elements = node.get('textElements', [])
                for elem in text_elements:
                    if elem.get('textRun'):
                        text_parts.append(elem['textRun'].get('text', ''))
                text_parts.append('\n')

            elif node_type == 'textRun':
                text_parts.append(node.get('text', ''))

            elif node_type == 'heading1':
                text_parts.append('\n# ')
                for elem in node.get('textElements', []):
                    if elem.get('textRun'):
                        text_parts.append(elem['textRun'].get('text', ''))
                text_parts.append('\n')

            elif node_type == 'heading2':
                text_parts.append('\n## ')
                for elem in node.get('textElements', []):
                    if elem.get('textRun'):
                        text_parts.append(elem['textRun'].get('text', ''))
                text_parts.append('\n')

            elif node_type == 'heading3':
                text_parts.append('\n### ')
                for elem in node.get('textElements', []):
                    if elem.get('textRun'):
                        text_parts.append(elem['textRun'].get('text', ''))
                text_parts.append('\n')

            elif node_type == 'bullet':
                for elem in node.get('textElements', []):
                    if elem.get('textRun'):
                        text_parts.append(' ' + elem['textRun'].get('text', ''))
                text_parts.append('\n')

            elif node_type == 'ordered':
                for elem in node.get('textElements', []):
                    if elem.get('textRun'):
                        text_parts.append(elem['textRun'].get('text', ''))
                text_parts.append('\n')

            elif node_type == 'table':
                # 处理表格
                for row in node.get('tableRows', []):
                    row_text = []
                    for cell in row.get('tableCells', []):
                        cell_text = ''
                        for elem in cell.get('textElements', []):
                            if elem.get('textRun'):
                                cell_text += elem['textRun'].get('text', '')
                        row_text.append(cell_text)
                    text_parts.append(' | '.join(row_text))
                text_parts.append('\n')

            # 递归处理子节点
            for key, value in node.items():
                if key == 'children' and isinstance(value, list):
                    for child in value:
                        parse_node(child)
                elif isinstance(value, (dict, list)):
                    parse_node(value)

        elif isinstance(node, list):
            for item in node:
                parse_node(item)

    parse_node(content)
    return ''.join(text_parts).strip()
