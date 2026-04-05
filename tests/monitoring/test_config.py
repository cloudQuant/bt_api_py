"""Tests for monitoring/config.py."""

import pytest

from bt_api_py.monitoring import config as monitoring_config
from bt_api_py.monitoring.config import MonitoringConfig


class TestMonitoringConfig:
    """Tests for MonitoringConfig."""

    def test_defaults(self):
        """Test default values."""
        config = MonitoringConfig()
        assert config.metrics_collection_interval == 5.0
        assert config.prometheus_port == 8080
        assert config.log_level == "INFO"

    def test_kwargs_override_defaults(self):
        config = MonitoringConfig(prometheus_port=9099, log_level="DEBUG", elk_enabled=True)
        assert config.prometheus_port == 9099
        assert config.log_level == "DEBUG"
        assert config.elk_enabled is True


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(("info", message))

    def error(self, message):
        self.messages.append(("error", message))

    def debug(self, message, *args, **kwargs):
        self.messages.append(("debug", message))


class TestMonitoringLifecycle:
    @pytest.mark.asyncio
    async def test_setup_grafana_dashboards(self, tmp_path, monkeypatch):
        saved = []
        monkeypatch.setattr(
            monitoring_config,
            "get_all_dashboard_configs",
            lambda: {"overview": {"title": "Overview"}, "latency": {"title": "Latency"}},
        )
        monkeypatch.setattr(
            monitoring_config,
            "save_dashboard_to_file",
            lambda dashboard, filename: saved.append((dashboard, filename)),
        )

        await monitoring_config.setup_grafana_dashboards(str(tmp_path))

        assert len(saved) == 2
        assert saved[0][1].endswith("_dashboard.json")

    @pytest.mark.asyncio
    async def test_setup_monitoring_without_elk(self, monkeypatch):
        logger = _Logger()
        calls = []
        monkeypatch.setattr(monitoring_config, "get_logger", lambda name: logger)
        monkeypatch.setattr(
            monitoring_config,
            "setup_logging_for_production",
            lambda **kwargs: calls.append(("logging", kwargs)),
        )

        async def fake_start_global_monitoring(interval):
            calls.append(("monitoring", interval))

        monkeypatch.setattr(
            monitoring_config, "start_global_monitoring", fake_start_global_monitoring
        )
        monkeypatch.setattr(
            monitoring_config,
            "start_prometheus_exporter",
            lambda **kwargs: calls.append(("prometheus", kwargs)),
        )
        monkeypatch.setattr(
            monitoring_config,
            "setup_grafana_dashboards",
            lambda output_dir: fake_start_global_monitoring(output_dir),
        )

        config = MonitoringConfig(prometheus_port=9091, elk_enabled=False)
        await monitoring_config.setup_monitoring(config)

        assert calls[0][0] == "logging"
        assert calls[1] == ("monitoring", 5.0)
        assert calls[2][0] == "prometheus"
        assert calls[3] == ("monitoring", "monitoring/grafana/dashboards")
        assert any(level == "info" for level, _ in logger.messages)

    @pytest.mark.asyncio
    async def test_setup_monitoring_with_elk(self, monkeypatch):
        logger = _Logger()
        calls = []
        monkeypatch.setattr(monitoring_config, "get_logger", lambda name: logger)
        monkeypatch.setattr(
            monitoring_config, "setup_logging_for_production", lambda **kwargs: None
        )

        async def fake_start_global_monitoring(interval):
            calls.append(("monitoring", interval))

        async def fake_setup_elk_integration(**kwargs):
            calls.append(("elk", kwargs))

        async def fake_setup_grafana_dashboards(output_dir):
            calls.append(("grafana", output_dir))

        monkeypatch.setattr(
            monitoring_config, "start_global_monitoring", fake_start_global_monitoring
        )
        monkeypatch.setattr(monitoring_config, "setup_elk_integration", fake_setup_elk_integration)
        monkeypatch.setattr(
            monitoring_config, "setup_grafana_dashboards", fake_setup_grafana_dashboards
        )
        monkeypatch.setattr(
            monitoring_config,
            "start_prometheus_exporter",
            lambda **kwargs: calls.append(("prometheus", kwargs)),
        )

        config = MonitoringConfig(
            elk_enabled=True, elasticsearch_username="user", elasticsearch_password="pass"
        )
        await monitoring_config.setup_monitoring(config)

        assert any(kind == "elk" for kind, _ in calls)
        assert any(kind == "grafana" for kind, _ in calls)
        assert any(level == "info" for level, _ in logger.messages)

    @pytest.mark.asyncio
    async def test_setup_monitoring_logs_and_reraises_errors(self, monkeypatch):
        logger = _Logger()
        monkeypatch.setattr(monitoring_config, "get_logger", lambda name: logger)
        monkeypatch.setattr(
            monitoring_config,
            "setup_logging_for_production",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        with pytest.raises(RuntimeError):
            await monitoring_config.setup_monitoring(MonitoringConfig())

        assert logger.messages[-1][0] == "error"

    @pytest.mark.asyncio
    async def test_cleanup_monitoring_swallows_errors(self, monkeypatch):
        logger = _Logger()
        monkeypatch.setattr(monitoring_config, "get_logger", lambda name: logger)

        async def fake_stop_global_monitoring():
            raise RuntimeError("cleanup-failed")

        module = type(
            "MonitoringModule",
            (),
            {
                "shutdown_elk_integration": staticmethod(lambda: fake_stop_global_monitoring()),
                "stop_global_monitoring": staticmethod(fake_stop_global_monitoring),
                "stop_prometheus_exporter": staticmethod(lambda: None),
            },
        )
        monkeypatch.setitem(__import__("sys").modules, "bt_api_py.monitoring", module)

        await monitoring_config.cleanup_monitoring()

        assert logger.messages[-1][0] == "debug"
