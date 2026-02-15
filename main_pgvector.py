"""
AI Knowledge Base - 生产级后端服务 (PostgreSQL + pgvector)

功能特性:
1. PostgreSQL + pgvector - 专业级向量数据库
2. ACID 事务 - 数据安全可靠
3. 异步任务处理 - 文档后台解析
4. 增量更新 - 智能文档更新
5. 智能分块 - 基于文档结构
6. 多格式支持 - TXT, MD, JSON, PDF, DOCX, XLSX, PPTX, HTML, CSV, XML

适用场景: 政府项目、企业级应用
"""
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
import json
from datetime import datetime, date
from json.encoder import JSONEncoder

# ==================== 导入配置和核心模块 ====================
from core.config import (
    UPLOAD_DIR,
    OLLAMA_BASE_URL,
    OLLAMA_EMBEDDING_MODEL,
    OLLAMA_CHAT_MODEL,
    RERANK_MODEL,
    VECTOR_STORE_TYPE,
    VECTOR_STORE_CONFIG,
    MILVUS_CONFIG,
)
from core.database import (
    init_db,
    close_db,
    setup_database,
    create_default_user,
    create_default_system_config,
)

# ==================== 导入服务层模块 ====================
from services import (
    CacheManager,
    RateLimiter,
    TaskQueue,
    WorkflowEngine,
    MCPServer,
    EmbeddingService,
    RerankService,
    DocumentProcessor,
    MonitoringService,
)

# ==================== 导入API路由模块 ====================
from api import (
    auth_router,
    get_current_user,
    users_router,
    profile_router,
    roles_router,
    departments_router,
    audit_router,
    knowledge_router,
    applications_router,
    share_router,
    documents_router,
    pool_router,
    chat_router,
    workflows_router,
    system_router,
    sso_router,
    mcp_router,
    mcp_services_router,
    monitoring_router,
    vector_store_router,
    feishu_router,
)

# ==================== 配置日志 ====================
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(asctime)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ==================== 自定义 JSON 编码器 ====================
# 处理 datetime 对象
class CustomJSONEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        return super().default(obj)


# 自定义 JSONResponse
class CustomJSONResponse(JSONResponse):
    def render(self, content):
        return json.dumps(content, cls=CustomJSONEncoder, ensure_ascii=False).encode('utf-8')


# ==================== FastAPI 应用 ====================
app = FastAPI(
    title="AI Knowledge Base API",
    version="2.0.0",
    description="生产级 AI 知识库后端服务 (PostgreSQL + pgvector)",
    default_response_class=CustomJSONResponse
)


# ==================== 注册路由 ====================
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(profile_router)
app.include_router(roles_router)
app.include_router(departments_router)
app.include_router(audit_router)
app.include_router(knowledge_router)
app.include_router(applications_router)
app.include_router(share_router)
app.include_router(documents_router)
app.include_router(pool_router)
app.include_router(chat_router)
app.include_router(workflows_router)
app.include_router(system_router)
app.include_router(sso_router)
app.include_router(mcp_router)
app.include_router(mcp_services_router)
app.include_router(monitoring_router)
app.include_router(vector_store_router)
app.include_router(feishu_router)


# ==================== 分享页面 HTML ====================

from fastapi.responses import HTMLResponse

