# 文档管理路由
# /api/v1/knowledge-bases/{kb_id}/documents/* 相关接口
# /api/v1/documents/pool/* 相关接口

import logging
from core import config, audit_log, audit_log_with_changes

import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from pydantic import BaseModel
from psycopg.rows import dict_row

from .auth import get_current_user

logger = logging.getLogger(__name__)

# 创建路由
router = APIRouter(prefix="/api/v1/knowledge-bases/{kb_id}/documents", tags=["文档管理"])

# 文档池路由
pool_router = APIRouter(prefix="/api/v1/documents", tags=["文档池"])


# ==================== 辅助函数 ====================

def get_main_module():
    """延迟获取主模块，避免循环导入

    注意：当运行 python main_pgvector.py 时，模块名为 __main__ 而不是 main_pgvector
    """
    import sys
    import importlib
    # 首先尝试 __main__（当直接运行 main_pgvector.py 时）
    _main_pgvector = sys.modules.get('__main__')
    # 验证是否是正确的模块（检查是否有 task_queue 属性）
    if _main_pgvector and hasattr(_main_pgvector, 'task_queue'):
        return _main_pgvector
    # 否则尝试 main_pgvector（当被作为模块导入时）
    _main_pgvector = sys.modules.get('main_pgvector')
    if _main_pgvector:
        return _main_pgvector
    # 最后尝试导入
    _main_pgvector = importlib.import_module('main_pgvector')
    return _main_pgvector


# ==================== 知识库文档 API ====================

