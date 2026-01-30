"""
Observability Decorators

Non-invasive decorators for:
- Timing processing stages
- Tracking search quality metrics
- Recording dependency calls

Usage:
    @timed_operation("llm_processing")
    async def process_query(query: str):
        ...

    @track_search_quality
    async def semantic_search(request):
        ...
"""

import time
import asyncio
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, TypeVar, Union
from contextlib import contextmanager

from .config import obs_config
from .logger import get_logger, get_request_context
from .metrics import metrics
from .tracing import trace_span

logger = get_logger(__name__)

T = TypeVar("T")


@contextmanager
def timed_stage(stage_name: str):
    """
    Context manager for timing a processing stage.

    Usage:
        with timed_stage("faiss_search"):
            results = faiss_index.search(query, k=100)

    Automatically records:
    - Stage duration in metrics
    - Timing in request context
    - Debug log with duration
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start) * 1000

        # Record in metrics
        metrics.record_stage(stage_name, duration_ms)

        # Record in request context
        ctx = get_request_context()
        if ctx:
            ctx.set_timing(stage_name, duration_ms)

        logger.debug(f"Stage {stage_name} completed", stage=stage_name, duration_ms=round(duration_ms, 2))


def timed_operation(stage_name: str):
    """
    Decorator for timing a function as a processing stage.

    Works with both sync and async functions.

    Usage:
        @timed_operation("db_query")
        def fetch_items(ids):
            return db.query(Item).filter(Item.id.in_(ids)).all()

        @timed_operation("llm_processing")
        async def process_query_async(query):
            return await openai.chat.complete(...)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            start = time.perf_counter()
            try:
                with trace_span(stage_name):
                    return await func(*args, **kwargs)
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                metrics.record_stage(stage_name, duration_ms)
                ctx = get_request_context()
                if ctx:
                    ctx.set_timing(stage_name, duration_ms)

        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> T:
            start = time.perf_counter()
            try:
                with trace_span(stage_name):
                    return func(*args, **kwargs)
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                metrics.record_stage(stage_name, duration_ms)
                ctx = get_request_context()
                if ctx:
                    ctx.set_timing(stage_name, duration_ms)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def track_search_quality(
    endpoint_name: str = "search",
    score_extractor: Optional[Callable[[Any], List[float]]] = None,
):
    """
    Decorator to track search quality metrics from search results.

    Automatically records:
    - Result count
    - Top score
    - Score gap (top1 - top2)
    - Zero result rate
    - Fallback usage

    Usage:
        @track_search_quality("semantic_search")
        async def semantic_search(request):
            # ... perform search ...
            return SearchResponse(items=items, total=total, ...)

    Args:
        endpoint_name: Name for labeling metrics
        score_extractor: Optional function to extract scores from response
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            result = await func(*args, **kwargs)
            _record_search_quality(result, endpoint_name, score_extractor)
            return result

        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> T:
            result = func(*args, **kwargs)
            _record_search_quality(result, endpoint_name, score_extractor)
            return result

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def _record_search_quality(
    result: Any,
    endpoint_name: str,
    score_extractor: Optional[Callable[[Any], List[float]]] = None,
) -> None:
    """Record search quality metrics from a search result."""
    try:
        # Try to extract items from common response patterns
        items = None
        total = 0
        filters_applied = {}

        if hasattr(result, "items"):
            items = result.items
        if hasattr(result, "total"):
            total = result.total
        if hasattr(result, "filters_applied"):
            filters_applied = result.filters_applied or {}

        # Count results
        result_count = len(items) if items else 0
        is_zero_result = result_count == 0

        # Extract scores
        scores: List[float] = []
        if score_extractor and result:
            try:
                scores = score_extractor(result)
            except Exception:
                pass

        # Calculate score metrics
        top_score = scores[0] if scores else None
        score_gap = (scores[0] - scores[1]) if len(scores) >= 2 else None

        # Check for fallback
        is_fallback = filters_applied.get("is_fallback", False) or \
                      filters_applied.get("search_type") == "fallback"
        fallback_type = filters_applied.get("fallback_type")

        # Record metrics
        metrics.record_search_quality(
            endpoint=endpoint_name,
            result_count=result_count,
            top_score=top_score,
            score_gap=score_gap,
            is_zero_result=is_zero_result,
            is_fallback=is_fallback,
            fallback_type=fallback_type,
        )

        # Log search result
        logger.debug(
            "Search quality recorded",
            endpoint=endpoint_name,
            result_count=result_count,
            total=total,
            top_score=top_score,
            is_zero_result=is_zero_result,
        )

    except Exception as e:
        logger.debug(f"Failed to record search quality: {e}")


def track_dependency(dependency_name: str):
    """
    Decorator to track external dependency calls.

    Records latency and errors for dependencies like:
    - Database queries
    - Redis operations
    - External API calls

    Usage:
        @track_dependency("mongodb")
        def fetch_business_info(seller_ids):
            return mongo_collection.find({"_id": {"$in": seller_ids}})
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            start = time.perf_counter()
            error = None
            try:
                with trace_span(f"dependency_{dependency_name}"):
                    return await func(*args, **kwargs)
            except Exception as e:
                error = type(e).__name__
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                metrics.record_dependency(dependency_name, duration_ms, error)

        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> T:
            start = time.perf_counter()
            error = None
            try:
                with trace_span(f"dependency_{dependency_name}"):
                    return func(*args, **kwargs)
            except Exception as e:
                error = type(e).__name__
                raise
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                metrics.record_dependency(dependency_name, duration_ms, error)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def detect_bad_query(query: str, min_length: int = 2, stopwords: Optional[set] = None) -> Optional[str]:
    """
    Detect signals of a low-quality search query.

    Returns the reason if query is bad, None otherwise.

    Bad query signals:
    - Too short (< min_length chars after stripping)
    - Only stopwords
    - Only punctuation/symbols
    - Empty after normalization

    Args:
        query: The search query
        min_length: Minimum acceptable query length
        stopwords: Set of stopwords to check against

    Returns:
        Reason string if bad query, None if acceptable
    """
    if stopwords is None:
        stopwords = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                     "being", "have", "has", "had", "do", "does", "did", "will",
                     "would", "could", "should", "may", "might", "must", "shall",
                     "can", "need", "dare", "ought", "used", "to", "of", "in",
                     "for", "on", "with", "at", "by", "from", "as", "into",
                     "through", "during", "before", "after", "above", "below",
                     "between", "under", "again", "further", "then", "once"}

    # Clean query
    cleaned = query.strip().lower()

    if not cleaned:
        return "empty_query"

    if len(cleaned) < min_length:
        return "too_short"

    # Remove non-alphanumeric
    alpha_only = "".join(c for c in cleaned if c.isalnum() or c.isspace())

    if not alpha_only.strip():
        return "only_symbols"

    # Check if only stopwords
    words = alpha_only.split()
    non_stopwords = [w for w in words if w not in stopwords]

    if not non_stopwords:
        return "only_stopwords"

    return None


def validate_and_track_query(query: str) -> Optional[str]:
    """
    Validate query and record bad query metrics if applicable.

    Returns the reason if query is bad, None otherwise.
    """
    reason = detect_bad_query(query)
    if reason:
        metrics.record_bad_query(reason)
    return reason
