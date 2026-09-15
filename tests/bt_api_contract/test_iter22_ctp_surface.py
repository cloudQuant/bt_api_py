"""Offline contract tests for the controlled top-level CTP surface."""

from __future__ import annotations

import queue
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from bt_api_py import BtApi, InstrumentSpec, NormalizedApiError, TransportMode
from bt_api_py._contracts import CapabilityNotSupportedError
from bt_api_py._execution_session import _ExecutionSession
from bt_api_py._normalization import _timestamps, instrument_spec, normalize_event

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
            "execution_gate_scope_version": "ctp-contract-bundle-v1",
            "execution_gate_authorized_instruments": [
                "CZCE.SA701",
                "CZCE.SA701C1080",
                "CZCE.SA701P1080",
            ],
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
    assert state["execution_gate_scope_version"] == "ctp-contract-bundle-v1"
    assert state["execution_gate_authorized_instruments"] == [
        "CZCE.SA701",
        "CZCE.SA701C1080",
        "CZCE.SA701P1080",
    ]
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


@pytest.mark.parametrize(
    "query_type,method_name,kwargs",
    [
        ("depth_market_data", "query_depth_market_data_result", {"timeout": 30}),
        (
            "option_trade_cost",
            "query_option_instrument_trade_cost_result",
            {
                "instrument_id": "SA701C1200",
                "input_price": 60,
                "underlying_price": 1200,
            },
        ),
        (
            "option_commission_rate",
            "query_option_instrument_commission_rate_result",
            {"instrument_id": "SA701C1200", "exchange_id": "CZCE"},
        ),
    ],
)
def test_ctp_discovery_queries_preserve_completion_proof_and_price_inputs(
    query_type, method_name, kwargs
) -> None:
    proof = object()
    method = Mock(return_value=proof)
    api = _api(SimpleNamespace(**{method_name: method}))
    api._execution_session = SimpleNamespace(config={"market_data_only": True})

    assert api.query_ctp_result(VENUE, query_type, **kwargs) is proof
    method.assert_called_once_with(**kwargs)

    with pytest.raises(CapabilityNotSupportedError, match=method_name):
        _api(SimpleNamespace()).query_ctp_result(VENUE, query_type, **kwargs)


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
    with pytest.raises(NormalizedApiError) as raised:
        api.confirm_ctp_settlement(VENUE, timeout=3)
    assert raised.value.code == "ctp_settlement_authorization_required"
    assert api.verify_ctp_settlement(VENUE, timeout=4) == "proof"
    confirmed.assert_not_called()
    verified.assert_called_once_with(timeout=4)


def test_public_managed_settlement_confirmation_cannot_inject_sdk_capability() -> None:
    capability = object()
    confirmed = Mock(return_value=True)
    feed = SimpleNamespace(
        confirm_settlement=confirmed,
        get_execution_gate_state=lambda: {"managed": True, "armed": False},
    )
    api = _api(feed)
    api._ctp_execution_capability = capability

    with pytest.raises(NormalizedApiError) as raised:
        api.confirm_ctp_settlement(VENUE, timeout=3)
    assert raised.value.code == "ctp_settlement_authorization_required"
    confirmed.assert_not_called()


def test_market_data_only_settlement_is_rejected_before_feed_call() -> None:
    confirmed = Mock(return_value=True)
    api = _api(SimpleNamespace(confirm_settlement=confirmed))
    api._execution_session = SimpleNamespace(config={"market_data_only": True})

    with pytest.raises(NormalizedApiError) as raised:
        api.confirm_ctp_settlement(VENUE)
    assert raised.value.code == "ctp_settlement_authorization_required"
    confirmed.assert_not_called()


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


