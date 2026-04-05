"""Tests for the structured logging system."""

from __future__ import annotations

import json
import logging
import sys

from bt_api_py import logging_system as logging_system_module
from bt_api_py.logging_system import (
    LoggingManager,
    StructuredFormatter,
    StructuredLogger,
    correlation_id_var,
)


class _CollectHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_structured_formatter_includes_context_and_exception_metadata() -> None:
    token = correlation_id_var.set("corr-123")
    formatter = StructuredFormatter(compact=True)

    try:
        raise ValueError("bad input")
    except ValueError:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=42,
            msg="failure: %s",
            args=("case",),
            exc_info=sys.exc_info(),
            func="test_func",
        )

    record.exchange_name = "BINANCE"
    record.component = "api"
    record.duration_ms = 12.5
    record.custom_field = "extra"

    try:
        payload = json.loads(formatter.format(record))
    finally:
        correlation_id_var.reset(token)

    assert payload["message"] == "failure: case"
    assert payload["correlation_id"] == "corr-123"
    assert payload["exchange_name"] == "BINANCE"
    assert payload["component"] == "api"
    assert payload["duration_ms"] == 12.5
    assert payload["metadata"]["custom_field"] == "extra"
    assert payload["error"]["type"] == "ValueError"
    assert payload["error"]["message"] == "bad input"


def test_logging_manager_reconfigure_closes_previous_handlers(tmp_path, monkeypatch) -> None:
    manager = LoggingManager()
    isolated_root = logging.Logger("isolated_root")
    old_handler = logging.FileHandler(tmp_path / "old.log", encoding="utf-8")
    isolated_root.addHandler(old_handler)

    real_get_logger = logging_system_module.logging.getLogger

    def fake_get_logger(name: str | None = None) -> logging.Logger:
        if name in {None, ""}:
            return isolated_root
        return real_get_logger(name)

    monkeypatch.setattr(logging_system_module.logging, "getLogger", fake_get_logger)

    manager.configure(
        log_file=tmp_path / "new.log",
        console_output=False,
        json_format=False,
        enable_rotation=False,
    )

    assert old_handler not in isolated_root.handlers
    assert old_handler.stream is None or old_handler.stream.closed
    assert len(isolated_root.handlers) == 1
    file_handler = isolated_root.handlers[0]
    assert isinstance(file_handler, logging.FileHandler)
    assert file_handler.baseFilename == str(tmp_path / "new.log")


def test_with_correlation_id_resets_context_after_exit() -> None:
    manager = LoggingManager()
    assert manager.get_correlation_id() is None

    with manager.with_correlation_id("ctx-1"):
        assert manager.get_correlation_id() == "ctx-1"

    assert manager.get_correlation_id() is None


def test_market_data_event_keeps_zero_values() -> None:
    logger_name = "test.logging.market_data"
    handler = _CollectHandler()
    base_logger = logging.getLogger(logger_name)
    base_logger.handlers.clear()
    base_logger.setLevel(logging.DEBUG)
    base_logger.propagate = False
    base_logger.addHandler(handler)

    StructuredLogger(logger_name).market_data_event(
        event_type="snapshot",
        exchange_name="OKX",
        symbol="BTC-USDT",
        data_type="ticker",
        count=0,
        lag_ms=0.0,
    )

    assert len(handler.records) == 1
    record = handler.records[0]
    assert record.count == 0
    assert record.lag_ms == 0.0
    assert "count=0" in record.getMessage()
    assert "lag=0.0ms" in record.getMessage()

    base_logger.handlers.clear()


def test_order_event_keeps_zero_quantity_and_price() -> None:
    logger_name = "test.logging.order"
    handler = _CollectHandler()
    base_logger = logging.getLogger(logger_name)
    base_logger.handlers.clear()
    base_logger.setLevel(logging.INFO)
    base_logger.propagate = False
    base_logger.addHandler(handler)

    StructuredLogger(logger_name).order_event(
        event_type="created",
        exchange_name="BINANCE",
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.0,
        price=0.0,
        status="NEW",
    )

    assert len(handler.records) == 1
    message = handler.records[0].getMessage()
    assert "BUY 0.0" in message
    assert "@ 0.0" in message
    assert "(NEW)" in message

    base_logger.handlers.clear()
