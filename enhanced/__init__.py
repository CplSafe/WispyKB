"""
增强功能集成模块

将新实现的功能集成到主代码中：
1. Embedding缓存
2. 混合搜索
3. 父子分块索引
4. 高级文档解析
5. 可观测性
"""

import sys
import os

# 添加模块路径
sys.path.insert(0, os.path.dirname(__file__))

from cache import embedding_cache
from retrieval import hybrid_search, RetrievalMethod
from indexing import parent_child_indexer
from parsing import advanced_parser
from observability import metrics, tracer, trace, trace_context, request_logger

__all__ = [
    'embedding_cache',
    'hybrid_search',
    'RetrievalMethod',
    'parent_child_indexer',
    'advanced_parser',
    'metrics',
    'tracer',
    'trace',
    'trace_context',
    'request_logger'
]
