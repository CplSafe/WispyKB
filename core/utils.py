# 工具函数和向量操作
# 从 main_pgvector.py 提取的工具函数

import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
import jwt
from fastapi import HTTPException, Header, Request
from jwt import PyJWTError
from psycopg.rows import dict_row

try:
    import bcrypt
    BCRYPT_AVAILABLE = True
except ImportError:
    BCRYPT_AVAILABLE = False

from .config import (
    JWT_SECRET,
    JWT_ALGORITHM,
    JWT_EXPIRATION_HOURS,
    OLLAMA_BASE_URL,
    RATE_LIMIT,
    VECTOR_STORE_TYPE,
    redis_client,
    vector_store_instance,
)
# RetrievalMethod 枚举已移到 api.models，为避免循环导入，使用字符串参数
# RetrievalMethod.SEMANTIC_SEARCH = "semantic_search"
# RetrievalMethod.FULL_TEXT_SEARCH = "full_text_search"
# RetrievalMethod.HYBRID_SEARCH = "hybrid_search"
# RetrievalMethod.HYBRID_RERANK = "hybrid_rerank"

logger = logging.getLogger(__name__)


# ==================== 密码哈希工具 ====================

def hash_password(password: str) -> str:
    """对密码进行安全哈希

    优先使用 bcrypt，如果不可用则使用 SHA256+salt
    """
    if BCRYPT_AVAILABLE:
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    else:
        # 备用方案：SHA256 + 随机 salt
        salt = secrets.token_hex(32)
        return hashlib.sha256(f"{salt}{password}".encode()).hexdigest() + f"${salt}"


def verify_password(password: str, hashed: str) -> bool:
    """验证密码是否正确

    支持多种密码格式的向后兼容：
    1. bcrypt 格式 (推荐)
    2. SHA256+salt 格式 (hash$salt)
    3. MD5 格式 (32位十六进制，仅用于兼容旧数据)
    """
    if not hashed or not password:
        return False

    # 1. 尝试 bcrypt 格式
    if BCRYPT_AVAILABLE:
        try:
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
        except (ValueError, TypeError):
            pass  # 不是 bcrypt 格式，继续尝试其他格式

    # 2. 尝试 SHA256+salt 格式 (hash$salt)
    if '$' in hashed and len(hashed) > 65:  # SHA256(64) + $(1) + salt(至少32)
        try:
            hash_part, salt = hashed.rsplit('$', 1)
            computed = hashlib.sha256(f"{salt}{password}".encode()).hexdigest()
            if hash_part == computed:
                return True
        except (ValueError, IndexError):
            pass

    # 3. 尝试纯 MD5 格式 (32位十六进制) - 兼容旧数据
    if len(hashed) == 32:
        try:
            int(hashed, 16)  # 验证是否为有效的十六进制字符串
            computed_md5 = hashlib.md5(password.encode()).hexdigest()
            if computed_md5 == hashed:
                logger.warning("检测到使用MD5格式的密码，建议用户重新设置密码以升级到bcrypt")
                return True
        except ValueError:
            pass

    # 4. 尝试纯 SHA256 格式 (64位十六进制) - 兼容旧数据
    if len(hashed) == 64:
        try:
            int(hashed, 16)  # 验证是否为有效的十六进制字符串
            computed_sha256 = hashlib.sha256(password.encode()).hexdigest()
            if computed_sha256 == hashed:
                logger.warning("检测到使用无盐SHA256格式的密码，建议用户重新设置密码以升级到bcrypt")
                return True
        except ValueError:
            pass

    return False


# ==================== JWT 工具 ====================

