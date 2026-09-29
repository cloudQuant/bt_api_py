"""Offline-only contracts for the SDK-owned SimNow set1 adapter."""

from __future__ import annotations

import queue
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bt_api_py import (
    CancelOrderRequest,
    CtpSimNowExecutionAdapter,
    CtpSimNowExecutionError,
    CtpSimNowOrderIdentity,
    CtpSimNowOrderRequest,
    build_ctp_simnow_cancel_request,
    map_ctp_simnow_cancel_result,
    map_ctp_simnow_order_result,
)
from bt_api_py._contracts.models import Side
from bt_api_py._execution_session import _ExecutionSession
from bt_api_py._normalization import normalize_result
from bt_api_py._venue_mappers.ctp import map_order_request

ACCOUNT = "acct_0123456789abcdef"
TRADING_DAY = "20260923"
INSTRUMENT = "rb2701"
EXCHANGE_ID = "SHFE"
CLIENT_ORDER_ID = "000000001234"


class FakeBtApi:
    def __init__(self, *, profile="set1_group1", environment="demo"):
        self.profile = profile
        self.environment = environment
        self.account_fingerprint = ACCOUNT
        self.account_id = "ctp-account-alias"
        self.trading_day = TRADING_DAY
        self.connection_generation = 8
        self.calls = []
        self.runtime_order_bindings = {}
        self.query_result = {"status": "accepted", "client_order_id": CLIENT_ORDER_ID}
        self.orders_result = SimpleNamespace(
            request_type="orders",
            account_fingerprint=ACCOUNT[5:],
            connection_generation=8,
            complete=True,
            records=(
                {
                    "InstrumentID": INSTRUMENT,
                    "ExchangeID": EXCHANGE_ID,
                    "OrderRef": CLIENT_ORDER_ID,
                    "OrderSysID": "sys-88",
                    "FrontID": 17,
                    "SessionID": 19,
                    "TradingDay": TRADING_DAY,
                    "OrderStatus": "a",
                    "VolumeTotalOriginal": 2,
                    "VolumeTraded": 0,
                },
            ),
        )

    def get_environment_info(self, _exchange_name):
        return {
            "verified": True,
            "environment": self.environment,
            "transport_mode": "direct",
        }

    def get_ctp_session_state(self, _exchange_name):
        return {
            "environment_profile": self.profile,
            "read_only_ready": True,
            "auto_settlement_confirm": False,
            "account_fingerprint": self.account_fingerprint,
            "trading_day": self.trading_day,
            "connection_generation": self.connection_generation,
        }

    def get_execution_identity(self, _exchange_name):
        return {
            "mode": "direct",
            "account_fingerprint": self.account_fingerprint,
            "account_id": self.account_id,
        }

    def get_runtime_order_bindings(
        self, _exchange_name, *, unresolved_only=True, runtime_order_id=None
    ):
        self.calls.append(("get_runtime_order_bindings", runtime_order_id))
        row = self.runtime_order_bindings.get(runtime_order_id)
        if row is None or (unresolved_only and row.get("status") != "unresolved"):
            return []
        return [dict(row)]

    def new_runtime_order_binding(
        self,
        _exchange_name,
        *,
        symbol,
        account_id,
        runtime_order_id,
        managed_intent_id,
        budget_capability=None,
    ):
        self.calls.append(
            (
                "new_runtime_order_binding",
                runtime_order_id,
                managed_intent_id,
                budget_capability,
            )
        )
        if runtime_order_id in self.runtime_order_bindings:
            raise AssertionError("adapter must not replace an existing reservation")
        if any(
            row.get("managed_intent_id") == managed_intent_id
            for row in self.runtime_order_bindings.values()
        ):
            raise RuntimeError("managed intent already belongs to another runtime order")
        row = {
            "runtime_order_id": runtime_order_id,
            "managed_intent_id": managed_intent_id,
            "client_order_id": "000000009876",
            "ctp_order_ref": "000000009876",
            "symbol": symbol,
            "account_id": account_id,
            "connection_generation": self.connection_generation,
            "trading_day": self.trading_day,
            "status": "reserved",
        }
        self.runtime_order_bindings[runtime_order_id] = row
        return dict(row)

    def query_order(self, _exchange_name, request, *, normalized=False):
        self.calls.append(("query_order", request, normalized))
        return self.query_result

    def query_ctp_result(self, _exchange_name, query_type):
        self.calls.append(("query_ctp_result", query_type))
        return self.orders_result

    def redeem_ctp_execution_approval(self, *_args, **_kwargs):
        self.calls.append(("redeem",))
        raise AssertionError("adapter must not issue approval without private binding")

    def arm_execution_from_approval(self, *_args, **_kwargs):
        self.calls.append(("arm",))
        raise AssertionError("adapter must not arm without private binding")

    def make_order(self, *_args, **_kwargs):
        self.calls.append(("make_order",))
        raise AssertionError("adapter must not send a native order")

    def cancel_order(self, *_args, **_kwargs):
        self.calls.append(("cancel_order",))
        raise AssertionError("adapter must not send a native cancel")