@router.post("/upload")
async def upload_document(
    kb_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: Dict = Depends(get_current_user)
):
    """
    上传文档到知识库 - 使用任务队列管理

    增强功能：
    - 创建异步任务跟踪处理进度
    - 支持大文件上传
    - 实时进度查询

    权限规则：
    - 知识库所有者可以上传
    - 公开且允许上传的知识库，所有人可以上传
    - 超级管理员可以上传到任何知识库
    """

    pool = config.pool
    task_queue = getattr(get_main_module(), 'task_queue', None)
    DocumentProcessor = getattr(get_main_module(), 'DocumentProcessor', None)
    UPLOAD_DIR = getattr(get_main_module(), 'UPLOAD_DIR', Path("./uploads"))

    user_id = user.get('user_id') if user else None
    user_role = user.get('role') if user else 'member'

    # 验证知识库和权限
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM knowledge_bases WHERE id = %s", (kb_id,))
            kb = await cur.fetchone()

            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")

            # 检查上传权限
            can_upload = (
                user_role == 'super_admin' or  # 超级管理员
                kb.get('owner_id') == user_id or  # 知识库所有者
                (kb.get('is_public') and kb.get('allow_public_upload'))  # 公开且允许上传
            )

            if not can_upload:
                raise HTTPException(status_code=403, detail="没有权限上传到此知识库")

    # 检查文件类型
    ext = Path(file.filename).suffix.lower()
    supported_extensions = ['.txt', '.md', '.json', '.pdf', '.docx', '.xlsx', '.pptx', '.html', '.csv', '.xml']
    if ext not in supported_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型。支持的类型: {', '.join(supported_extensions)}"
        )

    # 保存文件
    doc_id = str(uuid.uuid4())
    file_path = UPLOAD_DIR / f"{doc_id}{ext}"

    content = await file.read()
    with open(file_path, 'wb') as f:
        f.write(content)

    # 创建文档记录
    username = user.get('username') if user else None

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO documents (id, kb_id, name, type, size, status, file_path, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id
            """, (doc_id, kb_id, file.filename, ext[1:], len(content), 'processing', str(file_path)))
            await conn.commit()

    # 记录审计日志
    log_audit = getattr(get_main_module(), 'log_audit', None)
    if log_audit:
        await log_audit(
            entity_type="document",
            entity_id=doc_id,
            action="create",
            user_id=user_id,
            username=username,
            changes={
                "kb_id": {"new": kb_id},
                "name": {"new": file.filename},
                "type": {"new": ext[1:]},
                "size": {"new": len(content)}
            }
        )

    # 创建异步任务
    if task_queue:
        task_id = await task_queue.create_task(
            task_type="document_upload",
            metadata={
                "doc_id": doc_id,
                "kb_id": kb_id,
                "filename": file.filename,
                "file_size": len(content),
                "file_type": ext[1:]
            },
            created_by=user_id
        )

        # 后台处理文档（使用 BackgroundTasks）
        background_tasks.add_task(
            _process_document_with_task,
            task_id,
            doc_id,
            kb_id,
            str(file_path),
            file.filename,
            kb
        )

        return {
            "id": doc_id,
            "task_id": task_id,
            "name": file.filename,
            "status": "processing",
            "message": "文档上传成功，正在后台处理"
        }
    else:
        # 没有任务队列，直接处理
        background_tasks.add_task(
            _process_document_background,
            doc_id,
            kb_id,
            str(file_path),
            file.filename,
            kb
        )

        return {
            "id": doc_id,
            "name": file.filename,
            "status": "processing",
            "message": "文档上传成功，正在后台处理"
        }


async def _process_document_with_task(task_id: str, doc_id: str, kb_id: str, file_path: str, filename: str, kb: Dict):
    """带任务跟踪的后台文档处理"""

    pool = config.pool
    main_pgvector = get_main_module()
    task_queue = getattr(main_pgvector, 'task_queue', None)
    document_processor = getattr(main_pgvector, 'document_processor', None)
    embedding_service = getattr(main_pgvector, 'embedding_service', None)
    vector_store_instance = getattr(main_pgvector, 'vector_store_instance', None)
    VECTOR_STORE_TYPE = getattr(main_pgvector, 'VECTOR_STORE_TYPE', 'milvus')

    logger.info(f"开始处理文档: doc_id={doc_id}, task_queue={task_queue is not None}, document_processor={document_processor is not None}")

    try:
        # 更新任务状态为处理中
        if task_queue:
            await task_queue.update_progress(task_id, 10, "processing")

        # 处理文档
        if document_processor:
            result = await document_processor.process(
                file_path=file_path,
                filename=filename,
                kb_id=kb_id,
                doc_id=doc_id,
                pool_ref=pool,
                embedding_service=embedding_service,
                chunk_size=kb.get('chunk_size', 512),
                chunk_overlap=kb.get('chunk_overlap', 50),
                incremental=True,
                vector_store_type=VECTOR_STORE_TYPE,
                vector_store_instance=vector_store_instance
            )

            # 更新进度
            if task_queue:
                await task_queue.update_progress(task_id, 90, "processing")

            # 标记任务完成
            await task_queue.complete_task(task_id, {
                "doc_id": doc_id,
                "chunks_count": result.get('chunks_count', 0),
                "tokens_count": result.get('tokens_count', 0)
            })

            logger.info(f"文档处理完成: {doc_id}, 任务: {task_id}")

    except Exception as e:
        logger.error(f"文档处理失败: {doc_id}, 错误: {e}")

        # 标记任务失败
        if task_queue:
            await task_queue.fail_task(task_id, str(e))

        # 更新文档状态为失败
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE documents SET status = 'failed', error_message = %s, updated_at = NOW()
                    WHERE id = %s
                """, (str(e), doc_id))
                await conn.commit()