SHARE_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{app_name} - AI 对话</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
        }}
        .chat-container {{
            width: 100%;
            max-width: 800px;
            height: 90vh;
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            margin: 20px;
        }}
        .chat-header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            text-align: center;
        }}
        .chat-header h1 {{
            font-size: 24px;
            margin-bottom: 5px;
        }}
        .chat-header p {{
            font-size: 14px;
            opacity: 0.9;
        }}
        .chat-messages {{
            flex: 1;
            overflow-y: auto;
            padding: 20px;
            background: #f8f9fa;
        }}
        .message {{
            margin-bottom: 15px;
            display: flex;
            flex-direction: column;
        }}
        .message.user {{
            align-items: flex-end;
        }}
        .message.assistant {{
            align-items: flex-start;
        }}
        .message-content {{
            max-width: 80%;
            padding: 12px 16px;
            border-radius: 12px;
            word-wrap: break-word;
        }}
        .message.user .message-content {{
            background: #667eea;
            color: white;
        }}
        .message.assistant .message-content {{
            background: white;
            color: #333;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
        }}
        .chat-input {{
            padding: 20px;
            background: white;
            border-top: 1px solid #e9ecef;
            display: flex;
            gap: 10px;
        }}
        .chat-input input {{
            flex: 1;
            padding: 12px 16px;
            border: 2px solid #e9ecef;
            border-radius: 24px;
            font-size: 16px;
            outline: none;
            transition: border-color 0.2s;
        }}
        .chat-input input:focus {{
            border-color: #667eea;
        }}
        .chat-input button {{
            padding: 12px 24px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 24px;
            cursor: pointer;
            font-size: 16px;
            font-weight: 600;
            transition: transform 0.2s;
        }}
        .chat-input button:hover {{
            transform: scale(1.05);
        }}
        .chat-input button:disabled {{
            opacity: 0.6;
            cursor: not-allowed;
        }}
        .typing {{
            display: flex;
            gap: 5px;
            padding: 12px 16px;
        }}
        .typing span {{
            width: 8px;
            height: 8px;
            background: #ccc;
            border-radius: 50%;
            animation: typing 1.4s infinite;
        }}
        .typing span:nth-child(2) {{
            animation-delay: 0.2s;
        }}
        .typing span:nth-child(3) {{
            animation-delay: 0.4s;
        }}
        @keyframes typing {{
            0%, 60%, 100% {{ transform: translateY(0); }}
            30% {{ transform: translateY(-10px); }}
        }}
        .password-modal {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.5);
            display: flex;
            justify-content: center;
            align-items: center;
            z-index: 1000;
        }}
        .password-modal-content {{
            background: white;
            padding: 30px;
            border-radius: 16px;
            text-align: center;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
        }}
        .password-modal-content h2 {{
            margin-bottom: 20px;
            color: #333;
        }}
        .password-modal-content input {{
            width: 100%;
            padding: 12px 16px;
            border: 2px solid #e9ecef;
            border-radius: 8px;
            font-size: 16px;
            margin-bottom: 15px;
        }}
        .password-modal-content button {{
            padding: 12px 30px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 16px;
            font-weight: 600;
        }}
        .hidden {{
            display: none !important;
        }}
        @media (max-width: 768px) {{
            .chat-container {{
                height: 100vh;
                margin: 0;
                border-radius: 0;
            }}
            .message-content {{
                max-width: 90%;
            }}
        }}
    </style>
