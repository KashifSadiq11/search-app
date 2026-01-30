"""
OpenTelemetry Tracing Support

Provides:
- Optional OpenTelemetry integration
- Span creation and management
- Trace ID propagation
- Graceful degradation when OTEL not available

Tracing is disabled by default. Enable with TRACING_ENABLED=true.
"""

import time
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional, Callable
from functools import wraps

from .config import obs_config
from .logger import get_request_context, RequestContext


# Try to import OpenTelemetry
OTEL_AVAILABLE = False
tracer = None

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.trace import StatusCode, Status
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

    OTEL_AVAILABLE = True
except ImportError:
    pass


class NoOpSpan:
    """No-op span for when tracing is disabled."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass

    def add_event(self, name: str, attributes: Dict[str, Any] = None) -> None:
        pass


class NoOpTracer:
    """No-op tracer for when tracing is disabled."""

    @contextmanager
    def start_as_current_span(self, name: str, **kwargs) -> Generator[NoOpSpan, None, None]:
        yield NoOpSpan()

    def start_span(self, name: str, **kwargs) -> NoOpSpan:
        return NoOpSpan()


def setup_tracing() -> None:
    """
    Initialize OpenTelemetry tracing if enabled and available.

    Should be called once at application startup.
    """
    global tracer

    if not obs_config.tracing_enabled:
        tracer = NoOpTracer()
        return

    if not OTEL_AVAILABLE:
        tracer = NoOpTracer()
        return

    try:
        # Create resource with service name
        resource = Resource.create({
            SERVICE_NAME: obs_config.otel_service_name,
        })

        # Create tracer provider
        provider = TracerProvider(resource=resource)

        # Add exporter if endpoint configured
        if obs_config.otel_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                exporter = OTLPSpanExporter(endpoint=obs_config.otel_endpoint)
                provider.add_span_processor(BatchSpanProcessor(exporter))
            except ImportError:
                pass  # OTLP exporter not available

        # Set as global tracer provider
        trace.set_tracer_provider(provider)

        # Get tracer
        tracer = trace.get_tracer(obs_config.otel_service_name)

    except Exception:
        tracer = NoOpTracer()


def get_tracer():
    """Get the configured tracer (or no-op tracer)."""
    global tracer
    if tracer is None:
        tracer = NoOpTracer()
    return tracer


@contextmanager
def trace_span(
    name: str,
    attributes: Dict[str, Any] = None
) -> Generator[Any, None, None]:
    """
    Create a tracing span with automatic timing and error handling.

    Usage:
        with trace_span("faiss_search", {"k": 100}) as span:
            results = faiss_index.search(query, k)
            span.set_attribute("result_count", len(results))

    Args:
        name: Span name (e.g., "llm_processing", "faiss_search")
        attributes: Initial span attributes
    """
    t = get_tracer()

    with t.start_as_current_span(name) as span:
        # Set initial attributes
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)

        # Add request context
        ctx = get_request_context()
        if ctx:
            span.set_attribute("request_id", ctx.request_id)

        start_time = time.perf_counter()
        try:
            yield span
        except Exception as e:
            span.record_exception(e)
            if OTEL_AVAILABLE:
                span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            span.set_attribute("duration_ms", round(duration_ms, 2))


def trace_function(name: str = None, attributes: Dict[str, Any] = None):
    """
    Decorator to trace a function.

    Usage:
        @trace_function("process_query")
        async def process_query(query: str) -> Result:
            ...

        @trace_function()  # Uses function name as span name
        def calculate_scores(items):
            ...
    """
    def decorator(func: Callable):
        span_name = name or func.__name__

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            with trace_span(span_name, attributes):
                return await func(*args, **kwargs)

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            with trace_span(span_name, attributes):
                return func(*args, **kwargs)

        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def get_trace_context() -> Dict[str, str]:
    """
    Get current trace context for propagation.

    Returns dict with traceparent header for W3C trace context.
    """
    if not OTEL_AVAILABLE or not obs_config.tracing_enabled:
        return {}

    try:
        propagator = TraceContextTextMapPropagator()
        carrier = {}
        propagator.inject(carrier)
        return carrier
    except Exception:
        return {}


def extract_trace_context(headers: Dict[str, str]) -> Optional[Any]:
    """
    Extract trace context from incoming request headers.

    Used for distributed tracing across services.
    """
    if not OTEL_AVAILABLE or not obs_config.tracing_enabled:
        return None

    try:
        propagator = TraceContextTextMapPropagator()
        return propagator.extract(headers)
    except Exception:
        return None
