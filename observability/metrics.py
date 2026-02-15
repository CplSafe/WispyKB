"""
可观测性模块
参考：Dify, RAGFlow 的监控追踪实现

功能：
1. 请求追踪
2. 性能指标
3. 日志记录
4. 错误监控
"""

import logging
import time
import uuid
from typing import Dict, Any, Optional, Callable
from functools import wraps
from datetime import datetime
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    指标收集器

    收集系统运行指标：
    - 请求数
    - 响应时间
    - 错误率
    - 向量搜索性能
    - LLM调用统计
    """

    def __init__(self):
        self.metrics = {
            "requests_total": 0,
            "requests_success": 0,
            "requests_error": 0,
            "chat_requests": 0,
            "search_requests": 0,
            "embedding_requests": 0,
            "llm_requests": 0,
            "avg_response_time_ms": 0,
            "avg_search_time_ms": 0,
            "avg_embedding_time_ms": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }

    def record_request(self, endpoint: str, success: bool, duration_ms: float):
        """记录请求"""
        self.metrics["requests_total"] += 1
        if success:
            self.metrics["requests_success"] += 1
        else:
            self.metrics["requests_error"] += 1

        # 更新平均响应时间
        total = self.metrics["requests_total"]
        current_avg = self.metrics["avg_response_time_ms"]
        self.metrics["avg_response_time_ms"] = (
            (current_avg * (total - 1) + duration_ms) / total
        )

        # 特定端点计数
        if "chat" in endpoint:
            self.metrics["chat_requests"] += 1
        elif "search" in endpoint or "retriev" in endpoint:
            self.metrics["search_requests"] += 1
        elif "embedding" in endpoint:
            self.metrics["embedding_requests"] += 1

    def record_llm_call(self, model: str, duration_ms: float, tokens: int):
        """记录LLM调用"""
        self.metrics["llm_requests"] += 1

    def record_cache_hit(self, hit: bool):
        """记录缓存命中"""
        if hit:
            self.metrics["cache_hits"] += 1
        else:
            self.metrics["cache_misses"] += 1

    def get_metrics(self) -> Dict[str, Any]:
        """获取所有指标"""
        total = self.metrics["cache_hits"] + self.metrics["cache_misses"]
        cache_hit_rate = (
            self.metrics["cache_hits"] / total if total > 0 else 0
        )

        return {
            **self.metrics,
            "cache_hit_rate": cache_hit_rate,
            "error_rate": (
                self.metrics["requests_error"] / self.metrics["requests_total"]
                if self.metrics["requests_total"] > 0 else 0
            ),
        }

    def reset(self):
        """重置指标"""
        for key in self.metrics:
            if isinstance(self.metrics[key], (int, float)):
                self.metrics[key] = 0


# 全局指标收集器
metrics = MetricsCollector()


class Tracer:
    """
    分布式追踪器

    记录请求的完整调用链路
    """

    def __init__(self):
        self.spans = {}  # {trace_id: [spans]}

    def create_span(
        self,
        trace_id: str,
        operation: str,
        parent_id: Optional[str] = None
    ) -> str:
        """
        创建一个span

        Args:
            trace_id: 追踪ID
            operation: 操作名称
            parent_id: 父span ID

        Returns:
            span ID
        """
        span_id = f"{operation}_{uuid.uuid4().hex[:8]}"
        span = {
            "span_id": span_id,
            "trace_id": trace_id,
            "parent_id": parent_id,
            "operation": operation,
            "start_time": time.time(),
            "end_time": None,
            "duration_ms": None,
            "status": "started",
            "metadata": {}
        }

        if trace_id not in self.spans:
            self.spans[trace_id] = []

        self.spans[trace_id].append(span)
        return span_id

    def finish_span(self, trace_id: str, span_id: str, status: str = "success", **metadata):
        """
        完成一个span

        Args:
            trace_id: 追踪ID
            span_id: span ID
            status: 状态
            **metadata: 额外元数据
        """
        if trace_id not in self.spans:
            return

        for span in self.spans[trace_id]:
            if span["span_id"] == span_id:
                span["end_time"] = time.time()
                span["duration_ms"] = (span["end_time"] - span["start_time"]) * 1000
                span["status"] = status
                span["metadata"] = metadata
                break

    def get_trace(self, trace_id: str) -> list:
        """获取追踪信息"""
        return self.spans.get(trace_id, [])

    def log_trace(self, trace_id: str):
        """记录追踪到日志"""
        spans = self.get_trace(trace_id)
        if not spans:
            return

        logger.info(f"Trace {trace_id}:")
        for span in spans:
            duration = span.get('duration_ms', 0)
            logger.info(f"  [{span['operation']}] {duration:.2f}ms - {span['status']}")
            if span.get('metadata'):
                logger.info(f"    {span['metadata']}")


# 全局追踪器
tracer = Tracer()


# 装饰器
def trace(operation: str):
    """
    追踪装饰器

    用法：
    @trace("vector_search")
    async def vector_search(...):
        ...
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            trace_id = kwargs.pop('trace_id', str(uuid.uuid4()))
            span_id = tracer.create_span(trace_id, operation)

            start = time.time()
            try:
                result = await func(*args, **kwargs, trace_id=trace_id)
                duration_ms = (time.time() - start) * 1000
                tracer.finish_span(trace_id, span_id, "success", duration_ms=duration_ms)
                return result
            except Exception as e:
                duration_ms = (time.time() - start) * 1000
                tracer.finish_span(trace_id, span_id, "error", error=str(e), duration_ms=duration_ms)
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            trace_id = kwargs.pop('trace_id', str(uuid.uuid4()))
            span_id = tracer.create_span(trace_id, operation)

            start = time.time()
            try:
                result = func(*args, **kwargs, trace_id=trace_id)
                duration_ms = (time.time() - start) * 1000
                tracer.finish_span(trace_id, span_id, "success", duration_ms=duration_ms)
                return result
            except Exception as e:
                duration_ms = (time.time() - start) * 1000
                tracer.finish_span(trace_id, span_id, "error", error=str(e), duration_ms=duration_ms)
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


