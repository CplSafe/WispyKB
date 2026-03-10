#!/usr/bin/env python3
"""
安全检查和密钥生成脚本

功能：
1. 检查当前环境的安全配置
2. 生成强随机密钥
3. 输出安全的 .env 配置

使用方法:
    # 运行安全检查
    python security_check.py

    # 生成新的密钥
    python security_check.py --generate

    # 输出生产环境配置
    python security_check.py --production
"""

import sys
import os
import secrets
import argparse
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from core.security import (
    SecurityConfig,
    generate_jwt_secret,
    generate_db_password,
    generate_api_key,
    print_security_recommendations,
)


def generate_secure_env(output_file: str = None, production: bool = False):
    """生成安全的 .env 配置"""

    env_config = f"""# ========================================
# AI Knowledge Base - 生产环境安全配置
# 生成时间: {os.popen('date').read().strip()}
# ========================================

# ==================== 应用配置 ====================
APP_NAME=AI Knowledge Base
APP_VERSION=2.0.0

# ==================== 安全配置 ====================
# 环境类型
ENVIRONMENT={"production" if production else "staging"}

# DEBUG 模式 - 生产环境必须为 false
DEBUG=false

# JWT 密钥 - 使用强随机密钥（64 字符）
JWT_SECRET={generate_jwt_secret()}

# ==================== CORS 配置 ====================
# 允许的源（JSON 数组格式）
# ⚠️ 请修改为实际的前端域名
CORS_ORIGINS=["https://your-domain.com", "https://www.your-domain.com"]

# IP 白名单（可选，用于政府服务器内网访问）
# ALLOWED_IPS=["192.168.1.100", "10.0.0.0/8"]
ALLOWED_IPS=[]

# ==================== API 配置 ====================
API_HOST=0.0.0.0
API_PORT=8888
API_PREFIX=/api/v1

# ==================== 数据库配置 ====================
# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_USER=kb_admin
POSTGRES_PASSWORD={generate_db_password()}
POSTGRES_DB=knowledge_base
POSTGRES_POOL_SIZE=20
POSTGRES_MAX_OVERFLOW=40

# ==================== Redis 配置 ====================
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD={generate_db_password()}
REDIS_DB=0

# ==================== Milvus 配置 ====================
MILVUS_HOST=localhost
MILVUS_PORT=19530
MILVUS_INDEX_TYPE=HNSW
MILVUS_METRIC_TYPE=COSINE

# ==================== LLM 引擎配置 ====================
# 引擎选择: ollama, vllm, auto
LLM_ENGINE=vllm

# vLLM 配置
VLLM_ENABLED=true
VLLM_BASE_URL=http://localhost:8000
VLLM_CHAT_MODEL=Qwen/Qwen2.5-7B-Instruct

# Ollama 配置（备用）
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_CHAT_MODEL=qwen2.5:7b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text

# Rerank 模型
RERANK_MODEL=bge-reranker-v2-m3

# 向量存储类型
VECTOR_STORE_TYPE=milvus

# ==================== 日志配置 ====================
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_OUTPUT=stdout

# ==================== 存储配置 ====================
# 上传文件存储路径
STORAGE_DIR=./storage
UPLOAD_DIR=./storage/uploads

# ==================== 限流配置 ====================
# 默认: 100 次/分钟
RATE_LIMIT_DEFAULT=100
# 聊天: 30 次/分钟
RATE_LIMIT_CHAT=30
# 搜索: 50 次/分钟
RATE_LIMIT_SEARCH=50
# 上传: 10 次/分钟
RATE_LIMIT_UPLOAD=10
"""

    if output_file:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(env_config)
        print(f"\n✅ 安全配置已写入: {output_file}")
        print(f"   请复制此文件到: {output_file}.production")
        print(f"   然后修改域名、IP 等配置项")
    else:
        print("\n" + "=" * 70)
        print("🔐 生成的安全配置")
        print("=" * 70)
        print(env_config)
        print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="安全检查和密钥生成工具")
    parser.add_argument(
        "--generate",
        "-g",
        action="store_true",
        help="生成新的安全密钥和配置"
    )
    parser.add_argument(
        "--production",
        "-p",
        action="store_true",
        help="生成生产环境配置（更严格）"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        help="输出到文件（如: .env.production）"
    )

    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("🔒 AI Knowledge Base - 安全检查工具")
    print("=" * 70)

    # 运行安全检查
    results = SecurityConfig.print_security_report()

    # 统计问题
    critical_count = len(results["critical"])
    warning_count = len(results["warning"])

    # 如果有生成密钥的请求
    if args.generate:
        print("\n" + "=" * 70)
        print("🔑 生成新密钥")
        print("=" * 70)

        print(f"\nJWT_SECRET (64 字符):")
        print(f"  {generate_jwt_secret()}")

        print(f"\n数据库密码 (24 字符):")
        print(f"  {generate_db_password()}")

        print(f"\nAPI 密钥:")
        print(f"  {generate_api_key()}")

        print(f"\nRedis 密码 (24 字符):")
        print(f"  {generate_db_password()}")

        # 生成完整配置
        if args.output or args.production:
            output_file = args.output or ".env.production"
            generate_secure_env(output_file, args.production)

    # 如果有严重问题，给出建议
    if critical_count > 0:
        print("\n" + "=" * 70)
        print("🚨 发现严重安全问题！")
        print("=" * 70)
        print("\n建议操作:\n")

        print("1. 生成新的安全密钥:")
        print("   python security_check.py --generate --output .env.production")

        print("\n2. 更新环境变量:")
        print("   cp .env.production .env")
        print("   # 然后修改域名、IP 等配置")

        print("\n3. 运行完整的安全检查:")
        print("   python security_check.py")

        print("\n" + "=" * 70)

    # 打印安全建议
    if critical_count > 0 or warning_count > 0 or args.generate:
        print_security_recommendations()


if __name__ == "__main__":
    main()
