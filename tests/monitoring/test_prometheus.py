"""Tests for monitoring/prometheus.py."""

from __future__ import annotations

import io
import json

import pytest

pytest.importorskip("psutil")

from bt_api_py.monitoring import prometheus
from bt_api_py.monitoring.metrics import Gauge, Histogram, MetricRegistry


class TestPrometheus:
    """Tests for Prometheus integration."""

    def test_module_exists(self):
        """Test module can be imported."""
        from bt_api_py.monitoring import prometheus

        assert prometheus is not None


class TestPrometheusFormatter:
    def test_formatter_helpers(self):
        assert (
            prometheus.PrometheusFormatter.format_metric_name("btapi.metric/name")
            == "btapi_metric_name"
        )
        assert prometheus.PrometheusFormatter.format_labels({}) == ""
        assert (
            prometheus.PrometheusFormatter.format_labels({"exchange": "binance"})
            == '{exchange="binance"}'
        )
        assert (
            prometheus.PrometheusFormatter.format_help("metric.name", "desc")
            == "# HELP metric_name desc"
        )
        assert (
            prometheus.PrometheusFormatter.format_type("metric.name", "gauge")
            == "# TYPE metric_name gauge"
        )

    def test_format_registry_includes_metric_values(self):
        registry = MetricRegistry()
        gauge = registry.register(Gauge("active_connections"))
        histogram = registry.register(Histogram("api_latency_seconds", buckets=[0.5, 1.0]))
        gauge.set(3)
        histogram.observe(0.75)

        formatted = prometheus.PrometheusFormatter.format_registry(registry)

        assert "# HELP active_connections Auto-generated metric" in formatted
        assert "# TYPE active_connections gauge" in formatted
        assert "active_connections 3" in formatted
        assert "# TYPE api_latency_seconds histogram" in formatted
        assert "api_latency_seconds_count 1.0" in formatted
        assert "api_latency_seconds_sum 0.75" in formatted
        assert "api_latency_seconds_bucket" in formatted


class TestMetricsHandler:
    @staticmethod
    def _make_handler(registry=None):
        handler = object.__new__(prometheus.MetricsHandler)
        handler.registry = registry or MetricRegistry()
        handler.wfile = io.BytesIO()
        handler.sent = []
        handler.send_response = lambda code: handler.sent.append(("code", code))
        handler.send_header = lambda name, value: handler.sent.append((name, value))
        handler.end_headers = lambda: handler.sent.append(("end", None))
        return handler

    def test_do_get_routes_expected_paths(self):
        handler = self._make_handler()
        calls = []
        handler._handle_metrics = lambda: calls.append("metrics")
        handler._handle_health = lambda: calls.append("health")
        handler._handle_root = lambda: calls.append("root")
        handler._handle_404 = lambda: calls.append("404")

        for path in ["/metrics", "/health", "/", "/missing"]:
            handler.path = path
            handler.do_GET()

        assert calls == ["metrics", "health", "root", "404"]

    def test_do_post_returns_405(self):
        handler = self._make_handler()
        handler.do_POST()

        assert handler.sent[0] == ("code", 405)
        assert ("Allow", "GET") in handler.sent
        assert handler.wfile.getvalue() == b"Method Not Allowed"

    def test_handle_metrics_success_and_failure(self, monkeypatch):
        handler = self._make_handler()
        monkeypatch.setattr(
            prometheus.PrometheusFormatter,
            "format_registry",
            lambda registry: "metric_name 1",
        )

        handler._handle_metrics()
        assert handler.sent[0] == ("code", 200)
        assert handler.wfile.getvalue() == b"metric_name 1"

        error_handler = self._make_handler()
        monkeypatch.setattr(
            prometheus.PrometheusFormatter,
            "format_registry",
            lambda registry: (_ for _ in ()).throw(ValueError("boom")),
        )

        error_handler._handle_metrics()
        payload = json.loads(error_handler.wfile.getvalue().decode("utf-8"))
        assert error_handler.sent[0] == ("code", 500)
        assert "boom" in payload["error"]

    def test_handle_health_root_404_and_send_error(self):
        registry = MetricRegistry()
        registry.register(Gauge("connections")).set(2)

        health_handler = self._make_handler(registry)
        health_handler._handle_health()
        health_payload = json.loads(health_handler.wfile.getvalue().decode("utf-8"))
        assert health_handler.sent[0] == ("code", 200)
        assert health_payload["status"] == "healthy"
        assert health_payload["metrics_count"] == 1

        root_handler = self._make_handler(registry)
        root_handler._handle_root()
        root_payload = json.loads(root_handler.wfile.getvalue().decode("utf-8"))
        assert root_payload["service"] == "bt_api_py metrics"
        assert "/metrics" in root_payload["endpoints"]

        missing_handler = self._make_handler(registry)
        missing_handler._handle_404()
        assert json.loads(missing_handler.wfile.getvalue().decode("utf-8")) == {
            "error": "Not Found"
        }

        error_handler = self._make_handler(registry)
        error_handler._send_error(418, "teapot")
        assert error_handler.sent[0] == ("code", 418)
        assert json.loads(error_handler.wfile.getvalue().decode("utf-8")) == {"error": "teapot"}