def create_token(user_id: str, username: str, role: str) -> str:
    """创建 JWT Token"""
    from api.models import UserRole
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role if isinstance(role, str) else role.value,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Dict:
    """验证 JWT Token"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的 Token")


async def get_current_user(authorization: Optional[str] = Header(None)) -> Optional[Dict]:
    """获取当前用户"""
    if not authorization:
        return None
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        return verify_token(token)
    return None


# 别名：可选用户认证（用于限流等场景）
get_current_user_optional = get_current_user


# ==================== 分页参数验证 ====================

def validate_pagination(page: int, page_size: int, max_page_size: int = 100) -> tuple:
    """验证分页参数，返回 (page, page_size) 或抛出异常"""
    if page < 1:
        raise HTTPException(status_code=400, detail="页码必须大于0")
    if page_size < 1:
        raise HTTPException(status_code=400, detail="每页数量必须大于0")
    if page_size > max_page_size:
        raise HTTPException(status_code=400, detail=f"每页数量不能超过{max_page_size}")
    return page, page_size


# ==================== LLM 服务调用（统一接口）====================

async def call_llm(
    model: str,
    messages: List[Dict],
    stream: bool = False,
    temperature: float = 0.1,
    top_p: float = 0.9,
    num_predict: int = 2048,
    llm_engine: str = None
):
    """调用 LLM 模型（统一接口，支持 Ollama 和 vLLM）

    Args:
        model: 模型名称
        messages: 消息列表
        stream: 是否使用流式输出
        temperature: 温度参数，0-1，越低越严格（默认0.1，适合RAG）
        top_p: nucleus sampling 参数（默认0.9）
        num_predict: 最大生成 token 数（默认2048）
        llm_engine: 指定 LLM 引擎 ("ollama", "vllm", None 表示自动选择)

    Returns:
        如果 stream=False，返回字符串
        如果 stream=True，返回异步生成器
    """
    from .config import LLM_ENGINE, VLLM_ENABLED, VLLM_BASE_URL, VLLM_CHAT_MODEL

    # 确定 LLM 引擎
    engine = llm_engine or LLM_ENGINE

    # 如果配置了 vLLM 且启用，优先使用 vLLM
    if engine == "vllm" or (engine == "auto" and VLLM_ENABLED):
        from services.llm import LLMService
        llm_service = LLMService(
            provider="vllm",
            base_url=VLLM_BASE_URL,
            model=model or VLLM_CHAT_MODEL
        )

        if stream:
            async def generate_stream():
                async for token in llm_service.chat_stream(
                    messages=messages,
                    temperature=temperature,
                    top_p=top_p,
                    max_tokens=num_predict
                ):
                    # 移除思考模式标签（如果存在）
                    token = re.sub(r'＜think＞.*?＜/think＞', '', token, flags=re.DOTALL)
                    if token:
                        yield f"data: {json.dumps({'content': token}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

            return generate_stream()
        else:
            content = await llm_service.chat(
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=num_predict
            )
            # 移除思考模式标签
            content = re.sub(r'＜think＞.*?＜/think＞', '', content, flags=re.DOTALL)
            return content

    # 默认使用 Ollama
    else:
        return await call_ollama(model, messages, stream, temperature, top_p, num_predict)


# ==================== Ollama API 调用（保留向后兼容）====================

async def call_ollama(
    model: str,
    messages: List[Dict],
    stream: bool = False,
    temperature: float = 0.1,
    top_p: float = 0.9,
    num_predict: int = 2048
):
    """调用 Ollama 本地模型

    Args:
        model: 模型名称
        messages: 消息列表
        stream: 是否使用流式输出
        temperature: 温度参数，0-1，越低越严格（默认0.1，适合RAG）
        top_p: nucleus sampling 参数（默认0.9）
        num_predict: 最大生成 token 数（默认2048）

    Returns:
        如果 stream=False，返回字符串
        如果 stream=True，返回异步生成器
    """
    async with httpx.AsyncClient(timeout=300.0) as client:
        payload = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": {
                "num_ctx": 8192,  # 上下文长度
                "temperature": temperature,  # 低温度让模型更严格遵循提示词
                "top_p": top_p,  # nucleus sampling
                "num_predict": num_predict,  # 最大生成长度
            }
        }

        if stream:
            # 流式输出 - 使用 SSE 格式
            async def generate_stream():
                async with client.stream("POST", f"{OLLAMA_BASE_URL}/api/chat", json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.strip():
                            try:
                                data = json.loads(line)
                                content = data.get("message", {}).get("content", "")
                                if content:
                                    # 移除思考模式标签
                                    content = re.sub(r'＜think＞.*?＜/think＞', '', content, flags=re.DOTALL)
                                    content = content.strip()
                                    if content:
                                        yield f"data: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"
                            except json.JSONDecodeError:
                                continue
                yield "data: [DONE]\n\n"

            return generate_stream()
        else:
            # 非流式输出（原有逻辑）
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json=payload
            )
            response.raise_for_status()
            data = response.json()
            content = data.get("message", {}).get("content", "")

            # DeepSeek-R1 等推理模型检查
            logger.info(f"call_ollama non-streaming response: model={model}, content_len={len(content)}, done={data.get('done')}, has_thinking={data.get('message',{}).get('thinking') is not None}")

            # 移除思考模式标签
            content = re.sub(r'＜think＞.*?＜/think＞', '', content, flags=re.DOTALL)
            content = content.strip()

            return content


# ==================== 向量操作（pgvector）====================

async def add_embedding(chunk_id: str, embedding: List[float], conn) -> bool:
    """添加向量到分块"""
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE chunks SET embedding = %s::vector WHERE id = %s",
                (embedding, chunk_id)
            )
            return True
    except Exception as e:
        logger.error(f"添加向量失败: {e}")
        return False


async def vector_search(
    query_embedding: List[float],
    kb_id: str,
    top_k: int = 5,
    threshold: float = 0.0
) -> List[Dict[str, Any]]:
    """pgvector 向量搜索（单个知识库）- 保留向后兼容"""
    from .config import pool
    return await vector_search_multi(pool, query_embedding, [kb_id], top_k, threshold)


async def vector_search_multi(
    pool_ref,
    query_embedding: List[float],
    kb_ids: List[str],
    top_k: int = 5,
    threshold: float = 0.0
) -> List[Dict[str, Any]]:
    """
    优化的多知识库向量搜索 - 一次查询所有知识库

    支持 Milvus 和 pgvector 两种向量存储
    """
    if not kb_ids:
        return []

    # 动态获取 vector_store_instance，避免导入时的 None 值
    from . import config
    vs_instance = config.vector_store_instance

    # 如果使用 Milvus，从 Milvus 搜索
    if VECTOR_STORE_TYPE == 'milvus' and vs_instance:
        try:
            # 从 Milvus 搜索 - 使用 kb_ids 过滤
            milvus_results = await vs_instance.search(
                embedding=query_embedding,
                top_k=top_k,
                kb_ids=kb_ids,
            )

            logger.info(f"Milvus 返回 {len(milvus_results)} 条结果")

            # 转换结果格式
            results = []
            for r in milvus_results:
                logger.info(f"处理结果: chunk_id={r.chunk_id}, document_id={r.document_id}, score={r.score}")

                # 从数据库获取文档信息
                async with pool_ref.connection() as conn:
                    async with conn.cursor(row_factory=dict_row) as cur:
                        await cur.execute("""
                            SELECT d.id as doc_id, d.name as doc_name, d.kb_id, kb.name as kb_name
                            FROM documents d
                            JOIN knowledge_bases kb ON kb.id = d.kb_id
                            WHERE d.id = %s
                        """, (r.document_id,))
                        doc_info = await cur.fetchone()

                logger.info(f"文档信息: {doc_info}")

                # Milvus 返回的 score 本身就是相似度（0-1，越大越相似）
                similarity = r.score if r.score else 0.0

                # 过滤阈值（使用相似度而不是距离）
                if threshold > 0 and similarity < threshold:
                    logger.info(f"相似度 {similarity:.3f} 低于阈值 {threshold}，跳过")
                    continue

                # 转换图片 URL：将 Markdown 格式改为普通文本格式，让 LLM 容易引用
                import re as regex_module
                import os
                # 使用服务器外网地址
                server_host = os.getenv("SERVER_HOST", "127.0.0.1")
                server_port = os.getenv("SERVER_PORT", "8888")
                base_url = f"http://{server_host}:{server_port}/static/files/images"
                content = r.content
                # 匹配 Markdown 图片格式 - 先匹配完整URL，再匹配相对路径
                content = regex_module.sub(
                    r'!\[([^\]]*)\]\(http://localhost:\d+/static/files/images/([^)]+)\)',
                    fr'[流程图链接: {base_url}/\2]',
                    content
                )
                content = regex_module.sub(
                    r'!\[([^\]]*)\]\(/static/files/images/([^)]+)\)',
                    fr'[流程图链接: {base_url}/\2]',
                    content
                )

                # 即使找不到文档信息也返回内容（数据一致性问题的临时修复）
                results.append({
                    'chunk_id': r.chunk_id,
                    'doc_id': r.document_id,
                    'doc_name': doc_info['doc_name'] if doc_info else '未知文档',
                    'kb_id': doc_info['kb_id'] if doc_info else kb_ids[0],
                    'kb_name': doc_info['kb_name'] if doc_info else '未知知识库',
                    'content': content,
                    'similarity': similarity,
                })

            return results
        except Exception as e:
            logger.error(f"Milvus 搜索失败: {e}")
            # 降级到 pgvector
            pass

    # pgvector 搜索（原有逻辑）
    async with pool_ref.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 核心优化：一次查询所有知识库，使用 ANY() 过滤
            await cur.execute("""
                SELECT
                    c.id as chunk_id,
                    c.doc_id,
                    d.name as doc_name,
                    d.kb_id,
                    kb.name as kb_name,
                    c.content,
                    1 - (c.embedding <=> %s::vector) as similarity
                FROM chunks c
                JOIN documents d ON d.id = c.doc_id
                JOIN knowledge_bases kb ON kb.id = d.kb_id
                WHERE d.kb_id = ANY(%s)
                  AND c.embedding IS NOT NULL
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s
            """, (query_embedding, kb_ids, query_embedding, top_k))

            results = await cur.fetchall()

            # 过滤低于阈值的结果
            if threshold > 0:
                results = [r for r in results if r['similarity'] >= threshold]

            # 转换图片 URL：将 Markdown 格式改为普通文本格式
            import re as regex_module
            import os
            server_host = os.getenv("SERVER_HOST", "127.0.0.1")
            server_port = os.getenv("SERVER_PORT", "8888")
            base_url = f"http://{server_host}:{server_port}/static/files/images"
            for r in results:
                if 'content' in r:
                    # 先匹配完整URL，再匹配相对路径
                    r['content'] = regex_module.sub(
                        r'!\[([^\]]*)\]\(http://localhost:\d+/static/files/images/([^)]+)\)',
                        fr'[流程图链接: {base_url}/\2]',
                        r['content']
                    )
                    r['content'] = regex_module.sub(
                        r'!\[([^\]]*)\]\(/static/files/images/([^)]+)\)',
                        fr'[流程图链接: {base_url}/\2]',
                        r['content']
                    )

            return results


async def hybrid_search(
    pool_ref,
    query: str,
    query_embedding: List[float],
    kb_ids: List[str],
    top_k: int = 5,
    alpha: float = 0.7
) -> List[Dict[str, Any]]:
    """
    混合搜索：向量相似度 + 关键词匹配

    参考 Dify 的混合搜索实现：
    - 向量搜索：语义相似度
    - 全文搜索：关键词匹配
    - 加权融合：alpha * 向量分数 + (1-alpha) * 关键词分数

    Args:
        pool_ref: 数据库连接池
        query: 查询文本
        query_embedding: 查询向量
        kb_ids: 知识库ID列表
        top_k: 返回结果数
        alpha: 向量权重 (0-1)，默认0.7表示70%向量+30%关键词

    Returns:
        检索结果列表，包含融合后的相似度分数
    """
    if not kb_ids:
        return []

    # 1. 向量搜索（获取更多候选结果）
    vector_results = await vector_search_multi(
        pool_ref, query_embedding, kb_ids, top_k=top_k * 3, threshold=0.0
    )

    # 2. 关键词搜索（全文匹配）
    async with pool_ref.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 使用 LIKE 进行关键词匹配（简化版）
            # 将查询词拆分成多个关键词进行匹配
            keywords = [k.strip() for k in query.split() if k.strip()]

            if not keywords:
                keyword_results = []
            else:
                # 构建动态查询
                like_conditions = []
                params = [kb_ids]
                for _ in keywords:
                    like_conditions.append("c.content LIKE %s")
                    params.append(f"%{keywords[0]}%")  # 简化：只匹配第一个关键词

                # 如果有多个关键词，用 OR 连接
                where_clause = f" AND ({' OR '.join(like_conditions)})" if like_conditions else ""

                await cur.execute(f"""
                    SELECT
                        c.id as chunk_id,
                        c.doc_id,
                        d.name as doc_name,
                        d.kb_id,
                        kb.name as kb_name,
                        c.content,
                        0.5 as keyword_score
                    FROM chunks c
                    JOIN documents d ON d.id = c.doc_id
                    JOIN knowledge_bases kb ON kb.id = d.kb_id
                    WHERE d.kb_id = ANY(%s)
                      AND c.content IS NOT NULL
                      {where_clause}
                """, params)

                keyword_results = await cur.fetchall()

    # 3. 分数融合
    vector_scores = {r['chunk_id']: r['similarity'] for r in vector_results}
    keyword_scores = {r['chunk_id']: r.get('keyword_score', 0.5) for r in keyword_results}

    # 合并结果并计算融合分数
    merged = {}
    for r in vector_results:
        merged[r['chunk_id']] = {
            'vector_score': r['similarity'],
            'keyword_score': 0.0,
            'data': r
        }

    for r in keyword_results:
        chunk_id = r['chunk_id']
        if chunk_id in merged:
            merged[chunk_id]['keyword_score'] = r.get('keyword_score', 0.5)
        else:
            merged[chunk_id] = {
                'vector_score': 0.0,
                'keyword_score': r.get('keyword_score', 0.5),
                'data': r
            }

    # 计算融合分数并返回结果
    results = []
    for chunk_id, scores in merged.items():
        vector_score = scores['vector_score']
        keyword_score = scores['keyword_score']

        # 归一化关键词分数到 0-1 范围
        normalized_keyword_score = min(keyword_score, 1.0)

        # 融合分数：alpha * 向量分数 + (1-alpha) * 关键词分数
        combined_score = alpha * vector_score + (1 - alpha) * normalized_keyword_score

        result = scores['data'].copy()
        result['similarity'] = combined_score
        result['vector_score'] = vector_score
        result['keyword_score'] = normalized_keyword_score
        results.append(result)

    # 排序并取top_k
    results.sort(key=lambda x: x['similarity'], reverse=True)
    logger.info(f"Hybrid search: {len(kb_ids)} KBs, {len(results)} results (alpha={alpha})")

    return results[:top_k]


async def full_text_search(
    pool_ref,
    query: str,
    kb_ids: List[str],
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    全文搜索：基于关键词匹配的搜索

    使用 PostgreSQL 的 LIKE 进行关键词匹配，适合：
    - 搜索专业术语、专有名词
    - 精确匹配需求
    - 向量模型未覆盖的领域词汇

    Args:
        pool_ref: 数据库连接池
        query: 查询文本
        kb_ids: 知识库ID列表
        top_k: 返回结果数

    Returns:
        检索结果列表
    """
    if not kb_ids:
        return []

    async with pool_ref.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            # 分词：按空格和常见标点符号分割
            keywords = [k.strip() for k in re.split(r'[\s,，。.、;；:：]', query) if k.strip() and len(k.strip()) > 1]

            if not keywords:
                # 如果没有有效关键词，返回空结果
                return []

            # 确保kb_ids是列表
            kb_ids_list = list(kb_ids) if isinstance(kb_ids, (list, tuple)) else [kb_ids]

            # 构建动态查询
            # 简化版本：只使用 WHERE 子句中的 LIKE，不使用 CASE 表达式计分
            # 注意：SQL中参数出现的顺序是: d.kb_id = ANY(%s), 然后是 c.content LIKE %s
            # 所以参数顺序必须是: kb_ids_list 先, 然后 LIKE 参数
            params = []
            like_conditions = []

            for keyword in keywords[:5]:
                like_conditions.append("c.content LIKE %s")

            # 添加 LIKE 参数
            for keyword in keywords[:5]:
                params.append(f"%{keyword}%")

            # 添加 kb_ids (必须在 LIKE 参数之前，因为 SQL 中 ANY(%s) 在 LIKE %s 之前)
            # 但实际上我们按 SQL 中出现的顺序添加参数:
            # SQL: WHERE d.kb_id = ANY($1) AND (c.content LIKE $2 OR ...)
            params_with_kb_first = [kb_ids_list] + params
            params_with_kb_first.append(top_k)

            where_clause = f" AND ({' OR '.join(like_conditions)})"

            await cur.execute(f"""
                SELECT
                    c.id as chunk_id,
                    c.doc_id,
                    d.name as doc_name,
                    d.kb_id,
                    kb.name as kb_name,
                    c.content,
                    0.5 as keyword_score
                FROM chunks c
                JOIN documents d ON d.id = c.doc_id
                JOIN knowledge_bases kb ON kb.id = d.kb_id
                WHERE d.kb_id = ANY(%s)
                  AND c.content IS NOT NULL
                  {where_clause}
                ORDER BY c.id DESC
                LIMIT %s
            """, params_with_kb_first)

            results = await cur.fetchall()

            # 为结果添加相似度字段（使用 keyword_score）
            for r in results:
                r['similarity'] = r.get('keyword_score', 0.5)
                r['search_type'] = 'full_text'

            logger.info(f"Full text search: {len(kb_ids)} KBs, {len(results)} results, keywords={keywords[:3]}")

            return results


