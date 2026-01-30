"""
Structured JSON Logger with Correlation ID Support

Provides:
- JSON-formatted log output for production
- Correlation ID (request_id) propagation
- PII anonymization (user IDs are hashed)
- Timing breakdown logging
- Safe handling of sensitive data
"""

import json
import logging
import sys
import hashlib
import random
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, Optional, List

from .config import obs_config


# Context variable for request-scoped data
_request_context: ContextVar[Optional["RequestContext"]] = ContextVar(
    "request_context", default=None
)


@dataclass
class RequestContext:
    """
    Request-scoped context for correlation and timing.

    Stored in a context variable and accessible throughout the request lifecycle.
    """
    request_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    user_id_hash: Optional[str] = None
    session_id_hash: Optional[str] = None
    endpoint: Optional[str] = None
    method: Optional[str] = None
    start_time: float = field(default_factory=time.time)

    # Timing breakdown (populated during request)
    timings: Dict[str, float] = field(default_factory=dict)

    # Request metadata
    query_length: Optional[int] = None
    category: Optional[str] = None

    def set_timing(self, stage: str, duration_ms: float) -> None:
        """Record timing for a processing stage."""
        self.timings[stage] = round(duration_ms, 2)

    def elapsed_ms(self) -> float:
        """Get elapsed time since request start in milliseconds."""
        return round((time.time() - self.start_time) * 1000, 2)

    def to_log_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging (excludes None values)."""
        return {k: v for k, v in asdict(self).items() if v is not None and v != {}}


def get_request_context() -> Optional[RequestContext]:
    """Get current request context from context variable."""
    return _request_context.get()


def set_request_context(ctx: RequestContext) -> None:
    """Set current request context."""
    _request_context.set(ctx)


def clear_request_context() -> None:
    """Clear current request context."""
    _request_context.set(None)


def _hash_pii(value: str) -> str:
    """
    Hash PII value for safe logging.
    Uses first 12 chars of SHA256 for readability while maintaining privacy.
    """
    if not value:
        return ""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


class JsonFormatter(logging.Formatter):
    """
    JSON log formatter for structured logging.

    Outputs each log record as a single JSON line with:
    - timestamp (ISO 8601)
    - level
    - logger name
    - message
    - request context (if available)
    - extra fields
    """

    def __init__(self, include_context: bool = True):
        super().__init__()
        self.include_context = include_context

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add request context if available
        if self.include_context:
            ctx = get_request_context()
            if ctx:
                log_data["request_id"] = ctx.request_id
                if ctx.trace_id:
                    log_data["trace_id"] = ctx.trace_id
                if ctx.endpoint:
                    log_data["endpoint"] = ctx.endpoint
                if ctx.user_id_hash:
                    log_data["user_id_hash"] = ctx.user_id_hash

        # Add extra fields from record
        extra_keys = set(record.__dict__.keys()) - {
            "name", "msg", "args", "created", "filename", "funcName",
            "levelname", "levelno", "lineno", "module", "msecs",
            "pathname", "process", "processName", "relativeCreated",
            "stack_info", "exc_info", "exc_text", "thread", "threadName",
            "message", "asctime"
        }
        for key in extra_keys:
            value = getattr(record, key)
            if value is not None:
                log_data[key] = value

        return json.dumps(log_data, default=str, ensure_ascii=False)


class StructuredLogger:
    """
    Wrapper around Python logger with structured logging support.

    Features:
    - Automatic request context inclusion
    - PII anonymization
    - Sampling for verbose logs
    - Stage timing helpers
    """

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)
        self._sample_rate = obs_config.log_sample_rate

    def _should_sample(self) -> bool:
        """Check if this log should be sampled (for rate limiting)."""
        if self._sample_rate >= 1.0:
            return True
        return random.random() < self._sample_rate

    def _add_context(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Add request context to extra dict."""
        result = extra.copy() if extra else {}
        ctx = get_request_context()
        if ctx:
            result["request_id"] = ctx.request_id
        return result

    def debug(self, msg: str, **kwargs) -> None:
        """Log debug message (sampled)."""
        if self._should_sample():
            self._logger.debug(msg, extra=self._add_context(kwargs))

    def info(self, msg: str, **kwargs) -> None:
        """Log info message."""
        self._logger.info(msg, extra=self._add_context(kwargs))

    def warning(self, msg: str, **kwargs) -> None:
        """Log warning message."""
        self._logger.warning(msg, extra=self._add_context(kwargs))

    def error(self, msg: str, **kwargs) -> None:
        """Log error message."""
        self._logger.error(msg, extra=self._add_context(kwargs))

    def critical(self, msg: str, **kwargs) -> None:
        """Log critical message."""
        self._logger.critical(msg, extra=self._add_context(kwargs))

    def log_search_request(
        self,
        query: str,
        query_length: int,
        category: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        """
        Log search request with anonymization.

        If LOG_PII is False, query is not logged (only length).
        User ID is always hashed.
        """
        data = {
            "event": "search_request",
            "query_length": query_length,
            "category": category,
        }

        if obs_config.log_pii:
            data["query"] = query

        if user_id:
            data["user_id_hash"] = _hash_pii(user_id)

        self.info("Search request received", **data)

    def log_search_result(
        self,
        result_count: int,
        total_matches: int,
        top_score: Optional[float] = None,
        score_gap: Optional[float] = None,
        timings: Optional[Dict[str, float]] = None,
        is_zero_result: bool = False,
        is_fallback: bool = False,
    ) -> None:
        """Log search result with quality metrics."""
        data = {
            "event": "search_result",
            "result_count": result_count,
            "total_matches": total_matches,
            "is_zero_result": is_zero_result,
            "is_fallback": is_fallback,
        }

        if top_score is not None:
            data["top_score"] = round(top_score, 4)
        if score_gap is not None:
            data["score_gap"] = round(score_gap, 4)
        if timings:
            data["timings"] = timings

        # Add total elapsed from context
        ctx = get_request_context()
        if ctx:
            data["total_ms"] = ctx.elapsed_ms()

        self.info("Search completed", **data)

    def log_stage_timing(self, stage: str, duration_ms: float) -> None:
        """Log timing for a processing stage."""
        ctx = get_request_context()
        if ctx:
            ctx.set_timing(stage, duration_ms)

        self.debug(f"Stage completed: {stage}", stage=stage, duration_ms=round(duration_ms, 2))


def setup_logging() -> None:
    """
    Configure root logger for structured logging.

    Call this once at application startup.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(obs_config.log_level_int)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create handler with appropriate formatter
    handler = logging.StreamHandler(sys.stdout)

    if obs_config.structured_logging:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))

    root_logger.addHandler(handler)

    # Suppress noisy loggers
    for logger_name in ["pymongo", "urllib3", "httpx", "httpcore"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str) -> StructuredLogger:
    """Get a structured logger instance."""
    return StructuredLogger(name)
