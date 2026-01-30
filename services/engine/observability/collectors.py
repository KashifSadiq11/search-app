"""
Search Quality Collectors

Provides:
- In-memory ring buffer for recent searches
- Aggregated search quality statistics
- Offline evaluation hooks
- Click-through rate tracking (when events available)

This collector can be queried by the observability dashboard
to display recent search quality metrics.
"""

import time
import threading
import statistics
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Deque, Dict, List, Optional
from datetime import datetime, timedelta

from .config import obs_config
from .logger import get_logger, _hash_pii

logger = get_logger(__name__)


@dataclass
class SearchEvent:
    """Record of a single search event for quality analysis."""

    timestamp: float = field(default_factory=time.time)
    request_id: str = ""

    # Query info (anonymized)
    query_hash: str = ""  # Hashed query for grouping
    query_length: int = 0
    category: Optional[str] = None

    # Timing breakdown (ms)
    total_ms: float = 0
    llm_ms: Optional[float] = None
    faiss_ms: Optional[float] = None
    db_ms: Optional[float] = None
    rerank_ms: Optional[float] = None

    # Results
    result_count: int = 0
    total_matches: int = 0

    # Quality signals
    top_score: Optional[float] = None
    score_gap: Optional[float] = None  # top1 - top2
    avg_top_k_score: Optional[float] = None  # avg of top K scores
    is_zero_result: bool = False
    is_fallback: bool = False
    is_llm_corrected: bool = False

    # Query quality
    bad_query_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        # Convert timestamp to ISO format
        d["timestamp_iso"] = datetime.fromtimestamp(self.timestamp).isoformat()
        return d


@dataclass
class SearchQualityStats:
    """Aggregated statistics over a time window."""

    window_seconds: int = 300  # 5 minutes
    request_count: int = 0
    error_count: int = 0

    # Latency stats
    latency_p50_ms: float = 0
    latency_p95_ms: float = 0
    latency_p99_ms: float = 0
    latency_avg_ms: float = 0

    # Quality stats
    avg_result_count: float = 0
    avg_top_score: float = 0
    avg_score_gap: float = 0
    zero_result_rate: float = 0
    fallback_rate: float = 0
    llm_correction_rate: float = 0
    bad_query_rate: float = 0

    # Throughput
    requests_per_second: float = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


