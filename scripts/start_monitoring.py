#!/usr/bin/env python3
"""
Start the BT API Py monitoring system.

This script initializes and starts the complete monitoring stack including
metrics collection, structured logging, and observability tools.
"""

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from bt_api_py.monitoring.config import (
    setup_monitoring,
    MonitoringConfig,
    PRODUCTION_CONFIG,
    DEVELOPMENT_CONFIG,
    TESTING_CONFIG,
)
from bt_api_py.monitoring import get_logger


async def main() -> None:
    """Main entry point for monitoring system."""
    parser = argparse.ArgumentParser(description="Start BT API Py monitoring system")
    parser.add_argument(
        "--env",
        choices=["production", "development", "testing"],
        default="development",
        help="Environment configuration",
    )
    parser.add_argument("--metrics-port", type=int, default=8080, help="Prometheus metrics port")
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Log level",
    )
    parser.add_argument("--log-file", type=str, help="Log file path (default: logs/bt_api_py.log)")
    parser.add_argument(
        "--prometheus-async", action="store_true", help="Use async Prometheus exporter"
    )
    parser.add_argument("--elk-enabled", action="store_true", help="Enable ELK stack integration")
    parser.add_argument(
        "--elasticsearch-host", type=str, default="localhost", help="Elasticsearch host"
    )
    parser.add_argument("--elasticsearch-port", type=int, default=9200, help="Elasticsearch port")
    parser.add_argument("--logstash-host", type=str, default="localhost", help="Logstash host")
    parser.add_argument("--logstash-port", type=int, default=5000, help="Logstash port")

    args = parser.parse_args()

    # Get base configuration
    if args.env == "production":
        config = PRODUCTION_CONFIG
    elif args.env == "testing":
        config = TESTING_CONFIG
    else:
        config = DEVELOPMENT_CONFIG

    # Override with command line arguments
    config.prometheus_port = args.metrics_port
    config.log_level = args.log_level
    config.prometheus_async = args.prometheus_async
    config.elk_enabled = args.elk_enabled

    if args.log_file:
        config.log_file = args.log_file

    if args.elk_enabled:
        config.elasticsearch_host = args.elasticsearch_host
        config.elasticsearch_port = args.elasticsearch_port
        config.logstash_host = args.logstash_host
        config.logstash_port = args.logstash_port

    logger = get_logger("monitoring_starter")

    try:
        logger.info(f"Starting BT API Py monitoring system in {args.env} mode")
        logger.info(
            f"Configuration: metrics_port={config.prometheus_port}, "
            f"log_level={config.log_level}, elk_enabled={config.elk_enabled}"
        )

        # Setup monitoring system
        await setup_monitoring(config)

        logger.info("Monitoring system started successfully")
        logger.info(
            f"Prometheus metrics available at: http://localhost:{config.prometheus_port}/metrics"
        )

        if config.elk_enabled:
            logger.info(f"ELK stack integration enabled")
            logger.info(f"Elasticsearch: {config.elasticsearch_host}:{config.elasticsearch_port}")
            logger.info(f"Logstash: {config.logstash_host}:{config.logstash_port}")

        # Keep the service running
        while True:
            await asyncio.sleep(60)
            logger.debug("Monitoring system heartbeat")

    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down monitoring system")
    except Exception as e:
        logger.error(f"Failed to start monitoring system: {e}")
        sys.exit(1)
    finally:
        # Cleanup
        from bt_api_py.monitoring.config import cleanup_monitoring

        await cleanup_monitoring()
        logger.info("Monitoring system shutdown complete")


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    print(f"\nReceived signal {signum}, initiating graceful shutdown...")
    sys.exit(0)


if __name__ == "__main__":
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Create logs directory if it doesn't exist
    Path("logs").mkdir(exist_ok=True)

    # Run the main function
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nMonitoring system stopped")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
