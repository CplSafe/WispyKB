# MonitoringService - 监控服务
# 从 main_pgvector.py 拆分

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

from psycopg.rows import dict_row

from .cache import CacheManager

logger = logging.getLogger(__name__)


class MonitoringService:
    """监控服务 - 跟踪和统计系统性能"""

    def __init__(self, pool_ref, cache_manager: CacheManager):
        self.pool_ref = pool_ref
        self.cache = cache_manager

    async def record_request(
        self,
        endpoint: str,
        method: str,
        status_code: int,
        duration_ms: int,
        user_id: Optional[str] = None
    ):
        """记录请求"""
        cache_key = f"metrics:requests:{datetime.now().strftime('%Y%m%d')}"
        try:
            async with self.cache._redis as redis:
                await redis.hincrby(f"{cache_key}:total", "count", 1)
                await redis.hincrby(f"{cache_key}:status:{status_code}", "count", 1)
                await redis.hincrbyfloat(f"{cache_key}:duration", "total", duration_ms)
                await redis.expire(f"{cache_key}:total", 86400 * 7)  # 保留7天
        except:
            pass

    async def get_request_stats(
        self,
        days: int = 7
    ) -> Dict[str, Any]:
        """获取请求统计"""
        stats = {
            'total': 0,
            'by_status': {},
            'by_endpoint': {},
            'avg_duration_ms': 0,
            'daily': []
        }

        try:
            for i in range(days):
                date = (datetime.now() - timedelta(days=i)).strftime('%Y%m%d')
                cache_key = f"metrics:requests:{date}"

                async with self.cache._redis as redis:
                    total = await redis.hget(f"{cache_key}:total", "count")
                    duration = await redis.hget(f"{cache_key}:duration", "total")

                if total:
                    stats['total'] += int(total)
                    stats['daily'].append({
                        'date': date,
                        'count': int(total),
                        'avg_duration_ms': float(duration) / int(total) if duration and total else 0
                    })
        except:
            pass

        return stats

    async def get_workflow_stats(
        self,
        workflow_id: Optional[str] = None,
        days: int = 30
    ) -> Dict[str, Any]:
        """获取工作流统计"""
        async with self.pool_ref.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                since = datetime.now() - timedelta(days=days)

                if workflow_id:
                    await cur.execute("""
                        SELECT
                            workflow_id,
                            COUNT(*) as total,
                            COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                            COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed,
                            COUNT(CASE WHEN status = 'paused' THEN 1 END) as paused,
                            COUNT(CASE WHEN status = 'waiting_input' THEN 1 END) as waiting,
                            AVG(duration_ms) as avg_duration_ms,
                            MAX(duration_ms) as max_duration_ms
                        FROM workflow_executions
                        WHERE workflow_id = %s AND started_at >= %s
                        GROUP BY workflow_id
                    """, (workflow_id, since))
                else:
                    await cur.execute("""
                        SELECT
                            workflow_id,
                            COUNT(*) as total,
                            COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                            COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed,
                            AVG(duration_ms) as avg_duration_ms
                        FROM workflow_executions
                        WHERE started_at >= %s
                        GROUP BY workflow_id
                        ORDER BY total DESC
                        LIMIT 20
                    """, (since,))

                results = await cur.fetchall()

        return {'workflows': results}

    async def get_execution_trace(
        self,
        execution_id: str
    ) -> Dict[str, Any]:
        """获取工作流执行追踪详情"""
        async with self.pool_ref.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT * FROM workflow_executions
                    WHERE id = %s
                """, (execution_id,))
                execution = await cur.fetchone()

        if not execution:
            raise ValueError(f"执行不存在: {execution_id}")

        # 解析执行上下文
        context = execution.get('execution_context', {})
        outputs = execution.get('outputs', {})

        return {
            'execution_id': execution['id'],
            'workflow_id': execution['workflow_id'],
            'status': execution['status'],
            'started_at': execution['started_at'].isoformat() if execution['started_at'] else None,
            'completed_at': execution['completed_at'].isoformat() if execution['completed_at'] else None,
            'duration_ms': execution.get('duration_ms'),
            'paused_at': execution.get('paused_at').isoformat() if execution.get('paused_at') else None,
            'resumed_at': execution.get('resumed_at').isoformat() if execution.get('resumed_at') else None,
            'current_node_id': execution.get('current_node_id'),
            'pending_human_input_node_id': execution.get('pending_human_input_node_id'),
            'inputs': execution.get('inputs'),
            'outputs': outputs,
            'error': execution.get('error'),
            'executed_nodes': list(context.get('outputs', {}).keys()) if context else []
        }

    async def get_system_metrics(self) -> Dict[str, Any]:
        """获取系统指标"""
        metrics = {
            'database': {},
            'cache': {},
            'knowledge_bases': {},
            'workflows': {}
        }

        # 数据库连接池状态
        from main_pgvector import pool
        metrics['database'] = {
            'pool_size': pool.max_size if pool else 0,
        }

        # Redis 缓存状态
        try:
            async with self.cache._redis as redis:
                info = await redis.info()
                metrics['cache'] = {
                    'connected': True,
                    'memory_used': info.get('used_memory_human'),
                    'keyspace': info.get('db0'),
                }
        except:
            metrics['cache'] = {'connected': False}

        # 知识库统计
        async with self.pool_ref.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT
                        COUNT(*) as total_kbs,
                        SUM(doc_count) as total_docs,
                        SUM(token_count) as total_tokens
                    FROM knowledge_bases
                """)
                kb_stats = await cur.fetchone()
                metrics['knowledge_bases'] = kb_stats

                await cur.execute("""
                    SELECT
                        COUNT(*) as total_workflows,
                        COUNT(CASE WHEN is_active THEN 1 END) as active_workflows
                    FROM workflows
                """)
                wf_stats = await cur.fetchone()
                metrics['workflows'] = wf_stats

        return metrics
