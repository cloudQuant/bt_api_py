"""Direct/ZMQ BtApi transport contract tests (Task 1.2).

MIG01-R1: the legacy ZMQ forwarding startup entry point is retired.  ZMQ
construction must fail closed with the typed migration error before any
socket/thread is created — never a silent fallback to the direct backend.
"""

from __future__ import annotations

import threading

import pytest

from bt_api_py import (
    BtApi,
    ForwardingConfig,
    TransportMode,
)
from bt_api_py._contracts.errors import LegacyForwardingRetiredError


def _forwarding_config() -> ForwardingConfig:
    return ForwardingConfig(
        command_endpoint="tcp://127.0.0.1:5999",
        market_endpoint="tcp://127.0.0.1:5998",
        private_endpoint="tcp://127.0.0.1:5997",
        account_id="paper",
        strategy_id="s1",
    )


def test_direct_is_default_transport_mode() -> None:
    api = BtApi()
    assert api.transport_mode is TransportMode.DIRECT


def test_zmq_transport_mode_raises_retired_migration_error() -> None:
    threads_before = threading.active_count()

    with pytest.raises(LegacyForwardingRetiredError) as excinfo:
        BtApi(transport_mode=TransportMode.ZMQ, forwarding_config=_forwarding_config())

    assert excinfo.value.target == "transport_mode=ZMQ"
    # No socket/thread side effect and no silent direct-backend fallback.
    assert threading.active_count() == threads_before


def test_zmq_transport_mode_without_forwarding_config_still_raises_migration_error() -> None:
    with pytest.raises(LegacyForwardingRetiredError) as excinfo:
        BtApi(transport_mode="zmq")

    assert excinfo.value.target == "transport_mode=ZMQ"


def test_direct_get_request_api_returns_none_for_unknown_exchange() -> None:
    api = BtApi()
    assert api.get_request_api("UNKNOWN___SPOT") is None


def test_bt_api_exposes_explicit_async_methods() -> None:
    api = BtApi()
    for name in (
        "async_get_tick",
        "async_get_depth",
        "async_get_kline",
        "async_make_order",
        "async_cancel_order",
        "async_get_balance",
        "async_get_account",
        "async_get_position",
    ):
        method = getattr(api, name, None)
        assert callable(method), f"missing explicit async method {name}"