@asynccontextmanager
async def trace_context(operation: str, trace_id: Optional[str] = None):
    """
    追踪上下文管理器

    用法：
    async with trace_context("chat"):
        # chat logic
        ...
    """
    if trace_id is None:
        trace_id = str(uuid.uuid4())

    span_id = tracer.create_span(trace_id, operation)
    start = time.time()

    try:
        yield {"trace_id": trace_id, "span_id": span_id}

        duration = (time.time() - start) * 1000
        tracer.finish_span(trace_id, span_id, "success", duration_ms=duration)
    except Exception as e:
        duration = (time.time() - start) * 1000
        tracer.finish_span(trace_id, span_id, "error", error=str(e), duration_ms=duration)
        raise


# API端点
async def get_metrics():
    """获取指标API"""
    return metrics.get_metrics()


async def get_trace_info(trace_id: str):
    """获取追踪信息API"""
    return tracer.get_trace(trace_id)


# 初始化时导入
import asyncio
from functools import wraps


class RequestLogger:
    """
    请求日志记录器

    记录每个请求的详细信息
    """

    def __init__(self):
        self.requests = []
        self.max_size = 1000

    def log_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        """记录请求"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "method": method,
            "path": path,
            "status_code": status_code,
            "duration_ms": duration_ms,
            "user_id": user_id,
            "metadata": metadata or {}
        }

        self.requests.append(log_entry)

        # 保持固定大小
        if len(self.requests) > self.max_size:
            self.requests = self.requests[-self.max_size:]

    def get_recent_requests(self, limit: int = 100):
        """获取最近的请求"""
        return self.requests[-limit:]

    def get_stats_by_endpoint(self) -> Dict[str, Dict[str, Any]]:
        """按端点统计"""
        stats = {}

        for req in self.requests:
            path = req["path"]
            if path not in stats:
                stats[path] = {
                    "count": 0,
                    "total_duration": 0,
                    "errors": 0,
                    "avg_duration": 0
                }

            stats[path]["count"] += 1
            stats[path]["total_duration"] += req["duration_ms"]
            if req["status_code"] >= 400:
                stats[path]["errors"] += 1

        # 计算平均耗时
        for path, stat in stats.items():
            stat["avg_duration"] = stat["total_duration"] / stat["count"]

        return stats


# 全局请求日志记录器
request_logger = RequestLogger()