</head>
<body>
    <div class="chat-container">
        <div class="chat-header">
            <h1>{app_name}</h1>
            <p>{app_description}</p>
        </div>
        <div class="chat-messages" id="messages">
            <div class="message assistant">
                <div class="message-content">{welcome_message}</div>
            </div>
        </div>
        <div class="chat-input">
            <input type="text" id="messageInput" placeholder="输入消息..." />
            <button onclick="sendMessage()">发送</button>
        </div>
    </div>

    <div class="password-modal hidden" id="passwordModal">
        <div class="password-modal-content">
            <h2>请输入访问密码</h2>
            <input type="password" id="passwordInput" placeholder="密码" />
            <button onclick="submitPassword()">确认</button>
        </div>
    </div>

    <script>
        const shareId = '{share_id}';
        const apiUrl = '/api/v1/share/' + shareId;
        let hasPassword = {has_password};
        let passwordVerified = false;

        // 检查是否需要密码
        if (hasPassword) {{
            document.getElementById('passwordModal').classList.remove('hidden');
        }}

        function submitPassword() {{
            const password = document.getElementById('passwordInput').value;
            fetch(apiUrl, {{
                headers: {{
                    'X-Share-Password': password
                }}
            }})
            .then(res => {{
                if (res.ok) {{
                    passwordVerified = true;
                    document.getElementById('passwordModal').classList.add('hidden');
                    hasPassword = false;
                }} else {{
                    alert('密码错误');
                }}
            }});
        }}

        document.getElementById('messageInput').addEventListener('keypress', function(e) {{
            if (e.key === 'Enter') sendMessage();
        }});

        function sendMessage() {{
            const input = document.getElementById('messageInput');
            const message = input.value.trim();
            if (!message) return;

            // 添加用户消息
            addMessage('user', message);
            input.value = '';

            // 显示加载动画
            const loadingId = addTyping();

            // 发送消息到后端
            const headers = {{ 'Content-Type': 'application/json' }};
            if (hasPassword && !passwordVerified) {{
                alert('请先输入密码');
                return;
            }}
            if (passwordVerified) {{
                headers['X-Share-Password'] = document.getElementById('passwordInput').value;
            }}

            fetch(apiUrl + '/chat', {{
                method: 'POST',
                headers: headers,
                body: JSON.stringify({{ message: message }})
            }})
            .then(res => res.json())
            .then(data => {{
                removeTyping(loadingId);
                if (data.reply) {{
                    addMessage('assistant', data.reply);
                }}
            }})
            .catch(err => {{
                removeTyping(loadingId);
                addMessage('assistant', '抱歉，发生了一些错误，请稍后重试。');
            }});
        }}

        function addMessage(role, content) {{
            const messagesDiv = document.getElementById('messages');
            const messageDiv = document.createElement('div');
            messageDiv.className = 'message ' + role;
            messageDiv.innerHTML = '<div class="message-content">' + escapeHtml(content) + '</div>';
            messagesDiv.appendChild(messageDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }}

        function addTyping() {{
            const messagesDiv = document.getElementById('messages');
            const typingDiv = document.createElement('div');
            typingDiv.className = 'message assistant';
            typingDiv.id = 'typing-' + Date.now();
            typingDiv.innerHTML = '<div class="typing"><span></span><span></span><span></span></div>';
            messagesDiv.appendChild(typingDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
            return typingDiv.id;
        }}

        function removeTyping(id) {{
            const el = document.getElementById(id);
            if (el) el.remove();
        }}

        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }}
    </script>
