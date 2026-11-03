"""Lossless SDK order request <-> typed Base command conversion (iteration 60.08, G5).

The SDK service order request (``ServiceExecutionClient.build_submit_payload``
/ ``build_cancel_payload``) and the Base typed wire commands
(``StrategySubmitCommand`` / ``StrategyCancelCommand``, wire minor 5) carry the
same business content. This module converts between them:

* every SDK request field maps to one typed field and back, so a request
  round-trips with an equal semantic fingerprint (decimal spelling becomes the
  canonical lexeme; numeric values are equal);
* anything the typed contract cannot express (an unknown request field, a
  provider-private binding such as a CTP FrontID/SessionID/OrderRef, an
  instrument that is not the declared key) is rejected *before* sending,
  never dropped;
* typed results map to the SDK's existing result semantics:
  ``ServiceExecutionUnknownOutcomeError`` for ``unknown`` and
  ``ServiceExecutionError`` for ``rejected``.

Scope facts passed in (account key, instrument key, route, strategy, run) are
the caller's trusted bindings; nothing here authenticates or authorizes them.
Typed service path (iteration 60 WP6): ``orders.submit``/``orders.cancel``
arguments are exactly ``{"command": <typed part>}`` and the reply result
carries ``command_result`` (a ``strategy_result`` typed part) next to the
durable record. :class:`ServiceCommandBinding` is the trusted account/route/
instrument/unit binding both peers configure; :class:`TypedOrderCommandGate`
decodes the part once (the codec verifies the semantic fingerprint), refuses
a command whose bindings differ from the authenticated scope and the host's
trusted binding, converts it to the internal SDK request and projects the
durable record back to a typed result. Because every typed field outside the
SDK request is pinned by the binding, the durable journal's payload digest
check is an exact fingerprint check: same key + same fingerprint replays the
stored record, same key + other content is ``INTENT_CONFLICT``.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from bt_api_base.account_protocol import AccountKey, account_venue_contains_instrument_venue
from bt_api_base.execution_command_wire_protocol import (
    StrategyCancelCommand,
    StrategyCommandResult,
    StrategySubmitCommand,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireScope

from bt_api_py.service_execution import (
    CancelRecordView,
    OrderRecordView,
    ServiceExecutionClient,
    ServiceExecutionError,
    ServiceExecutionUnknownOutcomeError,
)

_SUBMIT_FIELDS = frozenset(
    {
        "intent_id",
        "signal_id",
        "instrument",
        "side",
        "position_effect",
        "order_type",
        "quantity",
        "price",
        "time_in_force",
        "reduce_only",
        "metadata_version",
        "deadline_ns",
        "tags",
        "position_side",
        "position_bucket",
    }
)
_CANCEL_FIELDS = frozenset(
    {"cancel_id", "target_intent_id", "provider_order_id", "metadata_version", "tags"}
)
#: Provider-private order bindings the typed contract cannot represent. They
#: identify a venue session, not the neutral command, so they are refused.
_PRIVATE_BINDINGS = frozenset(
    {
        "front_id",
        "session_id",
        "order_ref",
        "order_sys_id",
        "exchange_order_ref",
        "broker_id",
        "investor_id",
        "user_id",
        "ctp_binding",
    }
)
_SIDE = {"BUY": "buy", "SELL": "sell"}
_EFFECT = {
    "OPEN": "open",
    "CLOSE": "close",
    "CLOSE_TODAY": "close_today",
    "CLOSE_YESTERDAY": "close_yesterday",
}
_ORDER_TYPE = {"MARKET": "market", "LIMIT": "limit"}
_POSITION_SIDE = {"NET": "net", "LONG": "long", "SHORT": "short"}
_BUCKET = {"TODAY": "today", "YESTERDAY": "yesterday"}
_UNKNOWN_STATES = frozenset({"UNKNOWN", "PENDING_ADMISSION", "PENDING_DISPATCH", "DISPATCHING"})
_REJECTED_STATES = frozenset({"REJECTED", "BLOCKED"})


class ServiceCommandWireError(ValueError):
    """A request cannot be represented by the typed contract; nothing was sent."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _inverse(mapping: Mapping[str, str]) -> dict[str, str]:
    return {value: key for key, value in mapping.items()}