class SearchQualityCollector:
    """
    Collects and aggregates search quality metrics.

    Maintains a ring buffer of recent search events and provides
    aggregated statistics for dashboard display.

    Thread-safe for concurrent access.
    """

    def __init__(self, buffer_size: int = None):
        self._buffer_size = buffer_size or obs_config.ring_buffer_size
        self._events: Deque[SearchEvent] = deque(maxlen=self._buffer_size)
        self._lock = threading.Lock()

        # Click/conversion tracking
        self._clicks: Deque[Dict[str, Any]] = deque(maxlen=self._buffer_size)
        self._conversions: Deque[Dict[str, Any]] = deque(maxlen=self._buffer_size)

    def record_search(
        self,
        request_id: str,
        query: str,
        result_count: int,
        total_matches: int,
        timings: Optional[Dict[str, float]] = None,
        scores: Optional[List[float]] = None,
        category: Optional[str] = None,
        is_fallback: bool = False,
        is_llm_corrected: bool = False,
        bad_query_reason: Optional[str] = None,
    ) -> None:
        """
        Record a search event.

        Args:
            request_id: Correlation ID
            query: Original search query (will be hashed)
            result_count: Number of results returned
            total_matches: Total matching documents
            timings: Timing breakdown by stage
            scores: List of relevance scores (for top-k)
            category: Search category filter
            is_fallback: Whether fallback was used
            is_llm_corrected: Whether LLM corrected the query
            bad_query_reason: If query was flagged as bad
        """
        timings = timings or {}
        scores = scores or []

        event = SearchEvent(
            request_id=request_id,
            query_hash=_hash_pii(query.lower().strip()),
            query_length=len(query),
            category=category,
            total_ms=timings.get("total", 0),
            llm_ms=timings.get("llm_processing"),
            faiss_ms=timings.get("faiss_search"),
            db_ms=timings.get("db_query"),
            rerank_ms=timings.get("rerank"),
            result_count=result_count,
            total_matches=total_matches,
            top_score=scores[0] if scores else None,
            score_gap=(scores[0] - scores[1]) if len(scores) >= 2 else None,
            avg_top_k_score=statistics.mean(scores[:10]) if scores else None,
            is_zero_result=result_count == 0,
            is_fallback=is_fallback,
            is_llm_corrected=is_llm_corrected,
            bad_query_reason=bad_query_reason,
        )

        with self._lock:
            self._events.append(event)

    def record_click(
        self,
        request_id: str,
        item_id: str,
        position: int,
        user_id: Optional[str] = None,
    ) -> None:
        """Record a click event for CTR calculation."""
        click = {
            "timestamp": time.time(),
            "request_id": request_id,
            "item_id": item_id,
            "position": position,
            "user_id_hash": _hash_pii(user_id) if user_id else None,
        }
        with self._lock:
            self._clicks.append(click)

    def record_conversion(
        self,
        request_id: str,
        item_id: str,
        conversion_type: str,  # "purchase", "add_to_cart", etc.
        user_id: Optional[str] = None,
    ) -> None:
        """Record a conversion event."""
        conversion = {
            "timestamp": time.time(),
            "request_id": request_id,
            "item_id": item_id,
            "conversion_type": conversion_type,
            "user_id_hash": _hash_pii(user_id) if user_id else None,
        }
        with self._lock:
            self._conversions.append(conversion)

    def get_recent_events(
        self,
        limit: int = 100,
        since_seconds: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get recent search events.

        Args:
            limit: Maximum number of events to return
            since_seconds: Only events within last N seconds

        Returns:
            List of search events as dictionaries
        """
        cutoff = time.time() - since_seconds if since_seconds else 0

        with self._lock:
            events = [
                e.to_dict() for e in reversed(self._events)
                if e.timestamp >= cutoff
            ]

        return events[:limit]

    def get_stats(self, window_seconds: int = 300) -> SearchQualityStats:
        """
        Get aggregated statistics for the given time window.

        Args:
            window_seconds: Time window for aggregation (default 5 minutes)

        Returns:
            SearchQualityStats with aggregated metrics
        """
        cutoff = time.time() - window_seconds

        with self._lock:
            events = [e for e in self._events if e.timestamp >= cutoff]

        if not events:
            return SearchQualityStats(window_seconds=window_seconds)

        # Calculate stats
        latencies = [e.total_ms for e in events if e.total_ms > 0]
        result_counts = [e.result_count for e in events]
        top_scores = [e.top_score for e in events if e.top_score is not None]
        score_gaps = [e.score_gap for e in events if e.score_gap is not None]

        zero_results = sum(1 for e in events if e.is_zero_result)
        fallbacks = sum(1 for e in events if e.is_fallback)
        llm_corrected = sum(1 for e in events if e.is_llm_corrected)
        bad_queries = sum(1 for e in events if e.bad_query_reason)

        n = len(events)

        stats = SearchQualityStats(
            window_seconds=window_seconds,
            request_count=n,
        )

        # Latency percentiles
        if latencies:
            latencies_sorted = sorted(latencies)
            stats.latency_avg_ms = round(statistics.mean(latencies), 2)
            stats.latency_p50_ms = round(self._percentile(latencies_sorted, 50), 2)
            stats.latency_p95_ms = round(self._percentile(latencies_sorted, 95), 2)
            stats.latency_p99_ms = round(self._percentile(latencies_sorted, 99), 2)

        # Quality metrics
        if result_counts:
            stats.avg_result_count = round(statistics.mean(result_counts), 2)
        if top_scores:
            stats.avg_top_score = round(statistics.mean(top_scores), 4)
        if score_gaps:
            stats.avg_score_gap = round(statistics.mean(score_gaps), 4)

        # Rates
        stats.zero_result_rate = round(zero_results / n, 4) if n else 0
        stats.fallback_rate = round(fallbacks / n, 4) if n else 0
        stats.llm_correction_rate = round(llm_corrected / n, 4) if n else 0
        stats.bad_query_rate = round(bad_queries / n, 4) if n else 0

        # Throughput
        if events:
            time_span = events[-1].timestamp - events[0].timestamp
            if time_span > 0:
                stats.requests_per_second = round(n / time_span, 2)
            else:
                stats.requests_per_second = n  # All in same second

        return stats

    def get_ctr_stats(self, window_seconds: int = 3600) -> Dict[str, float]:
        """
        Calculate click-through rate statistics.

        Returns:
            Dict with CTR metrics
        """
        cutoff = time.time() - window_seconds

        with self._lock:
            searches = [e for e in self._events if e.timestamp >= cutoff and e.result_count > 0]
            clicks = [c for c in self._clicks if c["timestamp"] >= cutoff]

        search_request_ids = {e.request_id for e in searches}
        clicked_request_ids = {c["request_id"] for c in clicks}

        searches_with_clicks = len(search_request_ids & clicked_request_ids)

        return {
            "total_searches": len(searches),
            "total_clicks": len(clicks),
            "searches_with_click": searches_with_clicks,
            "ctr": round(searches_with_clicks / len(searches), 4) if searches else 0,
            "avg_clicks_per_search": round(len(clicks) / len(searches), 2) if searches else 0,
        }

    def get_position_distribution(self, window_seconds: int = 3600) -> Dict[int, int]:
        """
        Get distribution of click positions.

        Returns:
            Dict mapping position to click count
        """
        cutoff = time.time() - window_seconds

        with self._lock:
            clicks = [c for c in self._clicks if c["timestamp"] >= cutoff]

        distribution: Dict[int, int] = {}
        for click in clicks:
            pos = click["position"]
            distribution[pos] = distribution.get(pos, 0) + 1

        return distribution

    def _percentile(self, sorted_data: List[float], p: float) -> float:
        """Calculate percentile from sorted data."""
        if not sorted_data:
            return 0
        k = (len(sorted_data) - 1) * (p / 100)
        f = int(k)
        c = f + 1 if f + 1 < len(sorted_data) else f
        return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])

    def clear(self) -> None:
        """Clear all collected data."""
        with self._lock:
            self._events.clear()
            self._clicks.clear()
            self._conversions.clear()


# Global singleton collector
search_quality = SearchQualityCollector()
