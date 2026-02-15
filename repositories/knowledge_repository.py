# 知识库数据访问层
# 负责知识库相关的数据库操作

import uuid
import logging
from typing import Optional, List, Dict, Any
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


class KnowledgeRepository:
    """知识库数据访问类"""

    def __init__(self, pool):
        """
        初始化知识库 Repository

        Args:
            pool: 数据库连接池
        """
        self.pool = pool

    async def find_by_id(self, kb_id: str) -> Optional[Dict[str, Any]]:
        """
        根据 ID 查找知识库

        Args:
            kb_id: 知识库 ID

        Returns:
            知识库信息字典，不存在则返回 None
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT kb.*,
                           u.username as owner_name,
                           u.avatar as owner_avatar
                    FROM knowledge_bases kb
                    LEFT JOIN users u ON kb.owner_id = u.id
                    WHERE kb.id = %s
                """, (kb_id,))
                return await cur.fetchone()

    async def list_knowledge_bases(
        self,
        owner_id: Optional[str] = None,
        is_public: Optional[bool] = None,
        include_doc_count: bool = True
    ) -> List[Dict[str, Any]]:
        """
        获取知识库列表

        Args:
            owner_id: 所有者 ID 筛选
            is_public: 是否公开筛选
            include_doc_count: 是否包含文档数量统计

        Returns:
            知识库列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 构建查询条件
                conditions = []
                params = []

                if owner_id:
                    conditions.append("kb.owner_id = %s")
                    params.append(owner_id)
                if is_public is not None:
                    conditions.append("kb.is_public = %s")
                    params.append(is_public)

                where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

                if include_doc_count:
                    query = f"""
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
                               COALESCE(doc_counts.token_count, 0) as token_count
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
                        {where_clause}
                        ORDER BY kb.created_at DESC
                    """
                else:
                    query = f"""
                        SELECT kb.*,
                               u.username as owner_name,
                               u.avatar as owner_avatar
                        FROM knowledge_bases kb
                        LEFT JOIN users u ON kb.owner_id = u.id
                        {where_clause}
                        ORDER BY kb.created_at DESC
                    """

                await cur.execute(query, params)
                return await cur.fetchall()

    async def create_knowledge_base(self, kb_data: Dict[str, Any]) -> str:
        """
        创建新知识库

        Args:
            kb_data: 知识库数据字典

        Returns:
            新创建知识库的 ID
        """
        kb_id = kb_data.get('id') or str(uuid.uuid4())

        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    INSERT INTO knowledge_bases (
                        id, name, description, embedding_model, chunk_size, chunk_overlap,
                        owner_id, is_public, allow_public_upload, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    RETURNING *
                """, (
                    kb_id,
                    kb_data['name'],
                    kb_data.get('description'),
                    kb_data.get('embedding_model', 'nomic-embed-text'),
                    kb_data.get('chunk_size', 512),
                    kb_data.get('chunk_overlap', 50),
                    kb_data.get('owner_id'),
                    kb_data.get('is_public', False),
                    kb_data.get('allow_public_upload', False)
                ))
                await conn.commit()

        return kb_id

    async def update_knowledge_base(self, kb_id: str, update_data: Dict[str, Any]) -> bool:
        """
        更新知识库信息

        Args:
            kb_id: 知识库 ID
            update_data: 要更新的字段字典

        Returns:
            是否更新成功
        """
        if not update_data:
            return False

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 检查知识库是否存在
                await cur.execute("SELECT id FROM knowledge_bases WHERE id = %s", (kb_id,))
                if not await cur.fetchone():
                    return False

                # 构建更新语句
                updates = []
                values = []
                for key, value in update_data.items():
                    if key not in ['id', 'created_at']:
                        updates.append(f"{key} = %s")
                        values.append(value)

                if updates:
                    updates.append("updated_at = NOW()")
                    values.append(kb_id)
                    await cur.execute(f"""
                        UPDATE knowledge_bases SET {', '.join(updates)}
                        WHERE id = %s
                    """, values)
                    await conn.commit()

        return True

    async def delete_knowledge_base(self, kb_id: str) -> bool:
        """
        删除知识库

        Args:
            kb_id: 知识库 ID

        Returns:
            是否删除成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM knowledge_bases WHERE id = %s", (kb_id,))
                await conn.commit()

        return True

    async def count_documents(self, kb_id: str, status: Optional[str] = None) -> int:
        """
        统计知识库的文档数量

        Args:
            kb_id: 知识库 ID
            status: 文档状态筛选

        Returns:
            文档数量
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                if status:
                    await cur.execute("""
                        SELECT COUNT(*) FROM documents WHERE kb_id = %s AND status = %s
                    """, (kb_id, status))
                else:
                    await cur.execute("""
                        SELECT COUNT(*) FROM documents WHERE kb_id = %s
                    """, (kb_id,))
                result = await cur.fetchone()
                return result[0] if result else 0

    async def get_documents(
        self,
        kb_id: str,
        page: int = 1,
        page_size: int = 20,
        status: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        获取知识库的文档列表

        Args:
            kb_id: 知识库 ID
            page: 页码
            page_size: 每页数量
            status: 文档状态筛选

        Returns:
            包含 documents, total, page, page_size 的字典
        """
        async with self.pool.connection() as conn:
            # 构建查询条件
            conditions = ["kb_id = %s"]
            params = [kb_id]

            if status:
                conditions.append("status = %s")
                params.append(status)

            where_clause = " AND ".join(conditions)

            # 获取总数
            async with conn.cursor(row_factory=dict_row) as cur_count:
                await cur_count.execute(f"""
                    SELECT COUNT(*) as total FROM documents WHERE {where_clause}
                """, params)
                total_result = await cur_count.fetchone()
                total = total_result['total'] if total_result else 0

            # 获取分页数据
            offset = (page - 1) * page_size

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(f"""
                    SELECT * FROM documents
                    WHERE {where_clause}
                    ORDER BY created_at DESC
                    LIMIT {page_size} OFFSET {offset}
                """, params)
                documents = await cur.fetchall()

        return {
            "documents": documents,
            "total": total,
            "page": page,
            "page_size": page_size
        }

    async def check_name_exists(self, name: str, owner_id: str, exclude_id: Optional[str] = None) -> bool:
        """
        检查知识库名称是否已存在（同一用户下）

        Args:
            name: 知识库名称
            owner_id: 所有者 ID
            exclude_id: 排除的知识库 ID（用于更新时检查）

        Returns:
            知识库名称是否已存在
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                if exclude_id:
                    await cur.execute("""
                        SELECT id FROM knowledge_bases WHERE name = %s AND owner_id = %s AND id != %s
                    """, (name, owner_id, exclude_id))
                else:
                    await cur.execute("""
                        SELECT id FROM knowledge_bases WHERE name = %s AND owner_id = %s
                    """, (name, owner_id))
                return await cur.fetchone() is not None

    async def update_public_status(self, kb_id: str, is_public: bool) -> bool:
        """
        更新知识库的公开状态

        Args:
            kb_id: 知识库 ID
            is_public: 是否公开

        Returns:
            是否更新成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE knowledge_bases SET is_public = %s, updated_at = NOW()
                    WHERE id = %s
                """, (is_public, kb_id))
                await conn.commit()

        return True

    async def get_accessible_knowledge_bases(self, user_id: str, user_role: str = 'member') -> List[Dict[str, Any]]:
        """
        获取用户可访问的知识库列表

        Args:
            user_id: 用户 ID
            user_role: 用户角色

        Returns:
            可访问的知识库列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 超级管理员可以查看所有知识库
                # 普通用户只能查看自己的知识库或公开的知识库
                if user_role == 'super_admin':
                    where_clause = ""
                    params = ()
                else:
                    where_clause = "WHERE kb.owner_id = %s OR kb.is_public = true"
                    params = (user_id,)

                await cur.execute(f"""
                    SELECT kb.id,
                           kb.name,
                           kb.description,
                           kb.embedding_model,
                           kb.owner_id,
                           kb.is_public,
                           u.username as owner_name,
                           u.avatar as owner_avatar,
                           kb.created_at,
                           kb.updated_at,
                           COALESCE(doc_counts.doc_count, 0) as doc_count
                    FROM knowledge_bases kb
                    LEFT JOIN users u ON kb.owner_id = u.id
                    LEFT JOIN (
                        SELECT kb_id, COUNT(*) as doc_count
                        FROM documents
                        WHERE status = 'completed'
                        GROUP BY kb_id
                    ) doc_counts ON kb.id = doc_counts.kb_id
                    {where_clause}
                    ORDER BY kb.created_at DESC
                """, params)
                return await cur.fetchall()

    async def get_statistics(self, kb_id: str) -> Dict[str, Any]:
        """
        获取知识库的统计信息

        Args:
            kb_id: 知识库 ID

        Returns:
            统计信息字典
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 文档统计
                await cur.execute("""
                    SELECT
                        COUNT(*) as total_documents,
                        SUM(chunk_count) as total_chunks,
                        SUM(token_count) as total_tokens
                    FROM documents
                    WHERE kb_id = %s AND status = 'completed'
                """, (kb_id,))
                doc_stats = await cur.fetchone()

                # 待处理文档数
                await cur.execute("""
                    SELECT COUNT(*) as pending_count
                    FROM documents
                    WHERE kb_id = %s AND status != 'completed'
                """, (kb_id,))
                pending_stats = await cur.fetchone()

                return {
                    "total_documents": doc_stats['total_documents'] if doc_stats else 0,
                    "total_chunks": doc_stats['total_chunks'] if doc_stats else 0,
                    "total_tokens": doc_stats['total_tokens'] if doc_stats else 0,
                    "pending_documents": pending_stats['pending_count'] if pending_stats else 0
                }

    async def add_document(self, doc_data: Dict[str, Any]) -> str:
        """
        添加文档到知识库

        Args:
            doc_data: 文档数据字典

        Returns:
            新创建文档的 ID
        """
        doc_id = doc_data.get('id') or str(uuid.uuid4())

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO documents (
                        id, kb_id, filename, content, status, chunk_count, token_count, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    doc_id,
                    doc_data['kb_id'],
                    doc_data.get('filename'),
                    doc_data.get('content'),
                    doc_data.get('status', 'pending'),
                    doc_data.get('chunk_count', 0),
                    doc_data.get('token_count', 0)
                ))
                await conn.commit()

        return doc_id

    async def delete_document(self, doc_id: str) -> bool:
        """
        删除文档

        Args:
            doc_id: 文档 ID

        Returns:
            是否删除成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
                await conn.commit()

        return True
