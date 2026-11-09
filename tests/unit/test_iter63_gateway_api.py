"""Offline tests for the explicit, providerless Gateway facade."""

import os
import subprocess
import sys
import threading
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bt_api_py import BtApi
from bt_api_py._contracts import OrderRequest, OrderType, Side
from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime_thread import ThreadedRuntimeError
from tests.test_mig01_gateway_entrypoints_retired import _is_legacy_module
from tests.unit.test_iter63_gateway_strategy_client import config_data, parts

ROUTE = "CTP___FUTURE"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("bt_api_py.gateway_api", False),
        ("bt_api_py.gateway_direct_client", False),
        ("bt_api_py.gateway", True),
        ("bt_api_py.gateway.foo", True),
        ("bt_api_py.forwarding", True),
        ("bt_api_py.forwarding.foo", True),
        ("bt_api_py.forwarding_api", False),
        ("bt_api_py._gateway_wire", True),
        ("bt_api_py.runtime_plugins.gateway_dispatch", True),
    ],
)
def test_retired_namespace_match_is_exact(name, expected):
    assert _is_legacy_module(name) is expected


def session_with_reads():
    from bt_api_py.gateway_strategy_market import (
        GatewayMarketClientSettings,
        GatewayMarketSubscriptionSettings,
    )
    from tests.unit.test_gateway_strategy_account import settings as account_settings
    from tests.unit.test_gateway_strategy_market import _KEY, _OPERATIONS
    from tests.unit.test_iter63_gateway_strategy_instrument import SETTINGS

    data = parts()
    config = config_data()
    bindings = config["clients"]["gateway"]["settings"]["bindings"]
    for mount in ("store.account", "store.market"):
        item = dict(bindings[0], mount=mount)
        if mount == "store.market":
            del item["account_ref"]
        bindings.append(item)
    config["communication"]["profiles"]["transport"]["endpoints"]["market_connect"] = (
        "tcp://127.0.0.1:12346"
    )
    data["config"] = parse_client_config(config)
    return GatewayStrategyClientSession(
        **data,
        account_settings=account_settings(),
        market_settings=GatewayMarketClientSettings(
            {"main": GatewayMarketSubscriptionSettings(_KEY)}, _OPERATIONS
        ),
        instrument_settings=SETTINGS,
    )


def order():
    return OrderRequest(
        symbol="rb",
        side=Side.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        account_id="account",
        client_order_id="client",
        price=Decimal("10"),
        idempotency_key="same-key",
        exchange_id="SHFE",
    )


def test_inert_construction_and_exact_session(monkeypatch):
    import bt_api_py.gateway_strategy_client as module

    def forbidden(*args, **kwargs):
        pytest.fail("created transport or runtime")

    monkeypatch.setattr(module, "ServiceClientRuntime", forbidden)
    monkeypatch.setattr(module, "ZmqTransportFactory", forbidden)
    session = GatewayStrategyClientSession(**parts())
    api = BtApi.from_gateway(session, exchange_name=ROUTE)
    assert type(api) is GatewayBtApi
    assert api.lifecycle_state == "open"
    assert api.list_exchanges() == [ROUTE]
    assert session.status().state == "new"
    assert not session.status().thread_alive
    with pytest.raises(TypeError, match="INVALID_GATEWAY_SESSION"):
        BtApi.from_gateway(object(), exchange_name=ROUTE)
    for name in ("", " ", " route ", None, 1):
        with pytest.raises(ValueError, match="INVALID_EXCHANGE_NAME"):
            BtApi.from_gateway(session, exchange_name=name)
    with pytest.raises(TypeError, match="INVALID_SESSION_OWNERSHIP"):
        BtApi.from_gateway(session, exchange_name=ROUTE, owns_session=1)
    api.close()
    assert api.lifecycle_state == "closed"
    assert session.status().state == "new"
    session.close()


