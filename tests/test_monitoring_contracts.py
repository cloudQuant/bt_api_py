from __future__ import annotations

import asyncio
import logging
import sys
import warnings
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import bt_api_py.monitoring.config as monitoring_config
import bt_api_py.monitoring.elk as elk_module
from bt_api_py.monitoring.elk import (
    ElasticsearchClient,
    ELKIntegration,
    LogstashHandler,
    correlation_id_var,
    request_id_var,
)
from bt_api_py.monitoring.metrics import Gauge, Histogram, MetricRegistry
from bt_api_py.monitoring.prometheus import (
    PrometheusFormatter,
    get_prometheus_exporter,
    start_prometheus_exporter,
)


class FakeClientTimeout:
    def __init__(self, total: float) -> None:
        self.total = total


class FakeClientSession:
    created: list[FakeClientSession] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.closed = False
        self.created.append(self)

    async def close(self) -> None:
        self.closed = True


class FakeTCPConnector:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs


class FakeBasicAuth:
    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password


class FakeHTTPResponse:
    def __init__(
        self,
        status: int,
        *,
        text_data: str = "",
        json_data: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.text_data = text_data
        self.json_data = json_data or {}

    async def __aenter__(self) -> FakeHTTPResponse:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def text(self) -> str:
        return self.text_data

    async def json(self) -> dict[str, Any]:
        return self.json_data


class FakeHTTPSession:
    def __init__(self, response: FakeHTTPResponse) -> None:
        self.response = response
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> FakeHTTPResponse:
        self.posts.append((url, kwargs))
        return self.response


def install_fake_aiohttp(monkeypatch: pytest.MonkeyPatch) -> type[FakeClientSession]:
    FakeClientSession.created.clear()
    fake_aiohttp = SimpleNamespace(
        BasicAuth=FakeBasicAuth,
        ClientSession=FakeClientSession,
        ClientTimeout=FakeClientTimeout,
        TCPConnector=FakeTCPConnector,
    )
    monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)
    return FakeClientSession


def test_monitoring_config_rejects_unknown_options() -> None:
    with pytest.raises(
        ValueError,
        match="Unknown monitoring config option\\(s\\): unknown_option",
    ):
        monitoring_config.MonitoringConfig(unknown_option=True)


def test_monitoring_config_rejects_invalid_port() -> None:
    with pytest.raises(
        ValueError,
        match="prometheus_port must be an integer between 1 and 65535",
    ):
        monitoring_config.MonitoringConfig(prometheus_port=70000)


def test_monitoring_config_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="elk_request_timeout must be a positive number"):
        monitoring_config.MonitoringConfig(elk_request_timeout=0)


def test_monitoring_config_rejects_invalid_backup_count() -> None:
    with pytest.raises(ValueError, match="log_backup_count must be a non-negative integer"):
        monitoring_config.MonitoringConfig(log_backup_count=-1)


def test_monitoring_config_rejects_invalid_log_level() -> None:
    with pytest.raises(ValueError, match="log_level must be one of:"):
        monitoring_config.MonitoringConfig(log_level="verbose")


def test_setup_logging_for_production_creates_parent_and_configures_logging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_kwargs: dict[str, Any] = {}
    log_file = tmp_path / "nested" / "bt_api.log"

    def fake_basic_config(**kwargs: Any) -> None:
        captured_kwargs.update(kwargs)

    monkeypatch.setattr(monitoring_config.logging, "basicConfig", fake_basic_config)

    monitoring_config.setup_logging_for_production(str(log_file), level="debug")

    assert log_file.parent.is_dir()
    assert captured_kwargs == {
        "filename": str(log_file),
        "level": logging.DEBUG,
        "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
    }


def test_setup_logging_for_production_rejects_invalid_level(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="log_level must be one of:"):
        monitoring_config.setup_logging_for_production(
            str(tmp_path / "bt_api.log"), level="verbose"
        )


