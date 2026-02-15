# 部门数据访问层
# 负责部门相关的数据库操作

import uuid
import logging
from typing import Optional, List, Dict, Any
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


class DepartmentRepository:
    """部门数据访问类"""

    def __init__(self, pool):
        """
        初始化部门 Repository

        Args:
            pool: 数据库连接池
        """
        self.pool = pool

    async def find_by_id(self, dept_id: str) -> Optional[Dict[str, Any]]:
        """
        根据 ID 查找部门

        Args:
            dept_id: 部门 ID

        Returns:
            部门信息字典，不存在则返回 None
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT d.*,
                           p.name as parent_name
                    FROM departments d
                    LEFT JOIN departments p ON d.parent_id = p.id
                    WHERE d.id = %s AND d.deleted_at IS NULL
                """, (dept_id,))
                return await cur.fetchone()

    async def list_departments(
        self,
        name: Optional[str] = None,
        status: Optional[int] = None,
        as_tree: bool = True
    ) -> List[Dict[str, Any]]:
        """
        获取部门列表

        Args:
            name: 部门名模糊搜索
            status: 状态筛选
            as_tree: 是否返回树形结构

        Returns:
            部门列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 构建查询条件
                conditions = ["1=1"]
                params = []

                if name:
                    conditions.append("name LIKE %s")
                    params.append(f"%{name}%")
                if status is not None:
                    conditions.append("status = %s")
                    params.append(status)

                where_clause = " AND ".join(conditions)

                await cur.execute(f"""
                    SELECT * FROM departments
                    WHERE {where_clause}
                    ORDER BY sort
                """, params)
                departments = await cur.fetchall()

        if as_tree:
            return self._build_tree(departments)

        return departments

    def _build_tree(self, departments: List[Dict[str, Any]], parent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        构建部门树形结构

        Args:
            departments: 部门列表
            parent_id: 父部门 ID

        Returns:
            树形结构的部门列表
        """
        result = []
        for dept in departments:
            if dept.get('parent_id') == parent_id:
                children = self._build_tree(departments, dept['id'])
                dept_copy = dict(dept)
                if children:
                    dept_copy['children'] = children
                result.append(dept_copy)
        return result

    async def create_department(self, dept_data: Dict[str, Any]) -> str:
        """
        创建新部门

        Args:
            dept_data: 部门数据字典

        Returns:
            新创建部门的 ID
        """
        dept_id = dept_data.get('id') or str(uuid.uuid4())

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO departments (id, name, parent_id, sort, status, remark, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                """, (
                    dept_id,
                    dept_data['name'],
                    dept_data.get('parent_id'),
                    dept_data.get('sort', 0),
                    dept_data.get('status', 0),
                    dept_data.get('remark')
                ))
                await conn.commit()

        return dept_id

    async def update_department(self, dept_id: str, update_data: Dict[str, Any]) -> bool:
        """
        更新部门信息

        Args:
            dept_id: 部门 ID
            update_data: 要更新的字段字典

        Returns:
            是否更新成功
        """
        if not update_data:
            return False

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 检查部门是否存在
                await cur.execute("SELECT id FROM departments WHERE id = %s AND deleted_at IS NULL", (dept_id,))
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
                    values.append(dept_id)
                    await cur.execute(f"""
                        UPDATE departments SET {', '.join(updates)}
                        WHERE id = %s
                    """, values)
                    await conn.commit()

        return True

    async def delete_department(self, dept_id: str) -> bool:
        """
        软删除部门

        Args:
            dept_id: 部门 ID

        Returns:
            是否删除成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE departments SET deleted_at = NOW()
                    WHERE id = %s
                """, (dept_id,))
                await conn.commit()

        return True

    async def has_children(self, dept_id: str) -> bool:
        """
        检查部门是否有子部门

        Args:
            dept_id: 部门 ID

        Returns:
            是否有子部门
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT id FROM departments WHERE parent_id = %s AND deleted_at IS NULL
                """, (dept_id,))
                return await cur.fetchone() is not None

    async def count_users(self, dept_id: str) -> int:
        """
        统计部门下的用户数量

        Args:
            dept_id: 部门 ID

        Returns:
            用户数量
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 尝试从关联表统计
                try:
                    await cur.execute("""
                        SELECT COUNT(*) FROM system_user_dept WHERE dept_id = %s
                    """, (dept_id,))
                    result = await cur.fetchone()
                    return result[0] if result else 0
                except Exception:
                    # 如果关联表不存在，从用户表统计
                    await cur.execute("""
                        SELECT COUNT(*) FROM users WHERE dept_id = %s AND deleted_at IS NULL
                    """, (dept_id,))
                    result = await cur.fetchone()
                    return result[0] if result else 0

    async def assign_user(self, user_id: str, dept_id: str) -> bool:
        """
        将用户分配到部门

        Args:
            user_id: 用户 ID
            dept_id: 部门 ID

        Returns:
            是否分配成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 验证用户和部门存在
                await cur.execute(
                    "SELECT id FROM users WHERE id = %s AND deleted_at IS NULL",
                    (user_id,)
                )
                if not await cur.fetchone():
                    return False

                await cur.execute(
                    "SELECT id FROM departments WHERE id = %s AND deleted_at IS NULL",
                    (dept_id,)
                )
                if not await cur.fetchone():
                    return False

                # 尝试使用关联表，如果不存在则直接更新用户的 dept_id
                try:
                    await cur.execute("""
                        INSERT INTO system_user_dept (user_id, dept_id)
                        VALUES (%s, %s)
                        ON CONFLICT (user_id) DO UPDATE SET dept_id = %s
                    """, (user_id, dept_id, dept_id))
                except Exception:
                    # 关联表不存在，直接更新用户表
                    await cur.execute("""
                        UPDATE users SET dept_id = %s WHERE id = %s
                    """, (dept_id, user_id))

                await conn.commit()

        return True

    async def remove_user(self, user_id: str, dept_id: str) -> bool:
        """
        将用户从部门移除

        Args:
            user_id: 用户 ID
            dept_id: 部门 ID

        Returns:
            是否移除成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                try:
                    await cur.execute("""
                        DELETE FROM system_user_dept
                        WHERE user_id = %s AND dept_id = %s
                    """, (user_id, dept_id))
                except Exception:
                    # 关联表不存在，将用户的 dept_id 设为 NULL
                    await cur.execute("""
                        UPDATE users SET dept_id = NULL WHERE id = %s AND dept_id = %s
                    """, (user_id, dept_id))

                await conn.commit()

        return True

    async def get_users(self, dept_id: str) -> List[Dict[str, Any]]:
        """
        获取部门下的用户列表

        Args:
            dept_id: 部门 ID

        Returns:
            用户列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT u.id, u.username, u.email, u.nickname, u.avatar, u.status
                    FROM users u
                    WHERE u.dept_id = %s AND u.deleted_at IS NULL
                    ORDER BY u.created_at DESC
                """, (dept_id,))
                return await cur.fetchall()

    async def get_user_departments(self, user_id: str) -> List[Dict[str, Any]]:
        """
        获取用户的部门列表

        Args:
            user_id: 用户 ID

        Returns:
            部门列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                try:
                    await cur.execute("""
                        SELECT d.*
                        FROM system_user_dept ud
                        JOIN departments d ON ud.dept_id = d.id
                        WHERE ud.user_id = %s AND d.deleted_at IS NULL
                    """, (user_id,))
                    return await cur.fetchall()
                except Exception:
                    # 关联表不存在，从用户表获取
                    await cur.execute("""
                        SELECT d.*
                        FROM departments d
                        JOIN users u ON u.dept_id = d.id
                        WHERE u.id = %s AND d.deleted_at IS NULL
                    """, (user_id,))
                    result = await cur.fetchall()
                    return result if result else []

    async def get_children(self, dept_id: str) -> List[Dict[str, Any]]:
        """
        获取部门的子部门列表

        Args:
            dept_id: 部门 ID

        Returns:
            子部门列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT * FROM departments
                    WHERE parent_id = %s AND deleted_at IS NULL
                    ORDER BY sort
                """, (dept_id,))
                return await cur.fetchall()

    async def get_all_children_ids(self, dept_id: str) -> List[str]:
        """
        递归获取部门的所有子部门 ID（包括孙部门）

        Args:
            dept_id: 部门 ID

        Returns:
            所有子部门 ID 列表
        """
        result = []

        async def _collect_children(parent_id: str):
            children = await self.get_children(parent_id)
            for child in children:
                result.append(child['id'])
                await _collect_children(child['id'])

        await _collect_children(dept_id)
        return result

    async def get_department_detail(self, dept_id: str) -> Optional[Dict[str, Any]]:
        """
        获取部门详细信息

        Args:
            dept_id: 部门 ID

        Returns:
            部门详细信息
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 获取基本信息
                await cur.execute("""
                    SELECT d.*, parent.name as parent_name
                    FROM departments d
                    LEFT JOIN departments parent ON d.parent_id = parent.id
                    WHERE d.id = %s AND d.deleted_at IS NULL
                """, (dept_id,))
                dept = await cur.fetchone()

                if not dept:
                    return None

                # 获取部门用户数量
                try:
                    await cur.execute("""
                        SELECT COUNT(*) as user_count
                        FROM system_user_dept
                        WHERE dept_id = %s
                    """, (dept_id,))
                    user_count = await cur.fetchone()
                    dept['user_count'] = user_count['user_count'] if user_count else 0
                except Exception:
                    await cur.execute("""
                        SELECT COUNT(*) as user_count
                        FROM users
                        WHERE dept_id = %s AND deleted_at IS NULL
                    """, (dept_id,))
                    user_count = await cur.fetchone()
                    dept['user_count'] = user_count['user_count'] if user_count else 0

        return dept

    async def check_exists(self, name: str, exclude_id: Optional[str] = None) -> bool:
        """
        检查部门名称是否已存在

        Args:
            name: 部门名称
            exclude_id: 排除的部门 ID（用于更新时检查）

        Returns:
            部门名称是否已存在
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                if exclude_id:
                    await cur.execute("""
                        SELECT id FROM departments WHERE name = %s AND id != %s AND deleted_at IS NULL
                    """, (name, exclude_id))
                else:
                    await cur.execute("""
                        SELECT id FROM departments WHERE name = %s AND deleted_at IS NULL
                    """, (name,))
                return await cur.fetchone() is not None
