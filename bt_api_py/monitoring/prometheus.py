"""
Prometheus metrics export for bt_api_py.

Provides Prometheus-compatible HTTP endpoint for metrics exposure.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread

from bt_api_base.logging_factory import get_logger
from bt_api_py.monitoring.metrics import MetricRegistry, get_registry

_logger = get_logger("prometheus")


class PrometheusFormatter:
    """Formats metrics in Prometheus exposition format."""

    @staticmethod
    def format_metric_name(name: str) -> str:
        """Format metric name for Prometheus."""
        # Replace invalid characters with underscores
        import re

        return re.sub(r"[^a-zA-Z0-9_:]", "_", name)

    @staticmethod
    def format_labels(labels: dict[str, str]) -> str:
        """Format labels for Prometheus."""
        if not labels:
            return ""

        label_pairs = [f'{k}="{v}"' for k, v in labels.items()]
        return "{" + ",".join(label_pairs) + "}"

    @classmethod
    def format_help(cls, name: str, description: str) -> str:
        """Format HELP line for Prometheus."""
        return f"# HELP {cls.format_metric_name(name)} {description}"

    @classmethod
    def format_type(cls, name: str, metric_type: str) -> str:
        """Format TYPE line for Prometheus."""
        return f"# TYPE {cls.format_metric_name(name)} {metric_type}"

    @classmethod
    def format_registry(cls, registry: MetricRegistry) -> str:
        """Format entire registry for Prometheus."""
        lines = []

        # Get all metrics from registry
        metrics_data = registry.collect_all()

        # Group metrics by base name
        grouped_metrics = {}
        for full_name, value in metrics_data.items():
            # Extract base name and labels
            if "_bucket{" in full_name or "_count" in full_name or "_sum" in full_name:
                # Histogram metric
                if "_bucket{" in full_name:
                    base_name = full_name.split("_bucket{")[0]
                    metric_type = "histogram"
                    # Extract bucket value
                    bucket_part = full_name.split("{")[1].rstrip("}")
                    labels_str = f'{{le="{bucket_part.split("=")[1]}"}}'
                    lines.append(f"{base_name}_bucket{labels_str} {value}")
                elif "_count" in full_name:
                    base_name = full_name.replace("_count", "")
                    metric_type = "histogram"
                    lines.append(f"{base_name}_count {value}")
                elif "_sum" in full_name:
                    base_name = full_name.replace("_sum", "")
                    metric_type = "histogram"
                    lines.append(f"{base_name}_sum {value}")

                grouped_metrics[base_name] = metric_type
            else:
                # Simple metric (counter or gauge)
                base_name = full_name
                metric_type = "gauge"  # Default to gauge
                lines.append(f"{base_name} {value}")
                grouped_metrics[base_name] = metric_type

        # Add HELP and TYPE lines at the top
        formatted_lines = []
        for base_name, metric_type in grouped_metrics.items():
            formatted_lines.append(f"# HELP {base_name} Auto-generated metric")
            formatted_lines.append(f"# TYPE {base_name} {metric_type}")

        # Add metric values
        formatted_lines.extend(lines)

        return "\n".join(formatted_lines)


class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP request handler for metrics endpoint."""

    def __init__(self, registry: MetricRegistry, *args, **kwargs) -> None:
        self.registry = registry
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path == "/metrics":
            self._handle_metrics()
        elif self.path == "/health":
            self._handle_health()
        elif self.path == "/":
            self._handle_root()
        else:
            self._handle_404()

    def do_POST(self) -> None:
        """Handle POST requests."""
        self.send_response(405)
        self.send_header("Allow", "GET")
        self.end_headers()
        self.wfile.write(b"Method Not Allowed")

    def _handle_metrics(self) -> None:
        """Handle /metrics endpoint."""
        try:
            # Format metrics for Prometheus
            metrics_text = PrometheusFormatter.format_registry(self.registry)

            # Send response
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(metrics_text.encode("utf-8"))
        except Exception as e:
            self._send_error(500, f"Internal Server Error: {str(e)}")

    def _handle_health(self) -> None:
        """Handle /health endpoint."""
        health_data = {
            "status": "healthy",
            "timestamp": time.time(),
            "metrics_count": len(self.registry.collect_all()),
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(health_data).encode("utf-8"))

    def _handle_root(self) -> None:
        """Handle root endpoint."""
        response = {
            "service": "bt_api_py metrics",
            "version": "1.0.0",
            "endpoints": {
                "/metrics": "Prometheus metrics",
                "/health": "Health check",
            },
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response, indent=2).encode("utf-8"))

    def _handle_404(self) -> None:
        """Handle 404 errors."""
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = {"error": "Not Found"}
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def _send_error(self, code: int, message: str) -> None:
        """Send error response."""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        response = {"error": message}
        self.wfile.write(json.dumps(response).encode("utf-8"))

    def log_message(self, format: str, *args) -> None:
        """Override to disable default logging."""
        # Suppress default HTTP server logging


