"""Offline shutdown contracts for the production monitoring example."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING
from unittest.mock import Mock

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO_PATH = ROOT / "examples" / "production_monitoring_demo.py"


def _load_demo(monkeypatch: pytest.MonkeyPatch, stop_side_effect=None):
    class LoggingSystemModule(ModuleType):
        get_logger: Mock
        setup_logging_for_production: Mock

    class MonitoringModule(ModuleType):
        counter: Mock
        gauge: Mock
        histogram: Mock
        get_business_collector: Mock
        start_prometheus_exporter: Mock
        stop_prometheus_exporter: Mock
        monitor_calls: Callable[..., Callable[[Callable[..., object]], Callable[..., object]]]
        monitor_performance: Callable[..., Callable[[Callable[..., object]], Callable[..., object]]]

    logger = Mock()
    logging_system = LoggingSystemModule("bt_api_py.logging_system")
    logging_system.get_logger = Mock(return_value=logger)
    logging_system.setup_logging_for_production = Mock()

    monitoring = MonitoringModule("bt_api_py.monitoring")
    monitoring.counter = Mock(return_value=Mock())
    monitoring.gauge = Mock(return_value=Mock())
    monitoring.histogram = Mock(return_value=Mock())
    monitoring.get_business_collector = Mock(return_value=Mock())
    monitoring.start_prometheus_exporter = Mock()
    monitoring.stop_prometheus_exporter = Mock(side_effect=stop_side_effect)

    def identity_decorator(*_args, **_kwargs):
        return lambda function: function

    monitoring.monitor_calls = identity_decorator
    monitoring.monitor_performance = identity_decorator

    monkeypatch.setitem(sys.modules, "bt_api_py.logging_system", logging_system)
    monkeypatch.setitem(sys.modules, "bt_api_py.monitoring", monitoring)

    spec = importlib.util.spec_from_file_location("isolated_production_monitoring_demo", DEMO_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, logger, monitoring.stop_prometheus_exporter, monitoring.start_prometheus_exporter


def test_prometheus_exporter_stop_success_keeps_existing_log(monkeypatch):
    module, logger, stop_exporter, start_exporter = _load_demo(monkeypatch)

    result = module._stop_prometheus_exporter_safely()

    assert result is None
    stop_exporter.assert_called_once_with()
    logger.info.assert_called_once_with("Prometheus exporter stopped")
    logger.warning.assert_not_called()
    start_exporter.assert_not_called()


def test_prometheus_exporter_stop_failure_warns_safely_and_returns(monkeypatch):
    exception_text = "exporter stop failure with private diagnostic"
    module, logger, stop_exporter, start_exporter = _load_demo(
        monkeypatch, stop_side_effect=RuntimeError(exception_text)
    )

    result = module._stop_prometheus_exporter_safely()
    example_can_continue = True

    assert result is None
    assert example_can_continue
    stop_exporter.assert_called_once_with()
    logger.warning.assert_called_once_with("Prometheus exporter stop failed (%s)", "RuntimeError")
    assert logger.warning.call_args.kwargs == {}
    assert exception_text not in repr(logger.warning.call_args)
    logger.info.assert_not_called()
    start_exporter.assert_not_called()