class TestPrometheusExporter:
    def test_exporter_start_stop_and_url(self, monkeypatch):
        created = {}

        class DummyServer:
            def __init__(self, address, handler):
                created["address"] = address
                created["handler"] = handler
                self.timeout = None
                self.closed = False

            def handle_request(self):
                return None

            def server_close(self):
                self.closed = True

        class DummyThread:
            def __init__(self, target, daemon):
                self.target = target
                self.daemon = daemon
                self.started = False
                self.join_timeout = None

            def start(self):
                self.started = True

            def join(self, timeout=None):
                self.join_timeout = timeout

        monkeypatch.setattr(prometheus, "HTTPServer", DummyServer)
        monkeypatch.setattr(prometheus, "Thread", DummyThread)

        exporter = prometheus.PrometheusExporter(
            host="127.0.0.1", port=9200, registry=MetricRegistry()
        )
        exporter.start()

        assert created["address"] == ("127.0.0.1", 9200)
        assert exporter.server.timeout == 1
        assert exporter.server_thread.started is True
        assert exporter.is_running() is True
        assert exporter.get_url() == "http://127.0.0.1:9200/metrics"

        with pytest.raises(RuntimeError):
            exporter.start()

        exporter.stop()
        assert exporter.server is None
        assert exporter.stop_event.is_set() is False

    def test_server_loop_handles_exceptions_and_global_helpers(self, monkeypatch):
        class LoopServer:
            def __init__(self):
                self.calls = 0

            def handle_request(self):
                self.calls += 1
                if self.calls == 1:
                    raise Exception("temporary")
                raise OSError("stop")

        exporter = prometheus.PrometheusExporter(registry=MetricRegistry())
        exporter.server = LoopServer()
        exporter._server_loop()
        assert exporter.server.calls == 2

        started = []
        stopped = []

        monkeypatch.setattr(
            prometheus.PrometheusExporter,
            "start",
            lambda self: started.append((self.host, self.port)),
        )
        monkeypatch.setattr(
            prometheus.PrometheusExporter, "stop", lambda self: stopped.append(True)
        )

        global_exporter = prometheus.start_prometheus_exporter(host="127.0.0.1", port=9300)
        assert started == [("127.0.0.1", 9300)]
        assert prometheus.get_prometheus_exporter() is global_exporter

        prometheus.stop_prometheus_exporter()
        assert stopped == [True]
        assert prometheus.get_prometheus_exporter() is None

        with pytest.raises(NotImplementedError):
            prometheus.start_prometheus_exporter(async_mode=True)
