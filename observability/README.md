# Observability Stack for Recommendation Engine

This directory contains the observability infrastructure for monitoring the recommendation engine's performance, search quality, and system health.

## Quick Start

### 1. Start the Observability Stack

```bash
cd observability
docker-compose up -d
```

This starts:
- **Prometheus** on http://localhost:9090 - Metrics collection
- **Grafana** on http://localhost:3000 - Dashboards (login: admin/admin)

### 2. Configure the API to Export Metrics

Add these environment variables to your `.env`:

```bash
# Observability Configuration
OBS_ENABLED=true
METRICS_ENABLED=true
STRUCTURED_LOGGING=true
LOG_LEVEL=INFO

# Optional: Enable tracing (requires OpenTelemetry collector)
TRACING_ENABLED=false
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

### 3. Integrate with the API

In `main.py`, add the observability setup after creating the FastAPI app:

```python
from observability import setup_observability

app = FastAPI(...)

# Add after app creation, before routes
setup_observability(app)
```

### 4. Access the Dashboards

Open Grafana at http://localhost:3000 and log in with `admin/admin`.

Pre-configured dashboards in the "Rec Engine" folder:
- **System Overview** - High-level health and performance
- **API Performance** - Request rates, latency percentiles, errors
- **Search Quality** - Top scores, zero-result rate, fallback rate
- **Dependencies** - Database, Redis, FAISS, LLM latency

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Application                   │
├─────────────────────────────────────────────────────────┤
│  ObservabilityMiddleware                                │
│  ├─ Correlation ID generation (X-Request-ID)           │
│  ├─ Request timing                                      │
│  ├─ Metrics recording                                   │
│  └─ Trace context propagation                          │
├─────────────────────────────────────────────────────────┤
│  /metrics endpoint (Prometheus format)                  │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                     Prometheus                          │
│  ├─ Scrapes /metrics every 10s                         │
│  ├─ Stores time-series data (15d retention)            │
│  └─ Provides PromQL query interface                    │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                       Grafana                           │
│  ├─ Pre-configured Prometheus datasource               │
│  └─ 4 dashboards auto-provisioned                      │
└─────────────────────────────────────────────────────────┘
```

## Metrics Reference

### Performance Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `rec_engine_requests_total` | Counter | endpoint, method, status | Total requests |
| `rec_engine_errors_total` | Counter | endpoint, error_type | Total errors |
| `rec_engine_request_duration_ms` | Histogram | endpoint, method | Request latency |
| `rec_engine_stage_duration_ms` | Histogram | stage | Processing stage latency |
| `rec_engine_active_requests` | Gauge | endpoint | Currently active requests |

### Search Quality Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `rec_engine_search_result_count` | Histogram | endpoint | Results per search |
| `rec_engine_search_top_score` | Histogram | endpoint | Top result score |
| `rec_engine_search_score_gap` | Histogram | endpoint | Gap between top 2 scores |
| `rec_engine_zero_result_total` | Counter | endpoint | Zero-result searches |
| `rec_engine_fallback_total` | Counter | fallback_type | Fallback usage |
| `rec_engine_bad_query_total` | Counter | reason | Low-quality queries |

### Dependency Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `rec_engine_dependency_duration_ms` | Histogram | dependency | External call latency |
| `rec_engine_dependency_errors_total` | Counter | dependency, error_type | External call errors |
| `rec_engine_cache_operations_total` | Counter | operation, result | Cache operations |

### System Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `rec_engine_vector_index_size` | Gauge | - | FAISS index size |
| `rec_engine_vector_index_last_update_timestamp` | Gauge | - | Last index update |
| `rec_engine_llm_cache_size` | Gauge | - | LLM cache entries |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OBS_ENABLED` | `true` | Master switch for observability |
| `METRICS_ENABLED` | `true` | Enable Prometheus metrics |
| `TRACING_ENABLED` | `false` | Enable OpenTelemetry tracing |
| `STRUCTURED_LOGGING` | `true` | Use JSON log format |
| `LOG_LEVEL` | `INFO` | Logging level |
| `LOG_PII` | `false` | Log raw queries (PII) |
| `LOG_SAMPLE_RATE` | `1.0` | Sample rate for verbose logs |
| `METRICS_PREFIX` | `rec_engine` | Prometheus metric prefix |
| `METRICS_PATH` | `/metrics` | Metrics endpoint path |
| `OBS_RING_BUFFER_SIZE` | `1000` | In-memory event buffer |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | - | OTLP exporter endpoint |
| `OTEL_SERVICE_NAME` | `rec-engine` | Service name for tracing |

## Local Development

### Prometheus Target Configuration

For local development (API running on host), the Prometheus config uses `host.docker.internal`:

```yaml
# observability/prometheus/prometheus.yml
scrape_configs:
  - job_name: 'rec-engine'
    static_configs:
      - targets: ['host.docker.internal:8016']
```

If running everything in Docker on the same network, use the service name:
```yaml
      - targets: ['rec_engine_api:8000']
```

### Load Testing

Generate traffic to see metrics in action:

```bash
# Using hey (https://github.com/rakyll/hey)
hey -n 1000 -c 10 -m POST -H "Content-Type: application/json" \
    -d '{"query": "office bag", "limit": 20}' \
    http://localhost:8016/search/semantic/

# Using k6 (https://k6.io)
k6 run --vus 10 --duration 30s loadtest.js

# Using wrk
wrk -t4 -c20 -d30s -s post.lua http://localhost:8016/search/semantic/
```

### Verifying Metrics

```bash
# Check metrics endpoint
curl http://localhost:8016/metrics

# Check specific metric in Prometheus
curl 'http://localhost:9090/api/v1/query?query=rec_engine_requests_total'
```

## Troubleshooting

### Prometheus can't reach the API

1. Check if the API is exposing `/metrics`:
   ```bash
   curl http://localhost:8016/metrics
   ```

2. For Docker, ensure correct target:
   - Host machine: `host.docker.internal:8016`
   - Docker network: `rec_engine_api:8000`

3. Check Prometheus targets at http://localhost:9090/targets

### No data in Grafana

1. Verify Prometheus is scraping: Check http://localhost:9090/targets
2. Verify metrics exist: Query `rec_engine_requests_total` in Prometheus
3. Check time range in Grafana (metrics need time to accumulate)

### High memory usage

Reduce the ring buffer size:
```bash
OBS_RING_BUFFER_SIZE=100
```

Or disable in-memory collection:
```bash
OBS_ENABLED=false
METRICS_ENABLED=true  # Keep Prometheus metrics
```

## Production Considerations

1. **Security**: Don't expose `/metrics` publicly. Use internal network or auth.

2. **Retention**: Default 15 days. Adjust in prometheus.yml:
   ```yaml
   command:
     - '--storage.tsdb.retention.time=30d'
   ```

3. **Alerting**: Add Alertmanager for production alerts.

4. **Scaling**: For high traffic, consider:
   - Prometheus federation
   - Thanos or Cortex for long-term storage
   - Grafana Mimir

5. **PII**: Keep `LOG_PII=false` in production to avoid logging raw queries.