def _check_fields(payload: Any, allowed: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping) or any(type(key) is not str for key in payload):
        raise ServiceCommandWireError("COMMAND_PAYLOAD_INVALID")
    extra = set(payload) - allowed
    if extra & _PRIVATE_BINDINGS or any(key.startswith("ctp") for key in extra):
        raise ServiceCommandWireError("COMMAND_PRIVATE_BINDING_UNREPRESENTABLE")
    if extra:
        raise ServiceCommandWireError("COMMAND_FIELD_UNREPRESENTABLE")
    tags = payload.get("tags") or {}
    if isinstance(tags, Mapping) and any(
        type(key) is str and (key.lower() in _PRIVATE_BINDINGS or key.lower().startswith("ctp"))
        for key in tags
    ):
        # Tags must not smuggle a venue session binding past the contract.
        raise ServiceCommandWireError("COMMAND_PRIVATE_BINDING_UNREPRESENTABLE")
    return payload


def _sdk_error(error: ServiceExecutionError) -> ServiceCommandWireError:
    return ServiceCommandWireError(error.code)


def typed_submit_from_payload(
    payload: Mapping[str, Any],
    *,
    account_key: AccountKey,
    instrument: MarketInstrumentKey,
    route: str,
    strategy_ref: str,
    run_id: str,
    request_id: str,
    quantity_unit: str,
    price_currency: str | None = None,
    position_mode: str | None = None,
    hedge_class: str | None = None,
) -> StrategySubmitCommand:
    """Convert one SDK submit request into the typed command, or refuse it.

    The SDK validator runs first, so only an admissible SDK request converts.
    ``intent_id`` is both the idempotency key and the client order id (the SDK
    request has no separate client order identity).
    """

    _check_fields(payload, _SUBMIT_FIELDS)
    try:
        canonical = ServiceExecutionClient.build_submit_payload(**payload)
    except ServiceExecutionError as error:
        raise _sdk_error(error) from None
    except TypeError:
        raise ServiceCommandWireError("COMMAND_PAYLOAD_INVALID") from None
    if type(instrument) is not MarketInstrumentKey or canonical["instrument"] != (
        instrument.instrument_id
    ):
        raise ServiceCommandWireError("COMMAND_INSTRUMENT_MISMATCH")
    position_side = canonical.get("position_side", "NET")
    bucket = canonical.get("position_bucket", "ALL")
    try:
        command = StrategySubmitCommand(
            account_key=account_key,
            instrument=instrument,
            route=route,
            strategy_ref=strategy_ref,
            run_id=run_id,
            request_id=request_id,
            idempotency_key=canonical["intent_id"],
            client_order_id=canonical["intent_id"],
            side=_SIDE[canonical["side"]],
            order_type=_ORDER_TYPE[canonical["order_type"]],
            quantity=canonical["quantity"],
            quantity_unit=quantity_unit,
            limit_price=canonical["price"],
            price_currency=price_currency,
            time_in_force=canonical["time_in_force"],
            valid_until_utc_ns=canonical["deadline_ns"],
            reduce_only=canonical["reduce_only"],
            position_side=_POSITION_SIDE[position_side],
            position_effect=_EFFECT[canonical["position_effect"]],
            position_mode=position_mode,
            hedge_class=hedge_class,
            signal_id=canonical["signal_id"],
            metadata_version=canonical["metadata_version"],
            tags=tuple(sorted(canonical["tags"].items())),
            position_bucket=_BUCKET.get(bucket),
        )
    except (KeyError, TypeError, ValueError):
        raise ServiceCommandWireError("COMMAND_UNREPRESENTABLE") from None
    if payload_from_typed_submit(command) != _with_canonical_numbers(canonical):
        raise ServiceCommandWireError("COMMAND_UNREPRESENTABLE")
    return command


