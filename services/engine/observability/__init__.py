"""
Observability Module for Recommendation Engine

Provides:
- Structured JSON logging with correlation IDs
- Prometheus metrics (performance, search quality, system health)
- OpenTelemetry tracing support
- Non-invasive middleware and decorators

Usage:
    from observability import setup_observability, metrics, logger

    # In FastAPI app startup
    setup_observability(app)
"""

from .config import ObservabilityConfig, obs_config
from .logger import StructuredLogger, get_logger, RequestContext
from .metrics import MetricsCollector, metrics
from .middleware import ObservabilityMiddleware, setup_observability
from .decorators import timed_operation, track_search_quality
from .collectors import SearchQualityCollector, search_quality

__all__ = [
    # Config
    "ObservabilityConfig",
    "obs_config",
    # Logging
    "StructuredLogger",
    "get_logger",
    "RequestContext",
    # Metrics
    "MetricsCollector",
    "metrics",
    # Middleware
    "ObservabilityMiddleware",
    "setup_observability",
    # Decorators
    "timed_operation",
    "track_search_quality",
    # Collectors
    "SearchQualityCollector",
    "search_quality",
]

__version__ = "1.0.0"