def _adapter(api=None, *, profile="set1_group1"):
    return CtpSimNowExecutionAdapter(api or FakeBtApi(), selected_profile=profile)


def _order_identity(runtime_order_id="runtime-order-42"):
    return CtpSimNowOrderIdentity(
        instrument_id=INSTRUMENT,
        exchange_id=EXCHANGE_ID,
        client_order_id=CLIENT_ORDER_ID,
        order_ref=CLIENT_ORDER_ID,
        order_sys_id="sys-88",
        front_id=17,
        session_id=19,
        trading_day=TRADING_DAY,
        runtime_order_id=runtime_order_id,
    )


def _managed_order_request(
    *, runtime_order_id="bt-managed-v1:" + "a" * 64, managed_intent_id="intent-42"
):
    return CtpSimNowOrderRequest(
        client_order_id=None,
        instrument_id=INSTRUMENT,
        exchange_id=EXCHANGE_ID,
        side="buy",
        quantity=Decimal("2"),
        limit_price=Decimal("100"),
        hedge_flag="2",
        runtime_order_id=runtime_order_id,
        managed_intent_id=managed_intent_id,
    )


@pytest.mark.parametrize("profile", ["set1_group1", "set1_group2"])
def test_only_exact_official_set1_profiles_are_accepted(profile):
    adapter = _adapter(FakeBtApi(profile=profile), profile=profile)
    assert adapter.get_execution_identity().profile == profile
    assert "acct_" not in repr(adapter.get_execution_identity())


@pytest.mark.parametrize(
    "profile",
    ["set2_7x24", "set2_7x24_4000x", "set1", "set1_group1_vpn", "custom"],
)
def test_set2_aliases_and_nonofficial_profiles_are_rejected_before_api_calls(profile):
    api = FakeBtApi(profile=profile)
    with pytest.raises(CtpSimNowExecutionError, match="ctp_simnow_set1_profile_required"):
        CtpSimNowExecutionAdapter(api, selected_profile=profile)
    assert api.calls == []


def test_selected_profile_and_verified_demo_scope_are_rechecked():
    with pytest.raises(CtpSimNowExecutionError, match="ctp_simnow_selected_profile_mismatch"):
        _adapter(FakeBtApi(profile="set1_group2"), profile="set1_group1")
    with pytest.raises(CtpSimNowExecutionError, match="ctp_simnow_official_demo_required"):
        _adapter(FakeBtApi(environment="production"))


def test_adapter_is_fixed_to_ctp_future():
    api = FakeBtApi()
    with pytest.raises(CtpSimNowExecutionError, match="ctp_simnow_exchange_scope_invalid"):
        CtpSimNowExecutionAdapter(api, selected_profile="set1_group1", exchange_name="CTP___OPTION")
    assert api.calls == []