def payload_from_typed_submit(command: StrategySubmitCommand) -> Mapping[str, Any]:
    """The SDK submit request a typed command represents (canonical decimals)."""

    if type(command) is not StrategySubmitCommand:
        raise ServiceCommandWireError("COMMAND_TYPE_INVALID")
    if command.signal_id is None or command.metadata_version is None:
        # The SDK request requires both; a typed command without them is not
        # an SDK request (rather than inventing values).
        raise ServiceCommandWireError("COMMAND_UNREPRESENTABLE")
    if command.position_side is None or command.position_effect is None:
        raise ServiceCommandWireError("COMMAND_UNREPRESENTABLE")
    if command.idempotency_key != command.client_order_id:
        raise ServiceCommandWireError("COMMAND_UNREPRESENTABLE")
    bucket = _inverse(_BUCKET).get(command.position_bucket, "ALL")
    try:
        payload = ServiceExecutionClient.build_submit_payload(
            intent_id=command.idempotency_key,
            signal_id=command.signal_id,
            instrument=command.instrument.instrument_id,
            side=_inverse(_SIDE)[command.side],
            position_effect=_inverse(_EFFECT)[command.position_effect],
            order_type=_inverse(_ORDER_TYPE)[command.order_type],
            quantity=command.quantity,
            price=command.limit_price,
            time_in_force=command.time_in_force,
            reduce_only=command.reduce_only,
            metadata_version=command.metadata_version,
            deadline_ns=command.valid_until_utc_ns,
            tags=dict(command.tags),
            position_side=_inverse(_POSITION_SIDE)[command.position_side],
            position_bucket=bucket,
        )
    except ServiceExecutionError as error:
        raise _sdk_error(error) from None
    except KeyError:
        raise ServiceCommandWireError("COMMAND_UNREPRESENTABLE") from None
    return _with_canonical_numbers(payload)


def _with_canonical_numbers(payload: Mapping[str, Any]) -> dict[str, Any]:
    from bt_api_base.wire_codec_v1 import canonical_decimal

    result = dict(payload)
    result["tags"] = dict(result["tags"])
    for name in ("quantity", "price"):
        if result.get(name) is not None:
            result[name] = canonical_decimal(Decimal(result[name]))
    return result


def typed_cancel_from_payload(
    payload: Mapping[str, Any],
    *,
    account_key: AccountKey,
    instrument: MarketInstrumentKey,
    route: str,
    strategy_ref: str,
    run_id: str,
    request_id: str,
    valid_until_utc_ns: int | None = None,
) -> StrategyCancelCommand:
    """Convert one SDK cancel request into the typed command, or refuse it."""

    _check_fields(payload, _CANCEL_FIELDS)
    try:
        canonical = ServiceExecutionClient.build_cancel_payload(**payload)
    except ServiceExecutionError as error:
        raise _sdk_error(error) from None
    except TypeError:
        raise ServiceCommandWireError("COMMAND_PAYLOAD_INVALID") from None
    try:
        command = StrategyCancelCommand(
            account_key=account_key,
            instrument=instrument,
            route=route,
            strategy_ref=strategy_ref,
            run_id=run_id,
            request_id=request_id,
            idempotency_key=canonical["cancel_id"],
            client_order_id=canonical["target_intent_id"],
            provider_order_ref=canonical["provider_order_id"],
            valid_until_utc_ns=valid_until_utc_ns,
            metadata_version=canonical["metadata_version"],
            tags=tuple(sorted(canonical["tags"].items())),
        )
    except (KeyError, TypeError, ValueError):
        raise ServiceCommandWireError("COMMAND_UNREPRESENTABLE") from None
    if payload_from_typed_cancel(command) != dict(canonical):
        raise ServiceCommandWireError("COMMAND_UNREPRESENTABLE")
    return command


