"""
父子分块索引模块
参考：Dify, FastGPT 的父子索引实现

功能：
1. 父块保存完整上下文
2. 子块用于向量检索
3. 检索时返回父块内容
4. 提升检索的上下文完整性
"""

import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ParentChunk:
    """父块：保存完整上下文"""
    id: str
    doc_id: str
    content: str
    chunk_index: int
    metadata: Dict[str, Any]
    child_ids: List[str] = None

    def __post_init__(self):
        if self.child_ids is None:
            self.child_ids = []


@dataclass
class ChildChunk:
    """子块：用于向量检索"""
    id: str
    parent_id: str
    content: str
    chunk_index: int
    metadata: Dict[str, Any]


class ParentChildIndexer:
    """
    父子分块索引器

    策略：
    1. 将文档切分成较大的父块（如500-1000字）
    2. 每个父块再切分成多个子块（如100-200字）
    3. 只对子块进行向量化
    4. 检索时返回父块，保留完整上下文
    """

    def __init__(
        self,
        parent_chunk_size: int = 800,
        parent_overlap: int = 100,
        child_chunk_size: int = 200,
        child_overlap: int = 50
    ):
        """
        初始化索引器

        Args:
            parent_chunk_size: 父块大小
            parent_overlap: 父块重叠
            child_chunk_size: 子块大小
            child_overlap: 子块重叠
        """
        self.parent_chunk_size = parent_chunk_size
        self.parent_overlap = parent_overlap
        self.child_chunk_size = child_chunk_size
        self.child_overlap = child_overlap

    def split_text(self, text: str, chunk_size: int, overlap: int) -> List[str]:
        """
        切分文本

        Args:
            text: 输入文本
            chunk_size: 块大小
            overlap: 重叠大小

        Returns:
            切分后的文本列表
        """
        chunks = []
        start = 0
        text_length = len(text)

        while start < text_length:
            end = start + chunk_size
            chunk = text[start:end]

            # 尝试在句子边界切分
            if end < text_length:
                # 寻找最后一个句号、问号或感叹号
                last_punct = max(
                    chunk.rfind('。'),
                    chunk.rfind('？'),
                    chunk.rfind('！'),
                    chunk.rfind('.'),
                    chunk.rfind('?'),
                    chunk.rfind('!')
                )
                if last_punct > chunk_size // 2:  # 至少保留一半内容
                    chunk = text[start:start + last_punct + 1]

            chunks.append(chunk)
            start = end - overlap

        return chunks

    def create_parent_child_chunks(
        self,
        doc_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> tuple[List[ParentChunk], List[ChildChunk]]:
        """
        创建父子分块

        Args:
            doc_id: 文档ID
            content: 文档内容
            metadata: 元数据

        Returns:
            (父块列表, 子块列表)
        """
        if metadata is None:
            metadata = {}

        # 1. 创建父块
        parent_chunks_data = self.split_text(
            content,
            self.parent_chunk_size,
            self.parent_overlap
        )

        parent_chunks = []
        for i, p_content in enumerate(parent_chunks_data):
            parent = ParentChunk(
                id=f"{doc_id}_parent_{i}",
                doc_id=doc_id,
                content=p_content,
                chunk_index=i,
                metadata={**metadata, "chunk_type": "parent"}
            )
            parent_chunks.append(parent)

        # 2. 为每个父块创建子块
        child_chunks = []
        for parent in parent_chunks:
            child_chunks_data = self.split_text(
                parent.content,
                self.child_chunk_size,
                self.child_overlap
            )

            for j, c_content in enumerate(child_chunks_data):
                child = ChildChunk(
                    id=f"{parent.id}_child_{j}",
                    parent_id=parent.id,
                    content=c_content,
                    chunk_index=j,
                    metadata={**metadata, "chunk_type": "child"}
                )
                child_chunks.append(child)

            # 关联子块ID到父块
            parent.child_ids = [c.id for c in child_chunks]

        logger.info(f"Created {len(parent_chunks)} parent chunks "
                    f"and {len(child_chunks)} child chunks for doc {doc_id}")

        return parent_chunks, child_chunks

    async def save_to_db(
        self,
        pool,
        parent_chunks: List[ParentChunk],
        child_chunks: List[ChildChunk]
    ) -> bool:
        """
        保存到数据库

        需要的表结构：
        - parent_chunks (id, doc_id, content, chunk_index, metadata)
        - child_chunks (id, parent_id, content, chunk_index, metadata)

        Args:
            pool: 数据库连接池
            parent_chunks: 父块列表
            child_chunks: 子块列表

        Returns:
            是否成功
        """
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    # 创建表（如果不存在）
                    await cur.execute("""
                        CREATE TABLE IF NOT EXISTS parent_chunks (
                            id TEXT PRIMARY KEY,
                            doc_id TEXT NOT NULL,
                            content TEXT NOT NULL,
                            chunk_index INTEGER NOT NULL,
                            metadata JSONB,
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        );

                        CREATE TABLE IF NOT EXISTS child_chunks (
                            id TEXT PRIMARY KEY,
                            parent_id TEXT NOT NULL REFERENCES parent_chunks(id) ON DELETE CASCADE,
                            content TEXT NOT NULL,
                            chunk_index INTEGER NOT NULL,
                            metadata JSONB,
                            embedding vector(768),
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        );

                        CREATE INDEX IF NOT EXISTS idx_child_chunks_parent_id
                        ON child_chunks(parent_id);
                    """)

                    # 插入父块
                    for parent in parent_chunks:
                        await cur.execute("""
                            INSERT INTO parent_chunks (id, doc_id, content, chunk_index, metadata)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                        """, (parent.id, parent.doc_id, parent.content,
                             parent.chunk_index, parent.metadata))

                    # 插入子块（embedding稍后生成）
                    for child in child_chunks:
                        await cur.execute("""
                            INSERT INTO child_chunks (id, parent_id, content, chunk_index, metadata)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING
                        """, (child.id, child.parent_id, child.content,
                             child.chunk_index, child.metadata))

                    await conn.commit()

            logger.info(f"Saved {len(parent_chunks)} parent chunks and "
                       f"{len(child_chunks)} child chunks to database")
            return True

        except Exception as e:
            logger.error(f"Error saving parent-child chunks: {e}")
            return False

    async def retrieve(
        self,
        pool,
        query_embedding: List[float],
        kb_ids: List[str],
        top_k: int = 3,
        threshold: float = 0.3
    ) -> List[Dict[str, Any]]:
        """
        使用父子索引检索

        1. 在子块中搜索
        2. 返回对应的父块内容（完整上下文）

        Args:
            pool: 数据库连接池
            query_embedding: 查询向量
            kb_ids: 知识库ID列表
            top_k: 返回结果数
            threshold: 相似度阈值

        Returns:
            检索结果列表（包含父块内容）
        """
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT
                        p.id as parent_id,
                        p.doc_id,
                        d.name as doc_name,
                        d.kb_id,
                        kb.name as kb_name,
                        p.content,
                        1 - (c.embedding <=> %s::vector) as similarity
                    FROM child_chunks c
                    JOIN parent_chunks p ON p.id = c.parent_id
                    JOIN documents d ON d.id = p.doc_id
                    JOIN knowledge_bases kb ON kb.id = d.kb_id
                    WHERE d.kb_id = ANY(%s)
                      AND c.embedding IS NOT NULL
                    ORDER BY c.embedding <=> %s::vector
                    LIMIT %s
                """, (query_embedding, kb_ids, query_embedding, top_k))

                results = await cur.fetchall()

                # 过滤低于阈值的结果
                if threshold > 0:
                    results = [r for r in results if r['similarity'] >= threshold]

                logger.info(f"Parent-child retrieval: {len(results)} results")

                return results


# 全局索引器实例
parent_child_indexer = ParentChildIndexer()