@pytest.mark.parametrize("value", (True, "true", "yes", "1"))
def test_ctp_auto_settlement_confirmation_is_rejected_before_feed_creation(
    monkeypatch, value
) -> None:
    create_feed = Mock()
    monkeypatch.setattr(
        "bt_api_py.bt_api.ExchangeRegistry.create_feed",
        create_feed,
    )
    api = BtApi(debug=False)

    with pytest.raises(NormalizedApiError) as error:
        api.add_exchange(VENUE, {"auto_settlement_confirm": value})

    assert error.value.code == "ctp_auto_settlement_confirm_enabled"
    assert error.value.definite_reject is True
    create_feed.assert_not_called()
    assert VENUE not in api.data_queues


def test_configure_execution_rejects_unsafe_ctp_setting_before_session_setup(
    tmp_path,
) -> None:
    api = BtApi(debug=False)

    with pytest.raises(NormalizedApiError) as error:
        api.configure_execution(
            {
                "order_journal": tmp_path / "orders.jsonl",
                "account_ids": {VENUE: "acct"},
                "required_environments": {VENUE: "demo"},
            },
            _exchange_names={VENUE: {"auto_settlement_confirm": "on"}},
        )

    assert error.value.code == "ctp_auto_settlement_confirm_enabled"
    assert error.value.definite_reject is True
    assert api._execution_session is None


def test_ctp_settings_pin_read_only_default_without_mutating_other_providers(
    monkeypatch,
) -> None:
    captured: dict[str, dict] = {}

    def create_feed(exchange_name, _data_queue, **kwargs):
        captured[exchange_name] = dict(kwargs)
        return SimpleNamespace(disconnect=lambda: None)

    monkeypatch.setattr(
        "bt_api_py.bt_api.ExchangeRegistry.create_feed",
        create_feed,
    )
    api = BtApi(debug=False)
    api.add_exchange(VENUE, {})
    api.add_exchange("COVERAGE___SPOT", {"auto_settlement_confirm": True})

    assert captured[VENUE]["auto_settlement_confirm"] is False
    assert api.exchange_kwargs[VENUE]["auto_settlement_confirm"] is False
    assert captured["COVERAGE___SPOT"]["auto_settlement_confirm"] is True


def _complete_ctp_quote_v2_payload(**overrides) -> dict:
    payload = {
        "kind": "tick",
        "symbol": "IF2506.CFFEX",
        "asset_type": "option",
        "product_class": "2",
        "contract_type": "option",
        "option_type": "call",
        "underlying_instrument": "IF2506",
        "strike_price": 4000,
        "price": 4000,
        "bid_price": 3999.8,
        "ask_price": 4000.2,
        "bid_volume": 2,
        "ask_volume": 3,
        "volume": 7,
        "schema_version": "ctp.quote.v2",
        "volume_semantics": "delta",
        "cum_volume": 107,
        "delta_volume": 7,
        "volume_complete": True,
        "volume_quality": "CONTINUOUS",
        "continuity_status": "continuous",
        "lower_limit_price": 3000,
        "upper_limit_price": 5000,
        "trading_day": "20260909",
        "action_day": "20260909",
        "event_time_utc": datetime(2026, 9, 9, 1, 30, 2, tzinfo=UTC),
        "recv_time_utc": datetime(2026, 9, 9, 1, 30, 2, 10000, tzinfo=UTC),
        "recv_monotonic_ns": 123,
        "connection_generation": 3,
        "ingest_seq": 8,
        "subscription_epoch": 2,
        "rules_hash": "rules-sha256",
        "clock_domain_id": "ctp-md-clock-a",
        "source": "ctp.native.md",
        "quality_flags": [],
        "event_time_source": "action_day",
        "source_clock_quality": "verified",
        "receive_clock_quality": "verified",
        "source_clock_error_ms": 1,
        "receive_clock_error_ms": 1,
        "freshness_verified": True,
        "execution_eligible": True,
    }
    payload.update(overrides)
    return payload


@dataclass
class _AdapterShapedQuoteV2:
    """A non-native transport object with a payload it wants to self-attest."""

    payload: dict


