"""Offline contract tests for the controlled top-level CTP surface."""

from __future__ import annotations

import queue
from collections import deque
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bt_api_py import BtApi, InstrumentSpec, NormalizedApiError, TransportMode
from bt_api_py._contracts import CapabilityNotSupportedError
from bt_api_py._execution_session import _ExecutionSession
from bt_api_py._normalization import instrument_spec, normalize_event

VENUE = "CTP___FUTURE"


def _api(feed, *, transport=TransportMode.DIRECT, execution=True) -> BtApi:
    api = object.__new__(BtApi)
    api.transport_mode = transport
    api.exchange_feeds = {VENUE: feed}
    api._execution_session = object() if execution else None
    return api


def test_controlled_ctp_surface_remains_available_with_execution_session() -> None:
    result = object()
    feed = SimpleNamespace(
        get_session_state=lambda: {
            "connected": True,
            "auth_state": "authenticated",
            "login_state": "logged_in",
            "trading_ready": False,
            "settlement_readback_verified": True,
            "account_fingerprint": "account-sha256",
            "request_counts": {"query_account": 1},
            "password": "must-not-escape",
            "api": object(),
        },
        query_account_result=Mock(return_value=result),
    )
    api = _api(feed)

    with pytest.raises(CapabilityNotSupportedError):
        api.get_request_api(VENUE)
    state = api.get_ctp_session_state(VENUE)
    assert state["auth_state"] == "authenticated"
    assert state["settlement_readback_verified"] is True
    assert state["account_fingerprint"] == "account-sha256"
    assert "password" not in state and "api" not in state
    assert api.query_ctp_result(VENUE, "account") is result
    feed.query_account_result.assert_called_once_with()


def test_market_data_only_session_does_not_hide_typed_ctp_preflight_reads() -> None:
    result = object()
    feed = SimpleNamespace(
        get_session_state=lambda: {"request_counts": {}},
        query_positions_result=Mock(return_value=result),
    )
    api = _api(feed)
    api._execution_session = SimpleNamespace(config={"market_data_only": True})
    assert api.query_ctp_result(VENUE, "positions", timeout=0) is result
    feed.query_positions_result.assert_called_once_with(timeout=0)


@pytest.mark.parametrize("method", ["state", "query", "confirm", "verify"])
def test_ctp_surface_rejects_zmq(method) -> None:
    api = _api(SimpleNamespace(), transport=TransportMode.ZMQ)
    with pytest.raises(CapabilityNotSupportedError):
        if method == "state":
            api.get_ctp_session_state(VENUE)
        elif method == "query":
            api.query_ctp_result(VENUE, "account")
        elif method == "confirm":
            api.confirm_ctp_settlement(VENUE)
        else:
            api.verify_ctp_settlement(VENUE)


def test_ctp_surface_rejects_non_ctp_and_unknown_query() -> None:
    api = _api(SimpleNamespace())
    with pytest.raises(CapabilityNotSupportedError):
        api.get_ctp_session_state("OKX___SWAP")
    with pytest.raises(CapabilityNotSupportedError):
        api.query_ctp_result("OKX___SWAP", "account")
    with pytest.raises(ValueError, match="unknown CTP query_type"):
        api.query_ctp_result(VENUE, "typo")


def test_explicit_settlement_actions_use_only_the_bounded_feed_methods() -> None:
    confirmed = Mock(return_value=True)
    verified = Mock(return_value="proof")
    feed = SimpleNamespace(
        confirm_settlement=confirmed,
        verify_settlement_confirmation=verified,
    )
    api = _api(feed, execution=False)
    assert api.confirm_ctp_settlement(VENUE, timeout=3) is True
    assert api.verify_ctp_settlement(VENUE, timeout=4) == "proof"
    confirmed.assert_called_once_with(timeout=3)
    verified.assert_called_once_with(timeout=4)


