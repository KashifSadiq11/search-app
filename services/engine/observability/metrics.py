"""
Prometheus Metrics Collector

Provides:
- Request count and latency metrics
- Error tracking by endpoint and error type
- Search quality metrics (scores, zero-result rate, fallback rate)
- External dependency latency (DB, Redis, FAISS, LLM)
- System health metrics
- Thread-safe metric collection

Exposes metrics in Prometheus format at /metrics endpoint.
"""

import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable
from functools import wraps
import statistics

from .config import obs_config


@dataclass
class HistogramBucket:
    """A single histogram bucket with upper bound and count."""
    le: float  # Less than or equal
    count: int = 0


class Histogram:
    """
    Prometheus-compatible histogram implementation.

    Thread-safe histogram with configurable buckets for latency tracking.
    """

    def __init__(self, name: str, help_text: str, buckets: List[float], labels: List[str] = None):
        self.name = name
        self.help = help_text
        self.bucket_bounds = sorted(buckets) + [float("inf")]
        self.labels = labels or []
        self._lock = threading.Lock()

        # Store data per label combination
        # Key: tuple of label values, Value: dict with buckets, sum, count
        self._data: Dict[tuple, Dict[str, Any]] = defaultdict(lambda: {
            "buckets": [0] * len(self.bucket_bounds),
            "sum": 0.0,
            "count": 0
        })

    def observe(self, value: float, **label_values) -> None:
        """Record an observation."""
        key = self._label_key(label_values)
        with self._lock:
            data = self._data[key]
            data["sum"] += value
            data["count"] += 1
            for i, bound in enumerate(self.bucket_bounds):
                if value <= bound:
                    data["buckets"][i] += 1

    def _label_key(self, label_values: Dict[str, str]) -> tuple:
        """Create a hashable key from label values."""
        return tuple(label_values.get(label, "") for label in self.labels)

    def collect(self) -> str:
        """Generate Prometheus exposition format."""
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} histogram"]

        with self._lock:
            for label_key, data in self._data.items():
                label_str = self._format_labels(label_key)

                for i, bound in enumerate(self.bucket_bounds):
                    cumulative = sum(data["buckets"][:i + 1])
                    le_val = "+Inf" if bound == float("inf") else str(bound)
                    le_label = f'le="{le_val}"'
                    if label_str:
                        lines.append(f'{self.name}_bucket{{{label_str},{le_label}}} {cumulative}')
                    else:
                        lines.append(f'{self.name}_bucket{{{le_label}}} {cumulative}')

                suffix = f"{{{label_str}}}" if label_str else ""
                lines.append(f"{self.name}_sum{suffix} {data['sum']:.6f}")
                lines.append(f"{self.name}_count{suffix} {data['count']}")

        return "\n".join(lines)

    def _format_labels(self, label_key: tuple) -> str:
        """Format label key as Prometheus label string."""
        if not self.labels:
            return ""
        pairs = [f'{label}="{value}"' for label, value in zip(self.labels, label_key) if value]
        return ",".join(pairs)


class Counter:
    """Prometheus-compatible counter implementation."""

    def __init__(self, name: str, help_text: str, labels: List[str] = None):
        self.name = name
        self.help = help_text
        self.labels = labels or []
        self._lock = threading.Lock()
        self._values: Dict[tuple, float] = defaultdict(float)

    def inc(self, value: float = 1.0, **label_values) -> None:
        """Increment counter."""
        key = self._label_key(label_values)
        with self._lock:
            self._values[key] += value

    def _label_key(self, label_values: Dict[str, str]) -> tuple:
        return tuple(label_values.get(label, "") for label in self.labels)

    def collect(self) -> str:
        """Generate Prometheus exposition format."""
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} counter"]

        with self._lock:
            for label_key, value in self._values.items():
                label_str = self._format_labels(label_key)
                suffix = f"{{{label_str}}}" if label_str else ""
                lines.append(f"{self.name}{suffix} {value}")

        return "\n".join(lines)

    def _format_labels(self, label_key: tuple) -> str:
        if not self.labels:
            return ""
        pairs = [f'{label}="{value}"' for label, value in zip(self.labels, label_key) if value]
        return ",".join(pairs)