async def semantic_search(
    pool_ref,
    query_embedding: List[float],
    kb_ids: List[str],
    top_k: int = 5,
    threshold: float = 0.0
) -> List[Dict[str, Any]]:
    """
    语义搜索：纯向量相似度搜索

    基于 pgvector 的余弦相似度搜索，适合：
    - 语义相似度查询
    - 概念性搜索
    - 跨语言检索（如果向量模型支持）

    Args:
        pool_ref: 数据库连接池
        query_embedding: 查询向量
        kb_ids: 知识库ID列表
        top_k: 返回结果数
        threshold: 相似度阈值

    Returns:
        检索结果列表
    """
    results = await vector_search_multi(
        pool_ref, query_embedding, kb_ids, top_k=top_k, threshold=threshold
    )

    # 为结果添加搜索类型标记
    for r in results:
        r['search_type'] = 'semantic'

    return results


async def unified_search(
    pool_ref,
    query: str,
    query_embedding: List[float],
    kb_ids: List[str],
    method: str = "semantic_search",  # 默认语义搜索
    top_k: int = 5,
    alpha: float = 0.7,
    enable_rerank: bool = False,
    rerank_alpha: float = 0.3
) -> Dict[str, Any]:
    """
    统一搜索入口 - 支持多种检索策略

    参考 Dify 的检索策略设计：
    - semantic_search: 纯向量语义搜索
    - full_text_search: 纯全文关键词搜索
    - hybrid_search: 混合搜索（向量+关键词）
    - hybrid_rerank: 混合搜索 + Rerank 重排序

    Args:
        pool_ref: 数据库连接池
        query: 查询文本
        query_embedding: 查询向量
        kb_ids: 知识库ID列表
        method: 检索方法 (semantic_search, full_text_search, hybrid_search, hybrid_rerank)
        top_k: 返回结果数
        alpha: 混合搜索中的向量权重
        enable_rerank: 是否启用 Rerank
        rerank_alpha: Rerank 分数权重

    Returns:
        包含搜索结果和元数据的字典
    """
    from .config import rerank_service

    if not kb_ids:
        return {"results": [], "count": 0, "method": method}

    results = []

    if method == "semantic_search":
        results = await semantic_search(pool_ref, query_embedding, kb_ids, top_k)

    elif method == "full_text_search":
        results = await full_text_search(pool_ref, query, kb_ids, top_k)

    elif method == "hybrid_search":
        results = await hybrid_search(pool_ref, query, query_embedding, kb_ids, top_k, alpha)

    elif method == "hybrid_rerank":
        # 先执行混合搜索获取更多候选
        results = await hybrid_search(
            pool_ref, query, query_embedding, kb_ids,
            top_k=top_k * 3 if enable_rerank else top_k,
            alpha=alpha
        )

        # 如果启用 Rerank，进行重排序
        if enable_rerank and results:
            results = await rerank_service.rerank_hybrid(
                query=query,
                documents=results,
                top_k=top_k,
                alpha=rerank_alpha
            )

    return {
        "results": results,
        "count": len(results),
        "method": method,
        "rerank_enabled": enable_rerank
    }