def _native_ctp_quote_v2(payload: dict):
    """Build the actual CTP ticker container used by direct market streams."""

    from bt_api_ctp.containers.ctp.ctp_ticker import CtpTickerData

    ticker_info = {
        "kind": "tick",
        "InstrumentID": payload["symbol"],
        "LastPrice": payload["price"],
        "BidPrice1": payload["bid_price"],
        "AskPrice1": payload["ask_price"],
        "BidVolume1": payload["bid_volume"],
        "AskVolume1": payload["ask_volume"],
        "Volume": payload["cum_volume"],
        "LowerLimitPrice": payload["lower_limit_price"],
        "UpperLimitPrice": payload["upper_limit_price"],
        "TradingDay": payload["trading_day"],
        "ActionDay": payload["action_day"],
        "ExchangeID": "CFFEX",
        "UpdateTime": "09:30:02",
        "UpdateMillisec": 0,
        "continuity_status": payload["continuity_status"],
        "subscription_epoch": payload["subscription_epoch"],
        "rules_hash": payload["rules_hash"],
        "clock_domain_id": payload["clock_domain_id"],
        "source": payload["source"],
        "source_clock_quality": payload["source_clock_quality"],
        "receive_clock_quality": payload["receive_clock_quality"],
        "source_clock_error_ms": payload["source_clock_error_ms"],
        "receive_clock_error_ms": payload["receive_clock_error_ms"],
        "freshness_verified": payload["freshness_verified"],
        "ProductClass": payload["product_class"],
        "ContractType": payload["contract_type"],
        "OptionsType": payload["option_type"],
        "UnderlyingInstrID": payload["underlying_instrument"],
        "StrikePrice": payload["strike_price"],
        "execution_eligible": payload["execution_eligible"],
        "stale": payload.get("stale", False),
    }
    native = CtpTickerData(
        ticker_info,
        payload["symbol"],
        payload["asset_type"],
        True,
        connection_generation=payload["connection_generation"],
        ingest_seq=payload["ingest_seq"],
        recv_time_utc=payload["recv_time_utc"],
        recv_monotonic_ns=payload["recv_monotonic_ns"],
    )
    # Keep this parent-contract fixture compatible with the installed CTP
    # package while also exercising the source checkout's expanded container.
    for field in (
        "subscription_epoch",
        "rules_hash",
        "clock_domain_id",
        "source",
        "source_clock_quality",
        "receive_clock_quality",
        "source_clock_error_ms",
        "receive_clock_error_ms",
        "freshness_verified",
        "product_class",
        "contract_type",
        "option_type",
        "underlying_instrument",
        "strike_price",
    ):
        setattr(native, field, payload[field])
    native.init_data()
    native.apply_volume_delta(
        payload["delta_volume"],
        complete=payload["volume_complete"],
        quality=payload["volume_quality"],
    )
    native.resolve_event_time()
    return native


def _parent_attested_ctp_api(
    source: queue.Queue,
    payload: dict,
    *,
    session: dict | None = None,
    stream_epoch: int | None = None,
    metadata: dict | None = None,
) -> BtApi:
    """Create the parent-owned stream/session records required by V2 ingress."""

    stream_epoch = payload["subscription_epoch"] if stream_epoch is None else stream_epoch
    session = {"read_only_ready": True, "connection_generation": 9} if session is None else session
    metadata = (
        {
            "subscription_epoch": stream_epoch,
            "source": payload["source"],
            "rules_hash": payload["rules_hash"],
            "clock_domain_id": payload["clock_domain_id"],
            # These are parent registration values.  The V2 payload is never
            # used to populate its cohort-now envelope.
            "receive_clock_error_ms": 0.0,
            "receive_clock_quality": "verified",
            "freshness_verified": True,
        }
        if metadata is None
        else metadata
    )
    api = object.__new__(BtApi)
    api.transport_mode = TransportMode.DIRECT
    api.data_queues = {VENUE: source}
    api._ctp_market_ingress_seal = object()
    api._ctp_market_ingress_queues = {}
    market_queue = api._ctp_market_stream_ingress_queue(VENUE, source)
    api.exchange_feeds = {VENUE: SimpleNamespace(get_session_state=lambda: dict(session))}
    api._subscription_streams = [
        SimpleNamespace(
            stream_name="ctp_market_stream",
            data_queue=market_queue,
            _running=True,
            state=SimpleNamespace(value="authenticated"),
            _connection_generation=payload["connection_generation"],
            _subscription_epoch=stream_epoch,
            _quote_v2_subscription_metadata={payload["symbol"]: metadata},
        )
    ]
    api._normalized_event_pending = {VENUE: deque()}
    api._event_metrics = {"raw_ingress_items": 0, "normalized_events": 0}
    return api