class PrometheusExporter:
    """Prometheus metrics exporter with HTTP server."""

    def __init__(
        self,
        host: str = "0.0.0.0",  # nosec B104 # intentional for prometheus exporter
        port: int = 8080,
        registry: MetricRegistry | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.registry = registry or get_registry()
        self.server: HTTPServer | None = None
        self.server_thread: Thread | None = None
        self.stop_event = Event()

    def start(self) -> None:
        """Start the HTTP server."""
        if self.server is not None:
            raise RuntimeError("Server is already running")

        # Create server
        def handler(*args, **kwargs):
            return MetricsHandler(self.registry, *args, **kwargs)

        self.server = HTTPServer((self.host, self.port), handler)
        self.server.timeout = 1  # Non-blocking accept

        # Start server in background thread
        self.server_thread = Thread(target=self._server_loop, daemon=True)
        self.server_thread.start()

    def stop(self) -> None:
        """Stop the HTTP server."""
        if self.server is None:
            return

        # Signal server to stop
        self.stop_event.set()

        # Shutdown server
        if self.server_thread:
            self.server_thread.join(timeout=5)

        if self.server:
            self.server.server_close()
            self.server = None

        self.stop_event.clear()

    def _server_loop(self) -> None:
        """Server loop with graceful shutdown."""
        while not self.stop_event.is_set():
            try:
                if self.server is not None:
                    self.server.handle_request()
            except (OSError, ValueError):
                # Handle server shutdown gracefully
                break
            except Exception as e:
                _logger.debug(f"Prometheus server loop error, continuing: {e}")
                continue

    def is_running(self) -> bool:
        """Check if server is running."""
        return self.server is not None and self.server_thread is not None

    def get_url(self) -> str:
        """Get the metrics URL."""
        return f"http://{self.host}:{self.port}/metrics"


# Global exporter instance
_global_exporter: PrometheusExporter | None = None


def start_prometheus_exporter(
    host: str = "0.0.0.0",  # nosec B104 # intentional for prometheus exporter
    port: int = 8080,
    async_mode: bool = False,
) -> PrometheusExporter:
    """Start the Prometheus metrics exporter.

    Args:
        host: Host to bind to
        port: Port to bind to
        async_mode: Whether to use async mode (requires aiohttp)

    Returns:
        Prometheus exporter instance
    """
    global _global_exporter

    if async_mode:
        raise NotImplementedError("Async mode not yet implemented")

    _global_exporter = PrometheusExporter(host, port)
    _global_exporter.start()
    return _global_exporter


def stop_prometheus_exporter() -> None:
    """Stop the Prometheus metrics exporter."""
    global _global_exporter
    if _global_exporter:
        _global_exporter.stop()
        _global_exporter = None


def get_prometheus_exporter() -> PrometheusExporter | None:
    """Get the global Prometheus exporter."""
    return _global_exporter
