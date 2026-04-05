"""
Performance monitoring configuration and initialization.

Setup complete monitoring system for production trading environment.
"""

from __future__ import annotations

from pathlib import Path

from bt_api_py.logging_system import get_logger, setup_logging_for_production
from bt_api_py.monitoring import (
    get_all_dashboard_configs,
    save_dashboard_to_file,
    setup_elk_integration,
    start_global_monitoring,
    start_prometheus_exporter,
)


class MonitoringConfig:
    """Configuration for monitoring system."""

    # System monitoring
    metrics_collection_interval: float = 5.0

    # Prometheus exporter
    prometheus_host: str = "0.0.0.0"  # nosec B104 # intentional for prometheus exporter
    prometheus_port: int = 8080
    prometheus_async: bool = False

    # Logging
    log_level: str = "INFO"
    log_file: str = "logs/bt_api_py.log"
    log_rotation: bool = True
    log_max_size: int = 100 * 1024 * 1024  # 100MB
    log_backup_count: int = 5

    # ELK Stack
    elk_enabled: bool = False
    elasticsearch_host: str = "localhost"
    elasticsearch_port: int = 9200
    elasticsearch_username: str = ""
    elasticsearch_password: str = ""
    elasticsearch_index_prefix: str = "bt_api_py"
    logstash_host: str = "localhost"
    logstash_port: int = 5000
    logstash_transport: str = "tcp"

    # Grafana dashboards
    dashboards_output_dir: str = "monitoring/grafana/dashboards"

    # Exchange health monitoring
    health_check_interval: float = 30.0
    health_check_timeout: float = 5.0

    def __init__(self, **kwargs: object) -> None:
        """Initialize config from kwargs with defaults from class attributes."""
        for key, default in {
            "metrics_collection_interval": 5.0,
            "prometheus_host": "0.0.0.0",  # nosec B104
            "prometheus_port": 8080,
            "prometheus_async": False,
            "log_level": "INFO",
            "log_file": "logs/bt_api_py.log",
            "log_rotation": True,
            "log_max_size": 100 * 1024 * 1024,
            "log_backup_count": 5,
            "elk_enabled": False,
            "elasticsearch_host": "localhost",
            "elasticsearch_port": 9200,
            "elasticsearch_username": "",
            "elasticsearch_password": "",
            "elasticsearch_index_prefix": "bt_api_py",
            "logstash_host": "localhost",
            "logstash_port": 5000,
            "logstash_transport": "tcp",
            "dashboards_output_dir": "monitoring/grafana/dashboards",
            "health_check_interval": 30.0,
            "health_check_timeout": 5.0,
        }.items():
            setattr(self, key, kwargs.get(key, default))


async def setup_monitoring(config: MonitoringConfig) -> None:
    """Setup complete monitoring system."""
    logger = get_logger(__name__)

    try:
        # Setup logging first
        setup_logging_for_production(
            log_file=config.log_file,
            level=config.log_level,
        )
        logger.info("Logging system configured")

        # Start metrics collection
        await start_global_monitoring(config.metrics_collection_interval)
        logger.info("Metrics collection started")

        # Start Prometheus exporter
        start_prometheus_exporter(
            host=config.prometheus_host,
            port=config.prometheus_port,
            async_mode=config.prometheus_async,
        )
        logger.info(
            f"Prometheus exporter started on {config.prometheus_host}:{config.prometheus_port}"
        )

        # Setup ELK integration if enabled
        if config.elk_enabled:
            await setup_elk_integration(
                elasticsearch_host=config.elasticsearch_host,
                elasticsearch_port=config.elasticsearch_port,
                elasticsearch_username=config.elasticsearch_username or None,
                elasticsearch_password=config.elasticsearch_password or None,
                elasticsearch_index_prefix=config.elasticsearch_index_prefix,
                logstash_host=config.logstash_host,
                logstash_port=config.logstash_port,
                logstash_transport=config.logstash_transport,
            )
            logger.info("ELK stack integration configured")

        # Generate Grafana dashboards
        await setup_grafana_dashboards(config.dashboards_output_dir)
        logger.info("Grafana dashboards generated")

        logger.info("Monitoring system setup complete")

    except Exception as e:
        logger.error(f"Failed to setup monitoring: {e}")
        raise


async def setup_grafana_dashboards(output_dir: str) -> None:
    """Generate and save Grafana dashboards."""
    # Get all dashboard configurations
    dashboards = get_all_dashboard_configs()

    # Save to files
    output_path = Path(output_dir)

    for name, dashboard in dashboards.items():
        filename = output_path / f"{name}_dashboard.json"
        save_dashboard_to_file(dashboard, str(filename))


async def cleanup_monitoring() -> None:
    """Cleanup monitoring resources."""
    from bt_api_py.monitoring import (
        shutdown_elk_integration,
        stop_global_monitoring,
        stop_prometheus_exporter,
    )

    try:
        await stop_global_monitoring()
        stop_prometheus_exporter()
        await shutdown_elk_integration()
    except Exception as e:
        get_logger("monitoring").debug("Cleanup monitoring resources failed: %s", e, exc_info=True)


# Production configuration
PRODUCTION_CONFIG = MonitoringConfig(
    metrics_collection_interval=5.0,
    prometheus_host="0.0.0.0",  # nosec B104
    prometheus_port=8080,
    log_level="INFO",
    log_file="logs/bt_api_py.log",
    elk_enabled=True,
    elasticsearch_host="elasticsearch.monitoring.svc.cluster.local",
    elasticsearch_port=9200,
    logstash_host="logstash.monitoring.svc.cluster.local",
    logstash_port=5000,
)

# Development configuration
DEVELOPMENT_CONFIG = MonitoringConfig(
    metrics_collection_interval=10.0,
    prometheus_host="127.0.0.1",
    prometheus_port=9090,
    log_level="DEBUG",
    log_file="logs/bt_api_py_dev.log",
    elk_enabled=False,
)

# Testing configuration
TESTING_CONFIG = MonitoringConfig(
    metrics_collection_interval=1.0,
    prometheus_host="127.0.0.1",
    prometheus_port=9091,
    log_level="DEBUG",
    log_file="/tmp/bt_api_py_test.log",  # nosec B108
    elk_enabled=False,
)
