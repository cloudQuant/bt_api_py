"""Iteration 60 WP6: typed ``orders.submit``/``orders.cancel`` service path.

The client sends one typed command part, the account-owner handler decodes it
once (fingerprint verified by the Base codec), binds it to the authenticated
scope and the host's trusted command binding, converts it to the internal SDK
request and returns a typed result next to the durable record. Requests and
replies cross a real strict v3 MessagePack envelope. UNIT/FAKE: local SQLite
journal and a synthetic dispatch port only.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from decimal import Decimal

import pytest
from bt_api_base.account_protocol import AccountKey
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import (
    WIRE_PROTOCOL_VERSION,
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
    decode_envelope,
    encode_envelope,
)
from bt_api_base.wire_codec_v1 import encode_typed_part, service_operation_contract
from bt_api_execution import (
    CancelObservation,
    ExecutionScope,
    ProviderObservation,
    SqliteExecutionStore,
)

from bt_api_py.service_command_wire import (
    ServiceCommandBinding,
    ServiceCommandWireError,
    TypedOrderCommandGate,
    build_cancel_command,
    build_submit_command,
    command_arguments,
    verify_command_reply,
)
from bt_api_py.service_execution import (
    ServiceExecutionClient,
    ServiceExecutionError,
    ServiceExecutionUnknownOutcomeError,
)
from bt_api_py.service_execution_account_books import ExecutionOrderEntryService
from bt_api_py.service_runtime import ServiceClientRuntime

_DOMAIN = "okx:typed-order-entry"
_ACCOUNT = "acct-typed"
_STRATEGY = "strategy-typed"
_PRINCIPAL = "principal-typed"
_TEMPLATE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_SCOPE = replace(_TEMPLATE, run_id="run-typed-1")
_IDENTITY = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY)
_ACCOUNT_KEY = AccountKey(provider="okx", venue="okx", environment="demo", account_id=_ACCOUNT)
_BTC = MarketInstrumentKey(
    provider="okx",
    venue="okx",
    environment="demo",
    product_type="spot",
    instrument_id="BTC-USDT",
    native_symbol="BTC-USDT",
)
_ETH = replace(_BTC, instrument_id="ETH-USDT", native_symbol="ETH-USDT")
_BINDING = ServiceCommandBinding(
    _ACCOUNT_KEY, "route-okx-a", {"BTC-USDT": _BTC, "ETH-USDT": _ETH}, "base", "USDT"
)


class _Dispatch:
    def __init__(self, *, unknown: bool = False) -> None:
        self.submitted: list[str] = []
        self.cancelled: list[str] = []
        self.unknown = unknown

    def submit(self, intent):
        self.submitted.append(intent.intent_id)
        if self.unknown:
            raise TimeoutError("synthetic provider outcome is unknown")
        return ProviderObservation.accepted(intent.intent_id, "provider-" + intent.intent_id)

    def cancel(self, intent):
        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


def _service(tmp_path, dispatch, *, bindings=True):
    return ExecutionOrderEntryService(
        SqliteExecutionStore(tmp_path / f"execution-{uuid.uuid4().hex}.sqlite3"),
        {_TEMPLATE: ExecutionScope("okx", "demo", _ACCOUNT, _STRATEGY)},
        dispatch,
        allow_unprotected=True,
        writer_id="typed-order-entry-test",
        command_bindings={_TEMPLATE: _BINDING} if bindings else None,
    )


def _payload(intent_id="intent-1", **changes):
    values = dict(
        intent_id=intent_id,
        signal_id="signal-" + intent_id,
        instrument="BTC-USDT",
        side="BUY",
        position_effect="OPEN",
        order_type="LIMIT",
        quantity=Decimal("2"),
        price=Decimal("50000"),
        metadata_version="typed-v1",
        tags={"origin": "typed-test"},
    )
    values.update(changes)
    return ServiceExecutionClient.build_submit_payload(**values)


def _header(kind, operation, *, message_id=None, correlation_id=None):
    return WireHeader(
        protocol_version=WIRE_PROTOCOL_VERSION,
        kind=kind,
        message_id=message_id or uuid.uuid4().hex,
        correlation_id=correlation_id,
        identity=_IDENTITY
        if kind is WireMessageKind.REQUEST
        else WireIdentity(WireRole.SERVICE, "service-principal-typed", "execution-service"),
        scope=_SCOPE,
        operation=operation,
        schema_id="sdk.service.call",
        schema_version=1,
        sent_at_utc="2026-11-04T00:00:00Z",
    )


def _wire(operation, payload, kind=WireMessageKind.REQUEST):
    """Cross one real strict v3 MessagePack envelope."""

    correlation = None if kind is WireMessageKind.REQUEST else uuid.uuid4().hex
    envelope = WireEnvelope(
        _header(kind, operation, correlation_id=correlation), {"arguments": dict(payload)}
    )
    return decode_envelope(encode_envelope(envelope)).payload["arguments"]


def _call(service, operation, arguments):
    reply = service.handle(
        operation,
        principal_ref=_PRINCIPAL,
        identity=_IDENTITY,
        scope=_SCOPE,
        arguments=_wire(operation, arguments),
    )
    return _wire(operation, reply, WireMessageKind.REPLY)


def test_registry_lists_order_entry_as_typed():
    assert service_operation_contract("orders.submit")[0] == "typed"
    assert service_operation_contract("orders.cancel")[0] == "typed"
    assert service_operation_contract("orders.submit")[1]["request"] == "strategy_submit"
    assert service_operation_contract("orders.cancel")[1]["reply"] == "strategy_result"
    # The only generic form left is the explicitly named binding-less host form.
    assert service_operation_contract("orders.submit:binding-less-hosts")[0] == "generic"


def test_typed_submit_decodes_once_and_returns_a_bound_typed_result(tmp_path):
    dispatch = _Dispatch()
    service = _service(tmp_path, dispatch)
    assert service.typed_commands is True
    command = build_submit_command(_BINDING, _payload(), scope=_SCOPE)
    reply = _call(service, "orders.submit", command_arguments(command))
    assert reply["ok"] is True and reply["code"] == "OK"
    assert set(reply["result"]) == {"found", "record", "command_result"}
    typed = verify_command_reply(reply["result"], command)
    assert typed.outcome == "accepted"
    assert typed.command_fingerprint == command.semantic_fingerprint
    assert typed.provider_order_ref == "provider-intent-1"
    assert dispatch.submitted == ["intent-1"]


def test_same_key_same_fingerprint_replays_and_other_content_is_refused(tmp_path):
    dispatch = _Dispatch()
    service = _service(tmp_path, dispatch)
    first = build_submit_command(_BINDING, _payload(), scope=_SCOPE)
    assert _call(service, "orders.submit", command_arguments(first))["ok"] is True
    # A retry is a new attempt (new request id) of the same content.
    retry = build_submit_command(_BINDING, _payload(), scope=_SCOPE)
    assert retry.request_id != first.request_id
    assert retry.semantic_fingerprint == first.semantic_fingerprint
    replayed = _call(service, "orders.submit", command_arguments(retry))
    assert replayed["ok"] is True
    assert verify_command_reply(replayed["result"], retry).outcome == "accepted"
    assert dispatch.submitted == ["intent-1"], "the replay must not dispatch again"

    changed = build_submit_command(_BINDING, _payload(quantity=Decimal("3")), scope=_SCOPE)
    assert changed.idempotency_key == first.idempotency_key
    assert changed.semantic_fingerprint != first.semantic_fingerprint
    conflict = _call(service, "orders.submit", command_arguments(changed))
    assert conflict["ok"] is False and conflict["code"] == "INTENT_CONFLICT"
    # The existing record does not answer the new content: no typed result.
    assert "command_result" not in conflict["result"]
    assert dispatch.submitted == ["intent-1"]


def test_generic_and_tampered_requests_are_refused_before_any_work(tmp_path):
    dispatch = _Dispatch()
    service = _service(tmp_path, dispatch)
    generic = _call(service, "orders.submit", _payload())
    assert (generic["ok"], generic["code"]) == (False, "COMMAND_TYPED_REQUIRED")

    command = build_submit_command(_BINDING, _payload(), scope=_SCOPE)
    arguments = command_arguments(command)
    tampered = dict(arguments["command"])
    tampered["data"] = dict(tampered["data"], quantity="5")
    reply = _call(service, "orders.submit", {"command": tampered})
    assert reply["code"] == "WIRE_COMMAND_FINGERPRINT_MISMATCH"

    extra = dict(arguments, intent_id="intent-1")
    assert _call(service, "orders.submit", extra)["code"] == "COMMAND_TYPED_REQUIRED"
    assert dispatch.submitted == []


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"strategy_ref": "strategy-other"}, "COMMAND_SCOPE_MISMATCH"),
        ({"run_id": "run-other"}, "COMMAND_SCOPE_MISMATCH"),
        ({"route": "route-other"}, "COMMAND_BINDING_MISMATCH"),
        ({"quantity_unit": "contracts"}, "COMMAND_BINDING_MISMATCH"),
        ({"hedge_class": "hedge"}, "COMMAND_BINDING_MISMATCH"),
        ({"price_currency": "USD"}, "COMMAND_BINDING_MISMATCH"),
        (
            {"account_key": replace(_ACCOUNT_KEY, account_id="acct-other")},
            "COMMAND_BINDING_MISMATCH",
        ),
    ],
)
def test_bindings_outside_the_sdk_request_are_pinned(tmp_path, change, code):
    dispatch = _Dispatch()
    service = _service(tmp_path, dispatch)
    command = replace(build_submit_command(_BINDING, _payload(), scope=_SCOPE), **change)
    reply = _call(service, "orders.submit", command_arguments(command))
    assert (reply["ok"], reply["code"]) == (False, code)
    assert dispatch.submitted == []


def test_unbound_instruments_and_private_bindings_are_refused_client_side():
    with pytest.raises(ServiceCommandWireError, match="COMMAND_INSTRUMENT_UNBOUND"):
        build_submit_command(_BINDING, _payload(instrument="SOL-USDT"), scope=_SCOPE)
    with pytest.raises(ServiceCommandWireError, match="COMMAND_PRIVATE_BINDING"):
        build_submit_command(
            _BINDING, dict(_payload(), front_id="1", session_id="2"), scope=_SCOPE
        )
    with pytest.raises(ServiceCommandWireError, match="COMMAND_SCOPE_UNAVAILABLE"):
        build_submit_command(_BINDING, _payload(), scope=_TEMPLATE)
    with pytest.raises(ServiceCommandWireError, match="COMMAND_INSTRUMENT_REQUIRED"):
        build_cancel_command(
            _BINDING,
            ServiceExecutionClient.build_cancel_payload(
                cancel_id="c", target_intent_id="i", provider_order_id="p"
            ),
            instrument=None,
            scope=_SCOPE,
        )
    with pytest.raises(ServiceCommandWireError, match="COMMAND_BINDING_INVALID"):
        ServiceCommandBinding(_ACCOUNT_KEY, "route", {"ETH-USDT": _BTC}, "base")


def test_typed_cancel_and_cancel_deadline_is_not_dropped(tmp_path):
    dispatch = _Dispatch()
    service = _service(tmp_path, dispatch)
    submitted = build_submit_command(_BINDING, _payload(), scope=_SCOPE)
    assert _call(service, "orders.submit", command_arguments(submitted))["ok"] is True
    payload = ServiceExecutionClient.build_cancel_payload(
        cancel_id="cancel-1",
        target_intent_id="intent-1",
        provider_order_id="provider-intent-1",
        metadata_version="typed-v1",
    )
    cancel = build_cancel_command(_BINDING, payload, instrument="BTC-USDT", scope=_SCOPE)
    with_deadline = replace(cancel, valid_until_utc_ns=1)
    refused = _call(service, "orders.cancel", command_arguments(with_deadline))
    assert refused["code"] == "COMMAND_FIELD_UNREPRESENTABLE"
    reply = _call(service, "orders.cancel", command_arguments(cancel))
    assert reply["ok"] is True, reply
    typed = verify_command_reply(reply["result"], cancel)
    assert typed.operation == "cancel" and typed.outcome == "accepted"
    assert dispatch.cancelled == ["cancel-1"]


def test_unknown_outcome_projects_to_unknown_and_tampered_results_are_refused(tmp_path):
    dispatch = _Dispatch(unknown=True)
    service = _service(tmp_path, dispatch)
    command = build_submit_command(_BINDING, _payload(), scope=_SCOPE)
    reply = _call(service, "orders.submit", command_arguments(command))
    assert reply["ok"] is True
    typed = verify_command_reply(reply["result"], command)
    assert typed.outcome == "unknown"
    # A reply cannot claim acceptance for an UNKNOWN durable record.
    forged = replace(typed, outcome="accepted", reason_code=None)
    result = dict(reply["result"], command_result=encode_typed_part("strategy_result", forged))
    with pytest.raises(ServiceCommandWireError, match="COMMAND_RESULT_RECORD_MISMATCH"):
        verify_command_reply(result, command)
    other = build_submit_command(_BINDING, _payload(quantity=Decimal("9")), scope=_SCOPE)
    with pytest.raises(ServiceCommandWireError, match="COMMAND_RESULT_BINDING_MISMATCH"):
        verify_command_reply(reply["result"], other)


def test_gate_requires_bindings():
    with pytest.raises(ServiceCommandWireError, match="COMMAND_BINDING_INVALID"):
        TypedOrderCommandGate({})
    with pytest.raises(ValueError):
        ExecutionOrderEntryService(
            None,  # type: ignore[arg-type]  # refused before the store is used
            {},
            None,
            command_bindings={},
        )


class _Binding:
    def __init__(self, scope):
        self.mount = "broker.orders"
        self.scope_ref = "strategy-orders-typed"
        self.scope = scope


class _FakeRuntime(ServiceClientRuntime):
    """Owner-thread runtime double: routes calls to an in-process handler.

    Every request and reply crosses a real v3 envelope encode/decode.
    """

    def __init__(self, service, scope=_SCOPE):  # noqa: D107 - test double
        self.service = service
        self.calls: list[tuple[str, object]] = []
        self._fake_bindings = (_Binding(scope),)
        self.mutate_reply = None

    @property
    def bindings(self):
        return self._fake_bindings

    def call(self, mount, operation, arguments, *, scope_ref=None, **_options):
        assert (mount, scope_ref) == ("broker.orders", "strategy-orders-typed")
        self.calls.append((operation, arguments))
        reply = _call(self.service, operation, arguments)
        if self.mutate_reply is not None:
            reply = self.mutate_reply(reply)
        return reply


def _client(runtime, *, binding=_BINDING):
    return ServiceExecutionClient(
        runtime, "broker.orders", scope_ref="strategy-orders-typed", command_binding=binding
    )


def test_client_sends_typed_commands_by_binding_and_maps_results(tmp_path):
    dispatch = _Dispatch()
    runtime = _FakeRuntime(_service(tmp_path, dispatch))
    client = _client(runtime)
    view = client.submit(**_submit_kwargs("intent-c1"))
    assert view.provider_order_id == "provider-intent-c1"
    operation, arguments = runtime.calls[-1]
    assert operation == "orders.submit" and set(arguments) == {"command"}
    assert arguments["command"]["kind"] == "strategy_submit"

    cancelled = client.cancel(
        cancel_id="cancel-c1",
        target_intent_id="intent-c1",
        provider_order_id="provider-intent-c1",
        instrument="BTC-USDT",
    )
    assert cancelled.cancel_id == "cancel-c1"
    assert runtime.calls[-1][1]["command"]["kind"] == "strategy_cancel"

    command = build_submit_command(_BINDING, _payload("intent-c2"), scope=_SCOPE)
    accepted = client.submit_command(command)
    assert accepted.outcome == "accepted"
    assert accepted.command_fingerprint == command.semantic_fingerprint
    assert dispatch.submitted == ["intent-c1", "intent-c2"]

    # Untypeable content is refused before anything is sent.
    sent = len(runtime.calls)
    with pytest.raises(ServiceExecutionError, match="COMMAND_INSTRUMENT_UNBOUND"):
        client.submit(**_submit_kwargs("intent-c3", instrument="SOL-USDT"))
    assert len(runtime.calls) == sent


def test_client_maps_unknown_to_reconcile_and_refuses_unbound_replies(tmp_path):
    runtime = _FakeRuntime(_service(tmp_path, _Dispatch(unknown=True)))
    client = _client(runtime)
    command = build_submit_command(_BINDING, _payload("intent-u1"), scope=_SCOPE)
    with pytest.raises(ServiceExecutionUnknownOutcomeError):
        client.submit_command(command)
    view = client.submit(**_submit_kwargs("intent-u2"))
    assert view.state == "UNKNOWN"

    def strip(reply):
        result = dict(reply["result"])
        result.pop("command_result", None)
        return dict(reply, result=result)

    runtime.mutate_reply = strip
    with pytest.raises(ServiceExecutionError, match="COMMAND_RESULT_MISSING"):
        client.submit(**_submit_kwargs("intent-u3"))


def test_binding_less_client_is_refused_by_a_typed_host(tmp_path):
    dispatch = _Dispatch()
    runtime = _FakeRuntime(_service(tmp_path, dispatch))
    client = _client(runtime, binding=None)
    with pytest.raises(ServiceExecutionError, match="COMMAND_TYPED_REQUIRED"):
        client.submit(**_submit_kwargs("intent-g1"))
    with pytest.raises(ServiceExecutionError, match="COMMAND_BINDING_REQUIRED"):
        client.submit_command(build_submit_command(_BINDING, _payload(), scope=_SCOPE))
    assert dispatch.submitted == []


def test_binding_less_host_keeps_the_explicit_legacy_generic_form(tmp_path):
    dispatch = _Dispatch()
    service = _service(tmp_path, dispatch, bindings=False)
    assert service.typed_commands is False
    reply = _call(service, "orders.submit", _payload("intent-l1"))
    assert reply["ok"] is True and "command_result" not in reply["result"]
    typed = build_submit_command(_BINDING, _payload("intent-l2"), scope=_SCOPE)
    refused = _call(service, "orders.submit", command_arguments(typed))
    assert refused["ok"] is False
    assert dispatch.submitted == ["intent-l1"]


def _submit_kwargs(intent_id, **changes):
    values = dict(
        intent_id=intent_id,
        signal_id="signal-" + intent_id,
        instrument="BTC-USDT",
        side="BUY",
        position_effect="OPEN",
        order_type="LIMIT",
        quantity=Decimal("2"),
        price=Decimal("50000"),
        metadata_version="typed-v1",
    )
    values.update(changes)
    return values