def test_managed_settlement_confirmation_injects_sdk_capability() -> None:
    capability = object()
    confirmed = Mock(return_value=True)
    feed = SimpleNamespace(
        confirm_settlement=confirmed,
        get_execution_gate_state=lambda: {"managed": True, "armed": False},
    )
    api = _api(feed)
    api._ctp_execution_capability = capability

    assert api.confirm_ctp_settlement(VENUE, timeout=3) is True
    confirmed.assert_called_once_with(
        timeout=3,
        _execution_capability=capability,
    )


@pytest.mark.parametrize("failure", ["missing_capability", "armed"])
def test_managed_settlement_confirmation_fails_before_native_write(failure) -> None:
    confirmed = Mock(return_value=True)
    feed = SimpleNamespace(
        confirm_settlement=confirmed,
        get_execution_gate_state=lambda: {
            "managed": True,
            "armed": failure == "armed",
        },
    )
    api = _api(feed)
    api._ctp_execution_capability = None if failure == "missing_capability" else object()

    with pytest.raises(NormalizedApiError):
        api.confirm_ctp_settlement(VENUE)
    confirmed.assert_not_called()


def test_settlement_confirmation_query_runs_bounded_session_verification() -> None:
    proof = object()
    verify = Mock(return_value=proof)
    api = _api(SimpleNamespace(verify_settlement_confirmation=verify))
    assert api.query_ctp_result(VENUE, "settlement_confirmation", timeout=2) is proof
    verify.assert_called_once_with(timeout=2)


def test_quote_v2_fields_survive_top_level_event_normalization() -> None:
    event = normalize_event(
        {
            "kind": "tick",
            "symbol": "IF2506.CFFEX",
            "price": 4000,
            "bid_price": 3999.8,
            "ask_price": 4000.2,
            "volume": 7,
            "schema_version": "ctp.quote.v2",
            "volume_semantics": "delta",
            "cum_volume": 107,
            "delta_volume": 7,
            "volume_complete": True,
            "volume_quality": "CONTINUOUS",
            "trading_day": "20260909",
            "action_day": "20260909",
            "event_time_utc": datetime(2026, 9, 9, 1, 30, 2, tzinfo=UTC),
            "recv_time_utc": datetime(2026, 9, 9, 1, 30, 2, 10000, tzinfo=UTC),
            "recv_monotonic_ns": 123,
            "connection_generation": 3,
            "ingest_seq": 8,
            "quality_flags": [],
            "event_time_source": "action_day",
        },
        VENUE,
    )
    assert event["schema_version"] == "ctp.quote.v2"
    assert event["volume_semantics"] == "delta"
    assert event["volume"] == Decimal("7")
    assert event["cum_volume"] == Decimal("107")
    assert event["delta_volume"] == Decimal("7")
    assert event["volume_complete"] is True
    assert event["trading_day"] == event["action_day"] == "20260909"
    assert event["connection_generation"] == 3
    assert event["ingest_seq"] == 8
    assert event["received_monotonic_ns"] == 123


def test_btapi_poll_event_preserves_quote_v2_contract() -> None:
    source = queue.Queue()
    source.put(
        {
            "kind": "tick",
            "symbol": "SA601.CZCE",
            "price": 1500,
            "volume": 3,
            "schema_version": "ctp.quote.v2",
            "volume_semantics": "delta",
            "cum_volume": 20,
            "delta_volume": 3,
            "volume_complete": True,
            "volume_quality": "CONTINUOUS",
            "action_day": "20260909",
            "trading_day": "20260909",
            "event_time_utc": datetime(2026, 9, 9, 1, 0, tzinfo=UTC),
            "recv_time_utc": datetime(2026, 9, 9, 1, 0, 0, 1000, tzinfo=UTC),
            "recv_monotonic_ns": 99,
            "connection_generation": 4,
            "ingest_seq": 10,
        }
    )
    api = object.__new__(BtApi)
    api.transport_mode = TransportMode.DIRECT
    api.data_queues = {VENUE: source}
    api._normalized_event_pending = {VENUE: deque()}
    api._event_metrics = {"raw_ingress_items": 0, "normalized_events": 0}

    event = api._poll_event_raw(VENUE)
    assert event is not None
    assert event["schema_version"] == "ctp.quote.v2"
    assert event["volume"] == event["delta_volume"] == Decimal("3")
    assert event["cum_volume"] == Decimal("20")
    assert event["connection_generation"] == 4
    assert event["ingest_seq"] == 10