def payload_from_typed_cancel(command: StrategyCancelCommand) -> Mapping[str, Any]:
    """The SDK cancel request a typed command represents."""

    if type(command) is not StrategyCancelCommand:
        raise ServiceCommandWireError("COMMAND_TYPE_INVALID")
    if command.provider_order_ref is None or command.metadata_version is None:
        raise ServiceCommandWireError("COMMAND_UNREPRESENTABLE")
    try:
        payload = ServiceExecutionClient.build_cancel_payload(
            cancel_id=command.idempotency_key,
            target_intent_id=command.client_order_id,
            provider_order_id=command.provider_order_ref,
            metadata_version=command.metadata_version,
            tags=dict(command.tags),
        )
    except ServiceExecutionError as error:
        raise _sdk_error(error) from None
    return dict(payload)


def _result(
    command: StrategySubmitCommand | StrategyCancelCommand,
    *,
    operation: str,
    state: str,
    provider_order_ref: str | None,
    unknown_reason: str | None,
    request_id: str,
    observed_at_utc_ns: int | None,
    source_precision_ns: int | None,
) -> StrategyCommandResult:
    if state in _UNKNOWN_STATES:
        outcome, reason = "unknown", unknown_reason or state
    elif state in _REJECTED_STATES:
        outcome, reason = "rejected", state
    else:
        outcome, reason = "accepted", None
    return StrategyCommandResult(
        account_key=command.account_key,
        route=command.route,
        strategy_ref=command.strategy_ref,
        run_id=command.run_id,
        request_id=request_id,
        idempotency_key=command.idempotency_key,
        operation=operation,
        outcome=outcome,
        provider_order_ref=provider_order_ref,
        reason_code=reason,
        observed_at_utc_ns=observed_at_utc_ns,
        source_precision_ns=source_precision_ns,
        command_fingerprint=command.semantic_fingerprint,
    )


def typed_result_from_order_record(
    record: OrderRecordView,
    command: StrategySubmitCommand,
    *,
    request_id: str,
    observed_at_utc_ns: int | None = None,
    source_precision_ns: int | None = None,
) -> StrategyCommandResult:
    """Project an SDK durable order record into a typed acknowledgement.

    ``accepted`` only means acknowledged; pending, dispatching and UNKNOWN
    records are ``unknown`` (not safe to resubmit), REJECTED/BLOCKED are
    ``rejected``.
    """

    if type(record) is not OrderRecordView or type(command) is not StrategySubmitCommand:
        raise ServiceCommandWireError("COMMAND_RESULT_INVALID")
    if record.intent_id != command.idempotency_key:
        raise ServiceCommandWireError("COMMAND_RESULT_BINDING_MISMATCH")
    return _result(
        command,
        operation="submit",
        state=record.state,
        provider_order_ref=record.provider_order_id,
        unknown_reason=record.unknown_reason,
        request_id=request_id,
        observed_at_utc_ns=observed_at_utc_ns,
        source_precision_ns=source_precision_ns,
    )


def typed_result_from_cancel_record(
    record: CancelRecordView,
    command: StrategyCancelCommand,
    *,
    request_id: str,
    observed_at_utc_ns: int | None = None,
    source_precision_ns: int | None = None,
) -> StrategyCommandResult:
    """Project an SDK durable cancellation record into a typed acknowledgement."""

    if type(record) is not CancelRecordView or type(command) is not StrategyCancelCommand:
        raise ServiceCommandWireError("COMMAND_RESULT_INVALID")
    if record.cancel_id != command.idempotency_key:
        raise ServiceCommandWireError("COMMAND_RESULT_BINDING_MISMATCH")
    return _result(
        command,
        operation="cancel",
        state=record.state,
        provider_order_ref=record.provider_order_id,
        unknown_reason=record.unknown_reason,
        request_id=request_id,
        observed_at_utc_ns=observed_at_utc_ns,
        source_precision_ns=source_precision_ns,
    )


