"""
FastAPI Observability Middleware

Provides:
- Automatic request timing
- Correlation ID generation and propagation
- Prometheus metrics recording
- Trace context extraction
- Non-invasive instrumentation

Usage:
    from observability import setup_observability

    app = FastAPI()
    setup_observability(app)
"""

import time
import uuid
from typing import Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from .config import obs_config
from .logger import (
    setup_logging,
    get_logger,
    RequestContext,
    set_request_context,
    clear_request_context,
    _hash_pii,
)
from .metrics import metrics
from .tracing import setup_tracing, extract_trace_context, get_tracer, trace_span

logger = get_logger(__name__)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """
    Middleware that instruments all requests with:
    - Correlation ID (X-Request-ID header)
    - Request timing
    - Prometheus metrics
    - Trace context propagation
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip metrics endpoint to avoid self-referential metrics
        if request.url.path == obs_config.metrics_path:
            return await call_next(request)

        # Skip health checks from metrics (optional)
        if request.url.path in ("/health", "/", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)

        # Generate or extract request ID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:12]

        # Extract trace context from headers
        trace_ctx = extract_trace_context(dict(request.headers))

        # Create request context
        ctx = RequestContext(
            request_id=request_id,
            endpoint=request.url.path,
            method=request.method,
        )

        # Extract and hash user ID if present
        user_id = request.headers.get("X-User-ID")
        if not user_id:
            # Try to get from query params for GET requests
            user_id = request.query_params.get("user_id")
        if user_id:
            ctx.user_id_hash = _hash_pii(user_id)

        # Set context for this request
        set_request_context(ctx)

        # Track active requests
        endpoint_clean = self._normalize_endpoint(request.url.path)
        metrics.active_requests.inc(endpoint=endpoint_clean)

        start_time = time.perf_counter()
        status_code = 500
        error_type = None

        try:
            # Create trace span for the request
            with trace_span(
                f"{request.method} {endpoint_clean}",
                {"http.method": request.method, "http.url": str(request.url)}
            ) as span:
                response = await call_next(request)
                status_code = response.status_code

                # Add response info to span
                span.set_attribute("http.status_code", status_code)

                # Add request ID to response headers
                response.headers["X-Request-ID"] = request_id

                return response

        except Exception as e:
            error_type = type(e).__name__
            logger.error(
                f"Request failed: {request.method} {request.url.path}",
                error=str(e),
                error_type=error_type,
            )
            raise

        finally:
            # Calculate duration
            duration_ms = (time.perf_counter() - start_time) * 1000

            # Record metrics
            metrics.record_request(
                endpoint=endpoint_clean,
                method=request.method,
                status=status_code,
                duration_ms=duration_ms,
            )

            if error_type:
                metrics.record_error(endpoint=endpoint_clean, error_type=error_type)

            # Decrement active requests
            metrics.active_requests.dec(endpoint=endpoint_clean)

            # Clear request context
            clear_request_context()

    def _normalize_endpoint(self, path: str) -> str:
        """
        Normalize endpoint path for metrics (replace IDs with placeholders).

        Examples:
            /items/abc123/similar -> /items/{item_id}/similar
            /users/user_456/preferences -> /users/{user_id}/preferences
        """
        parts = path.strip("/").split("/")
        normalized = []

        for i, part in enumerate(parts):
            # Skip empty parts
            if not part:
                continue

            # Check if this looks like an ID (hex, uuid, or alphanumeric with numbers)
            if self._looks_like_id(part):
                # Use previous part as context for placeholder name
                if normalized:
                    placeholder = f"{{{normalized[-1].rstrip('s')}_id}}"
                else:
                    placeholder = "{id}"
                normalized.append(placeholder)
            else:
                normalized.append(part)

        return "/" + "/".join(normalized) if normalized else "/"

    def _looks_like_id(self, s: str) -> bool:
        """Check if string looks like an ID."""
        # MongoDB ObjectId (24 hex chars)
        if len(s) == 24 and all(c in "0123456789abcdef" for c in s.lower()):
            return True
        # UUID
        if len(s) == 36 and s.count("-") == 4:
            return True
        # Alphanumeric with at least one digit and 6+ chars
        if len(s) >= 6 and any(c.isdigit() for c in s) and s.isalnum():
            return True
        return False


def create_metrics_endpoint(app: FastAPI) -> None:
    """Add /metrics endpoint to FastAPI app."""

    @app.get(obs_config.metrics_path, include_in_schema=False)
    async def prometheus_metrics():
        """Prometheus metrics endpoint."""
        content = metrics.collect()
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")


def create_observability_health_endpoint(app: FastAPI) -> None:
    """Add /observability/health endpoint for observability status."""

    @app.get("/observability/health", include_in_schema=False)
    async def observability_health():
        """Observability system health check."""
        return {
            "status": "healthy",
            "observability_enabled": obs_config.enabled,
            "metrics_enabled": obs_config.metrics_enabled,
            "tracing_enabled": obs_config.tracing_enabled,
            "structured_logging": obs_config.structured_logging,
            "log_level": obs_config.log_level,
        }


def setup_observability(app: FastAPI) -> None:
    """
    Set up complete observability for FastAPI application.

    This is the main entry point. Call this once during app startup.

    Args:
        app: FastAPI application instance

    Usage:
        app = FastAPI()
        setup_observability(app)
    """
    if not obs_config.enabled:
        logger.info("Observability disabled via OBS_ENABLED=false")
        return

    # Set up structured logging
    setup_logging()

    # Set up tracing
    setup_tracing()

    # Add middleware
    app.add_middleware(ObservabilityMiddleware)

    # Add metrics endpoint
    if obs_config.metrics_enabled:
        create_metrics_endpoint(app)

    # Add observability health endpoint
    create_observability_health_endpoint(app)

    logger.info(
        "Observability initialized",
        metrics_enabled=obs_config.metrics_enabled,
        tracing_enabled=obs_config.tracing_enabled,
        structured_logging=obs_config.structured_logging,
        metrics_path=obs_config.metrics_path,
    )