@pytest.mark.parametrize("client_order_id", ["1234567890123", "12345678901é", "12\x0034"])
def test_native_order_ref_candidates_over_12_ascii_bytes_are_rejected(client_order_id):
    with pytest.raises(CtpSimNowExecutionError, match="ctp_native_order_ref_mapping_unavailable"):
        CtpSimNowOrderRequest(
            client_order_id=client_order_id,
            instrument_id=INSTRUMENT,
            exchange_id=EXCHANGE_ID,
            side="buy",
            quantity=Decimal("1"),
            limit_price=Decimal("100"),
        )


def test_order_request_preserves_set1_hedge_flag_and_native_identity_mapping():
    adapter = _adapter()
    request = CtpSimNowOrderRequest(
        client_order_id=CLIENT_ORDER_ID,
        instrument_id=INSTRUMENT,
        exchange_id=EXCHANGE_ID,
        side="buy",
        quantity=Decimal("2"),
        limit_price=Decimal("100"),
        hedge_flag="2",
    )
    typed = adapter.build_order_request(request)
    native = map_order_request(typed)
    assert typed.side is Side.BUY
    assert typed.quantity_unit == "lots"
    assert typed.hedge_flag == native["hedge_flag"] == "2"
    mapped = map_ctp_simnow_order_result(
        {
            "client_order_id": CLIENT_ORDER_ID,
            "order_ref": CLIENT_ORDER_ID,
            "order_id": "sys-88",
            "front_id": 17,
            "session_id": 19,
            "instrument_id": INSTRUMENT,
            "exchange_id": EXCHANGE_ID,
            "trading_day": TRADING_DAY,
            "status": "accepted",
        },
        request,
        adapter.get_execution_identity(),
    )
    assert mapped.identity.client_order_id == mapped.identity.order_ref == CLIENT_ORDER_ID
    assert mapped.identity.order_sys_id == "sys-88"
    assert (mapped.identity.front_id, mapped.identity.session_id) == (17, 19)
    assert mapped.status == "ACCEPTED" and not mapped.execution_unknown


def test_managed_order_request_uses_the_durable_ref_and_exposes_port_binding():
    api = FakeBtApi()
    adapter = _adapter(api)
    request = _managed_order_request()
    budget_capability = object()

    typed = adapter.build_order_request(request, budget_capability=budget_capability)
    repeated = adapter.build_order_request(request, budget_capability=budget_capability)

    assert typed.client_order_id == repeated.client_order_id == "000000009876"
    assert len(typed.client_order_id) == 12 and typed.client_order_id.isascii()
    assert typed.client_order_id.isdigit()
    assert typed.runtime_order_id == request.runtime_order_id
    assert typed.managed_intent_id == request.managed_intent_id
    assert typed.hedge_flag == "2"
    assert api.runtime_order_bindings[request.runtime_order_id]["status"] == "reserved"

    resolver_binding = adapter.resolve_runtime_order_binding(request.runtime_order_id, True)
    # The port callback can verify this staged reference but does not commit a
    # BtApi order intent or change the durable row's state.
    assert api.runtime_order_bindings[request.runtime_order_id]["status"] == "reserved"
    assert resolver_binding == {
        "runtime_order_id": request.runtime_order_id,
        "connection_generation": 8,
        "trading_day": TRADING_DAY,
        "ctp_order_ref": "000000009876",
        "client_order_id": "000000009876",
        "managed_intent_id": request.managed_intent_id,
    }

    result_row = {
        "client_order_id": "000000009876",
        "order_ref": "000000009876",
        "order_id": "sys-managed-88",
        "instrument_id": INSTRUMENT,
        "exchange_id": EXCHANGE_ID,
        "trading_day": TRADING_DAY,
        "status": "accepted",
    }
    with pytest.raises(CtpSimNowExecutionError, match="ctp_runtime_order_binding_unavailable"):
        map_ctp_simnow_order_result(result_row, request, adapter.get_execution_identity())

    # This represents BtApi's order-intent journal transition. Managed mapping
    # must recover the OrderRef from that durable unresolved row, not accept one
    # supplied by the caller.
    api.runtime_order_bindings[request.runtime_order_id]["status"] = "unresolved"
    mapped = adapter.map_order_result(result_row, request)
    assert mapped.identity.runtime_order_id == request.runtime_order_id
    assert mapped.identity.managed_intent_id == request.managed_intent_id
    assert mapped.identity.order_ref == typed.client_order_id
    reservations = [call for call in api.calls if call[0] == "new_runtime_order_binding"]
    assert reservations == [
        (
            "new_runtime_order_binding",
            request.runtime_order_id,
            request.managed_intent_id,
            budget_capability,
        )
    ]
    assert not any(call[0] in {"make_order", "cancel_order"} for call in api.calls)