def test_ctp_instrument_rules_bridge_to_strict_public_instrument_spec() -> None:
    raw = {
        "symbol": "IF2506",
        "asset_type": "future",
        "base_currency": "IF",
        "quote_currency": "CNY",
        "contract_type": "future",
        "linear": True,
        "contract_value": 300,
        "contract_multiplier": 1,
        "price_tick": 0.2,
        "quantity_step": 1,
        "min_quantity": 1,
        "max_quantity": 100,
        "quantity_unit": "lots",
        "status": "trading",
        "metadata_complete": True,
        "evidence_complete": True,
    }
    result = instrument_spec(raw, VENUE, "IF2506")
    assert isinstance(result, InstrumentSpec) and result.available
    assert result.price_tick == Decimal("0.2")
    assert result.quantity_unit == "lots"
    assert result.native_to_base_quantity(Decimal("2")) == Decimal("600")


def test_ctp_instrument_bridge_rejects_metadata_without_terminal_evidence() -> None:
    raw = {
        "symbol": "IF2506",
        "contract_value": 300,
        "contract_multiplier": 1,
        "price_tick": 0.2,
        "quantity_step": 1,
        "min_quantity": 1,
        "metadata_complete": False,
        "evidence_complete": False,
    }
    with pytest.raises(Exception, match="ctp_metadata_incomplete"):
        instrument_spec(raw, VENUE, "IF2506")


def test_trade_dedup_identity_includes_account_trading_day_and_exchange() -> None:
    session = object.__new__(_ExecutionSession)
    session._ledger_identity = lambda _venue, account_id, _row: {
        "provider": "CTP",
        "environment": "demo",
        "account_id": account_id,
    }
    base = {
        "account_id": "A1",
        "trading_day": "20260909",
        "exchange_id": "CFFEX",
        "symbol": "IF2506",
        "trade_id": "T1",
    }
    first = session._trade_key(VENUE, base)
    assert first != session._trade_key(VENUE, {**base, "account_id": "A2"})
    assert first != session._trade_key(VENUE, {**base, "trading_day": "20260910"})
    assert first != session._trade_key(VENUE, {**base, "exchange_id": "SHFE"})


def test_ctp_order_and_trade_normalization_preserve_account_day_identity() -> None:
    order = normalize_event(
        {
            "kind": "order",
            "InstrumentID": "SA601",
            "OrderSysID": "SYS1",
            "OrderRef": "REF1",
            "OrderStatus": "0",
            "VolumeTotalOriginal": 1,
            "VolumeTraded": 1,
            "ExchangeID": "CZCE",
            "InvestorID": "ACCOUNT1",
            "TradingDay": "20260909",
        },
        VENUE,
    )
    trade = normalize_event(
        {
            "kind": "trade",
            "InstrumentID": "SA601",
            "TradeID": "T1",
            "OrderSysID": "SYS1",
            "Volume": 1,
            "Price": 1500,
            "ExchangeID": "CZCE",
            "InvestorID": "ACCOUNT1",
            "TradingDay": "20260909",
            "trade_fee_verified": False,
            "fee_unresolved": True,
        },
        VENUE,
    )
    for event in (order, trade):
        assert event["account_id"] == "ACCOUNT1"
        assert event["trading_day"] == "20260909"
        assert event["exchange_id"] == "CZCE"
    assert trade["fee"] is None and trade["fee_unresolved"] is True
