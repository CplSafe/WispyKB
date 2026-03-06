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
import json
from datetime import datetime, date
from json.encoder import JSONEncoder

# ==================== 导入配置和核心模块 ====================
from core.config import (
    UPLOAD_DIR,
    OLLAMA_BASE_URL,
    OLLAMA_EMBEDDING_MODEL,
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
    init_rbac_default_data,
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

# 操作日志中间件（自动记录所有写操作到 system_operate_log）
from core.audit import OperateLogMiddleware
app.add_middleware(OperateLogMiddleware)


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
    await init_rbac_default_data()

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
    config.workflow_engine = workflow_engine  # 写入 config，供其他模块通过 config 引用
    logger.info("工作流引擎初始化完成")

    # 初始化 MCP 服务
    try:
        mcp_server = MCPServer(pool, cache)
        logger.info("MCP 服务初始化完成")
    except Exception as e:
        logger.warning(f"MCP 服务初始化失败，MCP 功能不可用: {e}")
        mcp_server = None

    # 初始化嵌入服务
    embedding_service = EmbeddingService(OLLAMA_EMBEDDING_MODEL, OLLAMA_BASE_URL)

    # 初始化 Rerank 服务
    rerank_service = RerankService(RERANK_MODEL, OLLAMA_BASE_URL)

    # 初始化文档处理器（自动检测可用库）
    document_processor = DocumentProcessor()

    # 初始化监控服务
    monitoring_service = MonitoringService(pool, cache)
    logger.info("监控服务初始化完成")

    # 初始化权限模块
    from api.permission import init_permission
    init_permission(pool)
    logger.info("权限模块初始化完成")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时的清理"""
    await cache.close_redis()
    await close_db()


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