@pytest.mark.parametrize(
    "field,value",
    [
        ("managed_intent_id", "another-intent"),
        ("account_id", "other-account"),
        ("symbol", "cu2701"),
        ("connection_generation", 9),
        ("trading_day", "20260924"),
        ("status", "unresolved"),
        ("ctp_order_ref", "123"),
        ("ctp_order_ref", None),
        ("client_order_id", "000000001111"),
    ],
)
def test_managed_order_request_rejects_foreign_scope_or_state_without_reallocation(field, value):
    api = FakeBtApi()
    request = _managed_order_request()
    api.runtime_order_bindings[request.runtime_order_id] = {
        "runtime_order_id": request.runtime_order_id,
        "managed_intent_id": request.managed_intent_id,
        "client_order_id": "000000009876",
        "ctp_order_ref": "000000009876",
        "symbol": INSTRUMENT,
        "account_id": "ctp-account-alias",
        "connection_generation": 8,
        "trading_day": TRADING_DAY,
        "status": "reserved",
    }
    api.runtime_order_bindings[request.runtime_order_id][field] = value

    with pytest.raises(CtpSimNowExecutionError, match="ctp_runtime_order_binding_scope_mismatch"):
        _adapter(api).build_order_request(request)

    assert not any(call[0] == "new_runtime_order_binding" for call in api.calls)
    assert not any(call[0] in {"make_order", "cancel_order"} for call in api.calls)


def test_managed_runtime_binding_lookup_requires_sdk_order_intent_transition():
    api = FakeBtApi()
    adapter = _adapter(api)
    request = _managed_order_request()
    adapter.build_order_request(request, budget_capability=object())

    with pytest.raises(CtpSimNowExecutionError, match="ctp_runtime_order_binding_scope_mismatch"):
        adapter.resolve_runtime_order_binding(request.runtime_order_id, False)
    with pytest.raises(CtpSimNowExecutionError, match="ctp_runtime_order_binding_scope_mismatch"):
        adapter.resolve_runtime_order_binding(request.runtime_order_id, 1)

    # Only the SDK's ordinary make_order intent journal performs this
    # transition; the read-only port resolver must not synthesize it.
    api.runtime_order_bindings[request.runtime_order_id]["status"] = "unresolved"
    resolved = adapter.resolve_runtime_order_binding(request.runtime_order_id, False)
    assert resolved["managed_intent_id"] == request.managed_intent_id
    assert resolved["ctp_order_ref"] == "000000009876"
    assert resolved["connection_generation"] == 8
    assert resolved["trading_day"] == TRADING_DAY


def test_port_resolver_cannot_reserve_without_typed_intent_context():
    api = FakeBtApi()
    adapter = _adapter(api)
    runtime_order_id = "bt-managed-v1:" + "c" * 64

    with pytest.raises(CtpSimNowExecutionError, match="ctp_runtime_order_binding_unavailable"):
        adapter.resolve_runtime_order_binding(runtime_order_id, True)

    assert api.runtime_order_bindings == {}
    assert not any(call[0] == "new_runtime_order_binding" for call in api.calls)


def test_managed_order_request_rejects_a_reused_intent_under_another_runtime_id():
    api = FakeBtApi()
    adapter = _adapter(api)
    first = _managed_order_request()
    second = _managed_order_request(
        runtime_order_id="bt-managed-v1:" + "b" * 64,
        managed_intent_id=first.managed_intent_id,
    )

    adapter.build_order_request(first, budget_capability=object())
    with pytest.raises(CtpSimNowExecutionError, match="ctp_runtime_order_binding_unavailable"):
        adapter.build_order_request(second, budget_capability=object())

    assert len([call for call in api.calls if call[0] == "new_runtime_order_binding"]) == 2
    assert second.runtime_order_id not in api.runtime_order_bindings
    assert not any(call[0] in {"make_order", "cancel_order"} for call in api.calls)