def require_accepted_result(
    result: StrategyCommandResult, command: StrategySubmitCommand | StrategyCancelCommand
) -> StrategyCommandResult:
    """Bind a typed result to its command and map it to SDK result semantics.

    A result for other content (fingerprint), another key, account, route or
    strategy is refused. ``unknown`` raises ``ServiceExecutionUnknownOutcomeError``
    (reconcile before retrying), ``rejected`` raises ``ServiceExecutionError``.
    """

    if type(result) is not StrategyCommandResult or type(command) not in (
        StrategySubmitCommand,
        StrategyCancelCommand,
    ):
        raise ServiceCommandWireError("COMMAND_RESULT_INVALID")
    operation = "submit" if type(command) is StrategySubmitCommand else "cancel"
    if (
        result.command_fingerprint != command.semantic_fingerprint
        or result.idempotency_key != command.idempotency_key
        or result.account_key != command.account_key
        or result.route != command.route
        or result.strategy_ref != command.strategy_ref
        or result.operation != operation
    ):
        raise ServiceCommandWireError("COMMAND_RESULT_BINDING_MISMATCH")
    if result.outcome == "unknown":
        raise ServiceExecutionUnknownOutcomeError(
            result.reason_code or "OUTCOME_UNKNOWN", command.idempotency_key
        )
    if result.outcome == "rejected":
        raise ServiceExecutionError(result.reason_code or "COMMAND_REJECTED")
    return result


COMMAND_ARGUMENT = "command"
COMMAND_RESULT_FIELD = "command_result"
SUBMIT_OPERATION = "orders.submit"
CANCEL_OPERATION = "orders.cancel"
TYPED_COMMAND_OPERATIONS = frozenset({SUBMIT_OPERATION, CANCEL_OPERATION})
_PART_KIND = {SUBMIT_OPERATION: "strategy_submit", CANCEL_OPERATION: "strategy_cancel"}
_COMMAND_TYPE = {SUBMIT_OPERATION: StrategySubmitCommand, CANCEL_OPERATION: StrategyCancelCommand}


@dataclass(frozen=True)
class ServiceCommandBinding:
    """Trusted typed-command binding of one order-entry scope.

    The host composition root supplies it as a resource (keyed like the
    execution targets); a client is configured with the same declaration.
    Every typed field the SDK request does not carry is pinned here, so the
    server refuses rather than silently re-interprets a different value.
    It authorizes nothing: scope authorization stays with the service.
    """

    account_key: AccountKey
    route: str
    instruments: Mapping[str, MarketInstrumentKey]
    quantity_unit: str
    price_currency: str | None = None
    position_mode: str | None = None
    hedge_class: str | None = None

    def __post_init__(self) -> None:
        if type(self.account_key) is not AccountKey:
            raise ServiceCommandWireError("COMMAND_BINDING_INVALID")
        if type(self.route) is not str or not self.route or self.route != self.route.strip():
            raise ServiceCommandWireError("COMMAND_BINDING_INVALID")
        if not isinstance(self.instruments, Mapping) or not self.instruments:
            raise ServiceCommandWireError("COMMAND_BINDING_INVALID")
        copied: dict[str, MarketInstrumentKey] = {}
        for instrument_id, key in self.instruments.items():
            if (
                type(instrument_id) is not str
                or type(key) is not MarketInstrumentKey
                or key.instrument_id != instrument_id
                or key.provider != self.account_key.provider
                or key.environment != self.account_key.environment
                or not account_venue_contains_instrument_venue(self.account_key, key.venue)
            ):
                raise ServiceCommandWireError("COMMAND_BINDING_INVALID")
            copied[instrument_id] = key
        object.__setattr__(self, "instruments", MappingProxyType(copied))
        if type(self.quantity_unit) is not str:
            raise ServiceCommandWireError("COMMAND_BINDING_INVALID")

    def instrument(self, instrument_id: object) -> MarketInstrumentKey:
        key = self.instruments.get(instrument_id) if type(instrument_id) is str else None
        if key is None:
            raise ServiceCommandWireError("COMMAND_INSTRUMENT_UNBOUND")
        return key