# ==================== 限流中间件 ====================

def create_rate_limiter(endpoint: str = "default"):
    """
    创建速率限制依赖函数

    Args:
        endpoint: 端点类型 (default, chat, search, upload)

    Returns:
        FastAPI 依赖函数
    """
    async def rate_limit_check(
        request: Request,
        user: Optional[Dict] = None
    ):
        """
        执行速率限制检查

        Raises:
            HTTPException: 超出速率限制时抛出 429 错误
        """
        # 动态导入 rate_limiter，避免循环导入
        from .config import rate_limiter

        # 检查 Redis 是否可用
        try:
            import redis.asyncio as aioredis
            REDIS_AVAILABLE = True
        except ImportError:
            REDIS_AVAILABLE = False

        if not REDIS_AVAILABLE or rate_limiter is None:
            return  # Redis 不可用时不限流

        # 使用用户ID或IP地址作为标识
        if user and 'user_id' in user:
            identifier = user['user_id']
        else:
            identifier = request.client.host if request.client else "anonymous"

        allowed = await rate_limiter.is_allowed(identifier, endpoint)

        if not allowed:
            config = RATE_LIMIT.get(endpoint, RATE_LIMIT["default"])
            raise HTTPException(
                status_code=429,
                detail=f"请求过于频繁，请在 {config['window']} 秒后重试",
                headers={"Retry-After": str(config['window'])}
            )

        return {"rate_limit_remaining": await rate_limiter.get_remaining(identifier, endpoint)}

    return rate_limit_check