def test_managed_order_request_rejects_a_recovered_orphan_reservation_state():
    api = FakeBtApi()
    request = _managed_order_request()
    first_adapter = _adapter(api)
    first_adapter.build_order_request(request, budget_capability=object())

    # Model the public getter output after journal replay of a crash between
    # reservation fsync and intent fsync. Actual replay/recovery is covered by
    # the _ExecutionSession journal tests.
    api.runtime_order_bindings[request.runtime_order_id]["status"] = "reservation_only"
    restarted_adapter = _adapter(api)
    with pytest.raises(CtpSimNowExecutionError, match="ctp_runtime_order_binding_scope_mismatch"):
        restarted_adapter.build_order_request(request, budget_capability=object())

    assert len([call for call in api.calls if call[0] == "new_runtime_order_binding"]) == 1
    assert api.runtime_order_bindings[request.runtime_order_id]["status"] == "reservation_only"
    assert not any(call[0] in {"make_order", "cancel_order"} for call in api.calls)


@pytest.mark.parametrize(
    "scope_field,scope_value",
    [
        ("account_id", "other-account"),
        ("trading_day", "20260924"),
        ("connection_generation", 9),
    ],
)
def test_managed_order_request_rejects_binding_from_another_active_scope(scope_field, scope_value):
    api = FakeBtApi()
    request = _managed_order_request()
    api.runtime_order_bindings[request.runtime_order_id] = {
        "runtime_order_id": request.runtime_order_id,
        "managed_intent_id": request.managed_intent_id,
        "client_order_id": "000000009876",
        "ctp_order_ref": "000000009876",
        "symbol": INSTRUMENT,
        "account_id": api.account_id,
        "connection_generation": api.connection_generation,
        "trading_day": api.trading_day,
        "status": "reserved",
    }
    api.runtime_order_bindings[request.runtime_order_id][scope_field] = scope_value

    with pytest.raises(CtpSimNowExecutionError, match="ctp_runtime_order_binding_scope_mismatch"):
        _adapter(api).build_order_request(request)

    assert not any(call[0] == "new_runtime_order_binding" for call in api.calls)
    assert not any(call[0] in {"make_order", "cancel_order"} for call in api.calls)


def test_managed_order_request_cannot_supply_its_own_reference():
    with pytest.raises(CtpSimNowExecutionError, match="ctp_managed_order_ref_must_be_reserved"):
        CtpSimNowOrderRequest(
            client_order_id=CLIENT_ORDER_ID,
            instrument_id=INSTRUMENT,
            exchange_id=EXCHANGE_ID,
            side="buy",
            quantity=Decimal("1"),
            limit_price=Decimal("100"),
            runtime_order_id="bt-managed-v1:" + "a" * 64,
            managed_intent_id="intent-42",
        )


def test_managed_native_submit_remains_closed_without_reserving_or_sending():
    api = FakeBtApi()
    request = _managed_order_request()

    with pytest.raises(
        CtpSimNowExecutionError, match="ctp_execution_credential_binding_unavailable"
    ):
        _adapter(api).submit_order_insert(request)

    assert api.runtime_order_bindings == {}
    assert not any(
        call[0] in {"new_runtime_order_binding", "make_order", "cancel_order"} for call in api.calls
    )


def test_query_order_accepts_typed_normalized_result():
    adapter = _adapter()
    adapter._api.query_result = SimpleNamespace(
        client_order_id=CLIENT_ORDER_ID,
        order_ref=CLIENT_ORDER_ID,
        order_id="sys-typed-89",
        front_id=17,
        session_id=19,
        instrument_id=INSTRUMENT,
        exchange_id=EXCHANGE_ID,
        trading_day=TRADING_DAY,
        status="accepted",
    )

    result = adapter.query_order(_order_identity())

    assert result.status == "ACCEPTED"
    assert not result.execution_unknown
    assert result.identity.order_sys_id == "sys-typed-89"


