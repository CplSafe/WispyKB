# 角色数据访问层
# 负责角色相关的数据库操作

import logging
from typing import Optional, List, Dict, Any
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


class RoleRepository:
    """角色数据访问类"""

    def __init__(self, pool):
        """
        初始化角色 Repository

        Args:
            pool: 数据库连接池
        """
        self.pool = pool

    async def find_by_id(self, role_id: str) -> Optional[Dict[str, Any]]:
        """
        根据 ID 查找角色

        Args:
            role_id: 角色 ID

        Returns:
            角色信息字典，不存在则返回 None
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT r.*,
                           COUNT(DISTINCT ur.user_id) as user_count
                    FROM system_role r
                    LEFT JOIN system_user_role ur ON r.id = ur.role_id
                    WHERE r.id = %s AND r.deleted_at IS NULL
                    GROUP BY r.id
                """, (role_id,))
                return await cur.fetchone()

    async def find_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        """
        根据角色代码查找角色

        Args:
            code: 角色代码

        Returns:
            角色信息字典，不存在则返回 None
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT * FROM system_role
                    WHERE code = %s AND deleted_at IS NULL
                """, (code,))
                return await cur.fetchone()

    async def list_roles(
        self,
        page: int = 1,
        page_size: int = 20,
        name: Optional[str] = None,
        status: Optional[int] = None,
        include_user_count: bool = True
    ) -> Dict[str, Any]:
        """
        获取角色列表（分页）

        Args:
            page: 页码
            page_size: 每页数量
            name: 角色名模糊搜索
            status: 状态筛选
            include_user_count: 是否包含用户数量

        Returns:
            包含 roles, total, page, page_size 的字典
        """
        async with self.pool.connection() as conn:
            # 构建查询条件
            conditions = ["r.deleted_at IS NULL"]
            params = []

            if name:
                conditions.append("r.name LIKE %s")
                params.append(f"%{name}%")
            if status is not None:
                conditions.append("r.status = %s")
                params.append(status)

            where_clause = " AND ".join(conditions)

            # 获取总数
            async with conn.cursor(row_factory=dict_row) as cur_count:
                await cur_count.execute(f"""
                    SELECT COUNT(*) as total
                    FROM system_role r
                    WHERE {where_clause}
                """, params)
                total_result = await cur_count.fetchone()
                total = total_result['total'] if total_result else 0

            # 获取分页数据
            offset = (page - 1) * page_size

            async with conn.cursor(row_factory=dict_row) as cur:
                if include_user_count:
                    main_query = f"""
                        SELECT
                            r.*,
                            COUNT(DISTINCT ur.user_id) as user_count
                        FROM system_role r
                        LEFT JOIN system_user_role ur ON r.id = ur.role_id
                        WHERE {where_clause}
                        GROUP BY r.id
                        ORDER BY r.sort
                        LIMIT {page_size} OFFSET {offset}
                    """
                else:
                    main_query = f"""
                        SELECT r.*
                        FROM system_role r
                        WHERE {where_clause}
                        ORDER BY r.sort
                        LIMIT {page_size} OFFSET {offset}
                    """

                await cur.execute(main_query, params)
                roles = await cur.fetchall()

        return {
            "roles": roles,
            "total": total,
            "page": page,
            "page_size": page_size
        }

    async def list_all_roles(self, status: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取所有角色（不分页）

        Args:
            status: 状态筛选

        Returns:
            角色列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                if status is not None:
                    await cur.execute("""
                        SELECT * FROM system_role
                        WHERE status = %s AND deleted_at IS NULL
                        ORDER BY sort
                    """, (status,))
                else:
                    await cur.execute("""
                        SELECT * FROM system_role
                        WHERE deleted_at IS NULL
                        ORDER BY sort
                    """)
                return await cur.fetchall()

    async def create_role(self, role_data: Dict[str, Any]) -> str:
        """
        创建新角色

        Args:
            role_data: 角色数据字典

        Returns:
            新创建角色的 ID
        """
        role_id = role_data.get('id') or f"role_{role_data['code']}"

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO system_role (id, name, code, sort, status, type, data_scope, remark, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """, (
                    role_id,
                    role_data['name'],
                    role_data['code'],
                    role_data.get('sort', 0),
                    role_data.get('status', 0),
                    role_data.get('type', 1),
                    role_data.get('data_scope', 5),
                    role_data.get('remark')
                ))
                await conn.commit()

        return role_id

    async def update_role(self, role_id: str, update_data: Dict[str, Any]) -> bool:
        """
        更新角色信息

        Args:
            role_id: 角色 ID
            update_data: 要更新的字段字典

        Returns:
            是否更新成功
        """
        if not update_data:
            return False

        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 检查角色是否存在
                await cur.execute("SELECT id FROM system_role WHERE id = %s AND deleted_at IS NULL", (role_id,))
                if not await cur.fetchone():
                    return False

                # 构建更新语句
                updates = []
                values = []
                for key, value in update_data.items():
                    if key not in ['id', 'created_at', 'code']:
                        updates.append(f"{key} = %s")
                        values.append(value)

                if updates:
                    updates.append("updated_at = NOW()")
                    values.append(role_id)
                    await cur.execute(f"""
                        UPDATE system_role SET {', '.join(updates)}
                        WHERE id = %s
                    """, values)
                    await conn.commit()

        return True

    async def delete_role(self, role_id: str) -> bool:
        """
        软删除角色

        Args:
            role_id: 角色 ID

        Returns:
            是否删除成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE system_role SET deleted_at = NOW()
                    WHERE id = %s
                """, (role_id,))
                await conn.commit()

        return True

    async def assign_permissions(self, role_id: str, permission_ids: List[str]) -> bool:
        """
        为角色分配权限

        Args:
            role_id: 角色 ID
            permission_ids: 权限 ID 列表

        Returns:
            是否分配成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 删除现有权限
                await cur.execute("DELETE FROM system_role_permission WHERE role_id = %s", (role_id,))

                # 分配新权限
                for permission_id in permission_ids:
                    await cur.execute("""
                        INSERT INTO system_role_permission (role_id, permission_id)
                        VALUES (%s, %s)
                        ON CONFLICT (role_id, permission_id) DO NOTHING
                    """, (role_id, permission_id))

                await conn.commit()

        return True

    async def get_permissions(self, role_id: str) -> List[Dict[str, Any]]:
        """
        获取角色的权限列表

        Args:
            role_id: 角色 ID

        Returns:
            权限列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT p.id, p.name, p.code
                    FROM system_permission p
                    JOIN system_role_permission rp ON p.id = rp.permission_id
                    WHERE rp.role_id = %s
                """, (role_id,))
                return await cur.fetchall()

    async def assign_menus(self, role_id: str, menu_ids: List[str]) -> bool:
        """
        为角色分配菜单权限

        Args:
            role_id: 角色 ID
            menu_ids: 菜单 ID 列表

        Returns:
            是否分配成功
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                # 删除现有菜单
                await cur.execute("DELETE FROM system_role_menu WHERE role_id = %s", (role_id,))

                # 分配新菜单
                for menu_id in menu_ids:
                    await cur.execute("""
                        INSERT INTO system_role_menu (role_id, menu_id)
                        VALUES (%s, %s)
                        ON CONFLICT (role_id, menu_id) DO NOTHING
                    """, (role_id, menu_id))

                await conn.commit()

        return True

    async def get_menus(self, role_id: str) -> List[Dict[str, Any]]:
        """
        获取角色的菜单列表

        Args:
            role_id: 角色 ID

        Returns:
            菜单列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT m.id, m.name, m.type, m.parent_id, m.sort
                    FROM system_menu m
                    JOIN system_role_menu rm ON m.id = rm.menu_id
                    WHERE rm.role_id = %s
                    ORDER BY m.sort
                """, (role_id,))
                return await cur.fetchall()

    async def get_users(self, role_id: str) -> List[Dict[str, Any]]:
        """
        获取角色下的用户列表

        Args:
            role_id: 角色 ID

        Returns:
            用户列表
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT u.id, u.username, u.email, u.nickname, u.avatar, u.status
                    FROM users u
                    JOIN system_user_role ur ON u.id = ur.user_id
                    WHERE ur.role_id = %s AND u.deleted_at IS NULL
                    ORDER BY u.created_at DESC
                """, (role_id,))
                return await cur.fetchall()

    async def count_users(self, role_id: str) -> int:
        """
        统计角色下的用户数量

        Args:
            role_id: 角色 ID

        Returns:
            用户数量
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT COUNT(*) FROM system_user_role WHERE role_id = %s
                """, (role_id,))
                result = await cur.fetchone()
                return result[0] if result else 0

    async def check_code_exists(self, code: str, exclude_id: Optional[str] = None) -> bool:
        """
        检查角色代码是否已存在

        Args:
            code: 角色代码
            exclude_id: 排除的角色 ID（用于更新时检查）

        Returns:
            角色代码是否已存在
        """
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                if exclude_id:
                    await cur.execute("""
                        SELECT id FROM system_role WHERE code = %s AND id != %s AND deleted_at IS NULL
                    """, (code, exclude_id))
                else:
                    await cur.execute("""
                        SELECT id FROM system_role WHERE code = %s AND deleted_at IS NULL
                    """, (code,))
                return await cur.fetchone() is not None

    async def get_role_detail(self, role_id: str) -> Optional[Dict[str, Any]]:
        """
        获取角色详细信息（包含权限和菜单）

        Args:
            role_id: 角色 ID

        Returns:
            角色详细信息字典
        """
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 获取基本信息
                await cur.execute("""
                    SELECT r.*,
                           COUNT(DISTINCT ur.user_id) as user_count
                    FROM system_role r
                    LEFT JOIN system_user_role ur ON r.id = ur.role_id
                    WHERE r.id = %s AND r.deleted_at IS NULL
                    GROUP BY r.id
                """, (role_id,))
                role = await cur.fetchone()

                if not role:
                    return None

                # 获取角色的权限
                await cur.execute("""
                    SELECT p.id, p.name, p.code
                    FROM system_permission p
                    JOIN system_role_permission rp ON p.id = rp.permission_id
                    WHERE rp.role_id = %s
                """, (role_id,))
                role['permissions'] = await cur.fetchall()

                # 获取角色的菜单
                await cur.execute("""
                    SELECT m.id, m.name, m.type
                    FROM system_menu m
                    JOIN system_role_menu rm ON m.id = rm.menu_id
                    WHERE rm.role_id = %s
                    ORDER BY m.sort
                """, (role_id,))
                role['menus'] = await cur.fetchall()

        return role
