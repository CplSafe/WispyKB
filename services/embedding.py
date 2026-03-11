# EmbeddingService - 向量嵌入生成服务
# 从 main_pgvector.py 拆分
# 支持 Ollama 和 Xinference 提供商

import logging
import os
from typing import List, Optional
from enum import Enum

import httpx

logger = logging.getLogger(__name__)


class EmbeddingProvider(str, Enum):
    """Embedding 服务提供商"""
    OLLAMA = "ollama"
    XINFERENCE = "xinference"
    VLLM = "vllm"  # 添加 vLLM 支持


class EmbeddingService:
    """
    向量嵌入生成服务

    支持多种提供商：
    - Ollama: 本地部署简单，支持 nomic-embed-text 等模型
    - Xinference: 支持更多中文优化模型 (bge-large-zh-v1.5, m3e-large 等)
    """

    def __init__(
        self,
        model: str,
        base_url: str,
        provider: EmbeddingProvider = EmbeddingProvider.OLLAMA,
        xinference_base_url: Optional[str] = None
    ):
        """
        初始化 Embedding 服务

        Args:
            model: 模型名称
            base_url: 服务地址
            provider: 服务提供商 (ollama/xinference/vllm)
            xinference_base_url: Xinference 服务地址（如果使用 Xinference）
        """
        self.model = model
        self.base_url = base_url
        self.provider = provider
        self.xinference_base_url = xinference_base_url or os.getenv("XINFERENCE_BASE_URL", "http://localhost:9997")

    async def generate(self, text: str) -> List[float]:
        """生成单个文本的向量嵌入"""
        if not text or not text.strip():
            logger.warning("输入文本为空，返回空向量")
            return []

        if self.provider == EmbeddingProvider.VLLM:
            return await self._generate_vllm(text)
        elif self.provider == EmbeddingProvider.XINFERENCE:
            return await self._generate_xinference(text)
        else:
            return await self._generate_ollama(text)

    async def _generate_vllm(self, text: str) -> List[float]:
        """使用 vLLM (OpenAI 兼容 API) 生成向量嵌入"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/v1/embeddings",
                    json={
                        "model": self.model,
                        "input": text
                    }
                )
                response.raise_for_status()
                data = response.json()
                # OpenAI 格式: {"data": [{"embedding": [...]}]}
                results = data.get("data", [])
                if results and len(results) > 0:
                    embedding = results[0].get("embedding", [])
                    if embedding:
                        logger.debug(f"vLLM Embedding 成功: 维度={len(embedding)}")
                    return embedding
                return []
        except httpx.HTTPStatusError as e:
            logger.error(f"vLLM Embedding 失败: {e.response.status_code} - {e.response.text}")
            return []
        except Exception as e:
            logger.error(f"vLLM Embedding 请求失败: {e}")
            return []

    async def _generate_ollama(self, text: str) -> List[float]:
        """使用 Ollama 生成向量嵌入"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text}
                )
                response.raise_for_status()
                data = response.json()
                embedding = data.get("embedding", [])
                if embedding:
                    logger.debug(f"Ollama Embedding 成功: 维度={len(embedding)}")
                return embedding
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.error(f"Ollama 模型 {self.model} 不存在，请先拉取: ollama pull {self.model}")
            else:
                logger.error(f"Ollama Embedding 失败: {e}")
            return []
        except Exception as e:
            logger.error(f"Ollama Embedding 请求失败: {e}")
            return []

    async def _generate_xinference(self, text: str) -> List[float]:
        """使用 Xinference 生成向量嵌入"""
        try:
            # Xinference 使用 /v1/embeddings 端点
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.xinference_base_url}/v1/embeddings",
                    json={
                        "model": self.model,
                        "input": text
                    }
                )
                response.raise_for_status()
                data = response.json()
                # Xinference 返回格式: {"data": [{"embedding": [...]}]}
                results = data.get("data", [])
                if results and len(results) > 0:
                    embedding = results[0].get("embedding", [])
                    if embedding:
                        logger.debug(f"Xinference Embedding 成功: 维度={len(embedding)}")
                    return embedding
                return []
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.error(f"Xinference 模型 {self.model} 不存在或未启动")
            else:
                logger.error(f"Xinference Embedding 失败: {e}")
            return []
        except Exception as e:
            logger.error(f"Xinference Embedding 请求失败: {e}")
            return []

    async def generate_batch(self, texts: List[str]) -> List[List[float]]:
        """
        批量生成向量嵌入

        Args:
            texts: 文本列表

        Returns:
            向量列表
        """
        embeddings = []
        for text in texts:
            embedding = await self.generate(text)
            embeddings.append(embedding)
        return embeddings

    async def generate_batch_parallel(self, texts: List[str], max_concurrent: int = 5) -> List[List[float]]:
        """
        并发生成向量嵌入（提高性能）

        Args:
            texts: 文本列表
            max_concurrent: 最大并发数

        Returns:
            向量列表
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)

        async def generate_with_semaphore(text: str) -> List[float]:
            async with semaphore:
                return await self.generate(text)

        tasks = [generate_with_semaphore(text) for text in texts]
        return await asyncio.gather(*tasks)

    def get_dimension(self) -> int:
        """获取当前模型的向量维度"""
        from core.config import get_embedding_model_config
        config = get_embedding_model_config(self.model)
        return config.get("dimension", 768)
