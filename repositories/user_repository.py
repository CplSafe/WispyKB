# 用户数据访问层
# 负责用户相关的数据库操作

import uuid
import logging
from typing import Optional, List, Dict, Any
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


class UserRepository:
    """用户数据访问类"""

    def __init__(self, pool):
        """
        初始化用户 Repository

        Args:
            pool: 数据库连接池
        """
        self.pool = pool

    async def find_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        根据 ID 查找用户

        Args:
            user_id: 用户 ID

        Returns:
            用户信息字典，不存在则返回 None
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT u.*,
                           d.name as dept_name
                    FROM users u
                    LEFT JOIN departments d ON u.dept_id = d.id
                    WHERE u.id = %s AND u.deleted_at IS NULL
                """, (user_id,))
                return await cur.fetchone()

    async def find_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """
        根据用户名查找用户

        Args:
            username: 用户名

        Returns:
            用户信息字典，不存在则返回 None
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT * FROM users
                    WHERE username = %s AND deleted_at IS NULL
                """, (username,))
                return await cur.fetchone()

    async def find_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """
        根据邮箱查找用户

        Args:
            email: 邮箱地址

        Returns:
            用户信息字典，不存在则返回 None
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT * FROM users
                    WHERE email = %s AND deleted_at IS NULL
                """, (email,))
                return await cur.fetchone()

    async def create(self, user_data: Dict[str, Any]) -> str:
        """
        创建新用户

        Args:
            user_data: 用户数据字典，包含 username, password_hash, email, nickname, mobile, avatar, dept_id, status 等

        Returns:
            新创建用户的 ID
        """
        user_id = user_data.get('id') or str(uuid.uuid4())

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO users (id, username, password_hash, email, nickname, mobile, avatar, dept_id, status, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (
                    user_id,
                    user_data['username'],
                    user_data['password_hash'],
                    user_data.get('email'),
                    user_data.get('nickname'),
                    user_data.get('mobile'),
                    user_data.get('avatar'),
                    user_data.get('dept_id'),
                    user_data.get('status', 0)
                ))
                await conn.commit()

        return user_id

    async def update(self, user_id: str, update_data: Dict[str, Any]) -> bool:
        """
        更新用户信息

        Args:
            user_id: 用户 ID
            update_data: 要更新的字段字典

        Returns:
            是否更新成功
        """
        if not update_data:
            return False

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 检查用户是否存在
                await cur.execute("SELECT id FROM users WHERE id = %s AND deleted_at IS NULL", (user_id,))
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
                    values.append(user_id)
                    await cur.execute(f"""
                        UPDATE users SET {', '.join(updates)}
                        WHERE id = %s
                    """, values)
                    await conn.commit()

        return True

    async def delete(self, user_id: str) -> bool:
        """
        软删除用户

        Args:
            user_id: 用户 ID

        Returns:
            是否删除成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE users SET deleted_at = NOW()
                    WHERE id = %s
                """, (user_id,))
                await conn.commit()

        return True

    async def list_users(
        self,
        page: int = 1,
        page_size: int = 20,
        username: Optional[str] = None,
        status: Optional[int] = None,
        dept_id: Optional[str] = None,
        include_roles: bool = True
    ) -> Dict[str, Any]:
        """
        获取用户列表（分页）

        Args:
            page: 页码
            page_size: 每页数量
            username: 用户名模糊搜索
            status: 状态筛选
            dept_id: 部门 ID 筛选
            include_roles: 是否包含角色信息

        Returns:
            包含 users, total, page, page_size 的字典
        """
        async with self.pool.connection() as conn:
            # 构建查询条件
            conditions = ["u.deleted_at IS NULL"]
            params = []

            if username:
                conditions.append("u.username LIKE %s")
                params.append(f"%{username}%")
            if status is not None:
                conditions.append("u.status = %s")
                params.append(status)
            if dept_id:
                conditions.append("u.dept_id = %s")
                params.append(dept_id)

            where_clause = " AND ".join(conditions)

            # 获取总数
            async with conn.cursor(row_factory=dict_row) as cur_count:
                await cur_count.execute(f"""
                    SELECT COUNT(*) as total
                    FROM users u
                    WHERE {where_clause}
                """, params)
                total_result = await cur_count.fetchone()
                total = total_result['total'] if total_result else 0

            # 获取分页数据
            offset = (page - 1) * page_size

            async with conn.cursor(row_factory=dict_row) as cur:
                if include_roles:
                    main_query = f"""
                        SELECT
                            u.id, u.username, u.email, u.nickname, u.mobile, u.avatar,
                            u.status, u.dept_id, u.created_at::text as created_at,
                            d.name as dept_name,
                            COALESCE(ARRAY_AGG(DISTINCT r.code) FILTER (WHERE r.code IS NOT NULL), ARRAY[]::TEXT[]) as roles,
                            COALESCE(ARRAY_AGG(DISTINCT r.name) FILTER (WHERE r.name IS NOT NULL), ARRAY[]::TEXT[]) as role_names
                        FROM users u
                        LEFT JOIN departments d ON u.dept_id = d.id
                        LEFT JOIN system_user_role ur ON u.id = ur.user_id
                        LEFT JOIN system_role r ON ur.role_id = r.id AND r.deleted_at IS NULL
                        WHERE {where_clause}
                        GROUP BY u.id, u.username, u.email, u.nickname, u.mobile, u.avatar, u.status, u.dept_id, u.created_at, d.name
                        ORDER BY u.created_at DESC
                        LIMIT {page_size} OFFSET {offset}
                    """
                else:
                    main_query = f"""
                        SELECT
                            u.id, u.username, u.email, u.nickname, u.mobile, u.avatar,
                            u.status, u.dept_id, u.created_at::text as created_at,
                            d.name as dept_name
                        FROM users u
                        LEFT JOIN departments d ON u.dept_id = d.id
                        WHERE {where_clause}
                        ORDER BY u.created_at DESC
                        LIMIT {page_size} OFFSET {offset}
                    """

                await cur.execute(main_query, params)
                users = await cur.fetchall()

        return {
            "users": users,
            "total": total,
            "page": page,
            "page_size": page_size
        }

    async def assign_roles(self, user_id: str, role_ids: List[str]) -> bool:
        """
        为用户分配角色

        Args:
            user_id: 用户 ID
            role_ids: 角色 ID 列表

        Returns:
            是否分配成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 删除现有角色
                await cur.execute("DELETE FROM system_user_role WHERE user_id = %s", (user_id,))

                # 分配新角色
                for role_id in role_ids:
                    await cur.execute("""
                        INSERT INTO system_user_role (user_id, role_id)
                        VALUES (%s, %s)
                        ON CONFLICT (user_id, role_id) DO NOTHING
                    """, (user_id, role_id))

                await conn.commit()

        return True

    async def get_roles(self, user_id: str) -> List[Dict[str, Any]]:
        """
        获取用户的角色列表

        Args:
            user_id: 用户 ID

        Returns:
            角色列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT r.id, r.name, r.code
                    FROM system_role r
                    JOIN system_user_role ur ON r.id = ur.role_id
                    WHERE ur.user_id = %s AND r.deleted_at IS NULL
                """, (user_id,))
                return await cur.fetchall()

    async def assign_posts(self, user_id: str, post_ids: List[str]) -> bool:
        """
        为用户分配岗位

        Args:
            user_id: 用户 ID
            post_ids: 岗位 ID 列表

        Returns:
            是否分配成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 删除现有岗位
                await cur.execute("DELETE FROM system_user_post WHERE user_id = %s", (user_id,))

                # 分配新岗位
                for post_id in post_ids:
                    await cur.execute("""
                        INSERT INTO system_user_post (user_id, post_id)
                        VALUES (%s, %s)
                        ON CONFLICT (user_id, post_id) DO NOTHING
                    """, (user_id, post_id))

                await conn.commit()

        return True

    async def get_posts(self, user_id: str) -> List[Dict[str, Any]]:
        """
        获取用户的岗位列表

        Args:
            user_id: 用户 ID

        Returns:
            岗位列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT p.id, p.code, p.name
                    FROM system_post p
                    JOIN system_user_post up ON p.id = up.post_id
                    WHERE up.user_id = %s AND p.deleted_at IS NULL
                """, (user_id,))
                return await cur.fetchall()

    async def update_password(self, user_id: str, password_hash: str) -> bool:
        """
        更新用户密码

        Args:
            user_id: 用户 ID
            password_hash: 密码哈希

        Returns:
            是否更新成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE users SET password_hash = %s
                    WHERE id = %s
                """, (password_hash, user_id))
                await conn.commit()

        return True

    async def update_avatar(self, user_id: str, avatar_url: str) -> bool:
        """
        更新用户头像

        Args:
            user_id: 用户 ID
            avatar_url: 头像 URL

        Returns:
            是否更新成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE users SET avatar = %s
                    WHERE id = %s
                """, (avatar_url, user_id))
                await conn.commit()

        return True

    async def check_username_exists(self, username: str, exclude_id: Optional[str] = None) -> bool:
        """
        检查用户名是否已存在

        Args:
            username: 用户名
            exclude_id: 排除的用户 ID（用于更新时检查）

        Returns:
            用户名是否已存在
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                if exclude_id:
                    await cur.execute("""
                        SELECT id FROM users WHERE username = %s AND id != %s AND deleted_at IS NULL
                    """, (username, exclude_id))
                else:
                    await cur.execute("""
                        SELECT id FROM users WHERE username = %s AND deleted_at IS NULL
                    """, (username,))
                return await cur.fetchone() is not None

    async def check_email_exists(self, email: str, exclude_id: Optional[str] = None) -> bool:
        """
        检查邮箱是否已存在

        Args:
            email: 邮箱
            exclude_id: 排除的用户 ID（用于更新时检查）

        Returns:
            邮箱是否已存在
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                if exclude_id:
                    await cur.execute("""
                        SELECT id FROM users WHERE email = %s AND id != %s AND deleted_at IS NULL
                    """, (email, exclude_id))
                else:
                    await cur.execute("""
                        SELECT id FROM users WHERE email = %s AND deleted_at IS NULL
                    """, (email,))
                return await cur.fetchone() is not None

    async def is_super_admin(self, user_id: str) -> bool:
        """
        检查用户是否是超级管理员

        Args:
            user_id: 用户 ID

        Returns:
            是否是超级管理员
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT r.code
                    FROM system_role r
                    JOIN system_user_role ur ON r.id = ur.role_id
                    WHERE ur.user_id = %s AND r.deleted_at IS NULL AND r.code = 'super_admin'
                """, (user_id,))
                return await cur.fetchone() is not None

    async def get_user_detail(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        获取用户详细信息（包含角色和岗位）

        Args:
            user_id: 用户 ID

        Returns:
            用户详细信息字典
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 获取基本信息
                await cur.execute("""
                    SELECT u.*,
                           d.name as dept_name
                    FROM users u
                    LEFT JOIN departments d ON u.dept_id = d.id
                    WHERE u.id = %s AND u.deleted_at IS NULL
                """, (user_id,))
                user_info = await cur.fetchone()

                if not user_info:
                    return None

                # 获取用户角色
                await cur.execute("""
                    SELECT r.id, r.name, r.code
                    FROM system_role r
                    JOIN system_user_role ur ON r.id = ur.role_id
                    WHERE ur.user_id = %s AND r.deleted_at IS NULL
                """, (user_id,))
                user_info['roles'] = await cur.fetchall()

                # 获取用户岗位
                await cur.execute("""
                    SELECT p.id, p.code, p.name
                    FROM system_post p
                    JOIN system_user_post up ON p.id = up.post_id
                    WHERE up.user_id = %s AND p.deleted_at IS NULL
                """, (user_id,))
                user_info['posts'] = await cur.fetchall()

        return user_info

    async def count_by_dept(self, dept_id: str) -> int:
        """
        统计部门下的用户数量

        Args:
            dept_id: 部门 ID

        Returns:
            用户数量
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT COUNT(*) FROM users WHERE dept_id = %s AND deleted_at IS NULL
                """, (dept_id,))
                result = await cur.fetchone()
                return result[0] if result else 0
