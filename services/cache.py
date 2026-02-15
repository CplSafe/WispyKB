# Redis 缓存管理器
# 从 main_pgvector.py 拆分的缓存服务

import json
import logging
from typing import Optional, Any, Dict

try:
    import aioredis
    from redis import RedisError
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    RedisError = Exception
    aioredis = None

logger = logging.getLogger(__name__)

# Redis 配置（从主模块传入）
REDIS_CONFIG = {
    "host": "localhost",
    "port": 6379,
    "db": 0,
    "password": None,
    "socket_connect_timeout": 5,
    "socket_keepalive": True,
    "decode_responses": True,
}


class CacheManager:
    """Redis 缓存管理器 - 参考 Dify 的缓存策略"""

    def __init__(self, config=None):
        self.enabled = REDIS_AVAILABLE
        self.config = config or REDIS_CONFIG
        self.client = None

    async def init_redis(self):
        """初始化 Redis 连接"""
        if not self.enabled:
            logger.warning("Redis 不可用，缓存功能已禁用")
            return

        try:
            self.client = await aioredis.from_url(
                f"redis://{self.config['host']}:{self.config['port']}/{self.config['db']}",
                password=self.config.get("password"),
                socket_connect_timeout=self.config.get("socket_connect_timeout", 5),
                socket_keepalive=self.config.get("socket_keepalive", True),
                decode_responses=self.config.get("decode_responses", True),
            )
            await self.client.ping()
            logger.info("Redis 连接成功")
        except Exception as e:
            logger.error(f"Redis 连接失败: {e}")
            self.client = None
            self.enabled = False

    async def close_redis(self):
        """关闭 Redis 连接"""
        if self.client:
            await self.client.close()
            logger.info("Redis 连接已关闭")

    async def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        if not self.enabled or not self.client:
            return None
        try:
            value = await self.client.get(key)
            if value:
                return json.loads(value)
            return None
        except (RedisError, json.JSONDecodeError):
            return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """设置缓存"""
        if not self.enabled or not self.client:
            return False
        try:
            await self.client.setex(key, ttl, json.dumps(value, ensure_ascii=False))
            return True
        except RedisError:
            return False

    async def delete(self, key: str) -> bool:
        """删除缓存"""
        if not self.enabled or not self.client:
            return False
        try:
            await self.client.delete(key)
            return True
        except RedisError:
            return False

    async def delete_pattern(self, pattern: str) -> int:
        """批量删除缓存"""
        if not self.enabled or not self.client:
            return 0
        try:
            keys = await self.client.keys(pattern)
            if keys:
                return await self.client.delete(*keys)
            return 0
        except RedisError:
            return 0

    async def exists(self, key: str) -> bool:
        """检查缓存是否存在"""
        if not self.enabled or not self.client:
            return False
        try:
            return await self.client.exists(key) > 0
        except RedisError:
            return False

    async def incr(self, key: str, expire: int = 60) -> int:
        """递增计数器（用于限流）"""
        if not self.enabled or not self.client:
            return 0
        try:
            value = await self.client.incr(key)
            if value == 1:  # 首次设置，添加过期时间
                await self.client.expire(key, expire)
            return value
        except RedisError:
            return 0

    async def acquire_lock(self, lock_name: str, timeout: int = 10) -> bool:
        """获取分布式锁"""
        if not self.enabled or not self.client:
            return True  # Redis 不可用时直接返回 True
        try:
            lock_key = f"lock:{lock_name}"
            return await self.client.set(lock_key, "1", nx=True, ex=timeout)
        except RedisError:
            return True

    async def release_lock(self, lock_name: str) -> bool:
        """释放分布式锁"""
        if not self.enabled or not self.client:
            return True
        try:
            lock_key = f"lock:{lock_name}"
            await self.client.delete(lock_key)
            return True
        except RedisError:
            return False


# ==================== 限流器 ====================

class RateLimiter:
    """API 限流器 - 滑动窗口算法"""

    def __init__(self, cache_manager: 'CacheManager'):
        self.cache = cache_manager

    async def is_allowed(self, identifier: str, endpoint: str = "default", rate_limit_config: Optional[Dict] = None) -> bool:
        """检查是否允许请求"""
        if not rate_limit_config:
            from core.config import RATE_LIMIT
            rate_limit_config = RATE_LIMIT

        config = rate_limit_config.get(endpoint, rate_limit_config["default"])
        max_requests = config["requests"]
        window = config["window"]

        key = f"ratelimit:{endpoint}:{identifier}"
        current = await self.cache.incr(key, window)

        return current <= max_requests

    async def get_remaining(self, identifier: str, endpoint: str = "default", rate_limit_config: Optional[Dict] = None) -> int:
        """获取剩余请求数"""
        if not rate_limit_config:
            from core.config import RATE_LIMIT
            rate_limit_config = RATE_LIMIT

        config = rate_limit_config.get(endpoint, rate_limit_config["default"])
        key = f"ratelimit:{endpoint}:{identifier}"
        current = await self.cache.incr(key, config["window"])
        return max(0, config["requests"] - current)
