# 聊天会话数据访问层
# 负责聊天会话相关的数据库操作

import uuid
import logging
from typing import Optional, List, Dict, Any
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


class ChatRepository:
    """聊天会话数据访问类"""

    def __init__(self, pool):
        """
        初始化聊天会话 Repository

        Args:
            pool: 数据库连接池
        """
        self.pool = pool

    # ==================== 会话管理 ====================

    async def create_session(
        self,
        app_id: str,
        user_id: Optional[str] = None,
        title: Optional[str] = None
    ) -> str:
        """
        创建新的聊天会话

        Args:
            app_id: 应用 ID
            user_id: 用户 ID（可选，用于匿名用户）
            title: 会话标题

        Returns:
            新创建会话的 ID
        """
        session_id = str(uuid.uuid4())

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO chat_sessions (id, application_id, user_id, title, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, NOW(), NOW())
                    RETURNING id
                """, (session_id, app_id, user_id, title))
                await conn.commit()

        return session_id

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        获取聊天会话详情

        Args:
            session_id: 会话 ID

        Returns:
            会话信息字典，不存在则返回 None
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT * FROM chat_sessions
                    WHERE id = %s
                """, (session_id,))
                return await cur.fetchone()

    async def list_sessions(
        self,
        app_id: str,
        user_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        获取聊天会话列表

        Args:
            app_id: 应用 ID
            user_id: 用户 ID（可选，用于筛选用户会话）
            page: 页码
            page_size: 每页数量

        Returns:
            包含 sessions, total, page, page_size 的字典
        """
        async with self.pool.connection() as conn:
            # 构建查询条件
            conditions = ["application_id = %s"]
            params = [app_id]

            if user_id:
                conditions.append("user_id = %s")
                params.append(user_id)

            where_clause = " AND ".join(conditions)

            # 获取总数
            async with conn.cursor(row_factory=dict_row) as cur_count:
                await cur_count.execute(f"""
                    SELECT COUNT(*) as total FROM chat_sessions WHERE {where_clause}
                """, params)
                total_result = await cur_count.fetchone()
                total = total_result['total'] if total_result else 0

            # 获取分页数据
            offset = (page - 1) * page_size

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(f"""
                    SELECT * FROM chat_sessions
                    WHERE {where_clause}
                    ORDER BY updated_at DESC
                    LIMIT {page_size} OFFSET {offset}
                """, params)
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

        return {
            "sessions": sessions,
            "total": total,
            "page": page,
            "page_size": page_size
        }

    async def update_session(self, session_id: str, update_data: Dict[str, Any]) -> bool:
        """
        更新会话信息

        Args:
            session_id: 会话 ID
            update_data: 要更新的字段字典

        Returns:
            是否更新成功
        """
        if not update_data:
            return False

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 检查会话是否存在
                await cur.execute("SELECT id FROM chat_sessions WHERE id = %s", (session_id,))
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
                    values.append(session_id)
                    await cur.execute(f"""
                        UPDATE chat_sessions SET {', '.join(updates)}
                        WHERE id = %s
                    """, values)
                    await conn.commit()

        return True

    async def update_session_title(self, session_id: str, title: str) -> bool:
        """
        更新会话标题

        Args:
            session_id: 会话 ID
            title: 新标题

        Returns:
            是否更新成功
        """
        return await self.update_session(session_id, {"title": title})

    async def delete_session(self, session_id: str) -> bool:
        """
        删除聊天会话

        Args:
            session_id: 会话 ID

        Returns:
            是否删除成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 删除会话（消息会通过外键级联删除）
                await cur.execute("DELETE FROM chat_sessions WHERE id = %s", (session_id,))
                await conn.commit()

        return True

    # ==================== 消息管理 ====================

    async def create_message(
        self,
        session_id: str,
        user_message: str,
        ai_response: str,
        message_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        创建新的聊天消息

        Args:
            session_id: 会话 ID
            user_message: 用户消息
            ai_response: AI 回复
            message_data: 额外的消息数据（如使用的知识库、引用的文档等）

        Returns:
            新创建消息的 ID
        """
        message_id = str(uuid.uuid4())

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO chat_messages (
                        id, session_id, user_message, ai_response,
                        metadata, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    RETURNING id
                """, (
                    message_id,
                    session_id,
                    user_message,
                    ai_response,
                    message_data or {}
                ))
                await conn.commit()

                # 更新会话的更新时间
                await cur.execute("""
                    UPDATE chat_sessions SET updated_at = NOW() WHERE id = %s
                """, (session_id,))
                await conn.commit()

        return message_id

    async def get_messages(
        self,
        session_id: str,
        page: int = 1,
        page_size: int = 50
    ) -> Dict[str, Any]:
        """
        获取会话的消息列表

        Args:
            session_id: 会话 ID
            page: 页码
            page_size: 每页数量

        Returns:
            包含 messages, total, page, page_size 的字典
        """
        async with self.pool.connection() as conn:
            # 获取总数
            async with conn.cursor(row_factory=dict_row) as cur_count:
                await cur_count.execute("""
                    SELECT COUNT(*) as total FROM chat_messages WHERE session_id = %s
                """, (session_id,))
                total_result = await cur_count.fetchone()
                total = total_result['total'] if total_result else 0

            # 获取分页数据（按时间正序排列）
            offset = (page - 1) * page_size

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT * FROM chat_messages
                    WHERE session_id = %s
                    ORDER BY created_at ASC
                    LIMIT %s OFFSET %s
                """, (session_id, page_size, offset))
                messages = await cur.fetchall()

        return {
            "messages": messages,
            "total": total,
            "page": page,
            "page_size": page_size
        }

    async def get_all_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """
        获取会话的所有消息（不分页）

        Args:
            session_id: 会话 ID

        Returns:
            消息列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT * FROM chat_messages
                    WHERE session_id = %s
                    ORDER BY created_at ASC
                """, (session_id,))
                return await cur.fetchall()

    async def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单条消息详情

        Args:
            message_id: 消息 ID

        Returns:
            消息信息字典，不存在则返回 None
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT * FROM chat_messages WHERE id = %s
                """, (message_id,))
                return await cur.fetchone()

    async def update_message(self, message_id: str, update_data: Dict[str, Any]) -> bool:
        """
        更新消息内容

        Args:
            message_id: 消息 ID
            update_data: 要更新的字段字典

        Returns:
            是否更新成功
        """
        if not update_data:
            return False

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 检查消息是否存在
                await cur.execute("SELECT id FROM chat_messages WHERE id = %s", (message_id,))
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
                    values.append(message_id)
                    await cur.execute(f"""
                        UPDATE chat_messages SET {', '.join(updates)}
                        WHERE id = %s
                    """, values)
                    await conn.commit()

        return True

    async def delete_message(self, message_id: str) -> bool:
        """
        删除单条消息

        Args:
            message_id: 消息 ID

        Returns:
            是否删除成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM chat_messages WHERE id = %s", (message_id,))
                await conn.commit()

        return True

    # ==================== 反馈管理 ====================

    async def add_feedback(
        self,
        message_id: str,
        application_id: str,
        feedback_type: str,
        comment: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> str:
        """
        添加消息反馈

        Args:
            message_id: 消息 ID
            application_id: 应用 ID
            feedback_type: 反馈类型（like/dislike）
            comment: 评论内容
            user_id: 用户 ID

        Returns:
            反馈记录 ID
        """
        feedback_id = str(uuid.uuid4())

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO message_feedback (
                        id, message_id, application_id, feedback_type, comment, user_id, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (message_id, user_id) DO UPDATE SET
                        feedback_type = EXCLUDED.feedback_type,
                        comment = EXCLUDED.comment,
                        created_at = NOW()
                    RETURNING id
                """, (feedback_id, message_id, application_id, feedback_type, comment, user_id))
                await conn.commit()

        return feedback_id

    async def get_feedbacks(
        self,
        application_id: str,
        feedback_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        获取应用的反馈列表

        Args:
            application_id: 应用 ID
            feedback_type: 反馈类型筛选
            page: 页码
            page_size: 每页数量

        Returns:
            包含 feedbacks, total, page, page_size 的字典
        """
        async with self.pool.connection() as conn:
            # 构建查询条件
            conditions = ["application_id = %s"]
            params = [application_id]

            if feedback_type:
                conditions.append("feedback_type = %s")
                params.append(feedback_type)

            where_clause = " AND ".join(conditions)

            # 获取总数
            async with conn.cursor(row_factory=dict_row) as cur_count:
                await cur_count.execute(f"""
                    SELECT COUNT(*) as total FROM message_feedback WHERE {where_clause}
                """, params)
                total_result = await cur_count.fetchone()
                total = total_result['total'] if total_result else 0

            # 获取分页数据
            offset = (page - 1) * page_size

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(f"""
                    SELECT * FROM message_feedback
                    WHERE {where_clause}
                    ORDER BY created_at DESC
                    LIMIT {page_size} OFFSET {offset}
                """, params)
                feedbacks = await cur.fetchall()

        return {
            "feedbacks": feedbacks,
            "total": total,
            "page": page,
            "page_size": page_size
        }

    async def get_feedback_statistics(self, application_id: str) -> Dict[str, Any]:
        """
        获取应用的反馈统计

        Args:
            application_id: 应用 ID

        Returns:
            反馈统计字典
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT
                        COUNT(*) as total_feedback,
                        SUM(CASE WHEN feedback_type = 'like' THEN 1 ELSE 0 END) as likes,
                        SUM(CASE WHEN feedback_type = 'dislike' THEN 1 ELSE 0 END) as dislikes
                    FROM message_feedback
                    WHERE application_id = %s
                """, (application_id,))
                result = await cur.fetchone()

                return {
                    "total": result['total_feedback'] if result else 0,
                    "likes": result['likes'] if result else 0,
                    "dislikes": result['dislikes'] if result else 0
                }

    # ==================== 会话统计 ====================

    async def count_sessions(self, application_id: str) -> int:
        """
        统计应用的会话数量

        Args:
            application_id: 应用 ID

        Returns:
            会话数量
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT COUNT(*) FROM chat_sessions WHERE application_id = %s
                """, (application_id,))
                result = await cur.fetchone()
                return result[0] if result else 0

    async def count_messages(self, session_id: Optional[str] = None) -> int:
        """
        统计消息数量

        Args:
            session_id: 会话 ID（可选，不指定则统计所有消息）

        Returns:
            消息数量
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                if session_id:
                    await cur.execute("""
                        SELECT COUNT(*) FROM chat_messages WHERE session_id = %s
                    """, (session_id,))
                else:
                    await cur.execute("SELECT COUNT(*) FROM chat_messages")
                result = await cur.fetchone()
                return result[0] if result else 0

    async def get_user_sessions(
        self,
        user_id: str,
        app_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        获取用户的会话列表

        Args:
            user_id: 用户 ID
            app_id: 应用 ID（可选）
            page: 页码
            page_size: 每页数量

        Returns:
            包含 sessions, total, page, page_size 的字典
        """
        async with self.pool.connection() as conn:
            # 构建查询条件
            conditions = ["user_id = %s"]
            params = [user_id]

            if app_id:
                conditions.append("application_id = %s")
                params.append(app_id)

            where_clause = " AND ".join(conditions)

            # 获取总数
            async with conn.cursor(row_factory=dict_row) as cur_count:
                await cur_count.execute(f"""
                    SELECT COUNT(*) as total FROM chat_sessions WHERE {where_clause}
                """, params)
                total_result = await cur_count.fetchone()
                total = total_result['total'] if total_result else 0

            # 获取分页数据
            offset = (page - 1) * page_size

            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(f"""
                    SELECT * FROM chat_sessions
                    WHERE {where_clause}
                    ORDER BY updated_at DESC
                    LIMIT {page_size} OFFSET {offset}
                """, params)
                sessions = await cur.fetchall()

        return {
            "sessions": sessions,
            "total": total,
            "page": page,
            "page_size": page_size
        }
