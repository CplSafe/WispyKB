# WispyKB 部署快速参考

## 一、模型服务启动

```bash
cd /data
./start_models.sh

# 验证
curl http://localhost:8000/v1/models          # DeepSeek
curl http://localhost:8003/health            # Qwen3
```

## 二、后端部署

```bash
cd /data/WispyKB
./deployment/server-deploy.sh

# 初始化数据库
docker exec wispykb-backend python -c "
from core.database import setup_database, create_default_user
import asyncio
asyncio.run(setup_database())
asyncio.run(create_default_user())
"
```

## 三、宝塔面板安装

```bash
# Ubuntu/Debian
wget -O install.sh https://download.bt.cn/install/install-ubuntu_6.0.sh
sudo bash install.sh

# CentOS
wget -O install.sh https://download.bt.cn/install/install_6.0.sh
sh install.sh
```

## 常用命令

### Docker 操作

```bash
# 查看状态
docker-compose -f deployment/docker-compose-prod.yml ps

# 查看日志
docker-compose -f deployment/docker-compose-prod.yml logs -f backend

# 重启服务
docker-compose -f deployment/docker-compose-prod.yml restart backend

# 停止所有
docker-compose -f deployment/docker-compose-prod.yml down
```

### 模型管理

```bash
# 检查 GPU
nvidia-smi

# 重启模型
/data/start_models.sh

# 查看模型日志
tail -f /tmp/deepseek.log
tail -f /tmp/qwen3_models.log
```

### 数据库

```bash
# 连接数据库
docker exec -it wispykb-postgres psql -U wispykb_user -d wispykb

# 备份
docker exec wispykb-postgres pg_dump -U wispykb_user wispykb > backup.sql

# 恢复
cat backup.sql | docker exec -i wispykb-postgres psql -U wispykb_user wispykb
```

## 服务端口

| 服务 | 端口 |
|------|------|
| 前端 (Nginx) | 80, 443 |
| 后端 API | 8888 |
| PostgreSQL | 5432 |
| Redis | 6379 |
| DeepSeek | 8000 |
| Qwen3 模型 | 8003 |

## 默认账号

- 用户名: `admin`
- 密码: `admin123`
- **重要：首次登录后立即修改！**

## 故障排查

### 后端启动失败
```bash
docker-compose -f deployment/docker-compose-prod.yml logs backend
```

### 模型无响应
```bash
ps aux | grep vllm
nvidia-smi
tail -f /tmp/deepseek.log
```

### 前端无法访问 API
```bash
# 检查 CORS 配置
cat /data/WispyKB/.env | grep CORS
```

## 更新部署

```bash
# 备份
/data/backup.sh

# 更新代码
cd /data/WispyKB
git pull

# 重新部署
docker-compose -f deployment/docker-compose-prod.yml up -d --build
```

## 文档索引

- `README.md` - 部署说明
- `BAOTA_SETUP.md` - 宝塔面板配置
- `DOCKER_QUICKSTART.md` - Docker 指南
- `SERVER_DEPLOYMENT.md` - 完整部署指南