async def _process_document_background(doc_id: str, kb_id: str, file_path: str, filename: str, kb: Dict):
    """后台处理文档"""

    pool = config.pool
    document_processor = getattr(get_main_module(), 'document_processor', None)
    embedding_service = getattr(get_main_module(), 'embedding_service', None)
    vector_store_instance = getattr(get_main_module(), 'vector_store_instance', None)
    VECTOR_STORE_TYPE = getattr(get_main_module(), 'VECTOR_STORE_TYPE', 'milvus')

    try:
        if document_processor:
            result = await document_processor.process(
                file_path=file_path,
                filename=filename,
                kb_id=kb_id,
                doc_id=doc_id,
                pool_ref=pool,
                embedding_service=embedding_service,
                chunk_size=kb.get('chunk_size', 512),
                chunk_overlap=kb.get('chunk_overlap', 50),
                incremental=True,
                vector_store_type=VECTOR_STORE_TYPE,
                vector_store_instance=vector_store_instance
            )
            logger.info(f"文档处理完成: {doc_id}, 结果: {result}")
    except Exception as e:
        logger.error(f"文档处理失败: {doc_id}, 错误: {e}")

        # 更新文档状态为失败
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE documents SET status = 'failed', error_message = %s, updated_at = NOW()
                    WHERE id = %s
                """, (str(e), doc_id))
                await conn.commit()


@router.get("")
async def list_documents(kb_id: str, user: Dict = Depends(get_current_user)):
    """获取知识库的文档列表"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 先检查是否有 created_by 和 updated_by 字段
            await cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'documents'
                AND column_name IN ('created_by', 'updated_by')
            """)
            audit_cols = await cur.fetchall()
            has_audit_fields = len(audit_cols) > 0

            if has_audit_fields:
                await cur.execute("""
                    SELECT d.*,
                           creator.username as created_by_name,
                           creator.avatar as created_by_avatar,
                           updater.username as updated_by_name,
                           updater.avatar as updated_by_avatar
                    FROM documents d
                    LEFT JOIN users creator ON d.created_by = creator.id
                    LEFT JOIN users updater ON d.updated_by = updater.id
                    WHERE d.kb_id = %s
                    ORDER BY d.created_at DESC
                """, (kb_id,))
            else:
                await cur.execute("""
                    SELECT * FROM documents WHERE kb_id = %s ORDER BY created_at DESC
                """, (kb_id,))
            rows = await cur.fetchall()

    return {"documents": rows}


@router.get("/{doc_id}")
async def get_document(kb_id: str, doc_id: str, user: Dict = Depends(get_current_user)):
    """获取文档详情"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 获取文档
            await cur.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
            doc = await cur.fetchone()

            if not doc:
                raise HTTPException(status_code=404, detail="文档不存在")

            # 获取分块
            await cur.execute("""
                SELECT id, chunk_index, content, metadata, created_at
                FROM chunks WHERE doc_id = %s
                ORDER BY chunk_index
            """, (doc_id,))
            chunks = await cur.fetchall()

    return {
        "document": doc,
        "chunks": chunks
    }


@router.put("/{doc_id}/chunks/{chunk_id}")
async def update_chunk(
    kb_id: str,
    doc_id: str,
    chunk_id: str,
    request: Dict[str, str],
    user: Dict = Depends(get_current_user)
):
    """更新文档分块（热插拔编辑）"""

    pool = config.pool
    embedding_service = getattr(get_main_module(), 'embedding_service', None)
    add_embedding = getattr(get_main_module(), 'add_embedding', None)
    log_audit = getattr(get_main_module(), 'log_audit', None)

    new_content = request.get("content")

    if not new_content:
        raise HTTPException(status_code=400, detail="内容不能为空")

    user_id = user.get('user_id') if user else None
    username = user.get('username') if user else None

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 获取旧内容用于审计
            await cur.execute("""
                SELECT content FROM chunks WHERE id = %s AND doc_id = %s
            """, (chunk_id, doc_id))
            old_chunk = await cur.fetchone()

            if not old_chunk:
                raise HTTPException(status_code=404, detail="分块不存在")

            old_content = old_chunk.get('content', '')

            # 更新分块内容
            await cur.execute("""
                UPDATE chunks SET content = %s
                WHERE id = %s AND doc_id = %s
            """, (new_content, chunk_id, doc_id))

            # 重新生成向量嵌入
            if embedding_service and add_embedding:
                new_embedding = await embedding_service.generate(new_content)

                # 更新向量
                await add_embedding(chunk_id, new_embedding, conn)

            await conn.commit()

    # 记录审计日志 - 内容变更
    if log_audit:
        await log_audit(
            entity_type="chunk",
            entity_id=chunk_id,
            action="update",
            user_id=user_id,
            username=username,
            changes={
                "content": {
                    "old": old_content,
                    "new": new_content
                },
                "doc_id": {"old": doc_id},
                "kb_id": {"old": kb_id}
            }
        )

    return {"message": "分块已更新，向量索引已重新生成"}


