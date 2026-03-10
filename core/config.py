# 配置和常量
# 从 main_pgvector.py 提取的配置项

import os
import secrets
from pathlib import Path

# ==================== JWT 配置 ====================

# JWT 密钥 - 开发环境使用固定密钥，生产环境请设置环境变量
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-key-do-not-use-in-production-2024")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24

# ==================== Ollama 配置 ====================

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_EMBEDDING_MODEL = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "qwen2.5:7b")
RERANK_MODEL = os.getenv("RERANK_MODEL", "bge-reranker-v2-m3")

# ==================== vLLM 配置 ====================

VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8000")
VLLM_CHAT_MODEL = os.getenv("VLLM_CHAT_MODEL", "Qwen/Qwen2.5-7B-Instruct")
VLLM_ENABLED = os.getenv("VLLM_ENABLED", "false").lower() == "true"

# ==================== LLM 引擎选择 ====================

# 可选值: "ollama", "vllm", "auto"（自动选择，优先 vLLM）
LLM_ENGINE = os.getenv("LLM_ENGINE", "ollama")

# ==================== 模型配置 ====================

# 支持的 Embedding 模型列表（按中文优化程度排序）
# 参考：MaxKB 开源项目选择的模型
EMBEDDING_MODELS = {
    # 中文优化模型（推荐）
    "bge-large-zh-v1.5": {
        "name": "BGE Large Chinese v1.5",
        "description": "目前中文效果最好的 Embedding 模型之一",
        "dimension": 1024,
        "language": "zh",
        "provider": "xinference",  # 需要使用 Xinference 部署
        "recommended": True
    },
    "bge-base-zh-v1.5": {
        "name": "BGE Base Chinese v1.5",
        "description": "中文优化，平衡性能和速度",
        "dimension": 768,
        "language": "zh",
        "provider": "xinference",
        "recommended": True
    },
    "m3e-large": {
        "name": "M3E Large",
        "description": "专门为中文优化的 Embedding 模型",
        "dimension": 1024,
        "language": "zh",
        "provider": "xinference",
        "recommended": True
    },
    "m3e-base": {
        "name": "M3E Base",
        "description": "中文优化，速度更快",
        "dimension": 768,
        "language": "zh",
        "provider": "xinference",
    },
    "text2vec-base-chinese": {
        "name": "Text2Vec Base Chinese",
        "description": "中文文本向量化模型",
        "dimension": 768,
        "language": "zh",
        "provider": "xinference",
    },
    "jina-embeddings-v2-base-zh": {
        "name": "Jina Embeddings v2 Base Chinese",
        "description": "Jina 的中文优化模型",
        "dimension": 768,
        "language": "zh",
        "provider": "xinference",
    },
    # 多语言模型
    "bge-m3": {
        "name": "BGE M3",
        "description": "多语言模型，支持100+种语言",
        "dimension": 1024,
        "language": "multilingual",
        "provider": "xinference",
    },
    "nomic-embed-text": {
        "name": "Nomic Embed Text",
        "description": "Ollama 默认 Embedding 模型，支持多语言",
        "dimension": 768,
        "language": "multilingual",
        "provider": "ollama",
    },
    "e5-large-v2": {
        "name": "E5 Large v2",
        "description": "多语言 Embedding 模型",
        "dimension": 1024,
        "language": "multilingual",
        "provider": "xinference",
    },
}

# 支持的 Rerank 模型列表
RERANK_MODELS = {
    "bge-reranker-v2-m3": {
        "name": "BGE Reranker v2 M3",
        "description": "轻量级重排模型，支持多语言，中文效果优秀",
        "language": "multilingual",
        "provider": "ollama",
        "recommended": True
    },
    "bge-reranker-v2-m4": {
        "name": "BGE Reranker v2 M4",
        "description": "更大更准确的重排模型",
        "language": "multilingual",
        "provider": "xinference",
    },
    "bce-reranker-base_v1": {
        "name": "BCE Reranker Base v1",
        "description": "网易 BCE 重排模型，中文优化",
        "language": "zh",
        "provider": "xinference",
    },
}

