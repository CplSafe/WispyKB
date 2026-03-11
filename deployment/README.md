# WispyKB 服务器部署文件包

## 文件说明

```
deployment/
├── README.md                    # 本文件
├── backend.Dockerfile           # 后端 Docker 镜像构建文件
├── docker-compose-prod.yml      # 生产环境 Docker Compose 配置
├── server-deploy.sh             # 服务器一键部署脚本
├── .env.production.example      # 生产环境变量模板
├── BAOTA_SETUP.md               # 宝塔面板安装和配置指南
└── DOCKER_QUICKSTART.md         # Docker 快速开始指南
```

## 快速部署步骤

### 1. 上传文件到服务器

将整个 `deployment/` 目录上传到服务器 `/data/WispyKB/` 目录。

### 2. 执行一键部署脚本

```bash
cd /data/WispyKB
chmod +x server-deploy.sh
./server-deploy.sh
```

### 3. 初始化数据库

```bash
docker exec wispykb-backend python -c "
from core.database import setup_database, create_default_user
import asyncio
asyncio.run(setup_database())
asyncio.run(create_default_user())
"
```

### 4. 安装宝塔面板（可选）

参考 `BAOTA_SETUP.md` 文件。

---

## Docker 快速开始指南

### 前置要求

- Docker 20.10+
- Docker Compose 2.0+
- 至少 4GB 内存
- 至少 20GB 磁盘空间

**重要**：后端项目需要有 `requirements.txt` 文件。如果缺失，部署脚本会自动生成。

### 配置环境变量

```bash
# 复制环境变量模板
cp deployment/.env.production.example .env

# 编辑 .env 文件，修改以下关键配置：
# - POSTGRES_PASSWORD: 数据库密码（至少 16 字符）
# - JWT_SECRET: JWT 密钥（至少 64 字符）
# - CORS_ORIGINS: 允许的前端域名
nano .env
```

### 一键部署

```bash
# 方式 A：使用自动化脚本（推荐）
chmod +x deployment/server-deploy.sh
cd /data/WispyKB
./deployment/server-deploy.sh

# 方式 B：手动执行
docker-compose -f deployment/docker-compose-prod.yml up -d --build
```

### 验证部署

```bash
# 检查服务状态
docker-compose -f deployment/docker-compose-prod.yml ps

# 查看日志
docker-compose -f deployment/docker-compose-prod.yml logs -f backend

# 访问应用
# 后端: http://localhost:8888/docs
```

### 服务端口

| 服务 | 容器端口 | 主机端口 |
|------|---------|---------|
| 后端 | 8888 | 8888 |
| PostgreSQL | 5432 | 5432 |
| Redis | 6379 | 6379 |

### 默认账号

- 用户名: `admin`
- 密码: `admin123`
- **重要：首次登录后立即修改密码！**