def _owner_ref(scope: WireScope) -> str | None:
    return scope.strategy_ref if scope.strategy_ref is not None else scope.manual_book_ref


def build_submit_command(
    binding: ServiceCommandBinding,
    payload: Mapping[str, Any],
    *,
    scope: WireScope,
    request_id: str | None = None,
) -> StrategySubmitCommand:
    """Client side: the typed submit command for one SDK request (or refusal)."""

    if type(binding) is not ServiceCommandBinding or type(scope) is not WireScope:
        raise ServiceCommandWireError("COMMAND_BINDING_INVALID")
    owner = _owner_ref(scope)
    if owner is None or scope.run_id is None:
        raise ServiceCommandWireError("COMMAND_SCOPE_UNAVAILABLE")
    instrument_id = payload.get("instrument") if isinstance(payload, Mapping) else None
    return typed_submit_from_payload(
        payload,
        account_key=binding.account_key,
        instrument=binding.instrument(instrument_id),
        route=binding.route,
        strategy_ref=owner,
        run_id=scope.run_id,
        request_id=request_id or uuid.uuid4().hex,
        quantity_unit=binding.quantity_unit,
        price_currency=(
            binding.price_currency if payload.get("price") is not None else None
        ),
        position_mode=binding.position_mode,
        hedge_class=binding.hedge_class,
    )


def build_cancel_command(
    binding: ServiceCommandBinding,
    payload: Mapping[str, Any],
    *,
    instrument: str | None,
    scope: WireScope,
    request_id: str | None = None,
) -> StrategyCancelCommand:
    """Client side: the typed cancel command for one SDK cancel request.

    The SDK cancel request has no instrument; a binding with exactly one
    instrument implies it, otherwise the caller must name it.
    """

    if type(binding) is not ServiceCommandBinding or type(scope) is not WireScope:
        raise ServiceCommandWireError("COMMAND_BINDING_INVALID")
    owner = _owner_ref(scope)
    if owner is None or scope.run_id is None:
        raise ServiceCommandWireError("COMMAND_SCOPE_UNAVAILABLE")
    if instrument is None:
        if len(binding.instruments) != 1:
            raise ServiceCommandWireError("COMMAND_INSTRUMENT_REQUIRED")
        instrument = next(iter(binding.instruments))
    return typed_cancel_from_payload(
        payload,
        account_key=binding.account_key,
        instrument=binding.instrument(instrument),
        route=binding.route,
        strategy_ref=owner,
        run_id=scope.run_id,
        request_id=request_id or uuid.uuid4().hex,
    )


def command_arguments(command: StrategySubmitCommand | StrategyCancelCommand) -> dict[str, Any]:
    """The ``orders.submit``/``orders.cancel`` arguments: one typed part."""

    from bt_api_base.wire_codec_v1 import WireCodecError, encode_typed_part

    kind = "strategy_submit" if type(command) is StrategySubmitCommand else "strategy_cancel"
    try:
        return {COMMAND_ARGUMENT: encode_typed_part(kind, command)}
    except WireCodecError as error:
        raise ServiceCommandWireError(error.code) from None


