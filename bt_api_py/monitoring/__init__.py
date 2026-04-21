"""
Performance monitoring system for bt_api_py.

This module provides comprehensive performance monitoring capabilities including:
- Real-time metrics collection
- Performance timing and profiling
- Resource usage tracking
- Exchange health monitoring
- Alert system for critical metrics
- Prometheus/Grafana integration
- ELK stack compatibility
"""

from __future__ import annotations

from bt_api_base.logging_factory import get_logger

from .collector import (
    MetricsCollector,
    PerformanceMetrics,
    get_global_collector,
    start_global_monitoring,
    stop_global_monitoring,
)
from .decorators import (
    monitor_async_performance,
    monitor_calls,
    monitor_execution_time,
    monitor_performance,
)
from .elk import (
    ElasticsearchClient,
    ELKIntegration,
    LogstashHandler,
    get_elk_integration,
    setup_elk_integration,
    shutdown_elk_integration,
)
from .exchange_health import (
    ExchangeHealthMonitor,
    ExchangeHealthSummary,
    HealthCheck,
    HealthCheckFactory,
    HealthCheckResult,
    HealthStatus,
)
from .grafana import (
    GrafanaDashboardBuilder,
    create_exchange_dashboard,
    create_system_dashboard,
    create_trading_dashboard,
    get_all_dashboard_configs,
    save_dashboard_to_file,
)
from .metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricRegistry,
    PerformanceTimer,
    counter,
    gauge,
    get_registry,
    histogram,
    timer,
)
from .prometheus import (
    PrometheusExporter,
    get_prometheus_exporter,
    start_prometheus_exporter,
    stop_prometheus_exporter,
)
from .system_metrics import (
    BusinessMetricsCollector,
    SystemMetrics,
    SystemMetricsCollector,
    get_business_collector,
    get_system_collector,
)

__all__ = [
    # Core monitoring
    "MetricsCollector",
    "PerformanceMetrics",
    "get_global_collector",
    "start_global_monitoring",
    "stop_global_monitoring",
    # Decorators
    "monitor_performance",
    "monitor_execution_time",
    "monitor_calls",
    "monitor_async_performance",
    # Exchange health
    "ExchangeHealthMonitor",
    "HealthStatus",
    "HealthCheck",
    "HealthCheckResult",
    "ExchangeHealthSummary",
    "HealthCheckFactory",
    # Metrics
    "Counter",
    "Gauge",
    "Histogram",
    "MetricRegistry",
    "PerformanceTimer",
    "timer",
    "counter",
    "gauge",
    "histogram",
    "get_registry",
    # System metrics
    "SystemMetricsCollector",
    "BusinessMetricsCollector",
    "SystemMetrics",
    "get_system_collector",
    "get_business_collector",
    # Prometheus
    "PrometheusExporter",
    "start_prometheus_exporter",
    "stop_prometheus_exporter",
    "get_prometheus_exporter",
    # ELK Stack
    "ELKIntegration",
    "ElasticsearchClient",
    "LogstashHandler",
    "setup_elk_integration",
    "get_elk_integration",
    "shutdown_elk_integration",
    # Grafana
    "GrafanaDashboardBuilder",
    "create_trading_dashboard",
    "create_system_dashboard",
    "create_exchange_dashboard",
    "save_dashboard_to_file",
    "get_all_dashboard_configs",
    "get_logger",
]