def test_delegates_exact_objects_arguments_results(monkeypatch):
    session = session_with_reads()
    api = BtApi.from_gateway(session, exchange_name=ROUTE)
    calls = []
    results = {
        name: object()
        for name in ("reserve", "dispatch", "status", "release", "cancel", "cancel_status")
    }
    for name in results:

        def capture(*args, _name=name, **kwargs):
            calls.append((_name, args, kwargs))
            return results[_name]

        monkeypatch.setattr(session.command_client, name, capture)
    request = order()
    assert api.reserve_order(ROUTE, request, ttl_seconds=9, timeout_seconds=3) is results["reserve"]
    assert api.dispatch_order(ROUTE, "key", timeout_seconds=3) is results["dispatch"]
    assert api.command_status(ROUTE, "key", timeout_seconds=3) is results["status"]
    assert (
        api.release_order(ROUTE, "key", operation_id="release", timeout_seconds=3)
        is results["release"]
    )
    assert (
        api.cancel_order(ROUTE, "key", operation_id="cancel", timeout_seconds=3)
        is results["cancel"]
    )
    assert api.cancel_status(ROUTE, "cancel", timeout_seconds=3) is results["cancel_status"]
    assert calls == [
        ("reserve", (request,), {"ttl_seconds": 9, "timeout_seconds": 3}),
        ("dispatch", ("key",), {"timeout_seconds": 3}),
        ("status", ("key",), {"timeout_seconds": 3}),
        ("release", ("key",), {"operation_id": "release", "timeout_seconds": 3}),
        ("cancel", ("key",), {"operation_id": "cancel", "timeout_seconds": 3}),
        ("cancel_status", ("cancel",), {"timeout_seconds": 3}),
    ]
    assert request.exchange_id == "SHFE" and request.idempotency_key == "same-key"
    assert api.gateway_execution_stream(ROUTE) is session.execution_stream_client
    assert api.account_read(ROUTE) is session.account_client
    assert api.market_subscription(ROUTE, "main") is session.market_client.subscription("main")
    result = object()
    seen = []
    monkeypatch.setattr(
        session, "get_instrument_spec", lambda *a, **kw: seen.append((a, kw)) or result
    )
    assert api.get_instrument_spec(ROUTE, "IF2612", timeout_seconds=2) is result
    assert seen == [((ROUTE, "IF2612"), {"timeout_seconds": 2})]
    api.close()
    session.close()


def test_wrong_route_missing_closed_and_pid_refuse_before_delegation(monkeypatch):
    session = GatewayStrategyClientSession(**parts())
    api = BtApi.from_gateway(session, exchange_name=ROUTE)
    monkeypatch.setattr(
        session.command_client, "reserve", lambda *a, **kw: pytest.fail("delegated")
    )
    for route in (None, "wrong", "ctp___future"):
        with pytest.raises(ValueError, match="GATEWAY_ROUTE_MISMATCH"):
            api.reserve_order(route, order())
    for call in (
        lambda: api.account_read(ROUTE),
        lambda: api.market_subscription(ROUTE, "missing"),
        lambda: api.get_instrument_spec(ROUTE, "x"),
    ):
        with pytest.raises(RuntimeError, match="UNAVAILABLE"):
            call()
    with monkeypatch.context() as context:
        context.setattr("bt_api_py.gateway_api.os.getpid", lambda: -1)
        with pytest.raises(RuntimeError, match="WRONG_PROCESS"):
            api.reserve_order(ROUTE, order())
    api.close()
    with pytest.raises(RuntimeError, match="GATEWAY_FACADE_CLOSED"):
        api.reserve_order(ROUTE, order())
    with pytest.raises(RuntimeError, match="GATEWAY_FACADE_CLOSED"):
        api.start()
    assert session.status().state == "new"
    session.close()


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize(
    "change",
    [
        "session_binding",
        "session_route",
        "binding_value",
        "route_value",
        "client_binding",
        "client_route",
        "host",
        "runtime",
        "command",
        "stream",
        "account",
        "market",
        "instrument",
        "subscription",
    ],
)
def test_binding_and_client_replacement_refused(change, owned, monkeypatch):
    session = session_with_reads()
    api = BtApi.from_gateway(session, exchange_name=ROUTE, owns_session=owned)
    runtime = session.runtime
    original_close = runtime.close
    closes = []

    def close_original(**kwargs):
        closes.append(kwargs)
        return original_close(**kwargs)

    monkeypatch.setattr(runtime, "close", close_original)
    monkeypatch.setattr(api._host, "close", lambda: pytest.fail("closed host"), raising=False)
    monkeypatch.setattr(session, "close", lambda **kw: pytest.fail("used mutable session close"))
    monkeypatch.setattr(session, "status", lambda: pytest.fail("used mutable session status"))

    class Replacement:
        def close(self, **kwargs):
            pytest.fail("closed replacement")

        def status(self):
            pytest.fail("queried replacement")

    if change == "session_binding":
        session._binding = replace(session._binding)
    elif change == "session_route":
        session._route = replace(session._route)
    elif change == "binding_value":
        object.__setattr__(session._binding, "principal", "changed")
    elif change == "route_value":
        object.__setattr__(session._route.scope, "run_id", "changed")
    elif change == "client_binding":
        session.command_client._binding = replace(session.command_client._binding)
    elif change == "client_route":
        session.execution_stream_client._route = replace(session.execution_stream_client._route)
    elif change == "host":
        session.command_client._host = object()
    elif change == "subscription":
        session.market_client._clients["main"] = object()
    else:
        attr = {
            "runtime": "_runtime",
            "command": "_command_client",
            "stream": "_execution_stream_client",
            "account": "_account_client",
            "market": "_market_client",
            "instrument": "_instrument_client",
        }[change]
        setattr(session, attr, Replacement())
    with pytest.raises(RuntimeError, match="GATEWAY_BINDING_CHANGED"):
        api.list_exchanges()
    with pytest.raises(RuntimeError, match="GATEWAY_BINDING_CHANGED"):
        api.start()
    assert api.lifecycle_state == "open"
    assert api.status().state == "new"
    snapshot = api.close(timeout_seconds=2)
    assert snapshot.state == ("closed" if owned else "new")
    assert closes == ([{"timeout_seconds": 2}] if owned else [])
    assert api.lifecycle_state == "closed"
    assert api.status().state == ("closed" if owned else "new")
    with pytest.raises(RuntimeError, match="GATEWAY_BINDING_CHANGED"):
        api.list_exchanges()
    if not owned:
        original_close()


