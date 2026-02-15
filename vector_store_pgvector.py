"""
PgVector 向量存储实现

使用 PostgreSQL + pgvector 扩展进行向量存储和检索
"""
import logging
from typing import List, Dict, Any, Optional
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from vector_store import VectorStore, VectorConfig, ChunkResult, MetricType

logger = logging.getLogger(__name__)


class PgVectorStore(VectorStore):
    """
    PostgreSQL + pgvector 向量存储实现

    适合中小规模数据（百万级向量以内）
    优势：部署简单、支持事务、ACID 保证
    """

    def __init__(self, config: VectorConfig, pool: AsyncConnectionPool):
        super().__init__(config)
        self.pool = pool

    async def initialize(self):
        """初始化 - 检查 pgvector 扩展"""
        if self._initialized:
            return

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 检查 pgvector 扩展
                await cur.execute("""
                    SELECT EXISTS(
                        SELECT 1 FROM pg_extension WHERE extname = 'vector'
                    )
                """)
                has_vector = (await cur.fetchone())[0]

                if not has_vector:
                    logger.warning("pgvector 扩展未安装，请运行: CREATE EXTENSION vector;")
                    raise RuntimeError("pgvector 扩展未安装")

                # 确保表存在
                await self._ensure_tables(cur)

        self._initialized = True
        logger.info("PgVectorStore 初始化完成")

    async def _ensure_tables(self, cur):
        """确保表结构存在"""
        # chunks 表在主应用中创建，这里只检查
        await cur.execute("""
            SELECT EXISTS(
                SELECT 1 FROM information_schema.tables
                WHERE table_name = 'chunks'
            )
        """)
        has_table = (await cur.fetchone())[0]

        if not has_table:
            logger.info("创建 chunks 表")
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding vector(%s),
                    metadata JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """, (self.config.dimension,))

            # 创建 HNSW 索引
            index_type = "vector_cosine_ops"
            if self.config.metric_type == MetricType.L2:
                index_type = "vector_l2_ops"
            elif self.config.metric_type == MetricType.IP:
                index_type = "vector_ip_ops"

            await cur.execute(f"""
                CREATE INDEX IF NOT EXISTS chunks_embedding_idx
                ON chunks USING hnsw (embedding {index_type})
            """)
            logger.info(f"创建 HNSW 索引: {index_type}")

    async def close(self):
        """关闭连接"""
        # 连接池由主应用管理，这里不做处理
        pass

    async def insert(
        self,
        chunk_id: str,
        document_id: str,
        content: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
        chunk_index: int = 0
    ) -> bool:
        """插入单个向量"""
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        INSERT INTO chunks (id, doc_id, chunk_index, content, embedding, metadata)
                        VALUES (%s, %s, %s, %s, %s::vector, %s)
                        ON CONFLICT (id) DO UPDATE
                        SET content = EXCLUDED.content,
                            embedding = EXCLUDED.embedding,
                            metadata = EXCLUDED.metadata
                    """, (
                        chunk_id,
                        document_id,
                        chunk_index,
                        content,
                        f"[{','.join(map(str, embedding))}]",
                        metadata or {}
                    ))
                    await conn.commit()
                    return True
        except Exception as e:
            logger.error(f"插入向量失败: {e}")
            return False

    async def insert_batch(
        self,
        items: List[Dict[str, Any]]
    ) -> int:
        """批量插入向量"""
        if not items:
            return 0

        try:
            async with self.pool.connection() as conn:
                async with conn.cursor() as cur:
                    data = [
                        (
                            item['chunk_id'],
                            item['document_id'],
                            item.get('chunk_index', 0),
                            item['content'],
                            f"[{','.join(map(str, item['embedding']))}]",
                            item.get('metadata') or {}
                        )
                        for item in items
                    ]

                    await cur.executemany("""
                        INSERT INTO chunks (id, doc_id, chunk_index, content, embedding, metadata)
                        VALUES (%s, %s, %s, %s, %s::vector, %s)
                        ON CONFLICT (id) DO UPDATE
                        SET content = EXCLUDED.content,
                            embedding = EXCLUDED.embedding,
                            metadata = EXCLUDED.metadata
                    """, data)
                    await conn.commit()
                    return len(items)
        except Exception as e:
            logger.error(f"批量插入向量失败: {e}")
            return 0

    async def search(
        self,
        embedding: List[float],
        top_k: int = 5,
        document_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[ChunkResult]:
        """向量搜索"""
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    # 构建查询
                    if self.config.metric_type == MetricType.COSINE:
                        # 余弦相似度：1 - cosine_distance
                        operator = "<=>"
                    elif self.config.metric_type == MetricType.L2:
                        operator = "<->"
                    else:  # IP
                        operator = "#>"

                    where_conditions = []
                    params = [f"[{','.join(map(str, embedding))}]::vector", top_k]

                    if document_ids:
                        placeholders = ','.join(['%s'] * len(document_ids))
                        where_conditions.append(f"doc_id IN ({placeholders})")
                        params.extend(document_ids)

                    # TODO: 支持 filters 元数据过滤

                    where_clause = ""
                    if where_conditions:
                        where_clause = "WHERE " + " AND ".join(where_conditions)

                    query = f"""
                        SELECT
                            id as chunk_id,
                            doc_id as document_id,
                            content,
                            chunk_index,
                            metadata,
                            1 - (embedding {operator} %s) as score
                        FROM chunks
                        {where_clause}
                        ORDER BY embedding {operator} %s
                        LIMIT %s
                    """

                    await cur.execute(query, params)
                    rows = await cur.fetchall()

                    return [
                        ChunkResult(
                            chunk_id=row['chunk_id'],
                            document_id=row['document_id'],
                            content=row['content'],
                            score=float(row['score']),
                            metadata=row.get('metadata'),
                            chunk_index=row.get('chunk_index')
                        )
                        for row in rows
                    ]
        except Exception as e:
            logger.error(f"向量搜索失败: {e}")
            return []

    async def delete(self, chunk_id: str) -> bool:
        """删除单个向量"""
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("DELETE FROM chunks WHERE id = %s", (chunk_id,))
                    await conn.commit()
                    return True
        except Exception as e:
            logger.error(f"删除向量失败: {e}")
            return False

    async def delete_by_document(self, document_id: str) -> int:
        """删除文档的所有向量"""
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("DELETE FROM chunks WHERE doc_id = %s", (document_id,))
                    count = cur.rowcount
                    await conn.commit()
                    return count or 0
        except Exception as e:
            logger.error(f"删除文档向量失败: {e}")
            return 0

    async def count(self, document_id: Optional[str] = None) -> int:
        """统计向量数量"""
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor() as cur:
                    if document_id:
                        await cur.execute(
                            "SELECT COUNT(*) FROM chunks WHERE doc_id = %s",
                            (document_id,)
                        )
                    else:
                        await cur.execute("SELECT COUNT(*) FROM chunks")
                    result = await cur.fetchone()
                    return result[0] if result else 0
        except Exception as e:
            logger.error(f"统计向量数量失败: {e}")
            return 0

    async def get_info(self) -> Dict[str, Any]:
        """获取向量存储信息"""
        try:
            async with self.pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    # 总向量数
                    await cur.execute("SELECT COUNT(*) as total FROM chunks")
                    total = (await cur.fetchone())['total']

                    # 维度
                    await cur.execute("""
                        SELECT typmod as dim
                        FROM pg_attribute
                        WHERE attrelid = 'chunks'::regclass
                        AND attname = 'embedding'
                    """)
                    dim_row = await cur.fetchone()
                    dimension = dim_row['dim'] if dim_row else self.config.dimension

                    # 索引信息
                    await cur.execute("""
                        SELECT
                            indexname,
                            indexdef
                        FROM pg_indexes
                        WHERE tablename = 'chunks'
                        AND indexname LIKE '%embedding%'
                    """)
                    indexes = await cur.fetchall()

                    return {
                        'type': 'pgvector',
                        'total_vectors': total,
                        'dimension': dimension,
                        'metric_type': self.config.metric_type.value,
                        'index_type': self.config.index_type,
                        'indexes': [idx['indexname'] for idx in indexes],
                    }
        except Exception as e:
            logger.error(f"获取向量存储信息失败: {e}")
            return {'type': 'pgvector', 'error': str(e)}


# 向量搜索包装函数（兼容现有代码）
async def vector_search(
    pool: AsyncConnectionPool,
    embedding: List[float],
    kb_ids: Optional[List[str]] = None,
    top_k: int = 5,
    threshold: float = 0.3
) -> List[Dict[str, Any]]:
    """
    向量搜索（兼容旧接口）

    Args:
        pool: 数据库连接池
        embedding: 查询向量
        kb_ids: 知识库ID列表（通过文档ID关联）
        top_k: 返回数量
        threshold: 相似度阈值

    Returns:
        搜索结果列表
    """
    # 如果已初始化全局向量存储，使用它
    from vector_store import get_vector_store
    store = get_vector_store()
    if store:
        # 需要将 kb_ids 转换为 document_ids
        if kb_ids:
            # 查询知识库关联的文档ID
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    placeholders = ','.join(['%s'] * len(kb_ids))
                    await cur.execute(
                        f"SELECT id FROM documents WHERE kb_id IN ({placeholders})",
                        tuple(kb_ids)
                    )
                    doc_rows = await cur.fetchall()
                    document_ids = [row[0] for row in doc_rows] if doc_rows else []
            return [
                {
                    'chunk_id': r.chunk_id,
                    'doc_id': r.document_id,
                    'content': r.content,
                    'score': r.score,
                    'metadata': r.metadata,
                }
                for r in await store.search(embedding, top_k, document_ids)
                if r.score >= threshold
            ]
        else:
            results = await store.search(embedding, top_k)
            return [
                {
                    'chunk_id': r.chunk_id,
                    'doc_id': r.document_id,
                    'content': r.content,
                    'score': r.score,
                    'metadata': r.metadata,
                }
                for r in results
                if r.score >= threshold
            ]

    # 降级到直接使用 pgvector
    try:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                where_clause = ""
                params = [f"[{','.join(map(str, embedding))}]::vector", top_k]

                if kb_ids:
                    # 通过 documents 表关联
                    where_clause = "WHERE c.doc_id IN (SELECT id FROM documents WHERE kb_id = ANY(%s))"
                    params.insert(1, kb_ids)

                query = f"""
                    SELECT
                        c.id as chunk_id,
                        c.doc_id,
                        c.content,
                        1 - (c.embedding <=> %s) as score
                    FROM chunks c
                    {where_clause}
                    ORDER BY c.embedding <=> %s
                    LIMIT %s
                """

                await cur.execute(query, params)
                rows = await cur.fetchall()

                return [
                    {
                        'chunk_id': row['chunk_id'],
                        'doc_id': row['doc_id'],
                        'content': row['content'],
                        'score': float(row['score']),
                    }
                    for row in rows
                    if float(row['score']) >= threshold
                ]
    except Exception as e:
        logger.error(f"向量搜索失败: {e}")
        return []


async def vector_search_multi(
    pool: AsyncConnectionPool,
    embedding: List[float],
    kb_ids: List[str],
    top_k: int = 5,
    threshold: float = 0.3
) -> List[Dict[str, Any]]:
    """多知识库向量搜索"""
    return await vector_search(pool, embedding, kb_ids, top_k, threshold)
