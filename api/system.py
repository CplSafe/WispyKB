"""
系统配置相关路由
从 main_pgvector.py 拆分出来
"""
import base64
import hashlib
import logging
import os
import random
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Dict, Optional

from core import config, audit_log, audit_log_with_changes
import httpx
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFont
from psycopg.rows import dict_row

from .auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["system"])

# ==================== 全局变量访问函数 ====================
def _get_globals():
    """获取 main_pgvector 模块中的全局变量"""
    # 延迟导入避免循环依赖
    import main_pgvector as mp
    return {
        'pool': mp.pool,
        'UPLOAD_DIR': mp.UPLOAD_DIR,
        'get_current_user': mp.get_current_user,
    }


# ==================== 验证码存储 ====================
# 验证码内存存储 (生产环境建议使用Redis)
captcha_store: Dict[str, Dict] = {}


# ==================== 数据模型 ====================
class CaptchaVerifyRequest(BaseModel):
    captcha_id: str
    x: int  # 用户拖动的x坐标
    y: int  # 用户拖动的y坐标（应该接近0）


class SystemConfigRequest(BaseModel):
    site_name: Optional[str] = None
    site_title: Optional[str] = None
    logo: Optional[str] = None  # base64 encoded image
    favicon: Optional[str] = None  # base64 encoded image
    primary_color: Optional[str] = None
    theme: Optional[str] = None  # 'light' or 'dark'