def _publish_ctp_market_ingress(api: BtApi, item: object) -> None:
    """Exercise the private producer queue used by the actual CTP stream."""

    api._ctp_market_ingress_queues[VENUE].put(item)


def test_quote_v2_unattested_mapping_preserves_fields_but_cannot_self_promote() -> None:
    event = normalize_event(_complete_ctp_quote_v2_payload(), VENUE)
    assert event["schema_version"] == "ctp.quote.v2"
    assert event["volume_semantics"] == "delta"
    assert event["volume"] == Decimal("7")
    assert event["cum_volume"] == Decimal("107")
    assert event["delta_volume"] == Decimal("7")
    assert event["volume_complete"] is True
    assert event["trading_day"] == event["action_day"] == "20260909"
    assert event["connection_generation"] == 3
    assert event["ingest_seq"] == 8
    assert event["subscription_epoch"] == 2
    assert event["asset_type"] == "option"
    assert event["lower_limit_price"] == 3000
    assert event["upper_limit_price"] == 5000
    assert event["bid_volume"] == 2
    assert event["ask_volume"] == 3
    assert event["rules_hash"] == "rules-sha256"
    assert event["clock_domain_id"] == "ctp-md-clock-a"
    assert event["source_clock_quality"] == "verified"
    assert event["receive_clock_quality"] == "verified"
    assert event["source_clock_error_ms"] == 1
    assert event["receive_clock_error_ms"] == 1
    assert event["event_time_source"] == "action_day"
    assert event["source"] == "ctp.native.md"
    assert event["continuity_status"] == "continuous"
    assert event["quality_flags"] == ()
    assert event["freshness_verified"] is True
    assert event["execution_eligible"] is False
    assert event["received_monotonic_ns"] == 123


def test_quote_v2_normalizer_keeps_cpf_identity_untrusted() -> None:
    event = normalize_event(_complete_ctp_quote_v2_payload(), VENUE)

    assert event["product_class"] == "2"
    assert event["contract_type"] == "option"
    assert event["option_type"] == "call"
    assert event["underlying_instrument"] == "IF2506"
    assert event["strike_price"] == Decimal("4000")
    assert event["execution_eligible"] is False


def test_btapi_direct_ctp_native_ingress_rejects_receiptless_native_ticker() -> None:
    payload = _complete_ctp_quote_v2_payload(execution_eligible=False)
    source = queue.Queue[Any]()
    api = _parent_attested_ctp_api(source, payload)
    _publish_ctp_market_ingress(api, _native_ctp_quote_v2(payload))

    event = api._poll_event_raw(VENUE)

    assert event is not None
    assert event["schema_version"] == "ctp.quote.v2"
    assert event["symbol"] == payload["symbol"]
    assert event["connection_generation"] == payload["connection_generation"]
    assert event["subscription_epoch"] == payload["subscription_epoch"]
    # Exact container identity and a sealed queue are insufficient.  A native
    # receipt must be issued by the managed CTP callback itself.
    assert event["execution_eligible"] is False
    assert "cohort_now_monotonic_ns" not in event


