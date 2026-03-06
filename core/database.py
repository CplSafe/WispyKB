# 数据库初始化和配置
# 从 main_pgvector.py 提取的数据库相关函数

import logging
import uuid
from typing import AsyncGenerator

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from .config import DB_CONFIG

logger = logging.getLogger(__name__)


# ==================== 数据库连接池 ====================

async def init_db():
    """初始化数据库连接池"""
    global pool
    import core.config as config_module
    pool = AsyncConnectionPool(
        conninfo=f"dbname={DB_CONFIG['dbname']} host={DB_CONFIG['host']} port={DB_CONFIG['port']} user={DB_CONFIG['user']}",
        min_size=DB_CONFIG["min_size"],
        max_size=DB_CONFIG["max_size"],
    )
    await pool.open()
    # 设置到 config 模块中
    config_module.pool = pool
    logger.info("PostgreSQL 连接池初始化成功")


async def close_db():
    """关闭数据库连接池"""
    global pool
    import core.config as config_module
    if pool:
        await pool.close()
        logger.info("PostgreSQL 连接池已关闭")


async def get_db_connection() -> AsyncGenerator:
    """获取数据库连接（依赖注入）"""
    global pool
    async with pool.connection() as conn:
        yield conn


# ==================== 数据库表结构初始化 ====================