# ==================== 验证码辅助函数 ====================
def generate_puzzle_image():
    """生成拼图验证码图片 - 标准格式用于 rc-slider-captcha"""
    # 标准尺寸 (rc-slider-captcha 默认 320x160)
    width = 320
    height = 160
    puzzle_width = 60  # 拼图块宽度

    # 创建渐变背景
    img = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(img)

    # 生成渐变背景 (蓝紫色)
    for y in range(height):
        r = int(102 + (118 - 102) * y / height)
        g = int(126 + (46 - 126) * y / height)
        b = int(234 + (135 - 234) * y / height)
        for x in range(width):
            img.putpixel((x, y), (r, g, b))

    # 添加产品名称 "Wispy" 放在底部
    try:
        font_large = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
    except:
        try:
            font_large = ImageFont.truetype("arial.ttf", 28)
        except:
            font_large = ImageFont.load_default()

    # 在底部绘制品牌文字（半透明白色）
    draw.text((width//2, height - 20), "Wispy",
             fill=(255, 255, 255, 80), anchor="mm", font=font_large)

    # 拼图位置
    # y: 20-80 (留出底部空间给品牌文字)
    puzzle_x = random.randint(100, 240)  # 缺口位置
    puzzle_y = random.randint(25, 70)

    # 创建拼图形状 (带凸起的拼图块 - 经典的拼图验证码形状)
    def create_puzzle_shape_path(x, y, w, h):
        """创建拼图形状的路径点 - 带凸起的方块"""
        half_w = w // 2
        bump = w // 3

        # 拼图形状路径 (从左上角开始顺时针)
        points = [
            (x, y),                           # 左上
            (x + half_w - bump//2, y),         # 顶边左
            (x + half_w - bump//2, y - bump),  # 凸起左外侧
            (x + half_w, y - bump - bump//2),  # 凸起顶部
            (x + half_w + bump//2, y - bump),  # 凸起右外侧
            (x + half_w + bump//2, y),         # 顶边右
            (x + w, y),                        # 右上
            (x + w, y + h),                    # 右下
            (x, y + h),                       # 左下
        ]
        return points

    # ========== 1. 生成带缺口的背景图 ==========
    main_img = img.copy()
    puzzle_points = create_puzzle_shape_path(puzzle_x, puzzle_y, puzzle_width, height - puzzle_y - 20)

    # 在背景图上绘制缺口 (深色半透明)
    gap_img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    gap_draw = ImageDraw.Draw(gap_img)
    gap_draw.polygon(puzzle_points, fill=(0, 0, 0, 180))
    gap_draw.polygon(puzzle_points, outline=(0, 0, 0, 255))
    main_img = Image.alpha_composite(main_img.convert('RGBA'), gap_img).convert('RGB')

    # ========== 2. 生成拼图块 ==========
    # 从原始背景图提取拼图块区域
    padding = 15
    extract_left = max(0, puzzle_x - padding)
    extract_top = max(0, puzzle_y - padding - 10)
    extract_right = min(width, puzzle_x + puzzle_width + padding)
    extract_bottom = min(height, puzzle_y + (height - puzzle_y - 20) + 10)

    # 提取原始图像区域
    piece_region = img.crop((extract_left, extract_top, extract_right, extract_bottom))

    # 创建拼图块遮罩
    mask = Image.new('L', (extract_right - extract_left, extract_bottom - extract_top), 0)
    mask_draw = ImageDraw.Draw(mask)

    # 调整拼图形状坐标到提取区域的坐标系
    mask_points = [(p[0] - extract_left, p[1] - extract_top) for p in puzzle_points]
    mask_draw.polygon(mask_points, fill=255)

    # 应用遮罩得到拼图块
    piece_img = Image.new('RGBA', (extract_right - extract_left, extract_bottom - extract_top), (0, 0, 0, 0))
    piece_img.paste(piece_region, (0, 0))
    piece_img.putalpha(mask)

    # 添加白色边框使拼图块更明显
    piece_draw = ImageDraw.Draw(piece_img)
    piece_draw.polygon(mask_points, outline=(255, 255, 255, 255), width=2)

    # ========== 3. 转换为 base64 ==========
    bg_buffer = BytesIO()
    main_img.save(bg_buffer, format='PNG')
    bg_base64 = base64.b64encode(bg_buffer.getvalue()).decode()

    piece_buffer = BytesIO()
    piece_img.save(piece_buffer, format='PNG')
    piece_base64 = base64.b64encode(piece_buffer.getvalue()).decode()

    return {
        'bg': f'data:image/png;base64,{bg_base64}',
        'piece': f'data:image/png;base64,{piece_base64}',
        'x': puzzle_x,  # 正确答案：拼图需要移动到这个x位置
        'y': puzzle_y,
    }


# ==================== 健康检查 ====================
@router.get("/health")
async def health_check():
    """健康检查"""
    g = _get_globals()
    db_status = "connected"
    try:
        async with g['pool'].connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
    except:
        db_status = "disconnected"

    return {
        "status": "healthy",
        "service": "ai-kb",
        "version": "2.0.0",
        "database": f"PostgreSQL+pgvector ({db_status})",
        "vector": "pgvector HNSW",
        "storage": "disk"
    }


# ==================== 验证码 API ====================
@router.get("/captcha/generate")
async def generate_captcha():
    """生成滑块验证码"""
    captcha_data = generate_puzzle_image()
    captcha_id = str(uuid.uuid4())

    # 存储验证码数据，5分钟过期
    captcha_store[captcha_id] = {
        'target_x': captcha_data['x'],  # 缺口位置的x坐标
        'expires_at': datetime.now() + timedelta(minutes=5)
    }

    # 清理过期验证码
    now = datetime.now()
    expired_ids = [cid for cid, data in captcha_store.items() if data['expires_at'] < now]
    for cid in expired_ids:
        del captcha_store[captcha_id]

    return {
        'id': captcha_id,
        'bgUrl': captcha_data['bg'],
        'puzzleUrl': captcha_data['piece']
    }


@router.post("/captcha/verify")
async def verify_captcha(request: CaptchaVerifyRequest):
    """验证滑块验证码"""
    if request.captcha_id not in captcha_store:
        raise HTTPException(status_code=400, detail="验证码已过期或不存在")

    captcha_data = captcha_store[request.captcha_id]

    # 检查是否过期
    if captcha_data['expires_at'] < datetime.now():
        del captcha_store[request.captcha_id]
        raise HTTPException(status_code=400, detail="验证码已过期")

    correct_x = captcha_data['target_x']
    user_x = request.x

    # 允许的误差范围（像素）
    tolerance = 6

    if abs(user_x - correct_x) <= tolerance:
        # 验证成功，删除验证码
        del captcha_store[request.captcha_id]
        return {'success': True, 'message': '验证成功'}
    else:
        # 验证失败，保留验证码让用户重试
        return {'success': False, 'message': '验证失败，请重试'}


# ==================== 系统配置 API ====================
@router.get("/system/config")
async def get_system_config():
    """获取系统配置（公开接口，无需登录）"""
    import core.config as config
    pool = config.pool
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM system_config WHERE id = '1'")
            config = await cur.fetchone()

    if not config:
        # 如果配置不存在，返回默认配置
        return {
            "site_name": "AI Knowledge Base",
            "site_title": "AI Knowledge Base",
            "logo": None,
            "favicon": None,
            "primary_color": "#3b82f6",
            "theme": "light"
        }

    return {
        "site_name": config.get('site_name'),
        "site_title": config.get('site_title'),
        "logo": config.get('logo'),
        "favicon": config.get('favicon'),
        "primary_color": config.get('primary_color'),
        "theme": config.get('theme')
    }


@router.put("/system/config")
@audit_log(entity_type="system_config", action="update")
async def update_system_config(
    request: SystemConfigRequest,
    user: Dict = Depends(get_current_user)
):
    """更新系统配置（仅管理员）"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    # 检查是否为管理员
    if user.get('role') != 'super_admin':
        raise HTTPException(status_code=403, detail="只有管理员可以修改系统配置")

    updated_config = None

    async with config.pool.connection() as conn:
        # 先检查配置是否存在
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM system_config WHERE id = '1'")
            db_config = await cur.fetchone()

        # 构建更新语句
        updates = []
        values = []

        if request.site_name is not None:
            updates.append("site_name = %s")
            values.append(request.site_name)
        if request.site_title is not None:
            updates.append("site_title = %s")
            values.append(request.site_title)
        if request.logo is not None:
            updates.append("logo = %s")
            values.append(request.logo)
        if request.favicon is not None:
            updates.append("favicon = %s")
            values.append(request.favicon)
        if request.primary_color is not None:
            updates.append("primary_color = %s")
            values.append(request.primary_color)
        if request.theme is not None:
            updates.append("theme = %s")
            values.append(request.theme)

        if updates:
            updates.append("updated_at = NOW()")
            values.append("1")

            async with conn.cursor(row_factory=dict_row) as cur:
                if db_config:
                    # 更新现有配置 - 使用 RETURNING 获取更新后的数据
                    await cur.execute(f"""
                        UPDATE system_config
                        SET {', '.join(updates)}
                        WHERE id = %s
                        RETURNING id, site_name, site_title, logo, favicon, primary_color, theme, updated_at
                    """, values)
                    updated_config = await cur.fetchone()
                else:
                    # 创建新配置
                    all_values = [request.site_name or 'AI Knowledge Base',
                                request.site_title or 'AI Knowledge Base',
                                request.logo,
                                request.favicon,
                                request.primary_color or '#3b82f6',
                                request.theme or 'light']
                    await cur.execute("""
                        INSERT INTO system_config (id, site_name, site_title, logo, favicon, primary_color, theme, updated_at)
                        VALUES ('1', %s, %s, %s, %s, %s, %s, NOW())
                        RETURNING id, site_name, site_title, logo, favicon, primary_color, theme, updated_at
                    """, all_values)
                    updated_config = await cur.fetchone()

            # 在 cursor 外面提交事务
            await conn.commit()
        else:
            # 没有更新，使用现有配置
            updated_config = db_config

    return {
        "site_name": updated_config.get('site_name'),
        "site_title": updated_config.get('site_title'),
        "logo": updated_config.get('logo'),
        "favicon": updated_config.get('favicon'),
        "primary_color": updated_config.get('primary_color'),
        "theme": updated_config.get('theme'),
        "message": "系统配置更新成功"
    }


@router.post("/system/upload-image")
@audit_log(entity_type="system_image", action="upload")
async def upload_system_image(
    file: UploadFile = File(...),
    user: Dict = Depends(get_current_user)
):
    """上传系统配置图片（LOGO 或 Favicon）"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    # 检查是否为管理员
    if user.get('role') != 'super_admin':
        raise HTTPException(status_code=403, detail="只有管理员可以上传系统图片")

    # 验证文件类型
    if not file.content_type or not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="只支持图片文件")

    # 验证文件大小（最大 2MB）
    MAX_SIZE = 2 * 1024 * 1024
    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(status_code=400, detail="图片大小不能超过 2MB")

    # 确保上传目录存在
    upload_dir = config.UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 生成文件名
    file_ext = file.filename.split('.')[-1] if '.' in file.filename else 'png'
    unique_filename = f"{uuid.uuid4()}.{file_ext}"
    file_path = upload_dir / unique_filename

    # 保存文件
    with open(file_path, "wb") as f:
        f.write(content)

    # 返回 URL（使用静态文件路径）
    file_url = f"/static/files/{unique_filename}"

    return {
        "url": file_url,
        "filename": unique_filename
    }


@router.get("/stats/dashboard")
async def get_dashboard_stats(user: Dict = Depends(get_current_user)):
    """获取仪表盘统计数据"""
    import core.config
    pool = core.config.pool  # 获取实时的 pool 值，而不是导入时的缓存值

    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 获取知识库统计
            await cur.execute("SELECT COUNT(*) as count FROM knowledge_bases")
            kb_count = (await cur.fetchone())['count']

            # 获取文档统计
            await cur.execute("SELECT COUNT(*) as count, COALESCE(SUM(chunk_count), 0) as token_count FROM documents WHERE status = 'completed'")
            doc_stats = await cur.fetchone()

            # 获取应用统计
            await cur.execute("SELECT COUNT(*) as count FROM applications")
            app_count = (await cur.fetchone())['count']

            # 获取会话统计 - 使用 chat_messages 表
            await cur.execute("SELECT COUNT(DISTINCT message_id) as count FROM chat_messages")
            message_count = (await cur.fetchone())['count']

            # 获取应用使用情况（包含应用名称和描述）
            await cur.execute("""
                SELECT
                    a.id as app_id,
                    a.name,
                    a.description,
                    COUNT(cm.message_id) as message_count,
                    COUNT(DISTINCT cm.session_id) as conversation_count
                FROM applications a
                LEFT JOIN chat_messages cm ON cm.application_id = a.id
                GROUP BY a.id, a.name, a.description
                ORDER BY message_count DESC
                LIMIT 5
            """)
            top_apps = await cur.fetchall()

    return {
        "knowledge_bases": kb_count,
        "documents": doc_stats['count'] if doc_stats else 0,
        "chunks": doc_stats['token_count'] if doc_stats else 0,
        "applications": app_count,
        "messages": message_count,
        "conversations": 0,  # TODO: 从 conversations 表统计
        "top_applications": [
            {
                "app_id": app['app_id'],
                "name": app['name'],
                "description": app['description'],
                "message_count": app['message_count'] or 0,
                "conversation_count": app['conversation_count'] or 0,
            }
            for app in top_apps
        ]
    }


# ==================== 模型管理 API ====================

class ModelStatusRequest(BaseModel):
    model_type: str  # 'embedding' or 'rerank' or 'chat'
    model_name: str
    provider: str  # 'ollama' or 'xinference'


@router.get("/models/available")
async def get_available_models(user: Dict = Depends(get_current_user)):
    """
    获取所有可用的模型列表

    参考 MaxKB 开源项目的模型选择
    """
    from core.config import EMBEDDING_MODELS, RERANK_MODELS

    return {
        "embedding": [
            {
                "model_id": model_id,
                "name": model_config["name"],
                "description": model_config["description"],
                "dimension": model_config.get("dimension", 768),
                "language": model_config.get("language", "multilingual"),
                "provider": model_config.get("provider", "ollama"),
                "recommended": model_config.get("recommended", False)
            }
            for model_id, model_config in EMBEDDING_MODELS.items()
        ],
        "rerank": [
            {
                "model_id": model_id,
                "name": model_config["name"],
                "description": model_config["description"],
                "language": model_config.get("language", "multilingual"),
                "provider": model_config.get("provider", "ollama"),
                "recommended": model_config.get("recommended", False)
            }
            for model_id, model_config in RERANK_MODELS.items()
        ]
    }


@router.get("/models/current")
async def get_current_models(user: Dict = Depends(get_current_user)):
    """获取当前正在使用的模型配置"""
    from core.config import (
        OLLAMA_EMBEDDING_MODEL, OLLAMA_CHAT_MODEL, RERANK_MODEL,
        OLLAMA_BASE_URL, get_embedding_model_config, get_rerank_model_config
    )

    embedding_config = get_embedding_model_config(OLLAMA_EMBEDDING_MODEL)
    rerank_config = get_rerank_model_config(RERANK_MODEL)

    return {
        "embedding": {
            "model_id": OLLAMA_EMBEDDING_MODEL,
            "name": embedding_config.get("name", OLLAMA_EMBEDDING_MODEL),
            "dimension": embedding_config.get("dimension", 768),
            "provider": embedding_config.get("provider", "ollama"),
            "base_url": OLLAMA_BASE_URL
        },
        "rerank": {
            "model_id": RERANK_MODEL,
            "name": rerank_config.get("name", RERANK_MODEL),
            "provider": rerank_config.get("provider", "ollama"),
            "base_url": OLLAMA_BASE_URL
        },
        "chat": {
            "model_id": OLLAMA_CHAT_MODEL,
            "base_url": OLLAMA_BASE_URL
        }
    }


@router.post("/models/test")
async def test_model(request: ModelStatusRequest, user: Dict = Depends(get_current_user)):
    """
    测试模型是否可用

    发送测试请求验证模型服务是否正常工作
    """
    from core.config import OLLAMA_BASE_URL

    provider_url = request.provider

    if request.model_type == "embedding":
        # 测试 Embedding 模型
        if request.provider == "ollama":
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        f"{OLLAMA_BASE_URL}/api/embeddings",
                        json={"model": request.model_name, "prompt": "测试"}
                    )
                    if response.status_code == 200:
                        data = response.json()
                        embedding = data.get("embedding", [])
                        return {
                            "status": "success",
                            "model": request.model_name,
                            "dimension": len(embedding),
                            "message": f"模型 {request.model_name} 可用"
                        }
                    elif response.status_code == 404:
                        return {
                            "status": "not_found",
                            "model": request.model_name,
                            "message": f"模型 {request.model_name} 不存在，请先拉取: ollama pull {request.model_name}"
                        }
                    else:
                        return {
                            "status": "error",
                            "model": request.model_name,
                            "message": f"模型测试失败: {response.status_code}"
                        }
            except Exception as e:
                return {
                    "status": "error",
                    "model": request.model_name,
                    "message": f"连接失败: {str(e)}"
                }
        else:
            # Xinference 测试
            return {
                "status": "not_implemented",
                "message": "Xinference 支持即将推出"
            }

    elif request.model_type == "rerank":
        # 测试 Rerank 模型
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{OLLAMA_BASE_URL}/api/rerank",
                    json={
                        "model": request.model_name,
                        "query": "测试查询",
                        "documents": ["文档1", "文档2"],
                        "top_k": 2
                    }
                )
                if response.status_code == 200:
                    return {
                        "status": "success",
                        "model": request.model_name,
                        "message": f"Rerank 模型 {request.model_name} 可用"
                    }
                elif response.status_code == 404:
                    return {
                        "status": "not_found",
                        "model": request.model_name,
                        "message": f"模型 {request.model_name} 不存在，请先拉取: ollama pull {request.model_name}"
                    }
                else:
                    return {
                        "status": "error",
                        "model": request.model_name,
                        "message": f"模型测试失败: {response.status_code}"
                    }
        except Exception as e:
            return {
                "status": "error",
                "model": request.model_name,
                "message": f"连接失败: {str(e)}"
            }

    return {"status": "error", "message": "不支持的模型类型"}


@router.get("/models/recommendations")
async def get_model_recommendations(user: Dict = Depends(get_current_user)):
    """
    获取模型推荐配置

    根据使用场景推荐最佳模型组合
    """
    from core.config import EMBEDDING_MODELS, RERANK_MODELS

    recommendations = {
        "chinese_optimized": {
            "name": "中文优化",
            "description": "适合中文文档为主的知识库",
            "models": {
                "embedding": "bge-large-zh-v1.5",
                "rerank": "bge-reranker-v2-m3"
            },
            "notes": "需要使用 Xinference 部署中文模型"
        },
        "multilingual": {
            "name": "多语言支持",
            "description": "适合中英混合或多语言文档",
            "models": {
                "embedding": "nomic-embed-text",  # Ollama 默认
                "rerank": "bge-reranker-v2-m3"
            },
            "notes": "使用 Ollama 即可，部署简单"
        },
        "lightweight": {
            "name": "轻量级",
            "description": "资源受限环境下的选择",
            "models": {
                "embedding": "bge-base-zh-v1.5",
                "rerank": "bge-reranker-v2-m3"
            },
            "notes": "平衡性能和资源消耗"
        },
        "high_accuracy": {
            "name": "高精度",
            "description": "追求最高检索准确率",
            "models": {
                "embedding": "bge-large-zh-v1.5",
                "rerank": "bge-reranker-v2-m4"
            },
            "notes": "需要更多资源，但效果最佳"
        }
    }

    return {
        "recommendations": recommendations,
        "installation_guide": {
            "ollama": {
                "embedding": "ollama pull nomic-embed-text",
                "rerank": "ollama pull linux6200/bge-reranker-v2-m3"
            },
            "xinference": {
                "command": "xinference-launch --model-name bge-large-zh-v1.5",
                "note": "需要先安装 Xinference: pip install xinference"
            }
        }
    }