# 获取当前模型的配置信息
def get_embedding_model_config(model_name: str) -> dict:
    """获取 Embedding 模型配置"""
    return EMBEDDING_MODELS.get(model_name, EMBEDDING_MODELS.get("nomic-embed-text", {}))

def get_rerank_model_config(model_name: str) -> dict:
    """获取 Rerank 模型配置"""
    return RERANK_MODELS.get(model_name, RERANK_MODELS.get("bge-reranker-v2-m3", {}))

# ==================== 存储配置 ====================

STORAGE_DIR = os.getenv("STORAGE_DIR", "./storage")
UPLOAD_DIR = Path(STORAGE_DIR) / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ==================== 数据库配置 ====================

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "ai_knowledge_base",
    "user": os.getenv("PGUSER", os.getenv("USER", "guijinhao")),
    "password": "",
    "min_size": 2,
    "max_size": 10,
}

# ==================== Redis 配置 ====================

REDIS_CONFIG = {
    "host": os.getenv("REDIS_HOST", "localhost"),
    "port": int(os.getenv("REDIS_PORT", "6379")),
    "db": int(os.getenv("REDIS_DB", "0")),
    "password": os.getenv("REDIS_PASSWORD"),
    "decode_responses": True,
    "socket_connect_timeout": 5,
    "socket_keepalive": True,
}

# ==================== 向量存储配置 ====================

VECTOR_STORE_TYPE = os.getenv("VECTOR_STORE_TYPE", "milvus")  # pgvector 或 milvus (默认切换到 milvus)
VECTOR_STORE_CONFIG = {
    "dimension": 768,           # 向量维度 (nomic-embed-text)
    "metric_type": "cosine",    # 距离度量: cosine, l2, ip
    "index_type": "hnsw",       # 索引类型: hnsw, ivf_flat, diskann
}

# ==================== Xinference 配置 ====================
# Xinference 是 Xorbits 团队开发的模型推理服务
# 支持更多中文优化模型，如 bge-large-zh-v1.5, m3e-large 等

XINFERENCE_BASE_URL = os.getenv("XINFERENCE_BASE_URL", "http://localhost:9997")
XINFERENCE_ENABLED = os.getenv("XINFERENCE_ENABLED", "false").lower() == "true"

# ==================== Milvus 配置 ====================

MILVUS_CONFIG = {
    "uri": os.getenv("MILVUS_URI", "http://localhost:19530"),
    "token": os.getenv("MILVUS_TOKEN", ""),
    "collection_name": os.getenv("MILVUS_COLLECTION", "knowledge_chunks"),
}

# ==================== 静态文件配置 ====================

# 后端服务地址，用于生成完整的静态资源 URL
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8888")
STATIC_URL = f"{BACKEND_URL}/static/files"

# ==================== API 限流配置 ====================

RATE_LIMIT = {
    "default": {"requests": 100, "window": 60},      # 默认 100次/分钟
    "chat": {"requests": 30, "window": 60},           # 聊天 30次/分钟
    "search": {"requests": 50, "window": 60},         # 搜索 50次/分钟
    "upload": {"requests": 10, "window": 60},         # 上传 10次/分钟
}

# ==================== 全局变量声明 ====================

# 数据库连接池
pool = None

# Redis 客户端
redis_client = None

# 缓存管理器
cache = None

# 限流器
rate_limiter = None

# 任务队列
task_queue = None

# 工作流引擎
workflow_engine = None

# MCP 服务
mcp_server = None

# 向量嵌入服务
embedding_service = None

# Rerank 服务
rerank_service = None

# 文档处理器
document_processor = None

# 监控服务
monitoring_service = None

# 向量存储实例
vector_store_instance = None