@router.post("/{doc_id}/reindex")
async def reindex_document(
    kb_id: str,
    doc_id: str,
    user: Dict = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    """重新索引文档"""

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
            doc = await cur.fetchone()

            if not doc:
                raise HTTPException(status_code=404, detail="文档不存在")

            # 获取知识库配置
            await cur.execute("SELECT * FROM knowledge_bases WHERE id = %s", (kb_id,))
            kb = await cur.fetchone()

            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")

            # 更新状态
            await cur.execute("UPDATE documents SET status = 'processing' WHERE id = %s", (doc_id,))
            await conn.commit()

    # 检查是否是飞书文档（content 字段有内容）
    if doc.get('content') and not doc.get('file_path'):
        # 飞书文档直接处理内容
        if background_tasks:
            background_tasks.add_task(_reindex_feishu_document, doc_id, kb_id, doc['content'], kb)
    else:
        # 普通文件文档
        if background_tasks:
            background_tasks.add_task(_process_document_background, doc_id, kb_id, doc['file_path'], doc['name'], kb)

    return {"message": "文档重新索引已开始"}


async def _reindex_feishu_document(doc_id: str, kb_id: str, content: str, kb: Dict):
    """重新索引飞书文档"""

    pool = config.pool
    VECTOR_STORE_TYPE = getattr(get_main_module(), 'VECTOR_STORE_TYPE', 'pgvector')
    vector_store_instance = getattr(get_main_module(), 'vector_store_instance', None)
    embedding_service = getattr(get_main_module(), 'embedding_service', None)

    chunk_size = kb.get('chunk_size', 512)
    chunk_overlap = kb.get('chunk_overlap', 50)

    # 分块
    paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
    chunks = []
    current_chunk = ""
    current_size = 0

    for para in paragraphs:
        para_size = len(para)
        if current_size + para_size > chunk_size and current_chunk:
            chunks.append(current_chunk.strip())
            overlap_text = current_chunk[-chunk_overlap:] if len(current_chunk) > chunk_overlap else current_chunk
            current_chunk = overlap_text + "\n\n" + para
            current_size = len(current_chunk)
        else:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
            current_size = len(current_chunk)

    if current_chunk:
        chunks.append(current_chunk.strip())

    if not chunks:
        logger.warning(f"飞书文档 {doc_id} 分块为空")
        # 标记为失败
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("UPDATE documents SET status = 'failed' WHERE id = %s", (doc_id,))
                await conn.commit()
        return

    # 使用同一个连接完成所有数据库操作
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 删除旧的分块
            await cur.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))

            # 插入新分块
            chunk_ids = []
            for i, chunk_text in enumerate(chunks):
                chunk_id = f"{doc_id}-{i}"
                chunk_ids.append(chunk_id)
                await cur.execute("""
                    INSERT INTO chunks (id, doc_id, chunk_index, content, chunk_size, created_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, (chunk_id, doc_id, i, chunk_text, len(chunk_text)))

            # 更新文档的 chunk_count
            await cur.execute("UPDATE documents SET chunk_count = %s WHERE id = %s", (len(chunks), doc_id))
            await conn.commit()

            # 生成向量嵌入
            if embedding_service:
                texts = [f"{doc_id}\n{chunk}" for chunk in chunks]
                embeddings = await embedding_service.generate_batch(texts)

                # 根据向量存储类型存储向量
                if VECTOR_STORE_TYPE == 'milvus' and vector_store_instance:
                    # 使用 Milvus 存储向量
                    items = []
                    for chunk_id, emb in zip(chunk_ids, embeddings):
                        if emb:
                            chunk_index = int(chunk_id.split('-')[-1])
                            items.append({
                                'chunk_id': chunk_id,
                                'document_id': doc_id,
                                'content': chunks[chunk_index],
                                'embedding': emb,
                                'chunk_index': chunk_index,
                            })
                    if items:
                        await vector_store_instance.insert_batch(items)
                else:
                    # 使用 pgvector 存储向量
                    add_embedding = getattr(get_main_module(), 'add_embedding', None)
                    for chunk_id, emb in zip(chunk_ids, embeddings):
                        if emb and add_embedding:
                            await add_embedding(chunk_id, emb, conn)

            # 更新文档状态
            await cur.execute("""
                UPDATE documents SET status = 'completed', processed_at = NOW() WHERE id = %s
            """, (doc_id,))
            await conn.commit()


@router.delete("/{doc_id}")
@audit_log()
async def delete_document(kb_id: str, doc_id: str, user: Dict = Depends(get_current_user)):
    """删除文档"""
    from core import config

    if not user:
        raise HTTPException(status_code=401, detail="请先登录")

    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 验证文档存在
            await cur.execute(
                "SELECT * FROM documents WHERE id = %s AND kb_id = %s",
                (doc_id, kb_id)
            )
            doc = await cur.fetchone()

            if not doc:
                raise HTTPException(status_code=404, detail="文档不存在")

            # 删除文档（硬删除）
            # 1. 先从 Milvus 删除向量（使用 document_id）
            if config.vector_store_instance:
                try:
                    count = await config.vector_store_instance.delete_by_document(doc_id)
                    logger.info(f"已从 Milvus 删除文档 {doc_id} 的 {count} 个向量")
                except Exception as e:
                    logger.warning(f"Milvus 删除失败: {e}")

            # 2. 删除 chunks
            await cur.execute("DELETE FROM chunks WHERE doc_id = %s", (doc_id,))
            # 3. 删除文档
            await cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
            await conn.commit()

    return {"message": "文档删除成功"}


# ==================== 文档池 API ====================

@pool_router.post("/pool/upload")
async def upload_to_pool(
    file: UploadFile = File(...),
    user: Dict = Depends(get_current_user)
):
    """
    上传文档到文档池（未分配到知识库）

    文档先上传到池中，稍后可以通过穿梭框分配到具体知识库
    """

    pool = config.pool
    UPLOAD_DIR = config.UPLOAD_DIR

    supported_extensions = ['.txt', '.md', '.json', '.pdf', '.docx', '.xlsx', '.pptx', '.html', '.csv', '.xml']

    # 检查文件类型
    ext = Path(file.filename).suffix.lower()
    if ext not in supported_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型。支持的类型: {', '.join(supported_extensions)}"
        )

    # 保存文件
    doc_id = str(uuid.uuid4())
    file_path = UPLOAD_DIR / f"{doc_id}{ext}"

    content = await file.read()
    with open(file_path, 'wb') as f:
        f.write(content)

    # 创建文档记录（kb_id 为 NULL，表示在文档池中）
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO documents (id, kb_id, name, type, size, status, file_path, created_at, updated_at)
                VALUES (%s, NULL, %s, %s, %s, %s, %s, NOW(), NOW())
                RETURNING id
            """, (doc_id, file.filename, ext[1:], len(content), 'pending', str(file_path)))
            await conn.commit()

    return {
        "id": doc_id,
        "name": file.filename,
        "status": "pending",
        "size": len(content),
        "message": "文档已上传到文档池"
    }


