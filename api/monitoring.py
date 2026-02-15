"""
监控相关路由
从 main_pgvector.py 拆分出来
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])

# ==================== 全局变量访问函数 ====================
def _get_globals():
    """获取 main_pgvector 模块中的全局变量"""
    # 延迟导入避免循环依赖
    import main_pgvector as mp
    return {
        'monitoring_service': mp.monitoring_service,
        'pool': mp.pool,
        'get_current_user': mp.get_current_user,
    }


# ==================== 监控仪表板 API ====================
@router.get("/dashboard")
async def get_monitoring_dashboard(
    days: int = 7,
    user: Dict = Depends(lambda: _get_globals()['get_current_user'])
):
    """获取监控仪表板数据"""
    if user.get('role') != 'super_admin':
        raise HTTPException(status_code=403, detail="无权访问")

    monitoring_service = _get_globals()['monitoring_service']
    if not monitoring_service:
        raise HTTPException(status_code=500, detail="监控服务未初始化")

    request_stats = await monitoring_service.get_request_stats(days)
    workflow_stats = await monitoring_service.get_workflow_stats(days=days)
    system_metrics = await monitoring_service.get_system_metrics()

    return {
        'requests': request_stats,
        'workflows': workflow_stats,
        'system': system_metrics,
        'summary': {
            'total_requests': request_stats['total'],
            'total_workflows': workflow_stats.get('workflows', []),
            'avg_duration_ms': request_stats.get('avg_duration_ms', 0)
        }
    }


@router.get("/workflows/{workflow_id}/stats")
async def get_workflow_monitoring_stats(
    workflow_id: str,
    days: int = 30,
    user: Dict = Depends(lambda: _get_globals()['get_current_user'])
):
    """获取工作流监控统计"""
    monitoring_service = _get_globals()['monitoring_service']
    if not monitoring_service:
        raise HTTPException(status_code=500, detail="监控服务未初始化")

    stats = await monitoring_service.get_workflow_stats(workflow_id, days)
    return stats


@router.get("/executions/{execution_id}/trace")
async def get_execution_trace(
    execution_id: str,
    user: Dict = Depends(lambda: _get_globals()['get_current_user'])
):
    """获取工作流执行追踪"""
    monitoring_service = _get_globals()['monitoring_service']
    if not monitoring_service:
        raise HTTPException(status_code=500, detail="监控服务未初始化")

    trace = await monitoring_service.get_execution_trace(execution_id)
    return trace


@router.get("/system/metrics")
async def get_system_metrics(user: Dict = Depends(lambda: _get_globals()['get_current_user'])):
    """获取系统指标"""
    if user.get('role') != 'super_admin':
        raise HTTPException(status_code=403, detail="无权访问")

    monitoring_service = _get_globals()['monitoring_service']
    if not monitoring_service:
        raise HTTPException(status_code=500, detail="监控服务未初始化")

    metrics = await monitoring_service.get_system_metrics()
    return metrics


@router.get("/performance")
async def get_performance_metrics(
    hours: int = 24,
    user: Dict = Depends(lambda: _get_globals()['get_current_user'])
):
    """获取性能指标"""
    if user.get('role') != 'super_admin':
        raise HTTPException(status_code=403, detail="无权访问")

    g = _get_globals()
    async with g['pool'].connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            since = datetime.now() - timedelta(hours=hours)

            # 获取工作流执行性能
            await cur.execute("""
                SELECT
                    DATE_TRUNC('hour', started_at) as hour,
                    COUNT(*) as count,
                    AVG(duration_ms) as avg_duration,
                    MAX(duration_ms) as max_duration,
                    COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed_count
                FROM workflow_executions
                WHERE started_at >= %s
                GROUP BY DATE_TRUNC('hour', started_at)
                ORDER BY hour DESC
            """, (since,))
            workflow_performance = await cur.fetchall()

            # 获取 API 响应时间统计（从缓存获取）
            cache_key = f"metrics:requests:{datetime.now().strftime('%Y%m%d')}"
            performance_stats = {
                'workflow_performance': workflow_performance,
                'api_requests': {}
            }

    return performance_stats
