# Performance Monitoring and Logging System

This directory contains the comprehensive monitoring and logging system for bt_api_py, designed for production trading environments with real-time observability.

## Overview

The monitoring system provides:

- **Real-time Performance Metrics**: CPU, memory, network, and business metrics
- **Structured Logging**: JSON logs with correlation IDs and context
- **Exchange Health Monitoring**: Automated health checks for all exchanges
- **Prometheus Integration**: Metrics exposure with industry-standard format
- **Grafana Dashboards**: Pre-built dashboards for visualization
- **ELK Stack Support**: Elasticsearch, Logstash, and Kibana integration
- **Business Metrics**: Trading-specific metrics and KPIs
- **Alert System**: Configurable alerts for critical conditions

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   BT API Py     │────│   Prometheus    │────│    Grafana      │
│   Application   │    │   (Metrics)     │    │  (Dashboards)   │
└─────────────────┘    └──────────────────┘    └─────────────────┘
         │                       │                       │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  Structured     │    │    ELK Stack    │    │   Alerting      │
│  Logging       │    │  (Logs Store)   │    │  (AlertMgr)     │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## Quick Start

### 1. Basic Setup

```python
import asyncio
from bt_api_py.monitoring.config import setup_monitoring, PRODUCTION_CONFIG
from bt_api_py.monitoring import monitor_performance

# Setup complete monitoring system
asyncio.run(setup_monitoring(PRODUCTION_CONFIG))

# Use performance monitoring
@monitor_performance("trading.execute_order")
async def execute_order(symbol, side, quantity):
    # Your trading logic here
    pass
```

### 2. Docker Stack

```bash
# Start the complete monitoring stack
docker-compose -f docker/monitoring-stack.yml up -d

# Access services
# Grafana: http://localhost:3000 (admin/admin123)
# Prometheus: http://localhost:9090
# Kibana: http://localhost:5601
```

### 3. Custom Metrics

```python
from bt_api_py.monitoring import counter, gauge, histogram

# Create metrics
order_counter = counter("orders_total", "Total number of orders")
price_gauge = gauge("current_price", "Current price of symbol")
latency_histogram = histogram("api_latency", "API request latency")

# Use metrics
order_counter.inc()
price_gauge.set(50000.0)
latency_histogram.observe(0.025)
```

## Components

### Metrics Collection

#### System Metrics
- CPU usage (overall and per-process)
- Memory usage (RSS, virtual, system)
- Network I/O (bytes sent/received)
- Disk usage and I/O
- Thread and file descriptor counts
- Garbage collection statistics

#### Business Metrics
- Order counts and success rates
- API request latency and error rates
- Market data update rates
- WebSocket connection status
- Exchange-specific metrics
- Trading volume and P&L

### Structured Logging

#### Features
- JSON formatted logs with correlation IDs
- Context-aware logging with request tracing
- Component and function-level logging
- Error tracking with stack traces
- Performance timing integration
- Log rotation and archival

#### Log Levels
- `DEBUG`: Detailed debugging information
- `INFO`: General information messages
- `WARNING`: Warning conditions
- `ERROR`: Error conditions
- `CRITICAL`: Critical errors

#### Log Types
```python
logger = get_logger("trading")

# API request logging
logger.api_request("GET", "/api/v1/ticker", "BINANCE", 200, 45.5)

# Order event logging
logger.order_event("placed", "BINANCE", "BTCUSDT", "BUY", 0.1)

# Connection event logging
logger.connection_event("connected", "BINANCE", "websocket")

# Market data logging
logger.market_data_event("received", "BINANCE", "BTCUSDT", "ticker")
```

### Exchange Health Monitoring

#### Health Checks
- API endpoint ping tests
- WebSocket connectivity checks
- Data freshness validation
- Rate limit monitoring
- Custom business logic checks

#### Health Status
- `HEALTHY`: All checks passing
- `DEGRADED`: Some non-critical checks failing
- `UNHEALTHY`: Critical checks failing
- `UNKNOWN`: Insufficient data

### Prometheus Integration

#### Metrics Endpoint
- HTTP endpoint at `/metrics`
- Prometheus exposition format
- Automatic metric registration
- Health check endpoint at `/health`

#### Metric Types
- Counters: Monotonically increasing values
- Gauges: Values that can increase/decrease
- Histograms: Distributions with configurable buckets

### Grafana Dashboards

#### Pre-built Dashboards
- **Trading Dashboard**: Order metrics, latency, success rates
- **System Dashboard**: CPU, memory, network, disk metrics
- **Exchange Dashboards**: Per-exchange health and performance
- **Business KPI**: Trading volume, P&L, error rates

#### Dashboard Features
- Real-time updates (5s refresh)
- Interactive filtering and drill-down
- Alert thresholds and indicators
- Export and sharing capabilities

### ELK Stack Integration