@pool_router.get("/pool")
async def list_pool_documents(
    status: Optional[str] = None,
    user: Dict = Depends(get_current_user)
):
    """获取文档池中的文档列表（kb_id 为 NULL 的文档）"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            if status:
                await cur.execute("""
                    SELECT * FROM documents WHERE kb_id IS NULL AND status = %s
                    ORDER BY created_at DESC
                """, (status,))
            else:
                await cur.execute("""
                    SELECT * FROM documents WHERE kb_id IS NULL
                    ORDER BY created_at DESC
                """)
            rows = await cur.fetchall()

    return {"documents": rows}


@pool_router.post("/assign")
async def assign_documents_to_kb(
    request: Dict[str, Any],
    user: Dict = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    """
    将文档池中的文档批量分配到知识库

    Body: {
        "doc_ids": ["doc1", "doc2"],
        "kb_id": "kb_id"
    }
    """

    pool = config.pool

    doc_ids = request.get("doc_ids", [])
    kb_id = request.get("kb_id")

    if not doc_ids or not kb_id:
        raise HTTPException(status_code=400, detail="请提供文档ID和知识库ID")

    # 验证知识库存在
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM knowledge_bases WHERE id = %s", (kb_id,))
            kb = await cur.fetchone()

            if not kb:
                raise HTTPException(status_code=404, detail="知识库不存在")

    # 分配文档并处理
    results = []
    for doc_id in doc_ids:
        try:
            async with pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    # 获取文档
                    await cur.execute("SELECT * FROM documents WHERE id = %s AND kb_id IS NULL", (doc_id,))
                    doc = await cur.fetchone()

                    if not doc:
                        results.append({"doc_id": doc_id, "status": "failed", "message": "文档不存在或已分配"})
                        continue

                    # 更新文档的 kb_id
                    await cur.execute("""
                        UPDATE documents SET kb_id = %s, status = 'processing', updated_at = NOW()
                        WHERE id = %s
                    """, (kb_id, doc_id))
                    await conn.commit()

            # 后台处理文档
            if background_tasks:
                background_tasks.add_task(
                    _process_document_with_task,
                    f"assign-{doc_id}",
                    doc_id,
                    kb_id,
                    doc['file_path'],
                    doc['name'],
                    kb
                )

            results.append({"doc_id": doc_id, "status": "success", "message": "分配成功"})

        except Exception as e:
            logger.error(f"分配文档失败: {doc_id}, 错误: {e}")
            results.append({"doc_id": doc_id, "status": "failed", "message": str(e)})

    return {"results": results}


@pool_router.post("/unassign")
async def unassign_documents(
    request: Dict[str, Any],
    user: Dict = Depends(get_current_user)
):
    """
    将文档从知识库移回文档池

    Body: {
        "doc_ids": ["doc1", "doc2"]
    }
    """

    pool = config.pool
    VECTOR_STORE_TYPE = getattr(get_main_module(), 'VECTOR_STORE_TYPE', 'pgvector')
    vector_store_instance = getattr(get_main_module(), 'vector_store_instance', None)

    doc_ids = request.get("doc_ids", [])

    if not doc_ids:
        raise HTTPException(status_code=400, detail="请提供文档ID列表")

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 删除向量（Milvus）
            if VECTOR_STORE_TYPE == 'milvus' and vector_store_instance:
                for doc_id in doc_ids:
                    try:
                        await vector_store_instance.delete_by_document(doc_id)
                    except Exception as e:
                        logger.warning(f"删除 Milvus 向量失败: {e}")

            # 将文档的 kb_id 设为 NULL
            await cur.execute("""
                UPDATE documents SET kb_id = NULL, status = 'pending', chunk_count = 0, updated_at = NOW()
                WHERE id = ANY(%s)
            """, (doc_ids,))

            # 删除相关 chunks
            await cur.execute("""
                DELETE FROM chunks WHERE doc_id = ANY(%s)
            """, (doc_ids,))

            await conn.commit()

    return {"message": f"已将 {len(doc_ids)} 个文档移回文档池"}


@pool_router.delete("/pool/{doc_id}")
async def delete_pool_document(
    doc_id: str,
    user: Dict = Depends(get_current_user)
):
    """从文档池删除文档"""
    pool = config.pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 验证文档存在且在文档池中
            await cur.execute("SELECT * FROM documents WHERE id = %s AND kb_id IS NULL", (doc_id,))
            doc = await cur.fetchone()

            if not doc:
                raise HTTPException(status_code=404, detail="文档不存在")

            # 删除文件
            if doc['file_path']:
                try:
                    file_path = Path(doc['file_path'])
                    if file_path.exists():
                        file_path.unlink()
                except Exception as e:
                    logger.warning(f"删除文件失败: {e}")

            # 删除数据库记录
            await cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
            await conn.commit()

    return {"message": "文档已删除"}


# 导出路由器
__all__ = ['router', 'pool_router']