def test_owned_close_timeout_preserves_handle_and_retry(monkeypatch):
    import bt_api_py.gateway_strategy_client as module

    entered = threading.Event()
    release = threading.Event()

    class Raw:
        def __init__(self, *args, **kwargs):
            self.identity = kwargs["identity"]
            self.run_id = kwargs["run_id"]
            self.bindings = ()
            self.healthy = True
            self.cleanup_failures = self.unregister_uncertainties = 0

        def start(self):
            pass

        def maintain(self):
            pass

        def next_maintenance_delay(self):
            return 1

        def close(self):
            entered.set()
            release.wait(5)

    monkeypatch.setattr(module, "ServiceClientRuntime", Raw)
    monkeypatch.setattr(module, "ZmqTransportFactory", lambda profiles: object())
    session = GatewayStrategyClientSession(**parts())
    api = BtApi.from_gateway(session, exchange_name=ROUTE, owns_session=True)
    try:
        assert api.start().state == "running"
        with pytest.raises(ThreadedRuntimeError, match="CLOSE_TIMEOUT_WORKER_ALIVE"):
            api.close(timeout_seconds=0.05)
        assert entered.is_set()
        assert api.status().state == "stopping"
        assert api.lifecycle_state == "closing"
        with pytest.raises(RuntimeError, match="GATEWAY_FACADE_CLOSING"):
            api.command_status(ROUTE, "key")
        assert session.runtime is api._runtime
        release.set()
        assert api.close(timeout_seconds=2).state == "closed"
        assert api.lifecycle_state == "closed"
        with pytest.raises(RuntimeError, match="GATEWAY_FACADE_CLOSED"):
            api.start()
    finally:
        release.set()
        session.close(timeout_seconds=2)


def test_borrowed_start_and_close_only_affect_facade(monkeypatch):
    session = GatewayStrategyClientSession(**parts())
    api = BtApi.from_gateway(session, exchange_name=ROUTE)
    marker = object()
    seen = []
    monkeypatch.setattr(session, "start", lambda **kw: seen.append(kw) or marker)
    monkeypatch.setattr(session, "close", lambda **kw: pytest.fail("closed borrowed session"))
    assert api.start(timeout_seconds=3) is marker
    assert seen == [{"timeout_seconds": 3}]
    assert api.close().state == "new"
    assert session.runtime.status().state == "new"