#### Components
- **Elasticsearch**: Log storage and indexing
- **Logstash**: Log processing and enrichment
- **Kibana**: Log visualization and analysis

#### Log Processing
- JSON parsing and field extraction
- Timestamp normalization
- Error categorization
- Geographic and user-agent parsing
- Custom field enrichment

## Configuration

### Production Configuration
```python
PRODUCTION_CONFIG = MonitoringConfig(
    metrics_collection_interval=5.0,
    prometheus_host="0.0.0.0",
    prometheus_port=8080,
    log_level="INFO",
    log_file="logs/bt_api_py.log",
    elk_enabled=True,
    elasticsearch_host="elasticsearch.monitoring.svc.cluster.local",
    elasticsearch_port=9200,
    logstash_host="logstash.monitoring.svc.cluster.local",
    logstash_port=5000,
)
```

### Development Configuration
```python
DEVELOPMENT_CONFIG = MonitoringConfig(
    metrics_collection_interval=10.0,
    prometheus_host="127.0.0.1",
    prometheus_port=9090,
    log_level="DEBUG",
    log_file="logs/bt_api_py_dev.log",
    elk_enabled=False,
)
```

## Best Practices

### Performance Monitoring
1. **Use appropriate metric types**:
   - Counters for events (orders, requests)
   - Gauges for current state (connections, memory)
   - Histograms for distributions (latency)

2. **Choose meaningful buckets**:
   ```python
   latency_histogram = histogram(
       "api_latency_seconds",
       buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
   )
   ```

3. **Add context to metrics**:
   ```python
   @monitor_performance("order.place", exchange_name="BINANCE")
   def place_order(symbol, side, quantity):
       pass
   ```

### Structured Logging
1. **Use correlation IDs** for request tracing
2. **Include business context** in log messages
3. **Log at appropriate levels** (DEBUG vs INFO vs ERROR)
4. **Add structured metadata** for easy searching

### Health Monitoring
1. **Define appropriate timeouts** for health checks
2. **Set critical thresholds** for business-critical checks
3. **Monitor dependency health** (databases, external APIs)
4. **Automate recovery** where possible

## Alerts and Notifications

### Alert Types
- System resource alerts (CPU, memory, disk)
- Application error rate alerts
- Exchange health alerts
- Business metric alerts (order failure rates)
- Performance degradation alerts

### Alert Channels
- Email notifications
- Slack/webhook integrations
- PagerDuty for critical alerts
- Custom webhook endpoints

## Troubleshooting

### Common Issues

1. **Metrics not appearing in Prometheus**
   - Check Prometheus configuration
   - Verify metrics endpoint is accessible
   - Check network connectivity

2. **Logs not appearing in Kibana**
   - Verify Logstash configuration
   - Check Elasticsearch connectivity
   - Validate log format

3. **High memory usage**
   - Monitor metric retention periods
   - Check for metric leaks
   - Profile application memory

### Debugging Tools
```python
# Get current metrics
from bt_api_py.monitoring import get_global_collector
collector = get_global_collector()
metrics = collector.get_current_metrics()

# Export metrics for debugging
metrics_json = collector.export_metrics_json()
print(metrics_json)

# Check monitoring system health
from bt_api_py.monitoring import get_prometheus_exporter
exporter = get_prometheus_exporter()
print(f"Exporter running: {exporter.is_running()}")
```

## Examples

See `examples/monitoring_examples.py` for comprehensive usage examples including:
- Function performance monitoring
- Custom metrics usage
- Structured logging patterns
- Exchange health monitoring
- Complete trading bot integration

## Security Considerations

### Metrics Security
- Restrict Prometheus endpoint access
- Use authentication for Grafana
- Enable TLS for external communications
- Implement rate limiting for metrics endpoints

### Log Security
- Sanitize sensitive data from logs
- Use secure log transport (TLS)
- Implement log retention policies
- Monitor for log injection attempts

### Production Deployment
- Run monitoring services in isolated networks
- Use service mesh for secure communication
- Implement proper authentication/authorization
- Regular security updates for monitoring stack

## Performance Tuning

### Metrics Collection
- Adjust collection intervals based on needs
- Use sampling for high-frequency metrics
- Optimize metric cardinality
- Consider metric aggregation

### Log Processing
- Tune Logstash worker threads
- Optimize Elasticsearch shard configuration
- Use appropriate index lifecycle policies
- Monitor pipeline performance

### Storage Optimization
- Configure appropriate retention periods
- Use data compression
- Implement tiered storage
- Monitor storage growth

## Contributing

When adding new monitoring features:
1. Follow existing patterns and conventions
2. Add comprehensive documentation
3. Include unit tests
4. Update configuration examples
5. Test with production-like workloads

## Support

For monitoring system questions:
- Check existing documentation and examples
- Review GitHub issues
- Consult the development team
- Join the monitoring working group