def test_native_insert_field_receives_selected_set1_hedge_flag(monkeypatch):
    from bt_api_ctp.feeds.live_ctp_feed import CtpRequestDataFuture

    class FakeTrader:
        _req_id = 20
        _front_id = 17
        _session_id = 19

        def __init__(self):
            self.field = None

        def next_order_ref(self):
            return CLIENT_ORDER_ID

        def _next_request_id(self):
            self._req_id += 1
            return self._req_id

        def submit_order_insert(self, field, _request_id, *, execution_capability):
            assert execution_capability is None
            self.field = field
            return 0

    feed = CtpRequestDataFuture(
        queue.Queue(), broker_id="9999", user_id="sim-account", td_front="tcp://offline"
    )
    trader = FakeTrader()
    feed._trader = trader
    monkeypatch.setattr(feed, "_ensure_execution_permitted", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(feed, "_ensure_trading_ready", lambda: None)

    feed.make_order(
        INSTRUMENT,
        volume=1,
        price="100",
        order_type="buy-limit",
        offset="open",
        exchange_id=EXCHANGE_ID,
        client_order_id=CLIENT_ORDER_ID,
        hedge_flag="2",
    )

    assert trader.field is not None
    assert trader.field.CombHedgeFlag == "2"


def test_adapter_write_calls_fail_closed_without_issuing_approval_or_native_calls():
    api = FakeBtApi()
    adapter = _adapter(api)
    request = CtpSimNowOrderRequest(
        client_order_id=CLIENT_ORDER_ID,
        instrument_id=INSTRUMENT,
        exchange_id=EXCHANGE_ID,
        side="buy",
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
    )
    assert adapter.write_admitted is False
    assert adapter.write_blockers == (
        "ctp_execution_credential_binding_unavailable",
        "ctp_native_order_ref_mapping_unavailable",
    )
    with pytest.raises(
        CtpSimNowExecutionError, match="ctp_execution_credential_binding_unavailable"
    ):
        adapter.arm_from_approval(object())
    with pytest.raises(CtpSimNowExecutionError, match="ctp_native_order_ref_mapping_unavailable"):
        adapter.submit_order_insert(request)
    with pytest.raises(
        CtpSimNowExecutionError, match="ctp_execution_credential_binding_unavailable"
    ):
        adapter.submit_order_action(_order_identity(), "cancel-1")
    assert api.calls == []


def test_cancel_request_maps_native_order_ids_and_sdk_action_id():
    request = build_ctp_simnow_cancel_request(
        _order_identity(),
        account_id="ctp-account-alias",
        action_id="cancel-42",
        idempotency_key="sdk-idempotency-42",
    )
    assert request.order_id == "sys-88"
    assert request.client_order_id == CLIENT_ORDER_ID
    assert request.order_ref == CLIENT_ORDER_ID
    assert (request.front_id, request.session_id) == (17, 19)
    assert request.runtime_order_id == "runtime-order-42"
    assert request.runtime_action_id == "cancel-42"
    assert request.idempotency_key == "sdk-idempotency-42"


def test_typed_action_ack_is_never_promoted_to_cancelled_and_redacted_dict_is_unknown():
    from bt_api_ctp.order_action import CtpOrderActionEvidence

    now = datetime.now(UTC)
    evidence = CtpOrderActionEvidence(
        request_id=42,
        order_action_ref="42",
        status="accepted",
        account_fingerprint=ACCOUNT,
        trading_day=TRADING_DAY,
        connection_generation=8,
        order_ref=CLIENT_ORDER_ID,
        order_sys_id="sys-88",
        front_id=17,
        session_id=19,
        instrument_id=INSTRUMENT,
        exchange_id=EXCHANGE_ID,
        action_flag="0",
        evidence_source="OnRspOrderAction",
        callback_received=True,
        evidence_received=True,
        error_code=0,
        error_message="",
        reason="",
        submitted_at_utc=now,
        observed_at_utc=now,
        submit_code=0,
    )
    result = map_ctp_simnow_cancel_result(
        "cancel-42",
        _order_identity(),
        evidence,
        _adapter().get_execution_identity(),
        request_id=42,
        order_action_ref="42",
    )
    assert result.status == "ACCEPTED"
    assert result.execution_unknown is True
    assert result.request_id == 42 and result.order_action_ref == 42

    redacted = evidence.as_dict()
    assert redacted["account_fingerprint"] == "<redacted>"
    redacted_result = map_ctp_simnow_cancel_result(
        "cancel-42",
        _order_identity(),
        redacted,
        _adapter().get_execution_identity(),
        request_id=42,
        order_action_ref="42",
    )
    assert redacted_result.status == "UNKNOWN" and redacted_result.execution_unknown

    forged_terminal = map_ctp_simnow_cancel_result(
        "cancel-42",
        _order_identity(),
        {**redacted, "status": "cancelled", "account_fingerprint": ACCOUNT},
        _adapter().get_execution_identity(),
        request_id=42,
        order_action_ref="42",
    )
    assert forged_terminal.status == "UNKNOWN" and forged_terminal.execution_unknown


def test_cancel_normalization_and_single_execution_update_keep_unknown():
    raw = SimpleNamespace(
        get_input_data=lambda: {},
        get_data=lambda: [{"OrderRef": CLIENT_ORDER_ID, "TradingDay": TRADING_DAY}],
        get_extra_data=lambda: {
            "ctp_cancel": {
                "request_id": 42,
                "order_action_ref": "42",
                "evidence": {
                    "request_id": 42,
                    "order_action_ref": "42",
                    "status": "accepted",
                    "account_fingerprint": "<redacted>",
                    "trading_day": TRADING_DAY,
                    "connection_generation": 8,
                    "order_ref": CLIENT_ORDER_ID,
                    "order_sys_id": "sys-88",
                    "front_id": 17,
                    "session_id": 19,
                    "instrument_id": INSTRUMENT,
                    "exchange_id": EXCHANGE_ID,
                    "action_flag": "0",
                    "callback_received": True,
                    "evidence_received": True,
                },
            }
        },
    )
    normalized = normalize_result(
        "cancel_order",
        raw,
        "CTP___FUTURE",
        INSTRUMENT,
        CancelOrderRequest(
            symbol=INSTRUMENT,
            account_id="ctp-account-alias",
            idempotency_key="cancel-42",
            client_order_id=CLIENT_ORDER_ID,
            order_id="sys-88",
            order_ref=CLIENT_ORDER_ID,
            exchange_id=EXCHANGE_ID,
            front_id=17,
            session_id=19,
        ),
    )
    assert normalized["cancel_status"] == "unknown"
    assert normalized["status"] == "submitted" and normalized["execution_unknown"]

    session = object.__new__(_ExecutionSession)
    session.accounts = {}
    session.config = {"account_currencies": {}, "account_currency": "CNY"}
    state = {
        "symbol": INSTRUMENT,
        "exchange_name": "CTP___FUTURE",
        "account_id": "ctp-account-alias",
        "client_order_id": CLIENT_ORDER_ID,
        "order_id": "sys-88",
        "side": "buy",
        "size": Decimal("1"),
        "order_ref": CLIENT_ORDER_ID,
        "exchange_id": EXCHANGE_ID,
        "trading_day": TRADING_DAY,
        "terminal": False,
        "last_update": {},
        "_explicit_identity_fields": set(),
    }
    update = session._order_update(state, normalized)
    assert update["execution_unknown"] is True and update["terminal_confirmed"] is False
    assert update["cancel_action_id"] == "cancel-42"
    assert update["native_request_id"] == 42
    assert update["order_action_ref"] == "42"
    assert update["cancel_evidence"]["status"] == "unknown"


def test_query_order_snapshot_never_claims_account_wide_completeness():
    adapter = _adapter()
    result = adapter.query_account_open_orders()
    assert result.complete is False
    assert len(result.records) == 1
    assert result.records[0].order_ref == CLIENT_ORDER_ID
    assert result.records[0].order_sys_id == "sys-88"
