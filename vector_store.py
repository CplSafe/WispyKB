"""
向量存储抽象层

支持多种向量数据库后端：
- pgvector: PostgreSQL + pgvector 扩展
- milvus: Milvus 分布式向量数据库
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """向量距离度量类型"""
    COSINE = "cosine"        # 余弦相似度
    L2 = "l2"                # 欧氏距离
    IP = "ip"                # 内积


@dataclass
class VectorConfig:
    """向量存储配置"""
    dimension: int = 768              # 向量维度
    metric_type: MetricType = MetricType.COSINE
    index_type: str = "hnsw"          # 索引类型: hnsw, ivf_flat, diskann
    index_params: Optional[Dict[str, Any]] = None


@dataclass
class ChunkResult:
    """向量检索结果"""
    chunk_id: str
    document_id: str
    content: str
    score: float
    metadata: Optional[Dict[str, Any]] = None
    chunk_index: Optional[int] = None


class VectorStore(ABC):
    """
    向量存储抽象接口

    所有向量存储实现都需要继承这个类
    """

    def __init__(self, config: VectorConfig):
        self.config = config
        self._initialized = False

    @abstractmethod
    async def initialize(self):
        """初始化向量存储"""
        pass

    @abstractmethod
    async def close(self):
        """关闭连接"""
        pass

    @abstractmethod
    async def insert(
        self,
        chunk_id: str,
        document_id: str,
        content: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
        chunk_index: int = 0
    ) -> bool:
        """
        插入向量

        Args:
            chunk_id: 分块ID
            document_id: 文档ID
            content: 文本内容
            embedding: 向量
            metadata: 元数据
            chunk_index: 分块索引

        Returns:
            是否成功
        """
        pass

    @abstractmethod
    async def insert_batch(
        self,
        items: List[Dict[str, Any]]
    ) -> int:
        """
        批量插入向量

        Args:
            items: 插入项列表，每项包含 chunk_id, document_id, content, embedding, metadata, chunk_index

        Returns:
            成功插入的数量
        """
        pass

    @abstractmethod
    async def search(
        self,
        embedding: List[float],
        top_k: int = 5,
        document_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[ChunkResult]:
        """
        向量搜索

        Args:
            embedding: 查询向量
            top_k: 返回结果数量
            document_ids: 限定搜索的文档ID列表
            filters: 过滤条件

        Returns:
            搜索结果列表
        """
        pass

    @abstractmethod
    async def delete(self, chunk_id: str) -> bool:
        """删除向量"""
        pass

    @abstractmethod
    async def delete_by_document(self, document_id: str) -> int:
        """
        删除文档的所有向量

        Returns:
            删除的数量
        """
        pass

    @abstractmethod
    async def count(self, document_id: Optional[str] = None) -> int:
        """
        统计向量数量

        Args:
            document_id: 指定文档ID，None 表示总数

        Returns:
            向量数量
        """
        pass

    @abstractmethod
    async def get_info(self) -> Dict[str, Any]:
        """获取向量存储信息"""
        pass


class VectorStoreFactory:
    """向量存储工厂"""

    _stores = {
        'pgvector': None,
        'milvus': None,
    }

    @classmethod
    async def create_store(
        cls,
        store_type: str,
        config: VectorConfig,
        **kwargs
    ) -> VectorStore:
        """
        创建向量存储实例

        Args:
            store_type: 存储类型 ('pgvector' 或 'milvus')
            config: 向量配置
            **kwargs: 额外参数

        Returns:
            VectorStore 实例
        """
        if store_type == 'pgvector':
            from vector_store_pgvector import PgVectorStore
            store = PgVectorStore(config, **kwargs)
        elif store_type == 'milvus':
            from vector_store_milvus import MilvusStore
            store = MilvusStore(config, **kwargs)
        else:
            raise ValueError(f"不支持的向量存储类型: {store_type}")

        await store.initialize()
        cls._stores[store_type] = store
        logger.info(f"向量存储初始化成功: {store_type}")
        return store

    @classmethod
    def get_store(cls, store_type: str) -> Optional[VectorStore]:
        """获取已创建的存储实例"""
        return cls._stores.get(store_type)


# 全局向量存储实例
vector_store: Optional[VectorStore] = None


async def init_vector_store(
    store_type: str = 'pgvector',
    config: Optional[VectorConfig] = None,
    **kwargs
) -> VectorStore:
    """
    初始化全局向量存储

    Args:
        store_type: 存储类型
        config: 向量配置
        **kwargs: 额外参数

    Returns:
        VectorStore 实例
    """
    global vector_store

    if config is None:
        config = VectorConfig()

    vector_store = await VectorStoreFactory.create_store(store_type, config, **kwargs)
    return vector_store


def get_vector_store() -> Optional[VectorStore]:
    """获取全局向量存储实例"""
    return vector_store
