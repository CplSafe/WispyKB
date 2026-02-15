# RerankService - Rerank 重排序服务
# 从 main_pgvector.py 拆分
# 支持 Ollama 和 Xinference 提供商

import logging
import os
from typing import Dict, List, Any, Optional
from enum import Enum

import httpx

logger = logging.getLogger(__name__)


class RerankProvider(str, Enum):
    """Rerank 服务提供商"""
    OLLAMA = "ollama"
    XINFERENCE = "xinference"


class RerankService:
    """
    Rerank 重排序服务 - 参考 Dify 和 MaxKB 的 Rerank 实现

    参考：
    - https://github.com/langgenius/dify/tree/main/api/core/rag/rerank
    - https://github.com/1Panel-dev/MaxKB

    作用：
    - 向量搜索获取候选结果后，用 Rerank 模型重新打分
    - 显著提高搜索准确率 (通常能提升 10-30%)
    - 支持多种 Rerank 模型

    支持的模型：
    - bge-reranker-v2-m3: 轻量级，支持多语言，中文效果优秀 (Ollama)
    - bge-reranker-v2-m4: 更大更准确 (Xinference)
    - bce-reranker-base_v1: 网易 BCE 中文优化 (Xinference)
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        provider: RerankProvider = RerankProvider.OLLAMA,
        xinference_base_url: Optional[str] = None
    ):
        """
        初始化 Rerank 服务

        Args:
            model: Rerank 模型名称
            base_url: Ollama 服务地址
            provider: 服务提供商 (ollama/xinference)
            xinference_base_url: Xinference 服务地址（如果使用 Xinference）
        """
        self.model = model
        self.base_url = base_url
        self.provider = provider
        self.xinference_base_url = xinference_base_url or os.getenv("XINFERENCE_BASE_URL", "http://localhost:9997")
        self.enabled = True

    async def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: Optional[int] = None,
        max_chunks_per_doc: int = 5
    ) -> List[Dict[str, Any]]:
        """
        对搜索结果进行重排序

        Args:
            query: 用户查询
            documents: 候选文档列表，每个文档包含 content 字段
            top_k: 返回前 K 个结果
            max_chunks_per_doc: 每个文档最多保留多少个 chunk

        Returns:
            重排序后的文档列表，添加 rerank_score 字段
        """
        if not documents:
            return []

        if not self.enabled:
            logger.warning("Rerank 服务未启用，返回原始结果")
            return documents

        # 提取文档内容
        doc_texts = [d.get('content', '') for d in documents]

        # 去重并保留原始索引
        unique_texts = []
        unique_indices = []
        seen = set()
        for i, text in enumerate(doc_texts):
            if text and text not in seen:
                unique_texts.append(text)
                unique_indices.append(i)
                seen.add(text)

        if not unique_texts:
            return documents

        if self.provider == RerankProvider.XINFERENCE:
            return await self._rerank_xinference(query, unique_texts, unique_indices, documents, top_k)
        else:
            return await self._rerank_ollama(query, unique_texts, unique_indices, documents, top_k)

    async def _rerank_ollama(
        self,
        query: str,
        unique_texts: List[str],
        unique_indices: List[int],
        documents: List[Dict[str, Any]],
        top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """使用 Ollama 进行 Rerank"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {
                    "model": self.model,
                    "query": query,
                    "documents": unique_texts,
                    "top_k": top_k if top_k else len(unique_texts)
                }

                response = await client.post(
                    f"{self.base_url}/api/rerank",
                    json=payload,
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()

                results = data.get("results", [])

                if not results:
                    logger.warning(f"Rerank 返回空结果: {data}")
                    return documents

                reranked_docs = []
                for r in results[:top_k] if top_k else results:
                    original_index = unique_indices[r['index']]
                    doc = documents[original_index].copy()
                    doc['rerank_score'] = r.get('score', 0.0)
                    doc['original_index'] = original_index
                    reranked_docs.append(doc)

                logger.info(f"Ollama Rerank 完成: 查询='{query[:30]}...', 候选={len(documents)}, 返回={len(reranked_docs)}")
                return reranked_docs

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(f"Rerank 模型 {self.model} 不可用，请先拉取: ollama pull {self.model}")
                self.enabled = False
            else:
                logger.error(f"Rerank API 错误: {e}")
            return documents
        except Exception as e:
            logger.error(f"Rerank 失败: {e}")
            return documents

    async def _rerank_xinference(
        self,
        query: str,
        unique_texts: List[str],
        unique_indices: List[int],
        documents: List[Dict[str, Any]],
        top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """使用 Xinference 进行 Rerank"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Xinference 使用 /v1/rerank 端点
                payload = {
                    "model": self.model,
                    "query": query,
                    "documents": unique_texts,
                    "top_n": top_k if top_k else len(unique_texts)
                }

                response = await client.post(
                    f"{self.xinference_base_url}/v1/rerank",
                    json=payload,
                    timeout=30.0
                )
                response.raise_for_status()
                data = response.json()

                # Xinference 返回格式: {"results": [{"index": 0, "relevance_score": 0.95, ...}]}
                results = data.get("results", [])

                if not results:
                    logger.warning(f"Xinference Rerank 返回空结果: {data}")
                    return documents

                reranked_docs = []
                for r in results[:top_k] if top_k else results:
                    original_index = unique_indices[r['index']]
                    doc = documents[original_index].copy()
                    doc['rerank_score'] = r.get('relevance_score', r.get('score', 0.0))
                    doc['original_index'] = original_index
                    reranked_docs.append(doc)

                logger.info(f"Xinference Rerank 完成: 查询='{query[:30]}...', 候选={len(documents)}, 返回={len(reranked_docs)}")
                return reranked_docs

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.error(f"Xinference Rerank 模型 {self.model} 不存在或未启动")
                self.enabled = False
            else:
                logger.error(f"Xinference Rerank API 错误: {e}")
            return documents
        except Exception as e:
            logger.error(f"Xinference Rerank 失败: {e}")
            return documents

    async def rerank_hybrid(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 5,
        alpha: float = 0.3
    ) -> List[Dict[str, Any]]:
        """
        混合重排序：结合向量分数和 Rerank 分数

        Args:
            query: 用户查询
            documents: 候选文档列表，每个文档需包含 similarity 字段
            top_k: 返回前 K 个结果
            alpha: Rerank 分数权重 (0-1)，越高越信任 Rerank

        Returns:
            重排序后的文档列表
        """
        # 先执行 Rerank
        reranked = await self.rerank(query, documents, top_k=top_k * 2)

        if not reranked:
            return documents[:top_k]

        # 计算混合分数
        for doc in reranked:
            vector_score = doc.get('similarity', 0.0)
            rerank_score = doc.get('rerank_score', 0.0)

            # 归一化到 0-1
            normalized_vector = max(0, min(1, vector_score))
            normalized_rerank = max(0, min(1, rerank_score))

            # 融合分数
            doc['hybrid_score'] = (1 - alpha) * normalized_vector + alpha * normalized_rerank
            doc['final_score'] = doc['hybrid_score']

        # 按最终分数排序
        reranked.sort(key=lambda x: x.get('final_score', 0), reverse=True)

        return reranked[:top_k]
