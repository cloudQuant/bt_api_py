"""Direct/ZMQ BtApi transport contract tests (Task 1.2)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock

import pytest

from bt_api_py import (
    BtApi,
    Consistency,
    ForwardingConfig,
    TransportMode,
)
from bt_api_py._contracts.errors import (
    CapabilityNotSupportedError,
    LiveQueryFailedError,
)
from bt_api_py._contracts.models import AccountSnapshot


def _forwarding_config() -> ForwardingConfig:
    return ForwardingConfig(
        command_endpoint="tcp://127.0.0.1:5999",
        market_endpoint="tcp://127.0.0.1:5998",
        private_endpoint="tcp://127.0.0.1:5997",
        account_id="paper",
        strategy_id="s1",
    )


def _zmq_bt_api_with_failing_client() -> tuple[BtApi, Mock]:
    api = BtApi(transport_mode=TransportMode.ZMQ, forwarding_config=_forwarding_config())
    client = Mock()
    client.connected = False
    api._backend._client = client  # inject a failing transport for determinism
    return api, client


def test_direct_is_default_transport_mode() -> None:
    api = BtApi()
    assert api.transport_mode is TransportMode.DIRECT


def test_zmq_bt_api_live_query_never_turns_transport_failure_into_zero_balance() -> None:
    api, client = _zmq_bt_api_with_failing_client()
    client.get_balance.side_effect = RuntimeError("transport down")
    with pytest.raises(LiveQueryFailedError):
        api.get_account("SIM___SPOT", consistency=Consistency.LIVE)


def test_zmq_bt_api_get_request_api_raises_capability_error() -> None:
    api = BtApi(transport_mode=TransportMode.ZMQ, forwarding_config=_forwarding_config())
    with pytest.raises(CapabilityNotSupportedError):
        api.get_request_api("SIM___SPOT")


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


def test_zmq_cached_account_query_returns_stale_snapshot() -> None:
    api, client = _zmq_bt_api_with_failing_client()
    cached = {"cash": 100.0, "value": 200.0, "equity": 200.0, "available_cash": 100.0}
    client.get_balance.side_effect = RuntimeError("transport down")
    api._backend._cache.put("get_account:SIM___SPOT", cached)

    snapshot = api.get_account("SIM___SPOT", consistency=Consistency.CACHE_OK)

    assert isinstance(snapshot, AccountSnapshot)
    assert snapshot.freshness.stale is True
    assert snapshot.freshness.source == "cache"
    assert snapshot.cash == Decimal("100")