@pytest.mark.asyncio
async def test_setup_monitoring_cleans_up_started_resources_on_later_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_setup_logging_for_production(*args: Any, **kwargs: Any) -> None:
        calls.append("logging")

    async def fake_start_global_monitoring(interval: float) -> None:
        calls.append(f"metrics:{interval}")

    def fake_start_prometheus_exporter(*args: Any, **kwargs: Any) -> None:
        calls.append("prometheus")

    async def fake_setup_elk_integration(*args: Any, **kwargs: Any) -> None:
        calls.append("elk")

    async def fake_setup_grafana_dashboards(output_dir: str) -> None:
        calls.append(f"grafana:{output_dir}")
        raise RuntimeError("grafana failed")

    async def fake_cleanup_monitoring() -> None:
        calls.append("cleanup")

    monkeypatch.setattr(
        monitoring_config,
        "setup_logging_for_production",
        fake_setup_logging_for_production,
    )
    monkeypatch.setattr(monitoring_config, "start_global_monitoring", fake_start_global_monitoring)
    monkeypatch.setattr(
        monitoring_config,
        "start_prometheus_exporter",
        fake_start_prometheus_exporter,
    )
    monkeypatch.setattr(monitoring_config, "setup_elk_integration", fake_setup_elk_integration)
    monkeypatch.setattr(
        monitoring_config,
        "setup_grafana_dashboards",
        fake_setup_grafana_dashboards,
    )
    monkeypatch.setattr(monitoring_config, "cleanup_monitoring", fake_cleanup_monitoring)

    config = monitoring_config.MonitoringConfig(
        metrics_collection_interval=2.0,
        elk_enabled=True,
        dashboards_output_dir="dashboards",
    )

    with pytest.raises(RuntimeError, match="grafana failed"):
        await monitoring_config.setup_monitoring(config)

    assert calls == [
        "logging",
        "metrics:2.0",
        "prometheus",
        "elk",
        "grafana:dashboards",
        "cleanup",
    ]


