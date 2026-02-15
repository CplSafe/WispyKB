"""
Embedding 缓存模块
参考：Dify (langgenius/dify) 的缓存实现

功能：
1. Redis缓存embedding，避免重复生成
2. 自动过期策略
3. 批量预热缓存
"""

import hashlib
import json
import logging
from typing import List, Optional, Dict
import redis.asyncio as redis

logger = logging.getLogger(__name__)


class EmbeddingCache:
    """
    Embedding缓存类

    使用Redis缓存embedding结果，避免重复调用Ollama
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0", ttl: int = 3600 * 24 * 7):
        """
        初始化缓存

        Args:
            redis_url: Redis连接URL
            ttl: 缓存过期时间（秒），默认7天
        """
        self.redis_url = redis_url
        self.ttl = ttl
        self._redis: Optional[redis.Redis] = None

    async def get_redis(self):
        """获取Redis连接（懒加载）"""
        if self._redis is None:
            try:
                self._redis = await redis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True
                )
                await self._redis.ping()
                logger.info("Embedding cache: Redis connected")
            except Exception as e:
                logger.warning(f"Redis not available: {e}")
                self._redis = None
        return self._redis

    def _make_key(self, text: str, model: str) -> str:
        """生成缓存key"""
        content = f"{model}:{text}"
        return f"emb:{hashlib.md5(content.encode()).hexdigest()}"

    async def get(self, text: str, model: str = "nomic-embed-text") -> Optional[List[float]]:
        """
        获取缓存的embedding

        Args:
            text: 文本内容
            model: 模型名称

        Returns:
            embedding向量，如果不存在返回None
        """
        redis_client = await self.get_redis()
        if redis_client is None:
            return None

        try:
            key = self._make_key(text, model)
            cached = await redis_client.get(key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.warning(f"Cache get error: {e}")
        return None

    async def set(self, text: str, embedding: List[float], model: str = "nomic-embed-text") -> bool:
        """
        设置缓存

        Args:
            text: 文本内容
            embedding: embedding向量
            model: 模型名称

        Returns:
            是否成功
        """
        redis_client = await self.get_redis()
        if redis_client is None:
            return False

        try:
            key = self._make_key(text, model)
            value = json.dumps(embedding)
            await redis_client.setex(key, self.ttl, value)
            return True
        except Exception as e:
            logger.warning(f"Cache set error: {e}")
            return False

    async def get_batch(self, texts: List[str], model: str = "nomic-embed-text") -> Dict[str, Optional[List[float]]]:
        """
        批量获取缓存

        Args:
            texts: 文本列表
            model: 模型名称

        Returns:
            {text: embedding} 字典，未命中的为None
        """
        redis_client = await self.get_redis()
        if redis_client is None:
            return {text: None for text in texts}

        try:
            keys = [self._make_key(text, model) for text in texts]
            values = await redis_client.mget(keys)

            result = {}
            for text, value in zip(texts, values):
                if value:
                    result[text] = json.loads(value)
                else:
                    result[text] = None
            return result
        except Exception as e:
            logger.warning(f"Batch cache get error: {e}")
            return {text: None for text in texts}

    async def set_batch(self, embeddings: Dict[str, List[float]], model: str = "nomic-embed-text") -> bool:
        """
        批量设置缓存

        Args:
            embeddings: {text: embedding} 字典
            model: 模型名称

        Returns:
            是否成功
        """
        redis_client = await self.get_redis()
        if redis_client is None:
            return False

        try:
            pipe = redis_client.pipeline()
            for text, embedding in embeddings.items():
                key = self._make_key(text, model)
                value = json.dumps(embedding)
                pipe.setex(key, self.ttl, value)
            await pipe.execute()
            return True
        except Exception as e:
            logger.warning(f"Batch cache set error: {e}")
            return False

    async def clear(self, pattern: str = "emb:*") -> bool:
        """
        清除缓存

        Args:
            pattern: key模式

        Returns:
            是否成功
        """
        redis_client = await self.get_redis()
        if redis_client is None:
            return False

        try:
            keys = await redis_client.keys(pattern)
            if keys:
                await redis_client.delete(*keys)
            return True
        except Exception as e:
            logger.warning(f"Cache clear error: {e}")
            return False

    async def get_stats(self) -> Dict[str, int]:
        """
        获取缓存统计

        Returns:
            统计信息
        """
        redis_client = await self.get_redis()
        if redis_client is None:
            return {"total": 0}

        try:
            key_count = len(await redis_client.keys("emb:*"))
            return {"total": key_count}
        except Exception as e:
            logger.warning(f"Cache stats error: {e}")
            return {"total": 0}


# 全局缓存实例
embedding_cache = EmbeddingCache()
