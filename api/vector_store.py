"""
向量存储相关路由
从 main_pgvector.py 拆分出来
"""
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/vector-store", tags=["vector-store"])

# ==================== 全局变量访问函数 ====================
def _get_globals():
    """获取 main_pgvector 模块中的全局变量"""
    # 延迟导入避免循环依赖
    import main_pgvector as mp
    return {
        'vector_store_instance': mp.vector_store_instance,
        'VECTOR_STORE_TYPE': mp.VECTOR_STORE_TYPE,
        'MILVUS_CONFIG': mp.MILVUS_CONFIG,
        'pool': mp.pool,
        'get_current_user': mp.get_current_user,
    }


# ==================== 向量存储 API ====================
@router.get("/info")
async def get_vector_store_info(user: Dict = Depends(lambda: _get_globals()['get_current_user'])):
    """获取向量存储信息"""
    g = _get_globals()
    vector_store_instance = g['vector_store_instance']
    vector_store_type = g['VECTOR_STORE_TYPE']

    if not vector_store_instance:
        return {
            'type': vector_store_type,
            'status': 'not_initialized',
            'message': '向量存储未初始化'
        }

    try:
        info = await vector_store_instance.get_info()
        info['status'] = 'active'
        return info
    except Exception as e:
        return {
            'type': vector_store_type,
            'status': 'error',
            'error': str(e)
        }


@router.get("/stats")
async def get_vector_store_stats(user: Dict = Depends(lambda: _get_globals()['get_current_user'])):
    """获取向量存储统计信息"""
    g = _get_globals()
    vector_store_instance = g['vector_store_instance']
    vector_store_type = g['VECTOR_STORE_TYPE']
    milvus_config = g['MILVUS_CONFIG']
    pool = g['pool']

    stats = {
        'type': vector_store_type,
        'collections': [],
        'total_vectors': 0,
    }

    try:
        if vector_store_type == 'milvus' and vector_store_instance:
            from pymilvus import MilvusClient
            client = MilvusClient(uri=milvus_config['uri'])
            stats['total_vectors'] = 0

            # 获取所有集合及其统计
            collections = client.list_collections()
            for coll in collections:
                coll_info = client.get_collection_stats(collection_name=coll)
                stats['collections'].append({
                    'name': coll,
                    'count': int(coll_info.get('row_count', 0)),
                    'dimension': coll_info.get('dimension', 768),
                })
                stats['total_vectors'] += int(coll_info.get('row_count', 0))

        elif vector_store_type == 'pgvector':
            # pgvector 统计
            async with pool.connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    # 知识库文档统计
                    await cur.execute("""
                        SELECT kb.id, kb.name,
                               (SELECT COUNT(*) FROM documents d WHERE d.kb_id = kb.id) as doc_count,
                               (SELECT COUNT(*) FROM chunks c JOIN documents d ON c.doc_id = d.id WHERE d.kb_id = kb.id) as chunk_count
                        FROM knowledge_bases kb
                        ORDER BY kb.created_at DESC
                    """)
                    kb_stats = await cur.fetchall()

                    total_chunks = 0
                    for kb in kb_stats:
                        stats['collections'].append({
                            'name': kb['name'],
                            'id': kb['id'],
                            'count': kb['chunk_count'] or 0,
                            'doc_count': kb['doc_count'] or 0,
                        })
                        total_chunks += kb['chunk_count'] or 0

                    stats['total_vectors'] = total_chunks

        stats['status'] = 'active'
        return stats

    except Exception as e:
        logger.error(f"获取向量存储统计失败: {e}")
        stats['status'] = 'error'
        stats['error'] = str(e)
        return stats
