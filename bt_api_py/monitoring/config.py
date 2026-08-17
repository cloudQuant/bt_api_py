"""
Performance monitoring configuration and initialization.

Setup complete monitoring system for production trading environment.
"""

from __future__ import annotations

import logging
from pathlib import Path

from bt_api_base.logging_factory import get_logger

from bt_api_py.monitoring.collector import start_global_monitoring
from bt_api_py.monitoring.elk import setup_elk_integration
from bt_api_py.monitoring.grafana import (
    get_all_dashboard_configs,
    save_dashboard_to_file,
)
from bt_api_py.monitoring.prometheus import start_prometheus_exporter

LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.FATAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def _resolve_log_level(level: object) -> int:
    if not isinstance(level, str):
        raise ValueError("log_level must be a valid logging level name")

    normalized_level = level.strip().upper()
    if normalized_level not in LOG_LEVELS:
        valid_levels = ", ".join(sorted(LOG_LEVELS))
        raise ValueError(f"log_level must be one of: {valid_levels}")

    return LOG_LEVELS[normalized_level]


def setup_logging_for_production(log_file: str, level: str = "INFO") -> None:
    """Configure process logging for monitoring setup."""
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(log_path),
        level=_resolve_log_level(level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


class MonitoringConfig:
    """Configuration for monitoring system."""

    # System monitoring
    metrics_collection_interval: float = 5.0

    # Prometheus exporter
    prometheus_host: str = "0.0.0.0"
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
    elk_request_timeout: float = 10.0

    # Grafana dashboards
    dashboards_output_dir: str = "monitoring/grafana/dashboards"

    # Exchange health monitoring
    health_check_interval: float = 30.0
    health_check_timeout: float = 5.0

    def __init__(self, **kwargs: object) -> None:
        """Initialize config from kwargs with defaults from class attributes."""
        defaults: dict[str, object] = {
            "metrics_collection_interval": 5.0,
            "prometheus_host": "0.0.0.0",
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
            "elk_request_timeout": 10.0,
            "dashboards_output_dir": "monitoring/grafana/dashboards",
            "health_check_interval": 30.0,
            "health_check_timeout": 5.0,
        }
        unknown_keys = sorted(set(kwargs) - set(defaults))
        if unknown_keys:
            raise ValueError(f"Unknown monitoring config option(s): {', '.join(unknown_keys)}")

        for key, default in defaults.items():
            setattr(self, key, kwargs.get(key, default))

        self._validate()

    def _validate(self) -> None:
        """Validate monitoring config values that should fail before setup starts."""
        for key in ("prometheus_port", "elasticsearch_port", "logstash_port"):
            self._validate_port(key)

        for key in (
            "metrics_collection_interval",
            "elk_request_timeout",
            "health_check_interval",
            "health_check_timeout",
        ):
            self._validate_positive_number(key)

        self._validate_positive_int("log_max_size")
        self._validate_non_negative_int("log_backup_count")
        _resolve_log_level(self.log_level)

    def _validate_port(self, key: str) -> None:
        value = getattr(self, key)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
            raise ValueError(f"{key} must be an integer between 1 and 65535")

    def _validate_positive_number(self, key: str) -> None:
        value = getattr(self, key)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{key} must be a positive number")

    def _validate_positive_int(self, key: str) -> None:
        value = getattr(self, key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{key} must be a positive integer")

    def _validate_non_negative_int(self, key: str) -> None:
        value = getattr(self, key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")


async def setup_monitoring(config: MonitoringConfig) -> None:
    """Setup complete monitoring system."""
    logger = get_logger(__name__)
    resources_started = False

    try:
        # Setup logging first
        setup_logging_for_production(
            log_file=config.log_file,
            level=config.log_level,
        )
        logger.info("Logging system configured")

        # Start metrics collection
        await start_global_monitoring(config.metrics_collection_interval)
        resources_started = True
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
                request_timeout=config.elk_request_timeout,
            )
            logger.info("ELK stack integration configured")

        # Generate Grafana dashboards
        await setup_grafana_dashboards(config.dashboards_output_dir)
        logger.info("Grafana dashboards generated")

        logger.info("Monitoring system setup complete")

    except Exception as e:
        logger.error(f"Failed to setup monitoring: {e}")
        if resources_started:
            await cleanup_monitoring()
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
    from bt_api_py.monitoring.collector import stop_global_monitoring
    from bt_api_py.monitoring.elk import shutdown_elk_integration
    from bt_api_py.monitoring.prometheus import stop_prometheus_exporter

    logger = get_logger("monitoring")

    try:
        await stop_global_monitoring()
    except Exception as e:
        logger.debug(f"Cleanup global monitoring failed: {e}")

    try:
        stop_prometheus_exporter()
    except Exception as e:
        logger.debug(f"Cleanup Prometheus exporter failed: {e}")

    try:
        await shutdown_elk_integration()
    except Exception as e:
        logger.debug(f"Cleanup ELK integration failed: {e}")


# Production configuration
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