def test_ctp_subscribe_rejects_all_favorable_public_quote_metadata(
    monkeypatch,
) -> None:
    payload = _complete_ctp_quote_v2_payload(execution_eligible=False)
    source: queue.Queue[Any] = queue.Queue()
    api = BtApi(debug=False)
    api.data_queues[VENUE] = source
    public_metadata = {
        "source": payload["source"],
        "rules_hash": payload["rules_hash"],
        "clock_domain_id": payload["clock_domain_id"],
        "source_clock_quality": "verified",
        "receive_clock_quality": "verified",
        "source_clock_error_ms": 0.0,
        "receive_clock_error_ms": 0.0,
        "freshness_verified": True,
    }
    api.exchange_kwargs[VENUE] = {"quote_v2_metadata": dict(public_metadata)}
    api.exchange_feeds[VENUE] = SimpleNamespace(
        get_session_state=lambda: {
            "read_only_ready": True,
            "connection_generation": 9,
        }
    )
    captured: dict[str, object] = {}

    def subscribe_handler(data_queue, params, topics, owner):
        captured["data_queue"] = data_queue
        captured["params"] = params
        captured["topics"] = topics
        metadata = dict(params["quote_v2_metadata"])
        metadata.update(topics[0]["quote_v2"])
        metadata["subscription_epoch"] = payload["subscription_epoch"]
        owner._subscription_streams.append(
            SimpleNamespace(
                stream_name="ctp_market_stream",
                data_queue=data_queue,
                _running=True,
                state=SimpleNamespace(value="authenticated"),
                _connection_generation=payload["connection_generation"],
                _subscription_epoch=payload["subscription_epoch"],
                _quote_v2_subscription_metadata={payload["symbol"]: metadata},
            )
        )

    monkeypatch.setattr(
        "bt_api_py.bt_api.ExchangeRegistry.get_stream_class",
        lambda _exchange_name, stream_type: (
            subscribe_handler if stream_type == "subscribe" else None
        ),
    )

    api.subscribe(
        f"CTP___FUTURE___{payload['symbol']}",
        [
            {
                "topic": "tick",
                "symbol": payload["symbol"],
                "quote_v2": dict(public_metadata),
            }
        ],
    )
    producer = cast("Any", captured["data_queue"])
    assert producer is not source
    assert captured["params"]["quote_v2_metadata"] == public_metadata
    assert captured["topics"][0]["quote_v2"] == public_metadata
    producer.put(_native_ctp_quote_v2(payload))

    event = api._poll_event_raw(VENUE)

    assert event is not None
    # A custom subscribe handler can receive the private queue, but it cannot
    # use public metadata to manufacture the native managed receipt.
    assert event["execution_eligible"] is False
    assert "cohort_now_monotonic_ns" not in event


def test_btapi_registered_ctp_queue_rejects_forged_mapping() -> None:
    payload = _complete_ctp_quote_v2_payload(
        cohort_now_monotonic_ns=1,
        cohort_now_epoch=1,
        cohort_now_clock_domain_id="forged-clock-domain",
        cohort_now_receive_clock_error_ms=0,
        cohort_now_receive_clock_quality="verified",
        cohort_now_freshness_verified=True,
    )
    source: queue.Queue[Any] = queue.Queue()
    source.put(dict(payload))

    event = _parent_attested_ctp_api(source, payload)._poll_event_raw(VENUE)

    assert event is not None
    assert event["last_price"] == 4000
    assert event["execution_eligible"] is False
    assert "cohort_now_monotonic_ns" not in event
    assert "cohort_now_epoch" not in event
    assert "cohort_now_clock_domain_id" not in event


