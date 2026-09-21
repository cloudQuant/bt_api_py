"""Offline safety contracts for OKX request-feed order cleanup."""

from __future__ import annotations

import ast
from pathlib import Path
from types import CodeType, FunctionType, SimpleNamespace
from unittest.mock import Mock

EXAMPLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "network_tests"
    / "feeds"
    / "test_live_okx_spot_request_data.py"
)


def _load_cleanup_open_orders():
    """Load the pure helper without importing live example dependencies."""
    source = ast.parse(EXAMPLE_PATH.read_text(encoding="utf-8"), filename=str(EXAMPLE_PATH))
    cleanup_node = next(
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "cleanup_open_orders"
    )
    isolated_module = ast.Module(body=[cleanup_node], type_ignores=[])
    namespace = {"logger": Mock(), "time": SimpleNamespace(sleep=Mock())}
    module_code = compile(isolated_module, str(EXAMPLE_PATH), "exec")
    cleanup_code = next(
        code
        for code in module_code.co_consts
        if isinstance(code, CodeType) and code.co_name == "cleanup_open_orders"
    )
    return FunctionType(cleanup_code, namespace), namespace["logger"], namespace["time"]


class _FakeOrderData:
    def __init__(self, symbol: str, order_id: str):
        self._symbol = symbol
        self._order_id = order_id

    def get_order_symbol_name(self) -> str:
        return self._symbol

    def get_order_id(self) -> str:
        return self._order_id


class _FakeOrder:
    def __init__(self, symbol: str, order_id: str):
        self._data = _FakeOrderData(symbol, order_id)

    def init_data(self) -> _FakeOrderData:
        return self._data


class _FakeRequestData:
    def __init__(self, orders: list[_FakeOrder]):
        self._orders = orders

    def get_data(self) -> list[_FakeOrder]:
        return self._orders


def _assert_safe_warning(logger: Mock, exception_type: str, secrets: list[str]) -> None:
    logger.warning.assert_called_once()
    args, kwargs = logger.warning.call_args
    assert len(args) == 2
    assert "cleanup failed" in args[0]
    assert args[1] == exception_type
    assert kwargs == {}
    rendered = args[0] % args[1]
    for secret in secrets:
        assert secret not in rendered
        assert secret not in repr(logger.warning.call_args)


def test_cleanup_continues_in_order_and_logs_only_exception_type():
    failure_text = "private-account-key-and-exchange-response"
    first_order_id = "private-order-1"
    second_order_id = "private-order-2"
    cleanup, logger, time = _load_cleanup_open_orders()
    feed = Mock()
    feed.get_open_orders.return_value = _FakeRequestData(
        [
            _FakeOrder("private-symbol-1", first_order_id),
            _FakeOrder("private-symbol-2", second_order_id),
        ]
    )
    feed.cancel_order.side_effect = [RuntimeError(failure_text), None]
    sleep = time.sleep

    result = cleanup(feed)

    assert result is None
    assert feed.cancel_order.call_args_list == [
        (("private-symbol-1",), {"order_id": first_order_id}),
        (("private-symbol-2",), {"order_id": second_order_id}),
    ]
    sleep.assert_called_once_with(1)
    _assert_safe_warning(
        logger,
        "RuntimeError",
        [failure_text, first_order_id, second_order_id, "private-symbol-1", "private-symbol-2"],
    )


def test_cleanup_open_orders_failure_logs_only_exception_type():
    failure_text = "private-account-secret-from-exception-body"
    cleanup, logger, time = _load_cleanup_open_orders()
    feed = Mock()
    feed.get_open_orders.side_effect = ValueError(failure_text)

    result = cleanup(feed)

    assert result is None
    feed.cancel_order.assert_not_called()
    time.sleep.assert_not_called()
    _assert_safe_warning(logger, "ValueError", [failure_text])


def test_cleanup_sleep_failure_is_swallowed_and_logs_only_exception_type():
    failure_text = "private-key-in-sleep-error-body"
    cleanup, logger, time = _load_cleanup_open_orders()
    feed = Mock()
    feed.get_open_orders.return_value = _FakeRequestData([])
    time.sleep.side_effect = RuntimeError(failure_text)

    result = cleanup(feed)

    assert result is None
    time.sleep.assert_called_once_with(1)
    _assert_safe_warning(logger, "RuntimeError", [failure_text])
