"""
Observability Configuration

Environment variables for controlling observability features.
All features are opt-in with safe defaults.
"""

import os
from dataclasses import dataclass, field
from typing import Optional, List
import logging


def _bool_env(key: str, default: bool = False) -> bool:
    """Parse boolean from environment variable."""
    val = os.getenv(key, str(default)).lower()
    return val in ("true", "1", "yes", "on")


def _int_env(key: str, default: int) -> int:
    """Parse integer from environment variable."""
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _float_env(key: str, default: float) -> float:
    """Parse float from environment variable."""
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


@dataclass
class ObservabilityConfig:
    """
    Observability configuration loaded from environment variables.

    Environment Variables:
        OBS_ENABLED: Master switch for observability (default: true)
        METRICS_ENABLED: Enable Prometheus metrics (default: true)
        TRACING_ENABLED: Enable OpenTelemetry tracing (default: false)
        STRUCTURED_LOGGING: Use JSON structured logs (default: true)

        LOG_LEVEL: Logging level (default: INFO)
        LOG_PII: Log PII data like raw queries (default: false)
        LOG_SAMPLE_RATE: Sample rate for verbose logs 0.0-1.0 (default: 1.0)

        OTEL_EXPORTER_OTLP_ENDPOINT: OTLP exporter endpoint
        OTEL_SERVICE_NAME: Service name for tracing (default: rec-engine)

        METRICS_PREFIX: Prefix for all metrics (default: rec_engine)
        METRICS_BUCKETS: Comma-separated latency buckets in ms

        OBS_RING_BUFFER_SIZE: Size of in-memory metrics buffer (default: 1000)
    """

    # Master switches
    enabled: bool = field(default_factory=lambda: _bool_env("OBS_ENABLED", True))
    metrics_enabled: bool = field(default_factory=lambda: _bool_env("METRICS_ENABLED", True))
    tracing_enabled: bool = field(default_factory=lambda: _bool_env("TRACING_ENABLED", False))
    structured_logging: bool = field(default_factory=lambda: _bool_env("STRUCTURED_LOGGING", True))

    # Logging configuration
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())
    log_pii: bool = field(default_factory=lambda: _bool_env("LOG_PII", False))
    log_sample_rate: float = field(default_factory=lambda: _float_env("LOG_SAMPLE_RATE", 1.0))

    # OpenTelemetry configuration
    otel_endpoint: Optional[str] = field(default_factory=lambda: os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"))
    otel_service_name: str = field(default_factory=lambda: os.getenv("OTEL_SERVICE_NAME", "rec-engine"))

    # Metrics configuration
    metrics_prefix: str = field(default_factory=lambda: os.getenv("METRICS_PREFIX", "rec_engine"))
    metrics_path: str = field(default_factory=lambda: os.getenv("METRICS_PATH", "/metrics"))

    # Latency histogram buckets (in milliseconds)
    latency_buckets: List[float] = field(default_factory=lambda: [
        5, 10, 25, 50, 75, 100, 150, 200, 300, 500, 750, 1000, 2000, 5000, 10000
    ])

    # In-memory buffer for recent requests (for dashboard)
    ring_buffer_size: int = field(default_factory=lambda: _int_env("OBS_RING_BUFFER_SIZE", 1000))

    # PII fields to hash/anonymize
    pii_fields: List[str] = field(default_factory=lambda: [
        "user_id", "session_id", "email", "ip_address"
    ])

    def __post_init__(self):
        """Validate configuration after initialization."""
        # Ensure log level is valid
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if self.log_level not in valid_levels:
            self.log_level = "INFO"

        # Clamp sample rate
        self.log_sample_rate = max(0.0, min(1.0, self.log_sample_rate))

        # Parse custom latency buckets from env
        custom_buckets = os.getenv("METRICS_LATENCY_BUCKETS")
        if custom_buckets:
            try:
                self.latency_buckets = [float(b.strip()) for b in custom_buckets.split(",")]
            except (ValueError, TypeError):
                pass  # Keep defaults

    @property
    def log_level_int(self) -> int:
        """Get logging level as integer."""
        return getattr(logging, self.log_level, logging.INFO)


# Global singleton configuration
obs_config = ObservabilityConfig()
