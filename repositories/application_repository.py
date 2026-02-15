# 应用数据访问层
# 负责应用相关的数据库操作

import uuid
import secrets
import logging
from typing import Optional, List, Dict, Any
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


class ApplicationRepository:
    """应用数据访问类"""

    def __init__(self, pool):
        """
        初始化应用 Repository

        Args:
            pool: 数据库连接池
        """
        self.pool = pool

    async def find_by_id(self, app_id: str) -> Optional[Dict[str, Any]]:
        """
        根据 ID 查找应用

        Args:
            app_id: 应用 ID

        Returns:
            应用信息字典，不存在则返回 None
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT a.*,
                           u.username as owner_name,
                           u.avatar as owner_avatar
                    FROM applications a
                    LEFT JOIN users u ON a.owner_id = u.id
                    WHERE a.id = %s
                """, (app_id,))
                return await cur.fetchone()

    async def find_by_share_id(self, share_id: str) -> Optional[Dict[str, Any]]:
        """
        根据分享 ID 查找应用

        Args:
            share_id: 分享 ID

        Returns:
            应用信息字典，不存在则返回 None
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT a.*,
                           u.username as owner_name,
                           u.avatar as owner_avatar
                    FROM applications a
                    LEFT JOIN users u ON a.owner_id = u.id
                    WHERE a.share_id = %s
                """, (share_id,))
                return await cur.fetchone()

    async def list_applications(
        self,
        owner_id: Optional[str] = None,
        is_public: Optional[bool] = None,
        include_statistics: bool = True
    ) -> List[Dict[str, Any]]:
        """
        获取应用列表

        Args:
            owner_id: 所有者 ID 筛选
            is_public: 是否公开筛选
            include_statistics: 是否包含统计信息

        Returns:
            应用列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 构建查询条件
                conditions = []
                params = []

                if owner_id:
                    conditions.append("a.owner_id = %s")
                    params.append(owner_id)
                if is_public is not None:
                    conditions.append("a.is_public = %s")
                    params.append(is_public)

                where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

                if include_statistics:
                    query = f"""
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
                    """
                else:
                    query = f"""
                        SELECT a.*,
                               u.username as owner_name,
                               u.avatar as owner_avatar
                        FROM applications a
                        LEFT JOIN users u ON a.owner_id = u.id
                        {where_clause}
                        ORDER BY a.created_at DESC
                    """

                await cur.execute(query, params)
                return await cur.fetchall()

    async def create_application(self, app_data: Dict[str, Any]) -> str:
        """
        创建新应用

        Args:
            app_data: 应用数据字典

        Returns:
            新创建应用的 ID 和分享 ID
        """
        app_id = app_data.get('id') or str(uuid.uuid4())
        share_id = app_data.get('share_id') or secrets.token_urlsafe(8)

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO applications (
                        id, name, description, model, knowledge_base_ids, is_public, owner_id,
                        system_prompt, welcome_message, share_password, share_id,
                        temperature, max_tokens, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    RETURNING id
                """, (
                    app_id,
                    app_data['name'],
                    app_data.get('description'),
                    app_data.get('model', 'llama3.1'),
                    app_data.get('knowledge_base_ids', []),
                    app_data.get('is_public', False),
                    app_data.get('owner_id'),
                    app_data.get('system_prompt'),
                    app_data.get('welcome_message'),
                    app_data.get('share_password'),
                    share_id,
                    app_data.get('temperature', 0.7),
                    app_data.get('max_tokens', 2048)
                ))
                await conn.commit()

        return app_id

    async def update_application(self, app_id: str, update_data: Dict[str, Any]) -> bool:
        """
        更新应用信息

        Args:
            app_id: 应用 ID
            update_data: 要更新的字段字典

        Returns:
            是否更新成功
        """
        if not update_data:
            return False

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 检查应用是否存在
                await cur.execute("SELECT id FROM applications WHERE id = %s", (app_id,))
                if not await cur.fetchone():
                    return False

                # 构建更新语句
                updates = []
                values = []
                for key, value in update_data.items():
                    if key not in ['id', 'created_at', 'share_id']:
                        updates.append(f"{key} = %s")
                        values.append(value)

                if updates:
                    updates.append("updated_at = NOW()")
                    values.append(app_id)
                    await cur.execute(f"""
                        UPDATE applications SET {', '.join(updates)}
                        WHERE id = %s
                    """, values)
                    await conn.commit()

        return True

    async def delete_application(self, app_id: str) -> bool:
        """
        删除应用

        Args:
            app_id: 应用 ID

        Returns:
            是否删除成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM applications WHERE id = %s", (app_id,))
                await conn.commit()

        return True

    async def get_sessions(
        self,
        app_id: str,
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        获取应用的会话列表

        Args:
            app_id: 应用 ID
            page: 页码
            page_size: 每页数量

        Returns:
            包含 sessions, total, page, page_size 的字典
        """
        async with self.pool.connection() as conn:
            # 获取总数
            async with conn.cursor(row_factory=dict_row) as cur_count:
                await cur_count.execute("""
                    SELECT COUNT(*) as total FROM conversations WHERE app_id = %s
                """, (app_id,))
                total_result = await cur_count.fetchone()
                total = total_result['total'] if total_result else 0

            # 获取分页数据
            offset = (page - 1) * page_size

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT c.*,
                           COUNT(m.id) as message_count
                    FROM conversations c
                    LEFT JOIN messages m ON c.id = m.conversation_id
                    WHERE c.app_id = %s
                    GROUP BY c.id
                    ORDER BY c.updated_at DESC
                    LIMIT %s OFFSET %s
                """, (app_id, page_size, offset))
                sessions = await cur.fetchall()

        return {
            "sessions": sessions,
            "total": total,
            "page": page,
            "page_size": page_size
        }

    async def get_analytics(self, app_id: str) -> Dict[str, Any]:
        """
        获取应用的统计数据

        Args:
            app_id: 应用 ID

        Returns:
            统计数据字典
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 会话统计
                await cur.execute("""
                    SELECT
                        COUNT(*) as total_conversations,
                        COUNT(CASE WHEN created_at > NOW() - INTERVAL '7 days' THEN 1 END) as recent_conversations
                    FROM conversations
                    WHERE app_id = %s
                """, (app_id,))
                conv_stats = await cur.fetchone()

                # 消息统计
                await cur.execute("""
                    SELECT
                        COUNT(*) as total_messages,
                        COUNT(CASE WHEN m.created_at > NOW() - INTERVAL '7 days' THEN 1 END) as recent_messages
                    FROM messages m
                    JOIN conversations c ON m.conversation_id = c.id
                    WHERE c.app_id = %s
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
            }
        }

    async def update_share_id(self, app_id: str) -> str:
        """
        重新生成分享 ID

        Args:
            app_id: 应用 ID

        Returns:
            新的分享 ID
        """
        new_share_id = secrets.token_urlsafe(8)

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE applications SET share_id = %s, updated_at = NOW()
                    WHERE id = %s
                """, (new_share_id, app_id))
                await conn.commit()

        return new_share_id

    async def check_name_exists(self, name: str, owner_id: str, exclude_id: Optional[str] = None) -> bool:
        """
        检查应用名称是否已存在（同一用户下）

        Args:
            name: 应用名称
            owner_id: 所有者 ID
            exclude_id: 排除的应用 ID（用于更新时检查）

        Returns:
            应用名称是否已存在
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                if exclude_id:
                    await cur.execute("""
                        SELECT id FROM applications WHERE name = %s AND owner_id = %s AND id != %s
                    """, (name, owner_id, exclude_id))
                else:
                    await cur.execute("""
                        SELECT id FROM applications WHERE name = %s AND owner_id = %s
                    """, (name, owner_id))
                return await cur.fetchone() is not None

    async def get_accessible_applications(self, user_id: str, user_role: str = 'user') -> List[Dict[str, Any]]:
        """
        获取用户可访问的应用列表

        Args:
            user_id: 用户 ID
            user_role: 用户角色

        Returns:
            可访问的应用列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 超级管理员可以查看所有应用
                # 普通用户只能查看自己的应用或公开的应用
                if user_role == 'super_admin':
                    where_clause = ""
                    params = ()
                else:
                    where_clause = "WHERE a.owner_id = %s OR a.is_public = true"
                    params = (user_id,)

                await cur.execute(f"""
                    SELECT a.*,
                           u.username as owner_name,
                           u.avatar as owner_avatar
                    FROM applications a
                    LEFT JOIN users u ON a.owner_id = u.id
                    {where_clause}
                    ORDER BY a.created_at DESC
                """, params)
                return await cur.fetchall()

    async def link_knowledge_bases(self, app_id: str, kb_ids: List[str]) -> bool:
        """
        关联知识库到应用

        Args:
            app_id: 应用 ID
            kb_ids: 知识库 ID 列表

        Returns:
            是否关联成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE applications SET knowledge_base_ids = %s, updated_at = NOW()
                    WHERE id = %s
                """, (kb_ids, app_id))
                await conn.commit()

        return True

    async def get_knowledge_bases(self, app_id: str) -> List[Dict[str, Any]]:
        """
        获取应用关联的知识库列表

        Args:
            app_id: 应用 ID

        Returns:
            知识库列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT a.knowledge_base_ids
                    FROM applications a
                    WHERE a.id = %s
                """, (app_id,))
                app = await cur.fetchone()

                if not app or not app['knowledge_base_ids']:
                    return []

                kb_ids = app['knowledge_base_ids']
                if not kb_ids:
                    return []

                # 获取知识库详情
                placeholders = ', '.join(['%s'] * len(kb_ids))
                await cur.execute(f"""
                    SELECT kb.id, kb.name, kb.description, kb.is_public
                    FROM knowledge_bases kb
                    WHERE kb.id IN ({placeholders})
                """, kb_ids)
                return await cur.fetchall()

    async def verify_password(self, app_id: str, password: str) -> bool:
        """
        验证应用的访问密码

        Args:
            app_id: 应用 ID
            password: 密码

        Returns:
            密码是否正确
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT share_password FROM applications WHERE id = %s
                """, (app_id,))
                app = await cur.fetchone()

                if not app:
                    return False

                # 没有设置密码则允许访问
                if not app['share_password']:
                    return True

                return app['share_password'] == password