def test_btapi_parent_attestation_rejects_forged_native_ticker_type() -> None:
    payload = _complete_ctp_quote_v2_payload(
        cohort_now_monotonic_ns=1,
        cohort_now_epoch=1,
        cohort_now_clock_domain_id="forged-clock-domain",
        cohort_now_receive_clock_error_ms=0,
        cohort_now_receive_clock_quality="verified",
        cohort_now_freshness_verified=True,
    )

    forged_type = type(
        "CtpTickerData",
        (),
        {
            "__module__": "bt_api_ctp.containers.ctp.ctp_ticker",
            "get_all_data": lambda _self: dict(payload),
        },
    )
    source: queue.Queue[Any] = queue.Queue()
    api = _parent_attested_ctp_api(source, payload)
    _publish_ctp_market_ingress(api, forged_type())

    event = api._poll_event_raw(VENUE)

    assert event is not None
    assert event["execution_eligible"] is False
    assert "cohort_now_monotonic_ns" not in event
    assert "cohort_now_epoch" not in event
    assert "cohort_now_clock_domain_id" not in event


def test_public_ctp_queue_cannot_attest_a_manually_constructed_native_ticker() -> None:
    payload = _complete_ctp_quote_v2_payload()
    source: queue.Queue[Any] = queue.Queue()
    api = _parent_attested_ctp_api(source, payload)

    public_queue = api.get_data_queue(VENUE)
    assert public_queue is not source
    with pytest.raises(CapabilityNotSupportedError) as error:
        public_queue.put(_native_ctp_quote_v2(payload))

    assert error.value.operation == "put_ticker"
    assert error.value.definite_reject is True
    assert source.empty()

    # Even an unsealed native item in the raw queue cannot receive a parent
    # attestation; only the private market-producer queue issues that proof.
    source.put(_native_ctp_quote_v2(payload))

    event = api._poll_event_raw(VENUE)

    assert event is not None
    assert event["execution_eligible"] is False
    assert "cohort_now_monotonic_ns" not in event


def test_btapi_put_ticker_rejects_ctp_queue_injection_before_dispatch() -> None:
    api = BtApi(debug=False)
    source: queue.Queue[Any] = queue.Queue()
    delivered: list[object] = []
    api.data_queues[VENUE] = source
    api.event_bus.on("ticker", delivered.append)

    with pytest.raises(CapabilityNotSupportedError) as error:
        api.put_ticker({"price": 4000}, VENUE)

    assert error.value.operation == "put_ticker"
    assert error.value.definite_reject is True
    assert source.empty()
    assert delivered == []


@pytest.mark.parametrize("field", ("source", "rules_hash", "clock_domain_id"))
def test_btapi_parent_attestation_rejects_unknown_v2_provenance(field: str) -> None:
    payload = _complete_ctp_quote_v2_payload(**{field: "unknown"})
    source: queue.Queue[Any] = queue.Queue()
    api = _parent_attested_ctp_api(source, payload)
    _publish_ctp_market_ingress(api, _native_ctp_quote_v2(payload))

    event = api._poll_event_raw(VENUE)

    assert event is not None
    assert event[field] == "unknown"
    assert event["execution_eligible"] is False
    assert "cohort_now_monotonic_ns" not in event


def test_btapi_parent_attestation_rejects_unready_session_or_stale_epoch() -> None:
    payload = _complete_ctp_quote_v2_payload()
    source: queue.Queue[Any] = queue.Queue()
    api = _parent_attested_ctp_api(
        source,
        payload,
        session={"read_only_ready": False, "connection_generation": 9},
    )
    _publish_ctp_market_ingress(api, _native_ctp_quote_v2(payload))
    event = api._poll_event_raw(VENUE)
    assert event is not None
    assert event["execution_eligible"] is False
    assert "cohort_now_monotonic_ns" not in event

    source = queue.Queue[Any]()
    api = _parent_attested_ctp_api(
        source,
        payload,
        stream_epoch=payload["subscription_epoch"] + 1,
    )
    _publish_ctp_market_ingress(api, _native_ctp_quote_v2(payload))
    event = api._poll_event_raw(VENUE)
    assert event is not None
    assert event["execution_eligible"] is False
    assert "cohort_now_monotonic_ns" not in event


