# WispyKB 服务器部署完整指南

## 目录

- [系统架构](#系统架构)
- [前置准备](#前置准备)
- [模型部署](#模型部署)
- [后端部署](#后端部署)
- [前端部署](#前端部署)
- [验证测试](#验证测试)
- [常见问题](#常见问题)

## 系统架构

```
用户浏览器 → Nginx (443) → 静态前端 /api → 后端 (8888)
                                       ↓
                    PostgreSQL (5432) + Redis (6379) + 模型 (8000/8003)
```

## 快速开始

### 1. 上传部署文件

将 deployment 目录上传到服务器

### 2. 启动模型服务

```bash
cd /data
./start_models.sh
```

### 3. 部署后端

```bash
cd /data/WispyKB
./deployment/server-deploy.sh
```

### 4. 安装宝塔面板

参考 BAOTA_SETUP.md

详细说明请见 deployment/ 目录下的各文件。