class Gauge:
    """Prometheus-compatible gauge implementation."""

    def __init__(self, name: str, help_text: str, labels: List[str] = None):
        self.name = name
        self.help = help_text
        self.labels = labels or []
        self._lock = threading.Lock()
        self._values: Dict[tuple, float] = defaultdict(float)

    def set(self, value: float, **label_values) -> None:
        """Set gauge value."""
        key = self._label_key(label_values)
        with self._lock:
            self._values[key] = value

    def inc(self, value: float = 1.0, **label_values) -> None:
        """Increment gauge."""
        key = self._label_key(label_values)
        with self._lock:
            self._values[key] += value

    def dec(self, value: float = 1.0, **label_values) -> None:
        """Decrement gauge."""
        key = self._label_key(label_values)
        with self._lock:
            self._values[key] -= value

    def _label_key(self, label_values: Dict[str, str]) -> tuple:
        return tuple(label_values.get(label, "") for label in self.labels)

    def collect(self) -> str:
        """Generate Prometheus exposition format."""
        lines = [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} gauge"]

        with self._lock:
            for label_key, value in self._values.items():
                label_str = self._format_labels(label_key)
                suffix = f"{{{label_str}}}" if label_str else ""
                lines.append(f"{self.name}{suffix} {value}")

        return "\n".join(lines)

    def _format_labels(self, label_key: tuple) -> str:
        if not self.labels:
            return ""
        pairs = [f'{label}="{value}"' for label, value in zip(self.labels, label_key) if value]
        return ",".join(pairs)


