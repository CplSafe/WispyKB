"""
混合搜索模块
参考：Dify, RAGFlow 的混合搜索实现

功能：
1. 向量搜索（语义）
2. 全文搜索（关键词）
3. 加权融合
4. Rerank重排序
"""

import logging
from typing import List, Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class RetrievalMethod(str, Enum):
    """检索方法"""
    SEMANTIC_SEARCH = "semantic_search"      # 纯向量搜索
    FULL_TEXT_SEARCH = "full_text_search"    # 纯全文搜索
    HYBRID_SEARCH = "hybrid_search"          # 混合搜索（向量+全文）
    KEYWORD_SEARCH = "keyword_search"        # 关键词搜索


class HybridSearch:
    """
    混合搜索类

    结合向量搜索和全文搜索，提升检索准确率
    """

    def __init__(
        self,
        pool,
        alpha: float = 0.7,
        rerank_enabled: bool = False,
        rerank_model: Optional[str] = None
    ):
        """
        初始化混合搜索

        Args:
            pool: 数据库连接池
            alpha: 向量搜索权重 (0-1)，全文搜索权重为 1-alpha
            rerank_enabled: 是否启用重排序
            rerank_model: 重排序模型
        """
        self.pool = pool
        self.alpha = alpha
        self.rerank_enabled = rerank_enabled
        self.rerank_model = rerank_model

    async def search(
        self,
        query: str,
        query_embedding: List[float],
        kb_ids: List[str],
        top_k: int = 3,
        threshold: float = 0.3,
        method: RetrievalMethod = RetrievalMethod.HYBRID_SEARCH
    ) -> List[Dict[str, Any]]:
        """
        执行混合搜索

        Args:
            query: 查询文本
            query_embedding: 查询向量
            kb_ids: 知识库ID列表
            top_k: 返回结果数
            threshold: 相似度阈值
            method: 检索方法

        Returns:
            检索结果列表
        """
        if method == RetrievalMethod.SEMANTIC_SEARCH:
            return await self._semantic_search(query_embedding, kb_ids, top_k, threshold)

        elif method == RetrievalMethod.FULL_TEXT_SEARCH:
            return await self._full_text_search(query, kb_ids, top_k)

        elif method == RetrievalMethod.KEYWORD_SEARCH:
            return await self._keyword_search(query, kb_ids, top_k)

        elif method == RetrievalMethod.HYBRID_SEARCH:
            return await self._hybrid_search(query, query_embedding, kb_ids, top_k, threshold)

        return []

    async def _semantic_search(
        self,
        query_embedding: List[float],
        kb_ids: List[str],
        top_k: int,
        threshold: float
    ) -> List[Dict[str, Any]]:
        """纯向量搜索"""
        from ..main_pgvector import vector_search_multi
        return await vector_search_multi(self.pool, query_embedding, kb_ids, top_k, threshold)

    async def _full_text_search(
        self,
        query: str,
        kb_ids: List[str],
        top_k: int
    ) -> List[Dict[str, Any]]:
        """全文搜索（使用PostgreSQL全文搜索）"""
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 使用PostgreSQL的tsvector进行全文搜索
                await cur.execute("""
                    SELECT
                        c.id as chunk_id,
                        c.doc_id,
                        d.name as doc_name,
                        d.kb_id,
                        kb.name as kb_name,
                        c.content,
                        ts_rank(cd.textvector, to_tsquery('simple', %s)) as similarity
                    FROM chunks c
                    JOIN documents d ON d.id = c.doc_id
                    JOIN knowledge_bases kb ON kb.id = d.kb_id,
                         LATERAL (SELECT to_tsvector('simple', c.content) as textvector) cd
                    WHERE d.kb_id = ANY(%s)
                      AND to_tsvector('simple', c.content) @@ to_tsquery('simple', %s)
                    ORDER BY similarity DESC
                    LIMIT %s
                """, (query, kb_ids, query, top_k))

                results = await cur.fetchall()
                return results

    async def _keyword_search(
        self,
        query: str,
        kb_ids: List[str],
        top_k: int
    ) -> List[Dict[str, Any]]:
        """关键词搜索（LIKE匹配）"""
        keywords = query.split()

        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # 构建LIKE条件
                like_conditions = []
                params = []
                for keyword in keywords:
                    like_conditions.append("c.content LIKE %s")
                    params.append(f"%{keyword}%")
                params.extend([kb_ids, top_k])

                where_clause = " OR ".join(like_conditions)

                await cur.execute(f"""
                    SELECT
                        c.id as chunk_id,
                        c.doc_id,
                        d.name as doc_name,
                        d.kb_id,
                        kb.name as kb_name,
                        c.content,
                        0.5 as similarity
                    FROM chunks c
                    JOIN documents d ON d.id = c.doc_id
                    JOIN knowledge_bases kb ON kb.id = d.kb_id
                    WHERE d.kb_id = ANY(%s)
                      AND ({where_clause})
                    ORDER BY LENGTH(c.content) ASC
                    LIMIT %s
                """, params)

                results = await cur.fetchall()
                return results

    async def _hybrid_search(
        self,
        query: str,
        query_embedding: List[float],
        kb_ids: List[str],
        top_k: int,
        threshold: float
    ) -> List[Dict[str, Any]]:
        """
        混合搜索：向量 + 全文

        算法：
        1. 分别执行向量搜索和全文搜索
        2. 加权融合：score = alpha * vector_score + (1-alpha) * fts_score
        3. 按融合分数排序
        4. 可选Rerank重排序
        """
        # 1. 向量搜索
        vector_results = await self._semantic_search(query_embedding, kb_ids, top_k * 3, threshold)

        # 2. 全文搜索
        fts_results = await self._full_text_search(query, kb_ids, top_k * 3)

        # 3. 归一化分数并融合
        vector_scores = {r['chunk_id']: r.get('similarity', 0) for r in vector_results}
        fts_scores = {r['chunk_id']: r.get('similarity', 0) for r in fts_results}

        # 归一化到0-1
        max_vector = max(vector_scores.values()) if vector_scores else 1
        max_fts = max(fts_scores.values()) if fts_scores else 1

        # 合并结果
        merged = {}
        for r in vector_results:
            chunk_id = r['chunk_id']
            norm_score = vector_scores[chunk_id] / max_vector if max_vector > 0 else 0
            merged[chunk_id] = {
                **r,
                'vector_score': norm_score,
                'fts_score': 0.0,
                'combined_score': self.alpha * norm_score
            }

        for r in fts_results:
            chunk_id = r['chunk_id']
            norm_score = fts_scores[chunk_id] / max_fts if max_fts > 0 else 0
            fts_contribution = (1 - self.alpha) * norm_score

            if chunk_id in merged:
                merged[chunk_id]['fts_score'] = norm_score
                merged[chunk_id]['combined_score'] += fts_contribution
            else:
                merged[chunk_id] = {
                    **r,
                    'vector_score': 0.0,
                    'fts_score': norm_score,
                    'combined_score': fts_contribution
                }

        # 按融合分数排序
        results = sorted(merged.values(), key=lambda x: x['combined_score'], reverse=True)
        results = results[:top_k]

        # 4. 可选Rerank
        if self.rerank_enabled:
            results = await self._rerank(query, results, top_k)

        # 添加调试信息
        for r in results:
            logger.debug(f"Hybrid search result: vector={r.get('vector_score', 0):.3f}, "
                        f"fts={r.get('fts_score', 0):.3f}, combined={r.get('combined_score', 0):.3f}")

        return results

    async def _rerank(
        self,
        query: str,
        results: List[Dict[str, Any]],
        top_k: int
    ) -> List[Dict[str, Any]]:
        """
        Rerank重排序

        使用交叉编码器重新计算query和document的相关性
        """
        if not self.rerank_model:
            return results

        # 简化实现：使用关键词匹配分数进行调整
        query_words = set(query.lower().split())

        for r in results:
            content_words = set(r['content'].lower().split())
            overlap = len(query_words & content_words)
            # 调整分数：奖励关键词重叠
            r['combined_score'] *= (1 + 0.1 * overlap)

        # 重新排序
        results = sorted(results, key=lambda x: x['combined_score'], reverse=True)
        return results[:top_k]


# 全局混合搜索实例
hybrid_search = HybridSearch(pool=None)