</body>
</html>
"""


@app.get("/share/{share_id}", response_class=HTMLResponse)
async def get_share_page(share_id: str):
    """获取分享应用的 HTML 页面"""
    from psycopg.rows import dict_row

    if not pool:
        return "<h1>服务未初始化</h1>"

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("""
                SELECT name, description, welcome_message, share_password
                FROM applications WHERE share_id = %s AND is_public = true
            """, (share_id,))
            app = await cur.fetchone()

    if not app:
        return "<h1>分享不存在或已失效</h1>"

    html = SHARE_HTML_TEMPLATE.format(
        app_name=app.get('name', 'AI 助手'),
        app_description=app.get('description') or '',
        welcome_message=app.get('welcome_message') or '你好，我是AI助手，请问有什么可以帮你的？',
        share_id=share_id,
        has_password='true' if app.get('share_password') else 'false'
    )
    return html


# ==================== 静态文件和中间件 ====================

# 挂载静态文件目录
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static/files", StaticFiles(directory=str(UPLOAD_DIR)), name="files")

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 全局变量 ====================
# 这些将在启动事件中初始化
pool = None
cache = None
rate_limiter = None
task_queue = None
workflow_engine = None
mcp_server = None
monitoring_service = None
vector_store_instance = None
embedding_service = None
rerank_service = None
document_processor = None


# ==================== 启动和关闭事件 ====================

@app.on_event("startup")
async def startup_event():
    """应用启动时的初始化"""
    global pool, cache, rate_limiter, task_queue, workflow_engine
    global mcp_server, monitoring_service, vector_store_instance
    global embedding_service, rerank_service, document_processor

    logger.info("应用启动中...")

    # 初始化缓存
    cache = CacheManager()
    await cache.init_redis()

    # 初始化限流器
    rate_limiter = RateLimiter(cache)

    # 初始化数据库
    await init_db()
    await setup_database()
    await create_default_user()
    await create_default_system_config()

    # 获取数据库连接池并设置为全局变量
    # core.config.pool 在 init_db() 中被正确设置
    from core import config
    pool = config.pool
    logger.info(f"Pool 设置完成: pool={pool is not None}")

    # 初始化任务队列
    task_queue = TaskQueue(cache)
    task_queue.pool_ref = pool
    logger.info("任务队列初始化完成")

    # 初始化向量存储
    from vector_store import init_vector_store, VectorConfig, MetricType

    metric_map = {
        "cosine": MetricType.COSINE,
        "l2": MetricType.L2,
        "ip": MetricType.IP,
    }

    vector_config = VectorConfig(
        dimension=VECTOR_STORE_CONFIG["dimension"],
        metric_type=metric_map.get(VECTOR_STORE_CONFIG["metric_type"], MetricType.COSINE),
        index_type=VECTOR_STORE_CONFIG["index_type"],
    )

    try:
        if VECTOR_STORE_TYPE == "milvus":
            vector_store_instance = await init_vector_store(
                store_type="milvus",
                config=vector_config,
                milvus_uri=MILVUS_CONFIG["uri"],
                token=MILVUS_CONFIG["token"] or None,
                collection_name=MILVUS_CONFIG["collection_name"],
            )
            logger.info(f"向量存储初始化完成: Milvus ({MILVUS_CONFIG['uri']})")
        else:
            vector_store_instance = await init_vector_store(
                store_type="pgvector",
                config=vector_config,
                pool=pool,
            )
            logger.info("向量存储初始化完成: pgvector")
    except Exception as e:
        logger.warning(f"向量存储初始化失败，将使用降级方案: {e}")
        vector_store_instance = None

    # 更新全局配置中的实例
    from core import config
    config.pool = pool
    config.cache = cache
    config.rate_limiter = rate_limiter
    config.task_queue = task_queue
    config.vector_store_instance = vector_store_instance

    # 初始化工作流引擎
    workflow_engine = WorkflowEngine(pool, cache)
    logger.info("工作流引擎初始化完成")

    # 初始化 MCP 服务
    mcp_server = MCPServer(pool, cache)
    logger.info("MCP 服务初始化完成")

    # 初始化嵌入服务
    embedding_service = EmbeddingService(OLLAMA_EMBEDDING_MODEL, OLLAMA_BASE_URL)

    # 初始化 Rerank 服务
    rerank_service = RerankService(RERANK_MODEL, OLLAMA_BASE_URL)

    # 检查文档解析库可用性
    try:
        from bs4 import BeautifulSoup
        BS4_AVAILABLE = True
    except ImportError:
        BS4_AVAILABLE = False

    try:
        import html2text
        HTML2TEXT_AVAILABLE = True
    except ImportError:
        HTML2TEXT_AVAILABLE = False

    try:
        from docx import Document as DocxDocument
        DOCX_AVAILABLE = True
    except ImportError:
        DOCX_AVAILABLE = False

    try:
        from pptx import Presentation
        PPTX_AVAILABLE = True
    except ImportError:
        PPTX_AVAILABLE = False

    try:
        import openpyxl
        XLSX_AVAILABLE = True
    except ImportError:
        XLSX_AVAILABLE = False

    # 初始化文档处理器
    document_processor = DocumentProcessor(
        html2text_available=HTML2TEXT_AVAILABLE,
        bs4_available=BS4_AVAILABLE,
        docx_available=DOCX_AVAILABLE,
        pptx_available=PPTX_AVAILABLE,
        xlsx_available=XLSX_AVAILABLE
    )

    # 初始化监控服务
    monitoring_service = MonitoringService(pool, cache)
    logger.info("监控服务初始化完成")

    # 初始化权限模块（双重验证：users.role + system_user_role 表）
    from api.permission import init_permission
    init_permission(pool, has_permission)
    logger.info("权限模块初始化完成")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时的清理"""
    await cache.close_redis()
    await close_db()


