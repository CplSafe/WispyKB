"""
RBAC 权限系统数据库迁移脚本
参考芋道源码(RuoYi-Vue-Pro)的权限模型设计
"""
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def create_rbac_tables(pool):
    """创建RBAC相关表"""

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # ==================== 系统菜单表 ====================
            # 参考 RuoYi-Vue-Pro 的 system_menu 表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_menu (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    name TEXT NOT NULL,
                    permission TEXT,              -- 权限标识，如 'system:user:create'
                    type INTEGER NOT NULL,        -- 1=目录 2=菜单 3=按钮
                    sort INTEGER DEFAULT 0,
                    parent_id TEXT DEFAULT '0',
                    path TEXT,
                    icon TEXT,
                    component TEXT,
                    component_name TEXT,
                    status INTEGER DEFAULT 0,     -- 0=正常 1=停用
                    visible BOOLEAN DEFAULT true,
                    keep_alive BOOLEAN DEFAULT false,
                    always_show BOOLEAN DEFAULT false,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    deleted_at TIMESTAMPTZ
                )
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_menu_parent_id ON system_menu(parent_id) WHERE deleted_at IS NULL
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_menu_permission ON system_menu(permission) WHERE deleted_at IS NULL
            """)

            # ==================== 系统角色表 ====================
            # 参考 RuoYi-Vue-Pro 的 system_role 表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_role (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    name TEXT NOT NULL,
                    code TEXT UNIQUE NOT NULL,     -- 角色标识，如 'super_admin'
                    sort INTEGER DEFAULT 0,
                    status INTEGER DEFAULT 0,      -- 0=正常 1=停用
                    type INTEGER DEFAULT 2,        -- 1=系统内置 2=自定义
                    data_scope INTEGER DEFAULT 1,  -- 数据范围：1=全部 2=自定义 3=本部门 4=本部门及以下 5=仅本人
                    remark TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    deleted_at TIMESTAMPTZ
                )
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_role_code ON system_role(code) WHERE deleted_at IS NULL
            """)

            # ==================== 用户角色关联表 ====================
            # 参考 RuoYi-Vue-Pro 的 system_user_role 表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_user_role (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    role_id TEXT NOT NULL REFERENCES system_role(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(user_id, role_id)
                )
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_user_role_user_id ON system_user_role(user_id)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_user_role_role_id ON system_user_role(role_id)
            """)

            # ==================== 角色菜单关联表 ====================
            # 参考 RuoYi-Vue-Pro 的 system_role_menu 表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_role_menu (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    role_id TEXT NOT NULL REFERENCES system_role(id) ON DELETE CASCADE,
                    menu_id TEXT NOT NULL REFERENCES system_menu(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(role_id, menu_id)
                )
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_role_menu_role_id ON system_role_menu(role_id)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_role_menu_menu_id ON system_role_menu(menu_id)
            """)

            # ==================== 数据权限部门关联表 ====================
            # 用于 data_scope=2 (自定义部门) 时的部门配置
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_role_data_scope_dept (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    role_id TEXT NOT NULL REFERENCES system_role(id) ON DELETE CASCADE,
                    dept_id TEXT NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(role_id, dept_id)
                )
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_role_data_scope_role_id ON system_role_data_scope_dept(role_id)
            """)

            # ==================== 用户表扩展 ====================
            # 添加 department_id 和其他 RBAC 相关字段
            try:
                await cur.execute("""
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS dept_id TEXT REFERENCES departments(id) ON DELETE SET NULL
                """)
                await cur.execute("""
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS nickname TEXT
                """)
                await cur.execute("""
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS mobile TEXT
                """)
                await cur.execute("""
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS avatar TEXT
                """)
                await cur.execute("""
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS status INTEGER DEFAULT 0
                """)
                await cur.execute("""
                    ALTER TABLE users
                    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ
                """)
                await cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_users_dept_id ON users(dept_id) WHERE deleted_at IS NULL
                """)
                await cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_users_status ON users(status) WHERE deleted_at IS NULL
                """)
            except Exception as e:
                logger.warning(f"用户表字段扩展警告: {e}")

            # ==================== 岗位表 ====================
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_post (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    code TEXT UNIQUE NOT NULL,
                    name TEXT NOT NULL,
                    sort INTEGER DEFAULT 0,
                    status INTEGER DEFAULT 0,
                    remark TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    deleted_at TIMESTAMPTZ
                )
            """)

            # ==================== 用户岗位关联表 ====================
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_user_post (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    post_id TEXT NOT NULL REFERENCES system_post(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(user_id, post_id)
                )
            """)

            # ==================== 在线用户记录表 ====================
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_user_session (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token TEXT NOT NULL,
                    ip_address TEXT,
                    user_agent TEXT,
                    login_at TIMESTAMPTZ DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL
                )
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_user_session_user_id ON system_user_session(user_id)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_user_session_token ON system_user_session(token)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_user_session_expires_at ON system_user_session(expires_at)
            """)

            # ==================== 操作日志表 ====================
            # 参考 RuoYi-Vue-Pro 的 system_operate_log
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_operate_log (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                    username TEXT,
                    module TEXT,                  -- 模块名
                    operation TEXT,               -- 操作名称
                    request_method TEXT,          -- 请求方法
                    request_url TEXT,             -- 请求URL
                    request_ip TEXT,
                    user_agent TEXT,
                    request_params TEXT,          -- 请求参数 (JSON)
                    response_data TEXT,           -- 响应数据 (JSON)
                    status INTEGER DEFAULT 0,     -- 0=成功 1=失败
                    error_msg TEXT,
                    execute_time INTEGER,         -- 执行时长(毫秒)
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_operate_log_user_id ON system_operate_log(user_id)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_operate_log_created_at ON system_operate_log(created_at)
            """)

            # ==================== 登录日志表 ====================
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_login_log (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    username TEXT,
                    status INTEGER DEFAULT 0,     -- 0=成功 1=失败
                    ip_address TEXT,
                    user_agent TEXT,
                    error_msg TEXT,
                    login_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_login_log_username ON system_login_log(username)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_login_log_created_at ON system_login_log(login_at)
            """)

            # ==================== 资源授权表 ====================
            # 用于对知识库、应用等资源进行细粒度授权
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_resource_permission (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    resource_type TEXT NOT NULL,  -- 'knowledge_base', 'application', 'workflow'
                    resource_id TEXT NOT NULL,
                    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                    role_id TEXT REFERENCES system_role(id) ON DELETE CASCADE,
                    dept_id TEXT REFERENCES departments(id) ON DELETE CASCADE,
                    permissions TEXT NOT NULL,    -- JSON数组，如 ["read", "write", "delete"]
                    granted_by TEXT REFERENCES users(id),
                    granted_at TIMESTAMPTZ DEFAULT NOW(),
                    expires_at TIMESTAMPTZ,
                    CHECK (user_id IS NOT NULL OR role_id IS NOT NULL OR dept_id IS NOT NULL)
                )
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_resource_permission_resource ON system_resource_permission(resource_type, resource_id)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_resource_permission_user_id ON system_resource_permission(user_id)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_resource_permission_role_id ON system_resource_permission(role_id)
            """)

            await conn.commit()
            logger.info("RBAC 数据库表创建完成")


async def init_default_data(pool):
    """初始化默认数据"""

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # ==================== 初始化默认角色 ====================
            default_roles = [
                {
                    'id': 'role_super_admin',
                    'name': '超级管理员',
                    'code': 'super_admin',
                    'sort': 1,
                    'status': 0,
                    'type': 1,  # 系统内置
                    'data_scope': 1,  # 全部数据权限
                    'remark': '系统超级管理员，拥有所有权限'
                },
                {
                    'id': 'role_admin',
                    'name': '管理员',
                    'code': 'admin',
                    'sort': 2,
                    'status': 0,
                    'type': 1,
                    'data_scope': 4,  # 本部门及以下
                    'remark': '部门管理员'
                },
                {
                    'id': 'role_member',
                    'name': '普通成员',
                    'code': 'member',
                    'sort': 3,
                    'status': 0,
                    'type': 1,
                    'data_scope': 5,  # 仅本人
                    'remark': '普通用户'
                },
                {
                    'id': 'role_viewer',
                    'name': '访客',
                    'code': 'viewer',
                    'sort': 4,
                    'status': 0,
                    'type': 1,
                    'data_scope': 5,
                    'remark': '只读用户'
                }
            ]

            for role in default_roles:
                await cur.execute("""
                    INSERT INTO system_role (id, name, code, sort, status, type, data_scope, remark)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (code) DO UPDATE SET
                        name = EXCLUDED.name,
                        sort = EXCLUDED.sort,
                        data_scope = EXCLUDED.data_scope,
                        remark = EXCLUDED.remark
                """, (
                    role['id'], role['name'], role['code'], role['sort'],
                    role['status'], role['type'], role['data_scope'], role['remark']
                ))

            # ==================== 初始化默认菜单 ====================
            default_menus = [
                # 知识库管理
                {
                    'id': 'menu_kb',
                    'name': '知识库管理',
                    'permission': 'kb:manage',
                    'type': 2,
                    'sort': 10,
                    'parent_id': '0',
                    'path': '/knowledge',
                    'icon': 'DatabaseOutlined',
                    'status': 0
                },
                {
                    'id': 'menu_kb_create',
                    'name': '创建知识库',
                    'permission': 'kb:create',
                    'type': 3,
                    'sort': 1,
                    'parent_id': 'menu_kb',
                    'status': 0
                },
                {
                    'id': 'menu_kb_update',
                    'name': '编辑知识库',
                    'permission': 'kb:update',
                    'type': 3,
                    'sort': 2,
                    'parent_id': 'menu_kb',
                    'status': 0
                },
                {
                    'id': 'menu_kb_delete',
                    'name': '删除知识库',
                    'permission': 'kb:delete',
                    'type': 3,
                    'sort': 3,
                    'parent_id': 'menu_kb',
                    'status': 0
                },
                {
                    'id': 'menu_kb_upload',
                    'name': '上传文档',
                    'permission': 'kb:document:upload',
                    'type': 3,
                    'sort': 4,
                    'parent_id': 'menu_kb',
                    'status': 0
                },

                # 应用管理
                {
                    'id': 'menu_app',
                    'name': 'AI应用管理',
                    'permission': 'app:manage',
                    'type': 2,
                    'sort': 20,
                    'parent_id': '0',
                    'path': '/applications',
                    'icon': 'AppstoreOutlined',
                    'status': 0
                },
                {
                    'id': 'menu_app_create',
                    'name': '创建应用',
                    'permission': 'app:create',
                    'type': 3,
                    'sort': 1,
                    'parent_id': 'menu_app',
                    'status': 0
                },
                {
                    'id': 'menu_app_update',
                    'name': '编辑应用',
                    'permission': 'app:update',
                    'type': 3,
                    'sort': 2,
                    'parent_id': 'menu_app',
                    'status': 0
                },
                {
                    'id': 'menu_app_delete',
                    'name': '删除应用',
                    'permission': 'app:delete',
                    'type': 3,
                    'sort': 3,
                    'parent_id': 'menu_app',
                    'status': 0
                },

                # 用户管理
                {
                    'id': 'menu_user',
                    'name': '用户管理',
                    'permission': 'system:user:manage',
                    'type': 2,
                    'sort': 30,
                    'parent_id': '0',
                    'path': '/settings/users',
                    'icon': 'UserOutlined',
                    'status': 0
                },
                {
                    'id': 'menu_user_create',
                    'name': '新建用户',
                    'permission': 'system:user:create',
                    'type': 3,
                    'sort': 1,
                    'parent_id': 'menu_user',
                    'status': 0
                },
                {
                    'id': 'menu_user_update',
                    'name': '编辑用户',
                    'permission': 'system:user:update',
                    'type': 3,
                    'sort': 2,
                    'parent_id': 'menu_user',
                    'status': 0
                },
                {
                    'id': 'menu_user_delete',
                    'name': '删除用户',
                    'permission': 'system:user:delete',
                    'type': 3,
                    'sort': 3,
                    'parent_id': 'menu_user',
                    'status': 0
                },

                # 角色管理
                {
                    'id': 'menu_role',
                    'name': '角色管理',
                    'permission': 'system:role:manage',
                    'type': 2,
                    'sort': 31,
                    'parent_id': '0',
                    'path': '/settings/roles',
                    'icon': 'TeamOutlined',
                    'status': 0
                },
                {
                    'id': 'menu_role_create',
                    'name': '新建角色',
                    'permission': 'system:role:create',
                    'type': 3,
                    'sort': 1,
                    'parent_id': 'menu_role',
                    'status': 0
                },
                {
                    'id': 'menu_role_update',
                    'name': '编辑角色',
                    'permission': 'system:role:update',
                    'type': 3,
                    'sort': 2,
                    'parent_id': 'menu_role',
                    'status': 0
                },
                {
                    'id': 'menu_role_delete',
                    'name': '删除角色',
                    'permission': 'system:role:delete',
                    'type': 3,
                    'sort': 3,
                    'parent_id': 'menu_role',
                    'status': 0
                },
                {
                    'id': 'menu_role_assign',
                    'name': '分配权限',
                    'permission': 'system:role:assign',
                    'type': 3,
                    'sort': 4,
                    'parent_id': 'menu_role',
                    'status': 0
                },

                # 部门管理
                {
                    'id': 'menu_dept',
                    'name': '部门管理',
                    'permission': 'system:dept:manage',
                    'type': 2,
                    'sort': 32,
                    'parent_id': '0',
                    'path': '/settings/departments',
                    'icon': 'ApartmentOutlined',
                    'status': 0
                },

                # 系统设置
                {
                    'id': 'menu_system',
                    'name': '系统设置',
                    'permission': 'system:config',
                    'type': 2,
                    'sort': 40,
                    'parent_id': '0',
                    'path': '/settings/system',
                    'icon': 'SettingOutlined',
                    'status': 0
                },

                # 审计日志
                {
                    'id': 'menu_audit',
                    'name': '审计日志',
                    'permission': 'system:audit:view',
                    'type': 2,
                    'sort': 41,
                    'parent_id': '0',
                    'path': '/settings/audit',
                    'icon': 'HistoryOutlined',
                    'status': 0
                },
            ]

            for menu in default_menus:
                await cur.execute("""
                    INSERT INTO system_menu
                    (id, name, permission, type, sort, parent_id, path, icon, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (
                    menu['id'], menu['name'], menu['permission'], menu['type'],
                    menu['sort'], menu['parent_id'], menu.get('path', ''),
                    menu.get('icon', ''), menu['status']
                ))

            # ==================== 为超级管理员角色分配所有菜单权限 ====================
            await cur.execute("""
                INSERT INTO system_role_menu (role_id, menu_id)
                SELECT 'role_super_admin', id FROM system_menu
                ON CONFLICT (role_id, menu_id) DO NOTHING
            """)

            # 为管理员角色分配部分菜单权限
            admin_menu_permissions = [
                'menu_kb', 'menu_kb_create', 'menu_kb_update', 'menu_kb_upload',
                'menu_app', 'menu_app_create', 'menu_app_update',
                'menu_user', 'menu_user_update',
                'menu_dept',
            ]
            for menu_id in admin_menu_permissions:
                await cur.execute("""
                    INSERT INTO system_role_menu (role_id, menu_id)
                    VALUES ('role_admin', %s)
                    ON CONFLICT (role_id, menu_id) DO NOTHING
                """, (menu_id,))

            # 为普通成员分配基本菜单权限
            member_menu_permissions = [
                'menu_kb', 'menu_kb_upload',
                'menu_app',
            ]
            for menu_id in member_menu_permissions:
                await cur.execute("""
                    INSERT INTO system_role_menu (role_id, menu_id)
                    VALUES ('role_member', %s)
                    ON CONFLICT (role_id, menu_id) DO NOTHING
                """, (menu_id,))

            # ==================== 初始化默认岗位 ====================
            default_posts = [
                {'code': 'ceo', 'name': 'CEO', 'sort': 1},
                {'code': 'cto', 'name': 'CTO', 'sort': 2},
                {'code': 'manager', 'name': '经理', 'sort': 10},
                {'code': 'developer', 'name': '开发工程师', 'sort': 20},
                {'code': 'tester', 'name': '测试工程师', 'sort': 30},
                {'code': 'operator', 'name': '运维工程师', 'sort': 40},
            ]

            for post in default_posts:
                await cur.execute("""
                    INSERT INTO system_post (code, name, sort, status)
                    VALUES (%s, %s, %s, 0)
                    ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
                """, (post['code'], post['name'], post['sort']))

            await conn.commit()
            logger.info("RBAC 默认数据初始化完成")


