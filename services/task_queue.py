# TaskQueue - 异步任务队列服务
# 从 main_pgvector.py 拆分

import json
import logging
import uuid
from typing import Dict, List, Any, Optional

from .cache import CacheManager

logger = logging.getLogger(__name__)


class TaskType:
    """任务类型枚举"""
    DOCUMENT_UPLOAD = "document_upload"
    DOCUMENT_INDEX = "document_index"
    KNOWLEDGE_BASE_DELETE = "kb_delete"
    BATCH_DELETE = "batch_delete"
    EMBEDDING_GENERATION = "embedding_gen"


class TaskQueue:
    """
    异步任务队列 - 参考 Dify 的任务系统

    功能：
    - 创建异步任务
    - 更新任务状态和进度
    - 获取任务状态
    - 任务失败重试
    """

    def __init__(self, cache_manager: CacheManager):
        self.cache = cache_manager
        self.pool_ref = None  # 将在运行时设置

    async def create_task(
        self,
        task_type: TaskType,
        metadata: Dict[str, Any],
        created_by: Optional[str] = None
    ) -> str:
        """创建新任务并返回任务 ID"""
        task_id = str(uuid.uuid4())

        async with self.pool_ref.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO async_tasks (id, type, status, metadata, created_by)
                    VALUES (%s, %s, 'pending', %s, %s)
                """, (task_id, task_type.value if hasattr(task_type, 'value') else task_type, json.dumps(metadata, ensure_ascii=False), created_by))
                await conn.commit()

        # 同时缓存到 Redis
        await self.cache.set(
            f"task:{task_id}",
            {"id": task_id, "type": task_type.value if hasattr(task_type, 'value') else task_type, "status": "pending", "progress": 0},
            ttl=3600
        )

        logger.info(f"任务已创建: {task_id}, 类型: {task_type.value if hasattr(task_type, 'value') else task_type}")
        return task_id

    async def update_progress(self, task_id: str, progress: float, status: str = "processing"):
        """更新任务进度"""
        async with self.pool_ref.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE async_tasks
                    SET progress = %s, status = %s, updated_at = NOW()
                    WHERE id = %s
                """, (progress, status, task_id))
                await conn.commit()

        # 更新 Redis 缓存
        cached = await self.cache.get(f"task:{task_id}")
        if cached:
            cached['progress'] = progress
            cached['status'] = status
            await self.cache.set(f"task:{task_id}", cached, ttl=3600)

    async def complete_task(self, task_id: str, result: Dict[str, Any]):
        """标记任务完成"""
        async with self.pool_ref.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE async_tasks
                    SET status = 'completed', progress = 100, result = %s,
                        updated_at = NOW(), completed_at = NOW()
                    WHERE id = %s
                """, (json.dumps(result, ensure_ascii=False), task_id))
                await conn.commit()

        # 更新 Redis 缓存
        cached = await self.cache.get(f"task:{task_id}")
        if cached:
            cached['status'] = 'completed'
            cached['progress'] = 100
            cached['result'] = result
            await self.cache.set(f"task:{task_id}", cached, ttl=3600)

        logger.info(f"任务完成: {task_id}")

    async def fail_task(self, task_id: str, error: str):
        """标记任务失败"""
        async with self.pool_ref.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE async_tasks
                    SET status = 'failed', error = %s, updated_at = NOW(), completed_at = NOW()
                    WHERE id = %s
                """, (error, task_id))
                await conn.commit()

        # 更新 Redis 缓存
        cached = await self.cache.get(f"task:{task_id}")
        if cached:
            cached['status'] = 'failed'
            cached['error'] = error
            await self.cache.set(f"task:{task_id}", cached, ttl=3600)

        logger.error(f"任务失败: {task_id}, 错误: {error}")

    async def get_task(self, task_id: str) -> Optional[Dict]:
        """获取任务状态"""
        # 先从 Redis 获取
        cached = await self.cache.get(f"task:{task_id}")
        if cached:
            return cached

        # 从数据库获取
        async with self.pool_ref.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM async_tasks WHERE id = %s", (task_id,))
                task = await cur.fetchone()
                if task:
                    # 缓存到 Redis
                    await self.cache.set(f"task:{task_id}", task, ttl=3600)
                return task

    async def list_tasks(
        self,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        created_by: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict]:
        """列出任务"""
        from psycopg.rows import dict_row
        async with self.pool_ref.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                conditions = []
                params = []
                param_idx = 1

                if status:
                    conditions.append(f"status = ${param_idx}")
                    params.append(status)
                    param_idx += 1
                if task_type:
                    conditions.append(f"type = ${param_idx}")
                    params.append(task_type)
                    param_idx += 1
                if created_by:
                    conditions.append(f"created_by = ${param_idx}")
                    params.append(created_by)
                    param_idx += 1

                where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
                params.extend([limit, offset])

                query = f"""
                    SELECT * FROM async_tasks
                    {where_clause}
                    ORDER BY created_at DESC
                    LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """

                await cur.execute(query, params)
                return await cur.fetchall()