class MetricsCollector:
    """
    Central metrics collector for the recommendation engine.

    Provides Prometheus-compatible metrics for:
    - Performance: request latency, throughput, errors
    - Search Quality: scores, zero-result rate, fallback rate
    - Dependencies: DB/Redis/FAISS/LLM latency
    - System: cache hit rate, index stats
    """

    def __init__(self):
        prefix = obs_config.metrics_prefix
        buckets = obs_config.latency_buckets

        # ================== PERFORMANCE METRICS ==================

        # Request counter
        self.request_total = Counter(
            f"{prefix}_requests_total",
            "Total number of requests",
            labels=["endpoint", "method", "status"]
        )

        # Error counter
        self.error_total = Counter(
            f"{prefix}_errors_total",
            "Total number of errors",
            labels=["endpoint", "error_type"]
        )

        # Request latency histogram
        self.request_latency = Histogram(
            f"{prefix}_request_duration_ms",
            "Request duration in milliseconds",
            buckets=buckets,
            labels=["endpoint", "method"]
        )

        # Stage latency (parse, retrieve, rerank, postprocess)
        self.stage_latency = Histogram(
            f"{prefix}_stage_duration_ms",
            "Processing stage duration in milliseconds",
            buckets=buckets,
            labels=["stage"]
        )

        # ================== SEARCH QUALITY METRICS ==================

        # Search result counts
        self.search_result_count = Histogram(
            f"{prefix}_search_result_count",
            "Number of results returned per search",
            buckets=[0, 1, 5, 10, 20, 50, 100, 200, 500],
            labels=["endpoint"]
        )

        # Top score distribution
        self.search_top_score = Histogram(
            f"{prefix}_search_top_score",
            "Top result score per search",
            buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
            labels=["endpoint"]
        )

        # Score gap (top1 - top2) distribution
        self.search_score_gap = Histogram(
            f"{prefix}_search_score_gap",
            "Gap between top 1 and top 2 scores",
            buckets=[0.0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5],
            labels=["endpoint"]
        )

        # Zero result rate
        self.zero_result_total = Counter(
            f"{prefix}_zero_result_total",
            "Total searches with zero results",
            labels=["endpoint"]
        )

        # Fallback usage
        self.fallback_total = Counter(
            f"{prefix}_fallback_total",
            "Total times fallback was triggered",
            labels=["fallback_type"]
        )

        # Bad query signals
        self.bad_query_total = Counter(
            f"{prefix}_bad_query_total",
            "Queries flagged as low quality",
            labels=["reason"]
        )

        # ================== DEPENDENCY METRICS ==================

        # External dependency latency
        self.dependency_latency = Histogram(
            f"{prefix}_dependency_duration_ms",
            "External dependency call duration in milliseconds",
            buckets=buckets,
            labels=["dependency"]  # db, redis, faiss, llm, mongo
        )

        # Dependency errors
        self.dependency_error_total = Counter(
            f"{prefix}_dependency_errors_total",
            "External dependency errors",
            labels=["dependency", "error_type"]
        )

        # ================== CACHE METRICS ==================

        # Cache operations
        self.cache_operations = Counter(
            f"{prefix}_cache_operations_total",
            "Cache operations count",
            labels=["operation", "result"]  # get/set, hit/miss
        )

        # ================== SYSTEM HEALTH METRICS ==================

        # Active requests gauge
        self.active_requests = Gauge(
            f"{prefix}_active_requests",
            "Number of currently active requests",
            labels=["endpoint"]
        )

        # Vector index stats
        self.vector_index_size = Gauge(
            f"{prefix}_vector_index_size",
            "Number of vectors in FAISS index",
            labels=[]
        )

        self.vector_index_last_update = Gauge(
            f"{prefix}_vector_index_last_update_timestamp",
            "Unix timestamp of last index update",
            labels=[]
        )

        # LLM cache stats
        self.llm_cache_size = Gauge(
            f"{prefix}_llm_cache_size",
            "Number of entries in LLM cache",
            labels=[]
        )

        # ================== RECOMMENDATION METRICS ==================

        self.recommendation_algorithm = Counter(
            f"{prefix}_recommendation_algorithm_total",
            "Recommendations by algorithm",
            labels=["algorithm"]
        )

        # Track all registered metrics for collection
        self._all_metrics = [
            self.request_total,
            self.error_total,
            self.request_latency,
            self.stage_latency,
            self.search_result_count,
            self.search_top_score,
            self.search_score_gap,
            self.zero_result_total,
            self.fallback_total,
            self.bad_query_total,
            self.dependency_latency,
            self.dependency_error_total,
            self.cache_operations,
            self.active_requests,
            self.vector_index_size,
            self.vector_index_last_update,
            self.llm_cache_size,
            self.recommendation_algorithm,
        ]

    def collect(self) -> str:
        """
        Collect all metrics in Prometheus exposition format.

        Returns text suitable for /metrics endpoint.
        """
        sections = []
        for metric in self._all_metrics:
            try:
                sections.append(metric.collect())
            except Exception:
                pass  # Skip failed metrics

        return "\n\n".join(sections) + "\n"

    # ================== CONVENIENCE METHODS ==================

    def record_request(
        self,
        endpoint: str,
        method: str,
        status: int,
        duration_ms: float
    ) -> None:
        """Record a completed request."""
        status_class = f"{status // 100}xx"
        self.request_total.inc(endpoint=endpoint, method=method, status=status_class)
        self.request_latency.observe(duration_ms, endpoint=endpoint, method=method)

    def record_error(self, endpoint: str, error_type: str) -> None:
        """Record an error."""
        self.error_total.inc(endpoint=endpoint, error_type=error_type)

    def record_search_quality(
        self,
        endpoint: str,
        result_count: int,
        top_score: Optional[float] = None,
        score_gap: Optional[float] = None,
        is_zero_result: bool = False,
        is_fallback: bool = False,
        fallback_type: Optional[str] = None
    ) -> None:
        """Record search quality metrics."""
        self.search_result_count.observe(result_count, endpoint=endpoint)

        if top_score is not None:
            self.search_top_score.observe(top_score, endpoint=endpoint)

        if score_gap is not None:
            self.search_score_gap.observe(score_gap, endpoint=endpoint)

        if is_zero_result:
            self.zero_result_total.inc(endpoint=endpoint)

        if is_fallback and fallback_type:
            self.fallback_total.inc(fallback_type=fallback_type)

    def record_stage(self, stage: str, duration_ms: float) -> None:
        """Record processing stage timing."""
        self.stage_latency.observe(duration_ms, stage=stage)

    def record_dependency(
        self,
        dependency: str,
        duration_ms: float,
        error: Optional[str] = None
    ) -> None:
        """Record external dependency call."""
        self.dependency_latency.observe(duration_ms, dependency=dependency)
        if error:
            self.dependency_error_total.inc(dependency=dependency, error_type=error)

    def record_cache(self, operation: str, hit: bool) -> None:
        """Record cache operation."""
        result = "hit" if hit else "miss"
        self.cache_operations.inc(operation=operation, result=result)

    def record_bad_query(self, reason: str) -> None:
        """Record a low-quality query."""
        self.bad_query_total.inc(reason=reason)


# Global singleton metrics collector
metrics = MetricsCollector()


def timed_dependency(dependency_name: str):
    """
    Decorator to time external dependency calls.

    Usage:
        @timed_dependency("redis")
        def get_from_cache(key):
            return redis.get(key)
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            error = None
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error = type(e).__name__
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                metrics.record_dependency(dependency_name, duration_ms, error)
        return wrapper
    return decorator


async def timed_dependency_async(dependency_name: str):
    """Async version of timed_dependency decorator."""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            error = None
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                error = type(e).__name__
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                metrics.record_dependency(dependency_name, duration_ms, error)
        return wrapper
    return decorator