# ==================== 权限检查函数 ====================

async def has_permission(user_id: str, permission: str) -> bool:
    """
    检查用户是否拥有指定权限

    Args:
        user_id: 用户ID
        permission: 权限标识，如 'system:role:manage'

    Returns:
        bool: 是否拥有权限
    """
    try:
        async with pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 首先检查 users 表中的 role 字段（兼容老系统）
                await cur.execute("""
                    SELECT role
                    FROM users
                    WHERE id = %s AND deleted_at IS NULL
                    LIMIT 1
                """, (user_id,))
                user = await cur.fetchone()
                if user and user.get('role') == 'super_admin':
                    # 超级管理员拥有所有权限
                    return True

                # 检查 system_role 表中的超级管理员角色（RBAC系统）
                await cur.execute("""
                    SELECT r.code
                    FROM system_role r
                    JOIN system_user_role ur ON r.id = ur.role_id
                    WHERE ur.user_id = %s AND r.code = 'super_admin' AND r.deleted_at IS NULL
                    LIMIT 1
                """, (user_id,))
                super_admin = await cur.fetchone()
                if super_admin:
                    return True

                # 检查用户角色的菜单权限
                await cur.execute("""
                    SELECT DISTINCT m.permission
                    FROM system_menu m
                    JOIN system_role_menu rm ON m.id = rm.menu_id
                    JOIN system_user_role ur ON rm.role_id = ur.role_id
                    WHERE ur.user_id = %s AND m.permission = %s AND m.status = 0
                    LIMIT 1
                """, (user_id, permission))
                result = await cur.fetchone()
                return result is not None

    except Exception as e:
        logger.error(f"权限检查失败: {e}")
        return False


async def get_user_roles(user_id: str) -> list:
    """
    获取用户的角色列表

    Args:
        user_id: 用户ID

    Returns:
        list: 角色代码列表
    """
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT r.code
                    FROM system_role r
                    JOIN system_user_role ur ON r.id = ur.role_id
                    WHERE ur.user_id = %s AND r.deleted_at IS NULL AND r.status = 0
                """, (user_id,))
                roles = await cur.fetchall()
                return [row[0] for row in roles] if roles else []
    except Exception as e:
        logger.error(f"获取用户角色失败: {e}")
        return []


async def get_user_departments(user_id: str) -> list:
    """
    获取用户的部门列表

    Args:
        user_id: 用户ID

    Returns:
        list: 部门ID列表
    """
    try:
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT dept_id
                    FROM system_user_dept
                    WHERE user_id = %s
                """, (user_id,))
                depts = await cur.fetchall()
                return [row[0] for row in depts] if depts else []
    except Exception as e:
        logger.error(f"获取用户部门失败: {e}")
        return []


# ==================== 入口点 ====================

if __name__ == "__main__":
    import uvicorn

    print("""
    ╔════════════════════════════════════════════════════════════════╗
    ║                   AI Knowledge Base v2.0                       ║
    ║                                                                ║
    ║  PostgreSQL + pgvector - 生产级向量数据库                      ║
    ║  适用于政府项目、企业级应用                                     ║
    ║                                                                ║
    ║  服务地址: http://localhost:8888                               ║
    ║  API 文档: http://localhost:8888/docs                          ║
    ║                                                                ║
    ║  默认账号: admin / admin123                                    ║
    ║  Ollama:   http://localhost:11434                              ║
    ╚════════════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(app, host="0.0.0.0", port=8888)