# 预定义的速率限制依赖（延迟初始化）
# 注意：这些函数需要在 rate_limiter 初始化后才能使用
rate_limit_chat = create_rate_limiter("chat")
rate_limit_search = create_rate_limiter("search")
rate_limit_upload = create_rate_limiter("upload")


# ==================== 安全增强：请求验证中间件 ====================

async def validate_request_size(request: Request):
    """验证请求大小，防止大文件攻击"""
    content_length = request.headers.get('content-length')
    if content_length:
        length = int(content_length)
        # 限制请求体最大 50MB
        if length > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="请求体过大")


# IP 白名单检查（可选）
async def check_ip_whitelist(request: Request):
    """检查 IP 白名单（如果启用）"""
    import os
    ip_whitelist = os.getenv("IP_WHITELIST", "")
    if not ip_whitelist:
        return  # 未启用白名单

    client_ip = request.client.host
    allowed_ips = [ip.strip() for ip in ip_whitelist.split(",")]

    if client_ip not in allowed_ips and "0.0.0.0/0" not in allowed_ips:
        raise HTTPException(status_code=403, detail="IP 地址未授权")


# ==================== 审计日志装饰器 ====================

import functools
import inspect

def audit_log(entity_type: str = None, action: str = None, id_param: str = None):
    """
    统一审计日志装饰器 - 类似 Spring Boot 的 @AuditLog 注解

    使用示例:
        # 方式1: 显式指定参数
        @audit_log(entity_type="knowledge_base", action="create", id_param="kb_id")
        async def create_knowledge_base(kb_id: str, ...):
            pass

        # 方式2: 自动推断（推荐）
        @audit_log()
        async def delete_document(kb_id: str, doc_id: str, user: Dict = Depends(...)):
            # 自动推断: entity_type=从函数名, action=delete, id_param=doc_id
            pass

    Args:
        entity_type: 实体类型，None 时自动从函数名推断
        action: 操作类型，None 时自动从函数名推断 (create/update/delete/get/list)
        id_param: ID 参数名，None 时自动选择最合适的参数
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 执行原函数
            result = await func(*args, **kwargs)

            # 自动推断参数
            _entity_type = entity_type
            _action = action
            _id_param = id_param

            # 从函数名推断
            func_name = func.__name__
            if _entity_type is None:
                # create_knowledge_base -> knowledge_base
                # delete_document -> document
                if '_' in func_name:
                    parts = func_name.split('_')
                    if len(parts) >= 2:
                        _entity_type = '_'.join(parts[1:])  # knowledge_base, document, etc.

            if _action is None:
                if 'create' in func_name or 'add' in func_name:
                    _action = 'create'
                elif 'update' in func_name or 'edit' in func_name or 'modify' in func_name:
                    _action = 'update'
                elif 'delete' in func_name or 'remove' in func_name:
                    _action = 'delete'
                elif 'get' in func_name or 'list' in func_name or 'query' in func_name:
                    _action = 'view'
                else:
                    _action = 'unknown'

            # 自动选择 ID 参数
            if _id_param is None:
                sig = inspect.signature(func)
                param_names = list(sig.parameters.keys())
                # 优先级: doc_id > app_id > kb_id > user_id > id
                for name in ['doc_id', 'document_id', 'app_id', 'application_id', 'kb_id', 'knowledge_base_id', 'user_id', 'id']:
                    if name in param_names:
                        _id_param = name
                        break

            # 获取用户信息
            user = kwargs.get('user')
            if not user:
                for arg in args:
                    if isinstance(arg, dict) and 'user_id' in arg:
                        user = arg
                        break

            if user:
                user_id = user.get('user_id')
                username = user.get('username')

                # 获取 entity_id
                entity_id = None

                # 1. 从返回值中获取
                if isinstance(result, dict) and 'id' in result:
                    entity_id = result['id']
                # 2. 从 kwargs 中获取
                elif _id_param and _id_param in kwargs:
                    entity_id = kwargs[_id_param]
                # 3. 从函数参数中获取
                elif _id_param:
                    sig = inspect.signature(func)
                    param_names = list(sig.parameters.keys())
                    if _id_param in param_names:
                        idx = param_names.index(_id_param)
                        if idx < len(args):
                            entity_id = args[idx]

                if entity_id:
                    # 获取 IP 和 User-Agent（从 Request 对象）
                    ip_address = None
                    user_agent = None
                    for arg in args:
                        # 检查是否是 FastAPI Request 对象
                        if hasattr(arg, 'client') and hasattr(arg, 'headers'):
                            ip_address = arg.client.host
                            user_agent = arg.headers.get('user-agent')
                            break

                    # 异步记录审计日志
                    try:
                        await log_audit(
                            entity_type=_entity_type or func_name,
                            entity_id=str(entity_id),
                            action=_action,
                            user_id=user_id,
                            username=username,
                            ip_address=ip_address,
                            user_agent=user_agent
                        )
                    except Exception as e:
                        logger.error(f"审计日志记录失败: {e}")

            return result
        return wrapper
    return decorator


def audit_log_with_changes(entity_type: str = None, action: str = None, id_param: str = None):
    """
    带变更记录的审计日志装饰器

    使用示例:
        @audit_log_with_changes(entity_type="knowledge_base", action="update")
        async def update_knowledge_base(kb_id: str, request: UpdateRequest, user=Depends(...)):
            # 返回包含 changes 的字典
            return {"message": "success", "changes": {...}}

    Args:
        entity_type: 实体类型
        action: 操作类型
        id_param: ID 参数名
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 执行原函数
            result = await func(*args, **kwargs)

            # 获取用户信息
            user = kwargs.get('user')
            if not user:
                for arg in args:
                    if isinstance(arg, dict) and 'user_id' in arg:
                        user = arg
                        break

            if user and isinstance(result, dict):
                user_id = user.get('user_id')
                username = user.get('username')
                changes = result.get('changes')

                # 自动推断参数
                _entity_type = entity_type
                _action = action
                _id_param = id_param

                func_name = func.__name__
                if _entity_type is None:
                    if '_' in func_name:
                        parts = func_name.split('_')
                        if len(parts) >= 2:
                            _entity_type = '_'.join(parts[1:])

                if _action is None:
                    _action = 'update'

                if _id_param is None:
                    sig = inspect.signature(func)
                    param_names = list(sig.parameters.keys())
                    for name in ['doc_id', 'app_id', 'kb_id', 'user_id', 'id']:
                        if name in param_names:
                            _id_param = name
                            break

                # 获取 entity_id
                entity_id = None
                if _id_param and _id_param in kwargs:
                    entity_id = kwargs[_id_param]
                elif isinstance(result, dict) and 'id' in result:
                    entity_id = result['id']
                else:
                    sig = inspect.signature(func)
                    param_names = list(sig.parameters.keys())
                    if _id_param and _id_param in param_names:
                        idx = param_names.index(_id_param)
                        if idx < len(args):
                            entity_id = args[idx]

                if entity_id and changes:
                    try:
                        await log_audit(
                            entity_type=_entity_type or func_name,
                            entity_id=str(entity_id),
                            action=_action,
                            user_id=user_id,
                            username=username,
                            changes=changes
                        )
                    except Exception as e:
                        logger.error(f"审计日志记录失败: {e}")

            return result
        return wrapper
    return decorator


