# WispyKB 生产环境配置
# 所有配置直接在这里修改，适用于服务器部署

import os
from pathlib import Path

# ==================== JWT 配置 ====================

JWT_SECRET = "change-this-to-a-very-strong-secret-key-in-production-at-least-64-chars"
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

# ==================== LLM 配置 ====================

# LLM 引擎：ollama, vllm
LLM_ENGINE = "vllm"

# vLLM Chat 配置（DeepSeek）
VLLM_ENABLED = True
VLLM_BASE_URL = "http://localhost:8000"
VLLM_CHAT_MODEL = "deepseek-chat"

# vLLM Embedding 配置（独立端口）
VLLM_EMBEDDING_ENABLED = True
VLLM_EMBEDDING_BASE_URL = "http://localhost:8002"
VLLM_EMBEDDING_MODEL = "embedding"

# vLLM Reranker 配置（独立端口）
VLLM_RERANK_ENABLED = True
VLLM_RERANK_BASE_URL = "http://localhost:8001"
VLLM_RERANK_MODEL = "reranker"

# Ollama 配置（备用）
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_EMBEDDING_MODEL = "nomic-embed-text"
OLLAMA_CHAT_MODEL = "qwen2.5:7b"
RERANK_MODEL = "bge-reranker-v2-m3"

# ==================== 数据库配置 ====================

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "wispykb",
    "user": "wispykb_user",
    "password": "your-strong-password-here",  # 修改为实际密码
    "min_size": 2,
    "max_size": 10,
}

# ==================== Redis 配置 ====================

REDIS_CONFIG = {
    "host": "localhost",
    "port": 6379,
    "db": 0,
    "password": None,
    "decode_responses": True,
    "socket_connect_timeout": 5,
    "socket_keepalive": True,
}

# ==================== 向量存储配置 ====================

# 向量数据库类型：milvus 或 pgvector
VECTOR_STORE_TYPE = "milvus"

VECTOR_STORE_CONFIG = {
    "dimension": 768,
    "metric_type": "cosine",
    "index_type": "hnsw",
}

# ==================== Milvus 配置 ====================

MILVUS_CONFIG = {
    "uri": "http://localhost:19530",
    "token": "",
    "collection_name": "knowledge_chunks",
}

# ==================== 文件存储配置 ====================

STORAGE_DIR = os.getenv("STORAGE_DIR", "./storage")
UPLOAD_DIR = Path(STORAGE_DIR) / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 静态文件访问 URL（用于 Excel 图片等资源的 URL 前缀）
STATIC_URL = os.getenv("STATIC_URL", "/static/files")

# ==================== CORS 配置 ====================

CORS_ORIGINS = ["http://localhost:8888"]  # 修改为实际前端域名

# ==================== 安全配置 ====================

ENVIRONMENT = "production"
DEBUG = False
ALLOWED_IPS = []

# ==================== API 限流配置 ====================

RATE_LIMIT = {
    "default": {"requests": 100, "window": 60},
    "chat": {"requests": 30, "window": 60},
    "search": {"requests": 50, "window": 60},
    "upload": {"requests": 10, "window": 60},
}

# ==================== 全局变量 ====================

pool = None
redis_client = None
cache = None
rate_limiter = None
task_queue = None
workflow_engine = None
mcp_server = None
embedding_service = None
rerank_service = None
document_processor = None
monitoring_service = None
vector_store_instance = None