def verify_command_reply(
    result: Mapping[str, Any],
    command: StrategySubmitCommand | StrategyCancelCommand,
) -> StrategyCommandResult:
    """Client side: decode the reply's typed result and bind it to ``command``.

    The typed result must answer this command's fingerprint, key, account,
    route and strategy, and its outcome must equal the projection of the
    durable record returned next to it (a reply cannot claim ``accepted`` for
    an UNKNOWN record).
    """

    from bt_api_base.wire_codec_v1 import WireCodecError, decode_typed_part

    from bt_api_py.service_execution import _cancel_view, _order_view

    if not isinstance(result, Mapping) or COMMAND_RESULT_FIELD not in result:
        raise ServiceCommandWireError("COMMAND_RESULT_MISSING")
    try:
        typed = decode_typed_part(result[COMMAND_RESULT_FIELD], kind="strategy_result")
    except WireCodecError as error:
        raise ServiceCommandWireError(error.code) from None
    operation = "submit" if type(command) is StrategySubmitCommand else "cancel"
    if (
        typed.command_fingerprint != command.semantic_fingerprint
        or typed.idempotency_key != command.idempotency_key
        or typed.account_key != command.account_key
        or typed.route != command.route
        or typed.strategy_ref != command.strategy_ref
        or typed.operation != operation
        or typed.request_id != command.request_id
    ):
        raise ServiceCommandWireError("COMMAND_RESULT_BINDING_MISMATCH")
    record = result.get("record")
    if isinstance(record, Mapping):
        try:
            view = _order_view(record) if operation == "submit" else _cancel_view(record)
        except Exception:
            raise ServiceCommandWireError("COMMAND_RESULT_INVALID") from None
        expected = (
            typed_result_from_order_record(view, command, request_id=typed.request_id)
            if operation == "submit"
            else typed_result_from_cancel_record(view, command, request_id=typed.request_id)
        )
        if (expected.outcome, expected.provider_order_ref, expected.reason_code) != (
            typed.outcome,
            typed.provider_order_ref,
            typed.reason_code,
        ):
            raise ServiceCommandWireError("COMMAND_RESULT_RECORD_MISMATCH")
    return typed


def _match_binding(
    bindings: Mapping[WireScope, ServiceCommandBinding], scope: WireScope
) -> ServiceCommandBinding | None:
    exact = bindings.get(scope)
    if exact is not None:
        return exact
    matches = [
        binding
        for template, binding in bindings.items()
        if template.run_id is None and replace(scope, run_id=None) == template
    ]
    return matches[0] if len(matches) == 1 else None