async def setup_database():
    """初始化数据库表结构"""
    global pool
    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 启用 pgvector 扩展
            await cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

            # 用户表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT,
                    password_hash TEXT NOT NULL,
                    role TEXT DEFAULT 'member',
                    is_active BOOLEAN DEFAULT true,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 部门表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS departments (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    name TEXT NOT NULL,
                    code TEXT NOT NULL UNIQUE,
                    description TEXT,
                    parent_id TEXT REFERENCES departments(id) ON DELETE SET NULL,
                    sort_order INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT true,
                    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 用户部门关联表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS user_departments (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    department_id TEXT NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
                    position TEXT,  -- 职位
                    is_primary BOOLEAN DEFAULT true,  -- 是否为主部门
                    is_manager BOOLEAN DEFAULT false,  -- 是否为部门管理员
                    joined_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(user_id, department_id)
                )
            """)

            # 部门索引
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_departments_user_id ON user_departments(user_id)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_departments_dept_id ON user_departments(department_id)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_departments_parent_id ON departments(parent_id)
            """)

            # MCP 配置表 - 存储远程 MCP 服务器配置
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS mcp_configs (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    name TEXT NOT NULL,
                    connection_type TEXT NOT NULL,  -- 'http', 'ws', 'sse', 'stdio'
                    url TEXT,                      -- HTTP/SSE/WS 模式的 URL
                    command TEXT,                  -- stdio 模式的命令
                    args JSONB,                    -- stdio 模式的命令参数
                    headers JSONB,                 -- 自定义请求头
                    auth_token TEXT,              -- Bearer Token
                    api_key TEXT,                 -- API Key
                    is_active BOOLEAN DEFAULT true,
                    created_by TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 知识库表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_bases (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    embedding_model TEXT DEFAULT 'nomic-embed-text',
                    chunk_size INTEGER DEFAULT 512,
                    chunk_overlap INTEGER DEFAULT 50,
                    owner_id TEXT,
                    is_public BOOLEAN DEFAULT false,
                    allow_public_upload BOOLEAN DEFAULT false,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 迁移：为已存在的知识库表添加公开相关字段
            try:
                await cur.execute("""
                    ALTER TABLE knowledge_bases
                    ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT false
                """)
                await cur.execute("""
                    ALTER TABLE knowledge_bases
                    ADD COLUMN IF NOT EXISTS allow_public_upload BOOLEAN DEFAULT false
                """)
            except Exception:
                pass  # 字段可能已存在

            # 文档表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    kb_id TEXT NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    type TEXT,
                    size INTEGER,
                    status TEXT DEFAULT 'pending',
                    file_path TEXT,
                    file_hash TEXT,
                    content TEXT,
                    chunk_count INTEGER DEFAULT 0,
                    error_message TEXT,
                    created_by TEXT REFERENCES users(id) ON DELETE SET NULL,
                    updated_by TEXT REFERENCES users(id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 分块表 (带 pgvector)
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding vector(768),
                    metadata JSONB,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # HNSW 索引 (专业级向量搜索)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS chunks_embedding_idx
                ON chunks USING hnsw (embedding vector_cosine_ops)
            """)

            # 应用表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    model TEXT DEFAULT 'qwen2.5:7b',
                    knowledge_base_ids TEXT[],
                    mcp_config_ids TEXT[],              -- 关联的 MCP 服务器配置 ID 列表
                    is_public BOOLEAN DEFAULT false,
                    owner_id TEXT,
                    system_prompt TEXT,
                    welcome_message TEXT,
                    share_id TEXT UNIQUE,
                    share_password TEXT,
                    temperature DECIMAL(3,2) DEFAULT 0.7,
                    max_tokens INTEGER DEFAULT 2048,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 会话表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    app_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                    title TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 消息表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 任务表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    progress INTEGER DEFAULT 0,
                    result JSONB,
                    error_message TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                )
            """)

            # 消息反馈表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS message_feedback (
                    id TEXT PRIMARY KEY,
                    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL UNIQUE,
                    feedback_type TEXT NOT NULL,
                    comment TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 会话统计表（用于记录每日对话数）
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS application_conversations (
                    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                    date DATE NOT NULL,
                    message_count INTEGER DEFAULT 1,
                    PRIMARY KEY (application_id, date)
                )
            """)

            # 聊天会话表 - 支持 Dify 风格的多轮对话管理
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                    user_id TEXT,
                    title TEXT,
                    message_count INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 为 chat_sessions 创建索引
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_sessions_app_user ON chat_sessions(application_id, user_id)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at DESC)
            """)

            # 聊天消息表 - 增强版，支持多轮对话和上下文
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL UNIQUE,
                    user_message TEXT NOT NULL,
                    ai_response TEXT NOT NULL,
                    sources JSONB DEFAULT '[]'::jsonb,
                    feedback INTEGER,
                    feedback_text TEXT,
                    tokens_used INTEGER DEFAULT 0,
                    model_used TEXT,
                    ip_address TEXT,
                    user_agent TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 为 chat_messages 创建索引
            await cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'chat_messages' AND column_name = 'session_id'
            """)
            has_session_id = await cur.fetchone()
            if has_session_id:
                await cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, created_at)
                """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_chat_messages_app ON chat_messages(application_id, created_at DESC)
            """)
            await cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'chat_messages' AND column_name = 'feedback'
            """)
            has_feedback = await cur.fetchone()
            if has_feedback:
                await cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_chat_messages_feedback ON chat_messages(feedback) WHERE feedback IS NOT NULL
                """)

            # 异步任务表 - 用于后台任务管理
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS async_tasks (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    progress FLOAT DEFAULT 0.0,
                    result JSONB,
                    error TEXT,
                    metadata JSONB DEFAULT '{}'::jsonb,
                    created_by TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    completed_at TIMESTAMPTZ
                )
            """)

            # 为 async_tasks 创建索引
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_async_tasks_status ON async_tasks(status, created_at DESC)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_async_tasks_type ON async_tasks(type, status)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_async_tasks_created_by ON async_tasks(created_by)
            """)

            # 工作流表 - 参考 Dify 的工作流系统
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    name TEXT NOT NULL,
                    description TEXT,
                    definition JSONB NOT NULL,  -- 工作流定义（节点、连线、变量）
                    version INTEGER DEFAULT 1,
                    is_published BOOLEAN DEFAULT false,
                    is_active BOOLEAN DEFAULT true,
                    created_by TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 为 workflows 创建索引
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_workflows_created_by ON workflows(created_by)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_workflows_active ON workflows(is_active, is_published)
            """)

            # 为 workflows 表添加 icon 列（如果不存在）
            await cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'workflows' AND column_name = 'icon') THEN
                        ALTER TABLE workflows ADD COLUMN icon TEXT;
                    END IF;
                END $$;
            """)

            # 为 workflows 表添加 share_id / is_public / share_password 列（如果不存在）
            await cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'workflows' AND column_name = 'share_id') THEN
                        ALTER TABLE workflows ADD COLUMN share_id TEXT UNIQUE;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'workflows' AND column_name = 'is_public') THEN
                        ALTER TABLE workflows ADD COLUMN is_public BOOLEAN DEFAULT false;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'workflows' AND column_name = 'share_password') THEN
                        ALTER TABLE workflows ADD COLUMN share_password TEXT;
                    END IF;
                END $$;
            """)

            # 工作流版本历史表（用于发布版本管理）
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS workflow_versions (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
                    version INTEGER NOT NULL,
                    definition JSONB NOT NULL,
                    description TEXT,
                    is_published BOOLEAN DEFAULT false,
                    created_by TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 为 workflow_versions 创建索引
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_workflow_versions_workflow ON workflow_versions(workflow_id, created_at DESC)
            """)

            # 工作流执行记录表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS workflow_executions (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    workflow_id TEXT NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'running',
                    inputs JSONB DEFAULT '{}'::jsonb,
                    outputs JSONB,
                    error TEXT,
                    started_at TIMESTAMPTZ DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    duration_ms INTEGER,
                    -- 增强字段
                    paused_at TIMESTAMPTZ,              -- 暂停时间
                    resumed_at TIMESTAMPTZ,             -- 恢复时间
                    paused_by TEXT,                      -- 暂停者用户ID
                    current_node_id TEXT,                -- 当前执行到的节点ID（用于暂停后恢复）
                    pending_human_input_node_id TEXT,    -- 等待人工输入的节点ID
                    human_input JSONB,                   -- 人工输入的数据
                    execution_context JSONB,             -- 执行上下文快照（用于恢复）
                    created_by TEXT                      -- 创建者用户ID
                )
            """)

            # 为 workflow_executions 创建索引
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow ON workflow_executions(workflow_id, started_at DESC)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_workflow_executions_status ON workflow_executions(status, started_at DESC)
            """)

            # 审计日志表 - 记录所有重要的修改操作
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
                    entity_type TEXT NOT NULL,              -- 实体类型: 'knowledge_base', 'document', 'chunk', 'application'
                    entity_id TEXT NOT NULL,               -- 实体ID
                    action TEXT NOT NULL,                  -- 操作类型: 'create', 'update', 'delete'
                    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                    username TEXT,                         -- 用户名快照（防止用户被删除后无法显示）
                    changes JSONB,                         -- 变更内容: {field_name: {old: value, new: value}}
                    ip_address TEXT,
                    user_agent TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 为 audit_logs 创建索引
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_logs_entity ON audit_logs(entity_type, entity_id)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_logs_user ON audit_logs(user_id, created_at DESC)
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC)
            """)

            # 系统配置表（单例配置，id=1）
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_config (
                    id TEXT PRIMARY KEY,
                    site_name TEXT,
                    site_title TEXT,
                    logo TEXT,
                    favicon TEXT,
                    primary_color TEXT,
                    theme TEXT DEFAULT 'light',
                    -- 飞书集成配置
                    feishu_app_id TEXT,
                    feishu_app_secret TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            # 资源授权表（将知识库、应用等资源授权给用户）
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS resource_permissions (
                    id TEXT PRIMARY KEY,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    granted_by TEXT,
                    granted_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(resource_type, resource_id, user_id)
                )
            """)

            # SSO 配置表
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS sso_configs (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    enabled BOOLEAN DEFAULT TRUE,
                    config JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(provider)
                )
            """)

            # 用户绑定表（第三方账号绑定）
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS user_bindings (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_user_id TEXT NOT NULL,
                    provider_email TEXT,
                    provider_username TEXT,
                    avatar_url TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(provider, provider_user_id)
                )
            """)

            # 迁移：为 applications 表添加新列（如果不存在）
            try:
                # 检查列是否存在
                await cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'applications' AND column_name = 'share_id'
                """)
                if not await cur.fetchone():
                    await cur.execute("ALTER TABLE applications ADD COLUMN share_id TEXT UNIQUE")
                    logger.info("已添加 applications.share_id 列")

                await cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'applications' AND column_name = 'share_password'
                """)
                if not await cur.fetchone():
                    await cur.execute("ALTER TABLE applications ADD COLUMN share_password TEXT")
                    logger.info("已添加 applications.share_password 列")

                await cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'applications' AND column_name = 'temperature'
                """)
                if not await cur.fetchone():
                    await cur.execute("ALTER TABLE applications ADD COLUMN temperature DECIMAL(3,2) DEFAULT 0.7")
                    logger.info("已添加 applications.temperature 列")

                await cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'applications' AND column_name = 'max_tokens'
                """)
                if not await cur.fetchone():
                    await cur.execute("ALTER TABLE applications ADD COLUMN max_tokens INTEGER DEFAULT 2048")
                    logger.info("已添加 applications.max_tokens 列")

                # 为 applications 表添加 mcp_config_ids 字段
                await cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'applications' AND column_name = 'mcp_config_ids'
                """)
                if not await cur.fetchone():
                    await cur.execute("ALTER TABLE applications ADD COLUMN mcp_config_ids TEXT[]")
                    logger.info("已添加 applications.mcp_config_ids 列")

                # 为 users 表添加 avatar 字段
                await cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'users' AND column_name = 'avatar'
                """)
                if not await cur.fetchone():
                    await cur.execute("ALTER TABLE users ADD COLUMN avatar TEXT")
                    logger.info("已添加 users.avatar 列")

                # 为 system_config 表添加飞书配置字段
                await cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'system_config' AND column_name = 'feishu_app_id'
                """)
                if not await cur.fetchone():
                    await cur.execute("ALTER TABLE system_config ADD COLUMN feishu_app_id TEXT")
                    await cur.execute("ALTER TABLE system_config ADD COLUMN feishu_app_secret TEXT")
                    logger.info("已添加 system_config 飞书配置字段")

                # 为 documents 表的 kb_id 字段允许 NULL（支持文档池）
                await cur.execute("""
                    SELECT is_nullable FROM information_schema.columns
                    WHERE table_name = 'documents' AND column_name = 'kb_id'
                """)
                row = await cur.fetchone()
                if row and row[0] == 'NO':
                    await cur.execute("ALTER TABLE documents ALTER COLUMN kb_id DROP NOT NULL")
                    logger.info("已修改 documents.kb_id 允许 NULL（支持文档池）")

                # 为 chat_messages 表添加 session_id 字段（如果不存在）
                await cur.execute("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'chat_messages' AND column_name = 'session_id'
                """)
                if not await cur.fetchone():
                    # 先添加 chat_sessions 表（如果不存在）
                    await cur.execute("""
                        SELECT table_name FROM information_schema.tables
                        WHERE table_name = 'chat_sessions'
                    """)
                    if not await cur.fetchone():
                        await cur.execute("""
                            CREATE TABLE chat_sessions (
                                id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                                application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
                                user_id TEXT,
                                title TEXT,
                                message_count INTEGER DEFAULT 0,
                                total_tokens INTEGER DEFAULT 0,
                                metadata JSONB DEFAULT '{}'::jsonb,
                                created_at TIMESTAMPTZ DEFAULT NOW(),
                                updated_at TIMESTAMPTZ DEFAULT NOW()
                            )
                        """)
                        logger.info("已创建 chat_sessions 表")
                        await cur.execute("""
                            CREATE INDEX IF NOT EXISTS idx_chat_sessions_app ON chat_sessions(application_id)
                        """)
                        await cur.execute("""
                            CREATE INDEX IF NOT EXISTS idx_chat_sessions_app_user ON chat_sessions(application_id, user_id)
                        """)
                        await cur.execute("""
                            CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at DESC)
                        """)

                    # 检查是否有 conversation_id 列（旧列名）
                    await cur.execute("""
                        SELECT column_name FROM information_schema.columns
                        WHERE table_name = 'chat_messages' AND column_name = 'conversation_id'
                    """)
                    has_conv_id = await cur.fetchone()

                    if has_conv_id:
                        # 重命名 conversation_id 为 session_id
                        await cur.execute("ALTER TABLE chat_messages RENAME COLUMN conversation_id TO session_id")
                        logger.info("已重命名 chat_messages.conversation_id 为 session_id")
                    else:
                        # 添加 session_id 列
                        await cur.execute("ALTER TABLE chat_messages ADD COLUMN session_id TEXT")
                        logger.info("已添加 chat_messages.session_id 列")

                    # 添加外键约束
                    try:
                        await cur.execute("""
                            ALTER TABLE chat_messages ADD CONSTRAINT fk_chat_messages_session
                            FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
                        """)
                        logger.info("已添加 chat_messages.session_id 外键约束")
                    except Exception:
                        pass  # 约束可能已存在

                    # 创建索引
                    await cur.execute("""
                        CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, created_at)
                    """)
            except Exception as e:
                logger.warning(f"列迁移跳过: {e}")

            # ==================== RBAC 表 ====================

            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_menu (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    name TEXT NOT NULL,
                    permission TEXT,
                    type INTEGER NOT NULL,
                    sort INTEGER DEFAULT 0,
                    parent_id TEXT DEFAULT '0',
                    path TEXT,
                    icon TEXT,
                    component TEXT,
                    component_name TEXT,
                    status INTEGER DEFAULT 0,
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

            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_role (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    name TEXT NOT NULL,
                    code TEXT UNIQUE NOT NULL,
                    sort INTEGER DEFAULT 0,
                    status INTEGER DEFAULT 0,
                    type INTEGER DEFAULT 2,
                    data_scope INTEGER DEFAULT 1,
                    remark TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW(),
                    deleted_at TIMESTAMPTZ
                )
            """)
            await cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_system_role_code ON system_role(code) WHERE deleted_at IS NULL
            """)

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

            # 用户表扩展字段（幂等）
            try:
                await cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS dept_id TEXT REFERENCES departments(id) ON DELETE SET NULL")
                await cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname TEXT")
                await cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mobile TEXT")
                await cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar TEXT")
                await cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS status INTEGER DEFAULT 0")
                await cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
                await cur.execute("CREATE INDEX IF NOT EXISTS idx_users_dept_id ON users(dept_id) WHERE deleted_at IS NULL")
                await cur.execute("CREATE INDEX IF NOT EXISTS idx_users_status ON users(status) WHERE deleted_at IS NULL")
            except Exception as e:
                logger.warning(f"用户表字段扩展跳过: {e}")

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

            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_user_post (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    post_id TEXT NOT NULL REFERENCES system_post(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(user_id, post_id)
                )
            """)

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
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_system_user_session_user_id ON system_user_session(user_id)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_system_user_session_token ON system_user_session(token)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_system_user_session_expires_at ON system_user_session(expires_at)")

            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_operate_log (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                    username TEXT,
                    module TEXT,
                    operation TEXT,
                    request_method TEXT,
                    request_url TEXT,
                    request_ip TEXT,
                    user_agent TEXT,
                    request_params TEXT,
                    response_data TEXT,
                    status INTEGER DEFAULT 0,
                    error_msg TEXT,
                    execute_time INTEGER,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_system_operate_log_user_id ON system_operate_log(user_id)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_system_operate_log_created_at ON system_operate_log(created_at)")

            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_login_log (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    username TEXT,
                    status INTEGER DEFAULT 0,
                    ip_address TEXT,
                    user_agent TEXT,
                    error_msg TEXT,
                    login_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_system_login_log_username ON system_login_log(username)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_system_login_log_created_at ON system_login_log(login_at)")

            await cur.execute("""
                CREATE TABLE IF NOT EXISTS system_resource_permission (
                    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                    role_id TEXT REFERENCES system_role(id) ON DELETE CASCADE,
                    dept_id TEXT REFERENCES departments(id) ON DELETE CASCADE,
                    permissions TEXT NOT NULL,
                    granted_by TEXT REFERENCES users(id),
                    granted_at TIMESTAMPTZ DEFAULT NOW(),
                    expires_at TIMESTAMPTZ,
                    CHECK (user_id IS NOT NULL OR role_id IS NOT NULL OR dept_id IS NOT NULL)
                )
            """)
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_system_resource_permission_resource ON system_resource_permission(resource_type, resource_id)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_system_resource_permission_user_id ON system_resource_permission(user_id)")
            await cur.execute("CREATE INDEX IF NOT EXISTS idx_system_resource_permission_role_id ON system_resource_permission(role_id)")

            await conn.commit()

            logger.info("数据库表结构初始化完成")


# ==================== 默认数据创建 ====================

async def create_default_user():
    """创建默认管理员用户"""
    global pool
    from .utils import hash_password

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT COUNT(*) as count FROM users WHERE username = 'admin'")
            result = await cur.fetchone()

            if result['count'] == 0:
                admin_id = str(uuid.uuid4())
                password_hash = hash_password("admin123")
                await cur.execute("""
                    INSERT INTO users (id, username, email, password_hash, role)
                    VALUES (%s, %s, 'admin@example.com', %s, 'super_admin')
                """, (admin_id, 'admin', password_hash))
                await conn.commit()
                logger.info("默认管理员用户创建成功: admin / admin123")
            else:
                logger.info("管理员用户已存在")


async def create_default_system_config():
    """创建默认系统配置"""
    global pool

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT COUNT(*) as count FROM system_config WHERE id = '1'")
            result = await cur.fetchone()

            if result['count'] == 0:
                await cur.execute("""
                    INSERT INTO system_config (id, site_name, site_title, logo, favicon, primary_color, theme)
                    VALUES ('1', 'AI Knowledge Base', 'AI Knowledge Base', NULL, NULL, '#3b82f6', 'light')
                """)
                await conn.commit()
                logger.info("默认系统配置创建成功")
            else:
                logger.info("系统配置已存在")


async def init_rbac_default_data():
    """初始化 RBAC 默认角色、菜单、岗位数据（幂等）"""
    global pool

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            # 默认角色
            default_roles = [
                ('role_super_admin', '超级管理员', 'super_admin', 1, 0, 1, 1, '系统超级管理员，拥有所有权限'),
                ('role_admin',       '管理员',     'admin',       2, 0, 1, 4, '部门管理员'),
                ('role_member',      '普通成员',   'member',      3, 0, 1, 5, '普通用户'),
                ('role_viewer',      '访客',       'viewer',      4, 0, 1, 5, '只读用户'),
            ]
            for r in default_roles:
                await cur.execute("""
                    INSERT INTO system_role (id, name, code, sort, status, type, data_scope, remark)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (code) DO UPDATE SET
                        name = EXCLUDED.name, sort = EXCLUDED.sort,
                        data_scope = EXCLUDED.data_scope, remark = EXCLUDED.remark
                """, r)

            # 默认菜单
            default_menus = [
                ('menu_kb',          '知识库管理', 'kb:manage',              2, 10, '0',        '/knowledge',          'DatabaseOutlined'),
                ('menu_kb_create',   '创建知识库', 'kb:create',              3, 1,  'menu_kb',   '',                    ''),
                ('menu_kb_update',   '编辑知识库', 'kb:update',              3, 2,  'menu_kb',   '',                    ''),
                ('menu_kb_delete',   '删除知识库', 'kb:delete',              3, 3,  'menu_kb',   '',                    ''),
                ('menu_kb_upload',   '上传文档',   'kb:document:upload',     3, 4,  'menu_kb',   '',                    ''),
                ('menu_app',         'AI应用管理', 'app:manage',             2, 20, '0',        '/applications',        'AppstoreOutlined'),
                ('menu_app_create',  '创建应用',   'app:create',             3, 1,  'menu_app',  '',                    ''),
                ('menu_app_update',  '编辑应用',   'app:update',             3, 2,  'menu_app',  '',                    ''),
                ('menu_app_delete',  '删除应用',   'app:delete',             3, 3,  'menu_app',  '',                    ''),
                ('menu_user',        '用户管理',   'system:user:manage',     2, 30, '0',        '/settings/users',      'UserOutlined'),
                ('menu_user_create', '新建用户',   'system:user:create',     3, 1,  'menu_user', '',                    ''),
                ('menu_user_update', '编辑用户',   'system:user:update',     3, 2,  'menu_user', '',                    ''),
                ('menu_user_delete', '删除用户',   'system:user:delete',     3, 3,  'menu_user', '',                    ''),
                ('menu_role',        '角色管理',   'system:role:manage',     2, 31, '0',        '/settings/roles',      'TeamOutlined'),
                ('menu_role_create', '新建角色',   'system:role:create',     3, 1,  'menu_role', '',                    ''),
                ('menu_role_update', '编辑角色',   'system:role:update',     3, 2,  'menu_role', '',                    ''),
                ('menu_role_delete', '删除角色',   'system:role:delete',     3, 3,  'menu_role', '',                    ''),
                ('menu_role_assign', '分配权限',   'system:role:assign',     3, 4,  'menu_role', '',                    ''),
                ('menu_dept',        '部门管理',   'system:dept:manage',     2, 32, '0',        '/settings/departments','ApartmentOutlined'),
                ('menu_system',      '系统设置',   'system:config',          2, 40, '0',        '/settings/system',     'SettingOutlined'),
                ('menu_audit',       '审计日志',   'system:audit:view',      2, 41, '0',        '/settings/audit',      'HistoryOutlined'),
            ]
            for m in default_menus:
                await cur.execute("""
                    INSERT INTO system_menu (id, name, permission, type, sort, parent_id, path, icon, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0)
                    ON CONFLICT DO NOTHING
                """, m)

            # 超级管理员拥有所有菜单
            await cur.execute("""
                INSERT INTO system_role_menu (role_id, menu_id)
                SELECT 'role_super_admin', id FROM system_menu
                ON CONFLICT (role_id, menu_id) DO NOTHING
            """)

            # 管理员菜单
            for mid in ('menu_kb','menu_kb_create','menu_kb_update','menu_kb_upload',
                        'menu_app','menu_app_create','menu_app_update',
                        'menu_user','menu_user_update','menu_dept'):
                await cur.execute("""
                    INSERT INTO system_role_menu (role_id, menu_id)
                    VALUES ('role_admin', %s) ON CONFLICT DO NOTHING
                """, (mid,))

            # 普通成员菜单
            for mid in ('menu_kb','menu_kb_upload','menu_app'):
                await cur.execute("""
                    INSERT INTO system_role_menu (role_id, menu_id)
                    VALUES ('role_member', %s) ON CONFLICT DO NOTHING
                """, (mid,))

            # 默认岗位
            default_posts = [
                ('ceo',       'CEO',        1),
                ('cto',       'CTO',        2),
                ('manager',   '经理',       10),
                ('developer', '开发工程师', 20),
                ('tester',    '测试工程师', 30),
                ('operator',  '运维工程师', 40),
            ]
            for p in default_posts:
                await cur.execute("""
                    INSERT INTO system_post (code, name, sort, status)
                    VALUES (%s, %s, %s, 0)
                    ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
                """, p)

            await conn.commit()
            logger.info("RBAC 默认数据初始化完成")
