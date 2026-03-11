# 宝塔面板安装和配置指南

## 安装宝塔面板

### CentOS/Rocky Linux:
```bash
yum install -y wget && wget -O install.sh https://download.bt.cn/install/install_6.0.sh && sh install.sh
```

### Ubuntu/Debian:
```bash
wget -O install.sh https://download.bt.cn/install/install-ubuntu_6.0.sh && sudo bash install.sh
```

安装完成后会显示：
- 宝塔面板地址
- 用户名
- 密码

**重要：保存这些信息！**

## 宝塔面板配置

### 1. 登录宝塔面板

访问安装时显示的地址（通常是 `http://your-server-ip:8888`）

### 2. 安装推荐软件

登录后选择"LNMP"或"LAMP"一键安装：
- **Nginx**（推荐）或 Apache
- MySQL（可选，我们用 PostgreSQL）
- PHP（如果需要）
- **必须安装**: PM2 管理器

### 3. 部署前端项目

#### 方案 A：使用宝塔的静态网站功能

1. **上传前端代码**
   - 将前端构建产物上传到 `/www/wwwroot/wispykb-frontend/`
   
2. **创建网站**
   - 点击"网站" → "添加站点"
   - 域名: `your-domain.com` 或服务器 IP
   - 根目录: `/www/wwwroot/wispykb-frontend`
   - PHP版本: 纯静态

3. **配置反向代理（重要！）**
   
   点击网站设置 → "反向代理" → "添加反向代理"
   ```
   代理名称: API代理
   目标URL: http://127.0.0.1:8888
   发送域名: $host
   代理目录: /api
   ```
   
   或者直接修改 Nginx 配置：
   ```nginx
   location /api {
       proxy_pass http://127.0.0.1:8888;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
   }
   ```

#### 方案 B：使用 PM2 运行前端开发服务器

1. **安装 Node.js**
   - 宝塔面板 → 软件商店 → 安装 Node.js 18+

2. **上传前端代码**
   ```bash
   cd /www/wwwroot
   git clone <your-frontend-repo> wispykb-frontend
   cd wispykb-frontend
   npm install
   npm run build
   ```

3. **使用 PM2 运行**
   - 宝塔面板 → 软件商店 → PM2 设置
   - 添加项目，选择前端目录
   - 启动命令: `npm run preview` 或类似

### 4. 配置 SSL 证书

1. **免费 Let's Encrypt 证书**
   - 网站设置 → SSL → Let's Encrypt
   - 输入邮箱
   - 申请证书

2. **强制 HTTPS**
   - SSL 设置中开启"强制 HTTPS"

### 5. 安全配置

1. **修改宝塔面板端口**
   - 面板设置 → 安全设置 → 修改面板端口（非 8888）

2. **防火墙配置**
   ```bash
   # 开放端口
   80    # HTTP
   443   # HTTPS
   8888  # 后端 API（如果不通过反向代理）
   8000  # DeepSeek
   8003  # Qwen3 模型
   ```

3. **关闭不安全的端口**
   - 宝塔默认使用 8888，建议修改

## 完整架构

```
                    ┌─────────────────┐
                    │   用户浏览器      │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │  Nginx (宝塔)    │
                    │  443 (HTTPS)     │
                    └────────┬─────────┘
                             │
                ┌────────────┴────────────┐
                │                         │
        ┌───────▼────────┐       ┌───────▼────────┐
        │  静态前端文件    │       │  API 反向代理   │
        │  (/www/wwwroot) │       │  → :8888       │
        └────────────────┘       └───────┬────────┘
                                         │
                            ┌────────────▼────────────┐
                            │  FastAPI 后端容器       │
                            │  (Docker Compose)       │
                            └────────────┬────────────┘
                                         │
                ┌────────────────────────┼────────────────────────┐
                │                        │                        │
        ┌───────▼────────┐      ┌───────▼────────┐     ┌────────▼────────┐
        │ PostgreSQL      │      │  Redis         │     │  vLLM 模型服务   │
        │  (Docker)       │      │  (Docker)      │     │  (Python)       │
        └────────────────┘      └────────────────┘     └─────────────────┘
```

## 常用管理命令

### 宝塔面板命令
```bash
# 查看宝塔命令
bt

# 重启 Nginx
/etc/init.d/nginx restart

# 查看 Nginx 日志
tail -f /www/wwwlogs/access.log
```

### Docker 容器管理
```bash
# 查看容器状态
cd /data/WispyKB
docker-compose -f docker-compose-prod.yml ps

# 查看日志
docker-compose -f docker-compose-prod.yml logs -f backend

# 重启后端
docker-compose -f docker-compose-prod.yml restart backend

# 重启所有服务
docker-compose -f docker-compose-prod.yml restart
```

### 模型服务管理
```bash
# 检查模型状态
ps aux | grep vllm
ps aux | grep qwen3_models.py

# 重启模型
cd /data
./start_models.sh

# 或使用 systemd
systemctl restart deepseek
systemctl restart qwen3-models
```

## 故障排查

### 问题 1：前端无法访问后端 API

检查 Nginx 反向代理配置：
```bash
# 编辑站点配置
nano /www/server/panel/vhost/nginx/your-domain.conf
```

确保有类似配置：
```nginx
location /api {
    proxy_pass http://127.0.0.1:8888;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

### 问题 2：后端容器启动失败

```bash
# 查看详细日志
docker-compose -f docker-compose-prod.yml logs backend

# 检查数据库连接
docker exec wispykb-postgres psql -U wispykb_user -d wispykb
```

### 问题 3：模型服务无响应

```bash
# 检查 GPU 状态
nvidia-smi

# 检查模型端口
curl http://localhost:8000/v1/models
curl http://localhost:8003/health
```

## 更新部署

```bash
# 1. 备份数据
docker exec wispykb-postgres pg_dump -U wispykb_user wispykb > backup_$(date +%Y%m%d).sql

# 2. 拉取新代码
cd /data/WispyKB
git pull

# 3. 重新部署
docker-compose -f docker-compose-prod.yml up -d --build

# 4. 清理旧镜像
docker image prune -a
```

## 性能优化

### 宝塔面板优化

1. **开启 OPcache**（PHP）
2. **配置 Redis 缓存**
3. **开启 Gzip 压缩**（Nginx）

### Docker 优化

编辑 `docker-compose-prod.yml` 添加资源限制：
```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 8G
```

## 监控

### 宝塔监控
- 系统状态
- 负载监控
- 网络流量

### 日志查看
```bash
# Nginx 访问日志
tail -f /www/wwwlogs/access.log

# 后端日志
docker-compose -f docker-compose-prod.yml logs -f backend
```