class TypedOrderCommandGate:
    """Server side: the typed ``orders.submit``/``orders.cancel`` boundary.

    ``decode`` returns ``(command, internal_arguments)`` or raises
    :class:`ServiceCommandWireError` before any journal or provider work.
    A generic SDK request map is refused (``COMMAND_TYPED_REQUIRED``).
    """

    def __init__(self, bindings: Mapping[WireScope, ServiceCommandBinding]) -> None:
        if not isinstance(bindings, Mapping) or not bindings:
            raise ServiceCommandWireError("COMMAND_BINDING_INVALID")
        copied: dict[WireScope, ServiceCommandBinding] = {}
        for scope, binding in bindings.items():
            if type(scope) is not WireScope or type(binding) is not ServiceCommandBinding:
                raise ServiceCommandWireError("COMMAND_BINDING_INVALID")
            copied[scope] = binding
        self._bindings = MappingProxyType(copied)

    def decode(
        self, operation: str, scope: WireScope, arguments: Mapping[str, Any]
    ) -> tuple[StrategySubmitCommand | StrategyCancelCommand, Mapping[str, Any]]:
        from bt_api_base.wire_codec_v1 import WireCodecError, decode_typed_part

        if operation not in TYPED_COMMAND_OPERATIONS:
            raise ServiceCommandWireError("COMMAND_OPERATION_INVALID")
        if not isinstance(arguments, Mapping) or set(arguments) != {COMMAND_ARGUMENT}:
            raise ServiceCommandWireError("COMMAND_TYPED_REQUIRED")
        if type(scope) is not WireScope:
            raise ServiceCommandWireError("COMMAND_SCOPE_MISMATCH")
        binding = _match_binding(self._bindings, scope)
        if binding is None:
            raise ServiceCommandWireError("COMMAND_BINDING_UNAVAILABLE")
        try:
            command = decode_typed_part(arguments[COMMAND_ARGUMENT], kind=_PART_KIND[operation])
        except WireCodecError as error:
            # Includes WIRE_COMMAND_FINGERPRINT_MISMATCH: the claimed content
            # identity differs from the decoded content.
            raise ServiceCommandWireError(error.code) from None
        if type(command) is not _COMMAND_TYPE[operation]:
            raise ServiceCommandWireError("COMMAND_TYPE_INVALID")
        if command.strategy_ref != _owner_ref(scope) or (
            scope.run_id is not None and command.run_id != scope.run_id
        ):
            raise ServiceCommandWireError("COMMAND_SCOPE_MISMATCH")
        if (
            command.account_key != binding.account_key
            or command.route != binding.route
            or binding.instruments.get(command.instrument.instrument_id) != command.instrument
        ):
            raise ServiceCommandWireError("COMMAND_BINDING_MISMATCH")
        if type(command) is StrategySubmitCommand:
            expected_currency = (
                binding.price_currency if command.limit_price is not None else None
            )
            if (
                command.quantity_unit != binding.quantity_unit
                or command.price_currency != expected_currency
                or command.position_mode != binding.position_mode
                or command.hedge_class != binding.hedge_class
            ):
                raise ServiceCommandWireError("COMMAND_BINDING_MISMATCH")
            if command.valid_until_utc_ns is not None and command.valid_until_utc_ns <= 0:
                raise ServiceCommandWireError("COMMAND_UNREPRESENTABLE")
            payload = payload_from_typed_submit(command)
        else:
            if command.valid_until_utc_ns is not None:
                # The SDK cancel request has no deadline: refuse, never drop.
                raise ServiceCommandWireError("COMMAND_FIELD_UNREPRESENTABLE")
            payload = payload_from_typed_cancel(command)
        return command, MappingProxyType(dict(payload))

    @staticmethod
    def attach_result(
        command: StrategySubmitCommand | StrategyCancelCommand, reply: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Add the typed result projected from the reply's durable record."""

        from bt_api_base.wire_codec_v1 import encode_typed_part

        from bt_api_py.service_execution import _cancel_view, _order_view

        if (
            not isinstance(reply, Mapping)
            or reply.get("ok") is not True
            or not isinstance(reply.get("result"), Mapping)
        ):
            # A refusal (e.g. INTENT_CONFLICT carrying the *existing* record)
            # does not answer this command's content: no typed result.
            return reply
        record = reply["result"].get("record")
        if not isinstance(record, Mapping):
            return reply
        try:
            if type(command) is StrategySubmitCommand:
                typed = typed_result_from_order_record(
                    _order_view(record), command, request_id=command.request_id
                )
            else:
                typed = typed_result_from_cancel_record(
                    _cancel_view(record), command, request_id=command.request_id
                )
            part = encode_typed_part("strategy_result", typed)
        except Exception:
            # A record that cannot be projected (e.g. bound to another key
            # after a conflict) is returned without a typed result; the typed
            # client refuses such a reply instead of guessing.
            return reply
        result = dict(reply["result"])
        result[COMMAND_RESULT_FIELD] = part
        updated = dict(reply)
        updated["result"] = result
        return updated


__all__ = [
    "CANCEL_OPERATION",
    "COMMAND_ARGUMENT",
    "COMMAND_RESULT_FIELD",
    "SUBMIT_OPERATION",
    "TYPED_COMMAND_OPERATIONS",
    "ServiceCommandBinding",
    "ServiceCommandWireError",
    "TypedOrderCommandGate",
    "build_cancel_command",
    "build_submit_command",
    "command_arguments",
    "verify_command_reply",
    "payload_from_typed_cancel",
    "payload_from_typed_submit",
    "require_accepted_result",
    "typed_cancel_from_payload",
    "typed_result_from_cancel_record",
    "typed_result_from_order_record",
    "typed_submit_from_payload",
]
