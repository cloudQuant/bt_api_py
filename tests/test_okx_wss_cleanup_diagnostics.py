import ast
import logging
from pathlib import Path
from types import CodeType, FunctionType, SimpleNamespace
from typing import Literal, TypeAlias

_Event: TypeAlias = (
    tuple[Literal["get_open_orders"]]
    | tuple[Literal["get_data"]]
    | tuple[Literal["init_data"], str]
    | tuple[Literal["symbol"], str]
    | tuple[Literal["order_id"], str]
    | tuple[Literal["cancel_order"], str, str]
    | tuple[Literal["sleep"], int]
)

_EXAMPLE_PATH = (
    Path(__file__).resolve().parents[1]
    / "examples/network_tests/feeds/test_live_okx_spot_wss_data.py"
)
_LOGGER = logging.getLogger(f"{__name__}.source_helper")


def _load_cleanup_helper(sleeper):
    """Compile only the cleanup helper, avoiding legacy OKX/network imports."""
    source_tree = ast.parse(_EXAMPLE_PATH.read_text(encoding="utf-8"))
    helper = next(
        (
            node
            for node in source_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_cleanup_open_orders"
        ),
        None,
    )
    assert helper is not None, "OKX cleanup helper is missing"

    isolated_module = ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[]))
    compiled_module = compile(isolated_module, str(_EXAMPLE_PATH), "exec")
    helper_code = next(
        constant
        for constant in compiled_module.co_consts
        if isinstance(constant, CodeType) and constant.co_name == "_cleanup_open_orders"
    )
    helper_globals = {
        "logger": _LOGGER,
        "time": SimpleNamespace(sleep=sleeper),
    }
    return FunctionType(helper_code, helper_globals)


class _FakeOrder:
    def __init__(self, symbol, order_id, events):
        self.symbol = symbol
        self.order_id = order_id
        self.events = events

    def init_data(self):
        self.events.append(("init_data", self.order_id))
        return self

    def get_order_symbol_name(self):
        self.events.append(("symbol", self.symbol))
        return self.symbol

    def get_order_id(self):
        self.events.append(("order_id", self.order_id))
        return self.order_id


class _FakeOpenOrders:
    def __init__(self, orders, events):
        self.orders = orders
        self.events = events

    def get_data(self):
        self.events.append(("get_data",))
        return self.orders


class _FakeFeed:
    def __init__(
        self,
        orders,
        events,
        *,
        failed_order_id=None,
        cancel_error_body="",
        open_orders_error_body="",
    ):
        self.orders = orders
        self.events = events
        self.failed_order_id = failed_order_id
        self.cancel_error_body = cancel_error_body
        self.open_orders_error_body = open_orders_error_body

    def get_open_orders(self):
        self.events.append(("get_open_orders",))
        if self.open_orders_error_body:
            raise RuntimeError(self.open_orders_error_body)
        return _FakeOpenOrders(self.orders, self.events)

    def cancel_order(self, instrument_id, *, order_id):
        self.events.append(("cancel_order", instrument_id, order_id))
        if order_id == self.failed_order_id:
            raise RuntimeError(self.cancel_error_body)


def test_cleanup_open_orders_cancels_in_order_and_sleeps_afterward():
    events: list[_Event] = []
    feed = _FakeFeed(
        [
            _FakeOrder("OP-USDT", "order-1", events),
            _FakeOrder("ETH-USDT", "", events),
        ],
        events,
    )
    cleanup = _load_cleanup_helper(lambda seconds: events.append(("sleep", seconds)))

    cleanup(feed)

    assert events == [
        ("get_open_orders",),
        ("get_data",),
        ("init_data", "order-1"),
        ("symbol", "OP-USDT"),
        ("order_id", "order-1"),
        ("cancel_order", "OP-USDT", "order-1"),
        ("init_data", ""),
        ("symbol", "ETH-USDT"),
        ("order_id", ""),
        ("sleep", 1),
    ]


def test_cleanup_open_orders_continues_after_one_cancel_failure(caplog):
    events: list[_Event] = []
    sensitive_body = "account=private-account api_secret=private-key order=private-order"
    feed = _FakeFeed(
        [
            _FakeOrder("OP-USDT", "order-1", events),
            _FakeOrder("ETH-USDT", "order-2", events),
        ],
        events,
        failed_order_id="order-1",
        cancel_error_body=sensitive_body,
    )
    cleanup = _load_cleanup_helper(lambda seconds: events.append(("sleep", seconds)))
    caplog.set_level(logging.DEBUG, logger=_LOGGER.name)

    cleanup(feed)

    assert [event for event in events if event[0] == "cancel_order"] == [
        ("cancel_order", "OP-USDT", "order-1"),
        ("cancel_order", "ETH-USDT", "order-2"),
    ]
    assert events[-1] == ("sleep", 1)
    assert "RuntimeError" in caplog.text
    assert sensitive_body not in caplog.text
    assert "private-account" not in caplog.text
    assert "private-key" not in caplog.text
    assert "private-order" not in caplog.text
    assert "order-1" not in caplog.text
    assert "OP-USDT" not in caplog.text


def test_cleanup_open_orders_records_only_type_on_outer_failure(caplog):
    events: list[_Event] = []
    sensitive_body = "account=private-account api_secret=private-key"
    feed = _FakeFeed(
        [],
        events,
        open_orders_error_body=sensitive_body,
    )
    cleanup = _load_cleanup_helper(lambda seconds: events.append(("sleep", seconds)))
    caplog.set_level(logging.DEBUG, logger=_LOGGER.name)

    cleanup(feed)

    assert events == [("get_open_orders",)]
    assert "RuntimeError" in caplog.text
    assert sensitive_body not in caplog.text
    assert "private-account" not in caplog.text
    assert "private-key" not in caplog.text