@pytest.mark.asyncio
async def test_setup_monitoring_passes_elk_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_elk_kwargs: dict[str, Any] = {}

    def fake_setup_logging_for_production(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_start_global_monitoring(interval: float) -> None:
        return None

    def fake_start_prometheus_exporter(*args: Any, **kwargs: Any) -> None:
        return None

    async def fake_setup_elk_integration(**kwargs: Any) -> None:
        captured_elk_kwargs.update(kwargs)

    async def fake_setup_grafana_dashboards(output_dir: str) -> None:
        return None

    monkeypatch.setattr(
        monitoring_config,
        "setup_logging_for_production",
        fake_setup_logging_for_production,
    )
    monkeypatch.setattr(monitoring_config, "start_global_monitoring", fake_start_global_monitoring)
    monkeypatch.setattr(
        monitoring_config,
        "start_prometheus_exporter",
        fake_start_prometheus_exporter,
    )
    monkeypatch.setattr(monitoring_config, "setup_elk_integration", fake_setup_elk_integration)
    monkeypatch.setattr(
        monitoring_config,
        "setup_grafana_dashboards",
        fake_setup_grafana_dashboards,
    )

    config = monitoring_config.MonitoringConfig(
        elk_enabled=True,
        elasticsearch_host="es.local",
        elasticsearch_port=9243,
        elasticsearch_username="elastic",
        elasticsearch_password="secret",
        elasticsearch_index_prefix="custom_logs",
        logstash_host="logstash.local",
        logstash_port=5044,
        logstash_transport="tcp",
        elk_request_timeout=2.5,
    )

    await monitoring_config.setup_monitoring(config)

    assert captured_elk_kwargs == {
        "elasticsearch_host": "es.local",
        "elasticsearch_port": 9243,
        "elasticsearch_username": "elastic",
        "elasticsearch_password": "secret",
        "elasticsearch_index_prefix": "custom_logs",
        "logstash_host": "logstash.local",
        "logstash_port": 5044,
        "logstash_transport": "tcp",
        "request_timeout": 2.5,
    }


@pytest.mark.asyncio
async def test_cleanup_monitoring_runs_all_steps_when_some_steps_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_stop_global_monitoring() -> None:
        calls.append("stop_global")
        raise RuntimeError("stop global failed")

    def fake_stop_prometheus_exporter() -> None:
        calls.append("stop_prometheus")
        raise RuntimeError("stop prometheus failed")

    async def fake_shutdown_elk_integration() -> None:
        calls.append("shutdown_elk")

    monkeypatch.setitem(
        sys.modules,
        "bt_api_py.monitoring.collector",
        SimpleNamespace(stop_global_monitoring=fake_stop_global_monitoring),
    )
    monkeypatch.setitem(
        sys.modules,
        "bt_api_py.monitoring.prometheus",
        SimpleNamespace(stop_prometheus_exporter=fake_stop_prometheus_exporter),
    )
    monkeypatch.setitem(
        sys.modules,
        "bt_api_py.monitoring.elk",
        SimpleNamespace(shutdown_elk_integration=fake_shutdown_elk_integration),
    )

    await monitoring_config.cleanup_monitoring()

    assert calls == ["stop_global", "stop_prometheus", "shutdown_elk"]


def test_start_prometheus_exporter_rejects_unsupported_async_mode() -> None:
    with pytest.raises(ValueError, match="async_mode=True is not supported"):
        start_prometheus_exporter(host="127.0.0.1", port=0, async_mode=True)

    assert get_prometheus_exporter() is None


@pytest.mark.asyncio
async def test_logstash_handler_rejects_unsupported_udp_transport() -> None:
    handler = LogstashHandler(transport="udp")

    with pytest.raises(ValueError, match="Unsupported transport: udp"):
        await handler.connect()


@pytest.mark.asyncio
async def test_elasticsearch_client_uses_configured_aiohttp_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = install_fake_aiohttp(monkeypatch)

    async def fake_test_connection(self: ElasticsearchClient) -> None:
        return None

    monkeypatch.setattr(ElasticsearchClient, "_test_connection", fake_test_connection)
    client = ElasticsearchClient(request_timeout=7.5)

    await client.connect()
    try:
        session = fake_session.created[-1]

        assert session.kwargs["base_url"] == "http://localhost:9200"
        assert session.kwargs["timeout"].total == 7.5
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_elasticsearch_index_document_reports_failed_response() -> None:
    response = FakeHTTPResponse(503, text_data="cluster unavailable")
    session = FakeHTTPSession(response)
    client = ElasticsearchClient()
    client._session = session

    with pytest.raises(
        RuntimeError,
        match="Failed to index document: 503 - cluster unavailable",
    ):
        await client.index_document("bt_api_py-test", {"message": "hello"}, doc_id="doc-1")

    assert session.posts == [
        (
            "/bt_api_py-test/_doc/doc-1",
            {"json": {"message": "hello"}},
        )
    ]


@pytest.mark.asyncio
async def test_elk_search_logs_reports_failed_response() -> None:
    response = FakeHTTPResponse(400, text_data="bad query")
    session = FakeHTTPSession(response)
    integration = ELKIntegration(elasticsearch_index_prefix="bt_api_py")
    integration._connected = True
    integration.elasticsearch_client._session = session

    with pytest.raises(RuntimeError, match="Search failed: 400 - bad query"):
        await integration.search_logs(query="timeout", level="ERROR", size=25)

    assert session.posts == [
        (
            "/bt_api_py-*/_search",
            {
                "json": {
                    "query": {
                        "bool": {
                            "must": [
                                {"term": {"level": "ERROR"}},
                                {
                                    "multi_match": {
                                        "query": "timeout",
                                        "fields": ["message", "error.message", "metadata.*"],
                                    }
                                },
                            ]
                        }
                    },
                    "sort": [{"timestamp": {"order": "desc"}}],
                    "size": 25,
                }
            },
        )
    ]


@pytest.mark.asyncio
async def test_elk_search_logs_uses_match_all_without_filters() -> None:
    search_result = {"hits": {"total": {"value": 0}, "hits": []}}
    response = FakeHTTPResponse(200, json_data=search_result)
    session = FakeHTTPSession(response)
    integration = ELKIntegration(elasticsearch_index_prefix="bt_api_py")
    integration._connected = True
    integration.elasticsearch_client._session = session

    result = await integration.search_logs()

    assert result == search_result
    assert session.posts == [
        (
            "/bt_api_py-*/_search",
            {
                "json": {
                    "query": {"match_all": {}},
                    "sort": [{"timestamp": {"order": "desc"}}],
                    "size": 100,
                }
            },
        )
    ]


@pytest.mark.asyncio
async def test_elk_search_logs_builds_filtered_query() -> None:
    search_result = {"hits": {"total": {"value": 1}, "hits": [{"_id": "log-1"}]}}
    response = FakeHTTPResponse(200, json_data=search_result)
    session = FakeHTTPSession(response)
    integration = ELKIntegration(elasticsearch_index_prefix="bt_api_py")
    integration._connected = True
    integration.elasticsearch_client._session = session
    start_time = datetime(2026, 6, 17, 9, 30, tzinfo=UTC)
    end_time = datetime(2026, 6, 17, 10, 0, tzinfo=UTC)

    result = await integration.search_logs(
        level="INFO",
        exchange_name="binance",
        component="order-router",
        start_time=start_time,
        end_time=end_time,
        size=10,
    )

    assert result == search_result
    assert session.posts == [
        (
            "/bt_api_py-*/_search",
            {
                "json": {
                    "query": {
                        "bool": {
                            "must": [
                                {"term": {"level": "INFO"}},
                                {"term": {"exchange_name": "binance"}},
                                {"term": {"component": "order-router"}},
                                {
                                    "range": {
                                        "timestamp": {
                                            "gte": "2026-06-17T09:30:00+00:00",
                                            "lte": "2026-06-17T10:00:00+00:00",
                                        }
                                    }
                                },
                            ]
                        }
                    },
                    "sort": [{"timestamp": {"order": "desc"}}],
                    "size": 10,
                }
            },
        )
    ]


@pytest.mark.asyncio
async def test_elk_connect_cleans_up_when_index_template_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration = ELKIntegration()
    calls: list[str] = []

    async def fake_elasticsearch_connect() -> None:
        calls.append("elasticsearch_connect")

    async def fake_create_index_template() -> None:
        calls.append("create_index_template")
        raise RuntimeError("template failed")

    async def fake_elasticsearch_disconnect() -> None:
        calls.append("elasticsearch_disconnect")

    async def fake_logstash_disconnect() -> None:
        calls.append("logstash_disconnect")

    monkeypatch.setattr(integration.elasticsearch_client, "connect", fake_elasticsearch_connect)
    monkeypatch.setattr(
        integration.elasticsearch_client,
        "create_index_template",
        fake_create_index_template,
    )
    monkeypatch.setattr(
        integration.elasticsearch_client,
        "disconnect",
        fake_elasticsearch_disconnect,
    )
    monkeypatch.setattr(integration.logstash_handler, "disconnect", fake_logstash_disconnect)

    with pytest.raises(RuntimeError, match="template failed"):
        await integration.connect()

    assert integration._connected is False
    assert calls == [
        "elasticsearch_connect",
        "create_index_template",
        "elasticsearch_disconnect",
        "logstash_disconnect",
    ]


@pytest.mark.asyncio
async def test_elk_connect_cleans_up_when_logstash_connect_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration = ELKIntegration()
    calls: list[str] = []

    async def fake_elasticsearch_connect() -> None:
        calls.append("elasticsearch_connect")

    async def fake_create_index_template() -> None:
        calls.append("create_index_template")

    async def fake_logstash_connect() -> None:
        calls.append("logstash_connect")
        raise RuntimeError("logstash failed")

    async def fake_elasticsearch_disconnect() -> None:
        calls.append("elasticsearch_disconnect")

    async def fake_logstash_disconnect() -> None:
        calls.append("logstash_disconnect")

    monkeypatch.setattr(integration.elasticsearch_client, "connect", fake_elasticsearch_connect)
    monkeypatch.setattr(
        integration.elasticsearch_client,
        "create_index_template",
        fake_create_index_template,
    )
    monkeypatch.setattr(integration.logstash_handler, "connect", fake_logstash_connect)
    monkeypatch.setattr(
        integration.elasticsearch_client,
        "disconnect",
        fake_elasticsearch_disconnect,
    )
    monkeypatch.setattr(integration.logstash_handler, "disconnect", fake_logstash_disconnect)

    with pytest.raises(RuntimeError, match="logstash failed"):
        await integration.connect()

    assert integration._connected is False
    assert calls == [
        "elasticsearch_connect",
        "create_index_template",
        "logstash_connect",
        "elasticsearch_disconnect",
        "logstash_disconnect",
    ]


@pytest.mark.asyncio
async def test_elk_connect_is_idempotent_when_already_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration = ELKIntegration()
    root_logger = logging.getLogger()
    calls: list[str] = []

    async def fake_elasticsearch_connect() -> None:
        calls.append("elasticsearch_connect")

    async def fake_create_index_template() -> None:
        calls.append("create_index_template")

    async def fake_logstash_connect() -> None:
        calls.append("logstash_connect")

    async def fake_elasticsearch_disconnect() -> None:
        calls.append("elasticsearch_disconnect")

    async def fake_logstash_disconnect() -> None:
        calls.append("logstash_disconnect")

    monkeypatch.setattr(integration.elasticsearch_client, "connect", fake_elasticsearch_connect)
    monkeypatch.setattr(
        integration.elasticsearch_client,
        "create_index_template",
        fake_create_index_template,
    )
    monkeypatch.setattr(integration.logstash_handler, "connect", fake_logstash_connect)
    monkeypatch.setattr(
        integration.elasticsearch_client,
        "disconnect",
        fake_elasticsearch_disconnect,
    )
    monkeypatch.setattr(integration.logstash_handler, "disconnect", fake_logstash_disconnect)

    try:
        await integration.connect()
        await integration.connect()

        assert calls == [
            "elasticsearch_connect",
            "create_index_template",
            "logstash_connect",
        ]
        assert root_logger.handlers.count(integration.logstash_handler) == 1
    finally:
        await integration.disconnect()

    assert calls == [
        "elasticsearch_connect",
        "create_index_template",
        "logstash_connect",
        "elasticsearch_disconnect",
        "logstash_disconnect",
    ]


@pytest.mark.asyncio
async def test_setup_elk_integration_clears_global_after_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, Any]] = []

    class FailingELKIntegration:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)

        async def connect(self) -> None:
            raise RuntimeError("connect failed")

    monkeypatch.setattr(elk_module, "_elk_integration", None)
    monkeypatch.setattr(elk_module, "ELKIntegration", FailingELKIntegration)

    with pytest.raises(RuntimeError, match="connect failed"):
        await elk_module.setup_elk_integration(elasticsearch_host="es.local")

    assert created == [{"elasticsearch_host": "es.local"}]
    assert await elk_module.get_elk_integration() is None