# ==================== 审计日志辅助函数 ====================

async def log_audit(
    entity_type: str,
    entity_id: str,
    action: str,
    user_id: str = None,
    username: str = None,
    changes: dict = None,
    ip_address: str = None,
    user_agent: str = None
):
    """
    记录审计日志

    Args:
        entity_type: 实体类型 (knowledge_base, document, chunk, application, etc.)
        entity_id: 实体ID
        action: 操作类型 (create, update, delete)
        user_id: 用户ID
        username: 用户名（快照，防止用户被删除后无法追溯）
        changes: 变更内容 JSONB，格式: {"field_name": {"old": value, "new": value}}
        ip_address: IP地址
        user_agent: 用户代理
    """
    from . import config
    try:
        async with config.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO audit_logs (entity_type, entity_id, action, user_id, username, changes, ip_address, user_agent, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                """, (entity_type, entity_id, action, user_id, username, json.dumps(changes) if changes else None, ip_address, user_agent))
                await conn.commit()
    except Exception as e:
        # 审计日志记录失败不应影响主流程
        logger.error(f"Failed to log audit: {e}")


def get_changes_dict(old_data: dict, new_data: dict, fields_to_track: list = None) -> dict:
    """
    比较旧数据和新数据，生成变更字典

    Args:
        old_data: 旧数据
        new_data: 新数据
        fields_to_track: 需要追踪的字段列表，None表示追踪所有不同字段

    Returns:
        变更字典，格式: {"field_name": {"old": value, "new": value}}
    """
    if not fields_to_track:
        fields_to_track = set(old_data.keys()) | set(new_data.keys())

    changes = {}
    for field in fields_to_track:
        old_value = old_data.get(field)
        new_value = new_data.get(field)
        if old_value != new_value:
            changes[field] = {"old": old_value, "new": new_value}

    return changes