async def migrate_existing_users(pool):
    """迁移现有用户数据到新的RBAC结构"""

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 获取所有现有用户
            await cur.execute("SELECT id, username, email, role FROM users WHERE deleted_at IS NULL")
            users = await cur.fetchall()

            for user in users:
                user_id, username, email, old_role = user

                # 根据 old_role 分配新角色
                role_code_map = {
                    'super_admin': 'super_admin',
                    'workspace_admin': 'admin',
                    'member': 'member',
                    'viewer': 'viewer'
                }

                new_role_code = role_code_map.get(old_role, 'member')

                # 获取角色ID
                await cur.execute(
                    "SELECT id FROM system_role WHERE code = %s",
                    (new_role_code,)
                )
                role_result = await cur.fetchone()

                if role_result:
                    role_id = role_result[0]

                    # 创建用户角色关联
                    await cur.execute("""
                        INSERT INTO system_user_role (user_id, role_id)
                        VALUES (%s, %s)
                        ON CONFLICT (user_id, role_id) DO NOTHING
                    """, (user_id, role_id))

            await conn.commit()
            logger.info(f"已迁移 {len(users)} 个用户到新的RBAC结构")


async def main():
    """主函数"""
    import psycopg
    from psycopg_pool import AsyncConnectionPool
    import os

    DB_CONFIG = {
        "host": "localhost",
        "port": 5432,
        "dbname": "ai_knowledge_base",
        "user": os.getenv("PGUSER", os.getenv("USER", "guijinhao")),
        "password": "",
        "min_size": 2,
        "max_size": 10,
    }

    pool = AsyncConnectionPool(
        f"host={DB_CONFIG['host']} port={DB_CONFIG['port']} "
        f"dbname={DB_CONFIG['dbname']} user={DB_CONFIG['user']}",
        min_size=DB_CONFIG['min_size'],
        max_size=DB_CONFIG['max_size'],
    )

    await pool.wait()
    logger.info("数据库连接池已就绪")

    try:
        # 1. 创建RBAC表
        await create_rbac_tables(pool)

        # 2. 初始化默认数据
        await init_default_data(pool)

        # 3. 迁移现有用户
        await migrate_existing_users(pool)

        logger.info("RBAC 系统迁移完成!")

    except Exception as e:
        logger.error(f"迁移失败: {e}", exc_info=True)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
