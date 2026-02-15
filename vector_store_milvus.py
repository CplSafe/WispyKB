"""
Milvus 向量存储实现

使用 Milvus 分布式向量数据库进行向量存储和检索
适合大规模数据（十亿级向量）
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

try:
    from pymilvus import MilvusClient, MilvusException
    from pymilvus.milvus_client.index import IndexParams
    from pymilvus import FieldSchema, CollectionSchema, DataType
    MILVUS_AVAILABLE = True
except ImportError:
    MILVUS_AVAILABLE = False
    IndexParams = None
    FieldSchema = None
    CollectionSchema = None
    DataType = None

from vector_store import VectorStore, VectorConfig, ChunkResult, MetricType

logger = logging.getLogger(__name__)


class MilvusStore(VectorStore):
    """
    Milvus 向量存储实现

    适合大规模数据（十亿级向量）
    优势：高性能、分布式、支持多种索引类型
    """

    # 集合名称
    COLLECTION_NAME = "knowledge_chunks"

    def __init__(
        self,
        config: VectorConfig,
        milvus_uri: str = "http://localhost:19530",
        token: str = "",
        collection_name: str = None
    ):
        super().__init__(config)

        if not MILVUS_AVAILABLE:
            raise RuntimeError(
                "pymilvus 未安装，请运行: pip install pymilvus"
            )

        self.milvus_uri = milvus_uri
        self.token = token
        self.collection_name = collection_name or self.COLLECTION_NAME
        self.client: Optional[MilvusClient] = None

    async def initialize(self):
        """初始化 Milvus 连接"""
        if self._initialized:
            return

        try:
            self.client = MilvusClient(
                uri=self.milvus_uri,
                token=self.token if self.token else None
            )

            # 检查或创建集合
            await self._ensure_collection()

            self._initialized = True
            logger.info(f"MilvusStore 初始化完成: {self.milvus_uri}")

        except MilvusException as e:
            logger.error(f"Milvus 连接失败: {e}")
            raise

    async def _ensure_collection(self):
        """确保集合存在并已加载"""
        # 检查集合是否存在
        has_collection = self.client.has_collection(self.collection_name)

        if not has_collection:
            logger.info(f"创建 Milvus 集合: {self.collection_name}")

            # 手动定义 schema
            schema = CollectionSchema(fields=[
                FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=255, is_primary=True, auto_id=False),
                FieldSchema(name="kb_id", dtype=DataType.VARCHAR, max_length=255),
                FieldSchema(name="document_id", dtype=DataType.VARCHAR, max_length=255),
                FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=65535),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.config.dimension),
                FieldSchema(name="chunk_index", dtype=DataType.INT64),
                FieldSchema(name="metadata", dtype=DataType.JSON),
                FieldSchema(name="created_at", dtype=DataType.VARCHAR, max_length=100),
            ], description="Knowledge chunks collection", enable_dynamic_field=True)

            # 使用 MilvusClient 的 v2v 模式创建集合
            # 需要使用低级 API 来创建自定义 schema
            from pymilvus import connections, Collection, utility

            # 连接到 Milvus
            connections.connect("default", uri=self.milvus_uri)

            try:
                # 如果已存在则删除
                if utility.has_collection(self.collection_name):
                    utility.drop_collection(self.collection_name)

                # 创建集合
                collection = Collection(
                    name=self.collection_name,
                    schema=schema,
                    consistency_level="Strong"
                )

                # 创建索引
                index_type = self.config.index_type.upper()
                index_params = {}

                if index_type == "HNSW":
                    index_params = {
                        "index_type": "HNSW",
                        "metric_type": self._map_metric_type(self.config.metric_type),
                        "params": {
                            "M": 16,
                            "efConstruction": 256
                        }
                    }
                elif index_type == "IVF_FLAT":
                    index_params = {
                        "index_type": "IVF_FLAT",
                        "metric_type": self._map_metric_type(self.config.metric_type),
                        "params": {
                            "nlist": 128
                        }
                    }
                elif index_type == "FLAT":
                    index_params = {
                        "index_type": "FLAT",
                        "metric_type": self._map_metric_type(self.config.metric_type),
                        "params": {}
                    }
                else:
                    # 默认使用 HNSW
                    index_params = {
                        "index_type": "HNSW",
                        "metric_type": self._map_metric_type(self.config.metric_type),
                        "params": {
                            "M": 16,
                            "efConstruction": 256
                        }
                    }

                collection.create_index(
                    field_name="embedding",
                    index_params=index_params
                )

                # 加载集合
                collection.load()

                logger.info(f"创建集合成功: {self.collection_name}, 索引: {self.config.index_type}")

            finally:
                connections.disconnect("default")
        else:
            # 集合已存在，确保已加载
            try:
                from pymilvus import connections, Collection
                connections.connect("default", uri=self.milvus_uri)
                try:
                    collection = Collection(self.collection_name)
                    # 检查是否已加载，如果没有则加载
                    from pymilvus import utility
                    load_state = utility.load_state(self.collection_name)
                    if load_state != 'Loaded':
                        collection.load()
                        logger.info(f"加载已存在的集合: {self.collection_name}")
                finally:
                    connections.disconnect("default")
            except Exception as e:
                logger.warning(f"加载集合失败（可能已加载）: {e}")

    def _map_metric_type(self, metric: MetricType) -> str:
        """映射度量类型"""
        mapping = {
            MetricType.COSINE: "COSINE",
            MetricType.L2: "L2",
            MetricType.IP: "IP",
        }
        return mapping.get(metric, "COSINE")

    def _get_index_params(self) -> IndexParams:
        """获取索引参数"""
        index_type = self.config.index_type.lower()

        params = IndexParams()
        params.add_index(
            field_name="embedding",
            index_type=index_type.upper(),
        )

        return params

    async def close(self):
        """关闭连接"""
        if self.client:
            self.client.close()
            self._initialized = False

    async def insert(
        self,
        chunk_id: str,
        document_id: str,
        content: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
        chunk_index: int = 0,
        kb_id: str = None
    ) -> bool:
        """插入单个向量"""
        try:
            data = [{
                'chunk_id': chunk_id,
                'kb_id': kb_id or '',
                'document_id': document_id,
                'content': content,
                'embedding': embedding,
                'chunk_index': chunk_index,
                'metadata': metadata or {},
                'created_at': datetime.now().isoformat(),
            }]

            self.client.insert(
                collection_name=self.collection_name,
                data=data
            )
            return True

        except MilvusException as e:
            logger.error(f"插入向量失败: {e}")
            return False

    async def insert_batch(
        self,
        items: List[Dict[str, Any]]
    ) -> int:
        """批量插入向量"""
        if not items:
            return 0

        try:
            data = []
            for item in items:
                data.append({
                    'chunk_id': item['chunk_id'],
                    'kb_id': item.get('kb_id', ''),
                    'document_id': item['document_id'],
                    'content': item['content'],
                    'embedding': item['embedding'],
                    'chunk_index': item.get('chunk_index', 0),
                    'metadata': item.get('metadata') or {},
                    'created_at': datetime.now().isoformat(),
                })

            self.client.insert(
                collection_name=self.collection_name,
                data=data
            )

            # 手动 flush 确保数据被持久化
            from pymilvus import Collection, connections
            connections.connect("default", uri=self.milvus_uri)
            try:
                collection = Collection(self.collection_name)
                collection.flush()
                logger.debug(f"Milvus flush 完成，插入 {len(data)} 条数据")
            finally:
                connections.disconnect("default")

            return len(items)

        except MilvusException as e:
            logger.error(f"批量插入向量失败: {e}")
            return 0

    async def search(
        self,
        embedding: List[float],
        top_k: int = 5,
        document_ids: Optional[List[str]] = None,
        kb_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[ChunkResult]:
        """向量搜索"""
        try:
            # 构建过滤表达式 - 使用 kb_id 而不是 document_id
            filter_expr = None

            if kb_ids:
                # 使用 kb_id 过滤
                ids_str = ', '.join([f'"{kid}"' for kid in kb_ids])
                filter_expr = f'kb_id in [{ids_str}]'
            elif document_ids:
                # 兼容旧的 document_id 过滤
                ids_str = ', '.join([f'"{did}"' for did in document_ids])
                filter_expr = f'document_id in [{ids_str}]'

            # TODO: 支持 filters 元数据过滤
            # if filters:
            #     for key, value in filters.items():
            #         expr = f'metadata["{key}"] == "{value}"'
            #         filter_expr = f"{filter_expr} and {expr}" if filter_expr else expr

            results = self.client.search(
                collection_name=self.collection_name,
                data=[embedding],
                limit=top_k,
                output_fields=[
                    'chunk_id', 'kb_id', 'document_id', 'content',
                    'chunk_index', 'metadata'
                ],
                filter=filter_expr,
            )

            chunks = []
            for result in results[0]:  # 第一条查询的结果
                chunks.append(ChunkResult(
                    chunk_id=result['entity'].get('chunk_id', ''),
                    document_id=result['entity'].get('document_id', ''),
                    content=result['entity'].get('content', ''),
                    score=float(result['distance']),
                    metadata=result['entity'].get('metadata'),
                    chunk_index=result['entity'].get('chunk_index'),
                ))

            return chunks

        except MilvusException as e:
            logger.error(f"向量搜索失败: {e}")
            return []

    async def delete(self, chunk_id: str) -> bool:
        """删除单个向量"""
        try:
            self.client.delete(
                collection_name=self.collection_name,
                filter=f'chunk_id == "{chunk_id}"'
            )
            return True

        except MilvusException as e:
            logger.error(f"删除向量失败: {e}")
            return False

    async def delete_by_document(self, document_id: str) -> int:
        """删除文档的所有向量"""
        try:
            # 先查询要删除的 chunk_id
            results = self.client.query(
                collection_name=self.collection_name,
                filter=f'document_id == "{document_id}"',
                output_fields=['chunk_id']
            )
            chunk_ids = [r['chunk_id'] for r in results]
            count = len(chunk_ids)

            if count == 0:
                return 0

            # 使用主键删除（Milvus 需要通过主键删除）
            # 使用低级 API 因为 MilvusClient.delete 不支持表达式
            from pymilvus import Collection, connections
            connections.connect("default", uri=self.milvus_uri)
            try:
                collection = Collection(self.collection_name)
                # 批量删除主键
                collection.delete(f'chunk_id in {str(chunk_ids)}')
                collection.flush()  # 确保删除生效
                logger.info(f"已删除文档 {document_id} 的 {count} 个向量")
            finally:
                connections.disconnect("default")

            return count

        except MilvusException as e:
            logger.error(f"删除文档向量失败: {e}")
            return 0

    async def count(self, document_id: Optional[str] = None) -> int:
        """统计向量数量"""
        try:
            if document_id:
                results = self.client.query(
                    collection_name=self.collection_name,
                    filter=f'document_id == "{document_id}"',
                    output_fields=['chunk_id']
                )
                return len(results)
            else:
                # 获取整个集合的统计信息
                stats = self.client.get_collection_stats(
                    collection_name=self.collection_name
                )
                return int(stats.get('row_count', 0))

        except MilvusException as e:
            logger.error(f"统计向量数量失败: {e}")
            return 0

    async def get_info(self) -> Dict[str, Any]:
        """获取向量存储信息"""
        try:
            stats = self.client.get_collection_stats(
                collection_name=self.collection_name
            )

            return {
                'type': 'milvus',
                'total_vectors': int(stats.get('row_count', 0)),
                'dimension': self.config.dimension,
                'metric_type': self.config.metric_type.value,
                'index_type': self.config.index_type,
                'collection_name': self.collection_name,
                'uri': self.milvus_uri,
            }

        except MilvusException as e:
            logger.error(f"获取向量存储信息失败: {e}")
            return {'type': 'milvus', 'error': str(e)}


# Milvus 配置类
class MilvusConfig:
    """Milvus 连接配置"""

    # 默认配置
    DEFAULT_URI = "http://localhost:19530"
    DEFAULT_TOKEN = ""

    # Docker 部署配置
    DOCKER_URI = "http://localhost:19530"

    # Milvus Cloud 配置
    CLOUD_URI = "https://<your_endpoint>.milvus-cloud.com"

    def __init__(
        self,
        uri: str = None,
        token: str = None,
        collection_name: str = None
    ):
        self.uri = uri or self.DEFAULT_URI
        self.token = token or self.DEFAULT_TOKEN
        self.collection_name = collection_name


def create_milvus_config(
    deployment: str = 'docker',
    uri: str = None,
    token: str = None,
    collection_name: str = None
) -> MilvusConfig:
    """
    创建 Milvus 配置

    Args:
        deployment: 部署类型 ('docker', 'cloud', 'custom')
        uri: 自定义 URI
        token: 认证 Token
        collection_name: 集合名称

    Returns:
        MilvusConfig 实例
    """
    if deployment == 'docker':
        uri = uri or MilvusConfig.DOCKER_URI
    elif deployment == 'cloud':
        uri = uri or MilvusConfig.CLOUD_URI
        if not token:
            raise ValueError("Milvus Cloud 需要 token")

    return MilvusConfig(uri, token, collection_name)