def test_btapi_parent_attestation_rejects_adapter_shaped_unknown_payload() -> None:
    payload = _complete_ctp_quote_v2_payload(
        source="unknown",
        rules_hash="unknown",
        clock_domain_id="unknown",
    )
    source: queue.Queue[Any] = queue.Queue()
    source.put(_AdapterShapedQuoteV2(payload))

    event = _parent_attested_ctp_api(source, payload)._poll_event_raw(VENUE)

    assert event is not None
    assert event["source"] == "unknown"
    assert event["rules_hash"] == "unknown"
    assert event["clock_domain_id"] == "unknown"
    assert event["execution_eligible"] is False
    assert "cohort_now_monotonic_ns" not in event


def test_btapi_parent_attestation_omits_cohort_now_for_stale_native_quote() -> None:
    payload = _complete_ctp_quote_v2_payload(stale=True)
    source: queue.Queue[Any] = queue.Queue()
    api = _parent_attested_ctp_api(source, payload)
    _publish_ctp_market_ingress(api, _native_ctp_quote_v2(payload))

    event = api._poll_event_raw(VENUE)

    assert event is not None
    assert event["stale"] is True
    assert event["execution_eligible"] is False
    assert "cohort_now_monotonic_ns" not in event


def test_quote_v2_normalizer_never_upgrades_missing_or_bad_evidence() -> None:
    missing = normalize_event(
        {
            "kind": "tick",
            "symbol": "SA701C1080",
            "schema_version": "ctp.quote.v2",
            "execution_eligible": True,
        },
        VENUE,
    )
    outside_limit = normalize_event(
        {
            "kind": "tick",
            "symbol": "SA701C1080",
            "asset_type": "option",
            "price": 101,
            "bid_price": 59,
            "ask_price": 61,
            "bid_volume": 2,
            "ask_volume": 3,
            "schema_version": "ctp.quote.v2",
            "volume_semantics": "delta",
            "cum_volume": 107,
            "delta_volume": 7,
            "volume_complete": True,
            "volume_quality": "CONTINUOUS",
            "continuity_status": "continuous",
            "lower_limit_price": 1,
            "upper_limit_price": 100,
            "trading_day": "20260909",
            "action_day": "20260909",
            "event_time_utc": datetime(2026, 9, 9, 1, 30, 1, tzinfo=UTC),
            "recv_time_utc": datetime(2026, 9, 9, 1, 30, 2, tzinfo=UTC),
            "recv_monotonic_ns": 123,
            "connection_generation": 3,
            "ingest_seq": 8,
            "subscription_epoch": 2,
            "rules_hash": "rules-sha256",
            "clock_domain_id": "ctp-md-clock-a",
            "source": "ctp.native.md",
            "quality_flags": [],
            "event_time_source": "action_day",
            "source_clock_quality": "verified",
            "receive_clock_quality": "verified",
            "source_clock_error_ms": 1,
            "receive_clock_error_ms": 1,
            "freshness_verified": True,
            "execution_eligible": True,
        },
        VENUE,
    )

    assert missing["asset_type"] == "unknown"
    assert missing["clock_domain_id"] == ""
    assert missing["source"] == "unknown"
    assert missing["received_wall_time"] is None
    assert missing["received_monotonic_ns"] == 0
    assert missing["execution_eligible"] is False
    assert outside_limit["execution_eligible"] is False
    assert outside_limit["last_price"] == 101


def test_quote_v2_timezone_free_timestamps_stay_unverified() -> None:
    timestamps = _timestamps(
        {
            "schema_version": "ctp.quote.v2",
            "event_time_utc": "2026-09-09T01:30:01",
            "recv_time_utc": "2026-09-09T01:30:02",
        }
    )

    assert timestamps["exchange_time"] is None
    assert timestamps["received_wall_time"] is None


def test_btapi_poll_event_preserves_quote_v2_contract() -> None:
    source: queue.Queue[Any] = queue.Queue()
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
    assert event["execution_eligible"] is False


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