def test_owned_close_failure_keeps_retry_and_does_not_hold_lock(monkeypatch):
    session = GatewayStrategyClientSession(**parts())
    api = BtApi.from_gateway(session, exchange_name=ROUTE, owns_session=True)
    entered, resume = threading.Event(), threading.Event()
    result = SimpleNamespace(
        state="close_failed", thread_alive=False, cleanup_failures=2, unregister_uncertainties=3
    )

    def close(**kw):
        entered.set()
        assert resume.wait(2)
        return result

    with monkeypatch.context() as context:
        context.setattr(session.runtime, "close", close)
        outcomes = []
        thread = threading.Thread(target=lambda: outcomes.append(api.close(timeout_seconds=1)))
        thread.start()
        try:
            assert entered.wait(1)
            assert api.status().state == "new"
        finally:
            resume.set()
            thread.join(2)
        assert outcomes == [result] and outcomes[0] is result
        assert outcomes[0].cleanup_failures == 2 and outcomes[0].unregister_uncertainties == 3
        assert api.lifecycle_state == "closing"
        with pytest.raises(RuntimeError, match="GATEWAY_FACADE_CLOSING"):
            api.list_exchanges()
    assert api.close().state == "closed"


def test_close_after_drift_retries_exceptions_and_unfinished_result(monkeypatch):
    session = GatewayStrategyClientSession(**parts())
    runtime = session.runtime
    api = BtApi.from_gateway(session, exchange_name=ROUTE, owns_session=True)
    session._runtime = object()
    original_close = runtime.close
    incomplete = SimpleNamespace(state="stopping", thread_alive=True)
    calls = []

    def close(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("SYNTHETIC_CLOSE_FAILED")
        if len(calls) == 2:
            return incomplete
        return original_close(**kwargs)

    monkeypatch.setattr(runtime, "close", close)
    with pytest.raises(RuntimeError, match="SYNTHETIC_CLOSE_FAILED"):
        api.close(timeout_seconds=1)
    assert api.lifecycle_state == "closing"
    assert api.status().state == "new"
    assert api.close(timeout_seconds=2) is incomplete
    assert api.lifecycle_state == "closing"
    assert api.close(timeout_seconds=3).state == "closed"
    assert api.lifecycle_state == "closed"
    assert calls == [
        {"timeout_seconds": 1},
        {"timeout_seconds": 2},
        {"timeout_seconds": 3},
    ]


@pytest.mark.parametrize("owned", [False, True])
def test_drift_cleanup_rejects_wrong_process_and_invalid_timeout(owned, monkeypatch):
    session = GatewayStrategyClientSession(**parts())
    runtime = session.runtime
    api = BtApi.from_gateway(session, exchange_name=ROUTE, owns_session=owned)
    session._runtime = object()
    with monkeypatch.context() as context:
        context.setattr("bt_api_py.gateway_api.os.getpid", lambda: -1)
        for call in (api.status, lambda: api.lifecycle_state, api.close):
            with pytest.raises(RuntimeError, match="WRONG_PROCESS"):
                call()
    with pytest.raises(ValueError, match="timeout_seconds"):
        api.close(timeout_seconds=0)
    assert api.lifecycle_state == "open" and api.status().state == "new"
    api.close()
    if not owned:
        runtime.close()


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan"), "1"])
def test_invalid_close_timeout_preserves_open_state(owned, timeout):
    session = GatewayStrategyClientSession(**parts())
    api = BtApi.from_gateway(session, exchange_name=ROUTE, owns_session=owned)
    with pytest.raises(ValueError, match="timeout_seconds"):
        api.close(timeout_seconds=timeout)
    assert api.lifecycle_state == "open" and session.status().state == "new"
    api.close()
    if not owned:
        session.close()


def test_cold_process_does_not_load_provider_or_plugin():
    code = """
import builtins
real_import = builtins.__import__
def guard(name, *args, **kwargs):
    if name.startswith(("bt_api_ctp", "bt_api_okx", "bt_api_binance")):
        raise AssertionError("provider import")
    return real_import(name, *args, **kwargs)
builtins.__import__ = guard
from bt_api_py import BtApi
from tests.unit.test_iter63_gateway_strategy_client import parts
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_base.registry import ExchangeRegistry
from bt_api_base.plugins.loader import PluginLoader
def forbidden(*a, **kw):
    raise AssertionError("provider factory or plugin loader")
PluginLoader.load_all = forbidden
ExchangeRegistry.create_feed = forbidden
import importlib.metadata
importlib.metadata.entry_points = forbidden
session = GatewayStrategyClientSession(**parts())
api = BtApi.from_gateway(session, exchange_name="CTP___FUTURE")
assert api.list_exchanges() == ["CTP___FUTURE"]
api.close()
session.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "BT_API_PY_LIGHT_IMPORT": "1"},
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