@pytest.mark.asyncio
async def test_shutdown_elk_integration_clears_global_after_disconnect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingDisconnectIntegration:
        async def disconnect(self) -> None:
            raise RuntimeError("disconnect failed")

    monkeypatch.setattr(elk_module, "_elk_integration", FailingDisconnectIntegration())

    with pytest.raises(RuntimeError, match="disconnect failed"):
        await elk_module.shutdown_elk_integration()

    assert await elk_module.get_elk_integration() is None


@pytest.mark.asyncio
async def test_elk_disconnect_attempts_all_resources_when_one_disconnect_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    integration = ELKIntegration()
    root_logger = logging.getLogger()
    calls: list[str] = []

    async def fake_elasticsearch_disconnect() -> None:
        calls.append("elasticsearch_disconnect")
        raise RuntimeError("elasticsearch close failed")

    async def fake_logstash_disconnect() -> None:
        calls.append("logstash_disconnect")

    monkeypatch.setattr(
        integration.elasticsearch_client,
        "disconnect",
        fake_elasticsearch_disconnect,
    )
    monkeypatch.setattr(integration.logstash_handler, "disconnect", fake_logstash_disconnect)

    root_logger.addHandler(integration.logstash_handler)
    integration._connected = True
    try:
        with pytest.raises(RuntimeError, match="Failed to disconnect ELK stack") as exc_info:
            await integration.disconnect()
    finally:
        if integration.logstash_handler in root_logger.handlers:
            root_logger.removeHandler(integration.logstash_handler)

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "elasticsearch close failed"
    assert integration._connected is False
    assert integration.logstash_handler not in root_logger.handlers
    assert calls == ["elasticsearch_disconnect", "logstash_disconnect"]


