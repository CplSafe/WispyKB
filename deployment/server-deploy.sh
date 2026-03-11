#!/bin/bash

set -e

echo "=========================================="
echo "🚀 WispyKB 服务器一键部署脚本"
echo "=========================================="
echo ""

PROJECT_DIR="/data/WispyKB"
cd "$PROJECT_DIR" || exit 1

# 1. 检查并创建 requirements.txt
if [ ! -f "requirements.txt" ]; then
    echo "📦 创建 requirements.txt..."
    cat > requirements.txt << 'REQUIREMENTS'
fastapi==0.115.0
uvicorn[standard]==0.32.0
python-multipart==0.0.12
pydantic==2.10.1
pydantic-settings==2.6.0
asyncpg==0.30.0
psycopg2-binary==2.9.9
sqlalchemy==2.0.36
alembic==1.14.0
redis==5.2.0
hiredis==3.1.0
pgvector==0.3.4
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
bcrypt==4.2.1
httpx==0.27.2
aiohttp==3.11.10
python-docx==1.1.2
PyPDF2==3.0.1
openpyxl==3.1.5
pypdf==5.1.0
pdfplumber==0.11.4
pillow==11.0.0
python-magic==0.4.27
jieba==0.42.1
langchain==0.3.10
langchain-community==0.3.10
python-dotenv==1.0.1
loguru==0.7.3
tenacity==9.0.0
orjson==3.10.12
opentelemetry-api==1.28.0
opentelemetry-sdk==1.28.0
prometheus-client==0.21.0
starlette==0.41.0
celery==5.4.0
kombu==5.4.3
minio==7.2.8
openai==1.54.0
anthropic==0.40.0
python-dateutil==2.9.0.post0
pytz==2024.2
validators==0.28.3
REQUIREMENTS
    echo "✅ requirements.txt 已创建"
fi

# 2. 检查 .env 文件
if [ ! -f ".env" ]; then
    echo "📝 创建 .env 文件..."
    cp .env.production.example .env 2>/dev/null || cat > .env << 'ENV'
# 数据库配置
POSTGRES_PASSWORD=wispykb_strong_password_$(openssl rand -hex 16)

# JWT 密钥（至少 64 字符）
JWT_SECRET=$(openssl rand -base64 64)

# CORS 配置
CORS_ORIGINS=["http://localhost:8888"]

# 环境配置
ENVIRONMENT=production
DEBUG=false

# LLM 配置
LLM_ENGINE=vllm
VLLM_ENABLED=true
VLLM_BASE_URL=http://localhost:8000
VLLM_CHAT_MODEL=deepseek-chat

# Embedding 配置
EMBEDDING_ENGINE=api
EMBEDDING_BASE_URL=http://localhost:8003
EMBEDDING_MODEL=qwen3-embedding

# Rerank 配置
RERANK_ENGINE=api
RERANK_BASE_URL=http://localhost:8003
RERANK_MODEL=qwen3-reranker
ENV
    echo "⚠️  请检查并修改 .env 文件中的配置"
fi

# 3. 检查 Docker Compose
if ! command -v docker &> /dev/null; then
    echo "❌ 错误: 未安装 Docker"
    exit 1
fi

# 4. 停止旧容器
echo "🛑 停止旧容器..."
docker-compose -f docker-compose-prod.yml down 2>/dev/null || true

# 5. 构建并启动
echo "🔨 构建 Docker 镜像..."
docker-compose -f docker-compose-prod.yml build

echo "🚀 启动服务..."
docker-compose -f docker-compose-prod.yml up -d

# 6. 等待服务启动
echo "⏳ 等待服务启动..."
sleep 15

# 7. 检查状态
echo ""
echo "=========================================="
echo "📊 服务状态"
echo "=========================================="
docker-compose -f docker-compose-prod.yml ps

echo ""
echo "=========================================="
echo "✅ 部署完成！"
echo "=========================================="
echo ""
echo "🌐 访问地址:"
echo "   后端 API: http://$(hostname -I | awk '{print $1}'):8888/api/v1"
echo "   API 文档: http://$(hostname -I | awk '{print $1}'):8888/docs"
echo ""
echo "📝 后续步骤:"
echo "   1. 初始化数据库: docker exec wispykb-backend python -c 'from core.database import setup_database; import asyncio; asyncio.run(setup_database())'"
echo "   2. 安装宝塔面板管理前端"
echo "   3. 配置域名和 SSL"
echo ""