@pytest.mark.asyncio
async def test_logstash_handler_uses_configured_aiohttp_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_session = install_fake_aiohttp(monkeypatch)
    handler = LogstashHandler(request_timeout=3.0)

    await handler.connect()
    try:
        session = fake_session.created[-1]

        assert session.kwargs["timeout"].total == 3.0
    finally:
        await handler.disconnect()


@pytest.mark.asyncio
async def test_logstash_handler_send_log_swallows_writer_errors() -> None:
    class FailingWriter:
        def post(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("writer failed")

    handler = LogstashHandler()
    handler._writer = FailingWriter()

    await handler._send_log({"message": "hello"})


@pytest.mark.asyncio
async def test_logstash_handler_send_log_warns_on_failed_http_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeHTTPResponse(503)
    session = FakeHTTPSession(response)
    handler = LogstashHandler()
    handler._writer = session
    warnings_logged: list[str] = []
    monkeypatch.setattr(
        elk_module.logger, "warning", lambda message: warnings_logged.append(message)
    )

    await handler._send_log({"message": "hello"})

    assert session.posts == [
        (
            "http://localhost:5000",
            {"json": {"message": "hello"}},
        )
    ]
    assert warnings_logged == ["Failed to send log to Logstash: 503"]


def test_logstash_handler_format_to_logstash_uses_local_context_vars() -> None:
    handler = LogstashHandler()
    record = logging.LogRecord(
        name="bt-api-test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    correlation_token = correlation_id_var.set("corr-1")
    request_token = request_id_var.set("req-1")

    try:
        payload = handler.format_to_logstash(record)
    finally:
        correlation_id_var.reset(correlation_token)
        request_id_var.reset(request_token)

    assert payload["message"] == "hello world"
    assert payload["correlation_id"] == "corr-1"
    assert payload["request_id"] == "req-1"


def test_logstash_handler_emit_without_running_loop_handles_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = LogstashHandler()
    handled: list[logging.LogRecord] = []
    record = logging.LogRecord(
        name="bt-api-test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    monkeypatch.setattr(handler, "handleError", lambda item: handled.append(item))

    with warnings.catch_warnings(record=True) as captured_warnings:
        warnings.simplefilter("always")
        handler.emit(record)

    assert handled == [record]
    assert not [item for item in captured_warnings if issubclass(item.category, RuntimeWarning)]


@pytest.mark.asyncio
async def test_logstash_handler_emit_schedules_send_when_loop_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = LogstashHandler()
    sent_payloads: list[dict[str, Any]] = []
    handled: list[logging.LogRecord] = []
    record = logging.LogRecord(
        name="bt-api-test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )

    async def fake_send_log(payload: dict[str, Any]) -> None:
        sent_payloads.append(payload)

    monkeypatch.setattr(handler, "_send_log", fake_send_log)
    monkeypatch.setattr(handler, "handleError", lambda item: handled.append(item))

    handler.emit(record)
    await asyncio.sleep(0)

    assert handled == []
    assert [payload["message"] for payload in sent_payloads] == ["hello"]


def test_prometheus_formatter_escapes_and_orders_label_values() -> None:
    labels = {
        "path": 'C:\\tmp\n"quoted"',
        "exchange": "SIM",
    }

    assert PrometheusFormatter.format_labels({}) == ""
    assert (
        PrometheusFormatter.format_labels(labels)
        == '{exchange="SIM",path="C:\\\\tmp\\n\\"quoted\\""}'
    )


def test_prometheus_formatter_sanitizes_metric_and_label_names() -> None:
    labels = {
        "route.path": "/orders",
        "9exchange": "SIM",
    }

    assert PrometheusFormatter.format_metric_name("9http.requests-total") == "_9http_requests_total"
    assert PrometheusFormatter.format_labels(labels) == '{_9exchange="SIM",route_path="/orders"}'


def test_prometheus_formatter_outputs_valid_histogram_bucket_labels() -> None:
    registry = MetricRegistry()
    histogram = Histogram("latency_seconds", "Latency", buckets=[0.1])
    registry.register(histogram)
    histogram.observe(0.05)

    output = PrometheusFormatter.format_registry(registry)

    assert 'latency_seconds_bucket{le="0.1"} 1.0' in output
    assert 'latency_seconds_bucket{le="+Inf"} 1.0' in output
    assert 'latency_seconds_bucket{le=""0.1""}' not in output


def test_prometheus_formatter_sanitizes_metric_names_in_registry_output() -> None:
    registry = MetricRegistry()
    gauge = Gauge("http.requests-total", "Request total")
    registry.register(gauge)
    gauge.set(2.0)

    output = PrometheusFormatter.format_registry(registry)

    assert "# HELP http_requests_total Auto-generated metric" in output
    assert "# TYPE http_requests_total gauge" in output
    assert "http_requests_total 2.0" in output
    assert "http.requests-total" not in output
