"""Light SDK-side client DTOs projected over the ``bt_api_gateway.command.v1`` envelope.

This module is the SDK half of the gateway write wire.  It is deliberately
self-contained: nothing here imports ``bt_api_gateway`` (a server-side package)
and nothing imports the retired ``bt_api_py.forwarding`` tree.  The DTO field
sets mirror the historical forwarding order-intent and acknowledgement wire
shapes (field-for-field), so the client path
``BtApi -> DirectBackend wire hook -> transport client`` keeps the same
serialized contract while the server side is the durable command router.

Write outcomes are normalized onto the SDK write-contract keys consumed by
``bt_api_py._normalization.order``: ``status``, ``execution_unknown``,
``terminal_confirmed``, ``definite_reject``, ``order_id`` and
``client_order_id``.  A missing or unrecognized router status maps to an
unknown outcome (never blind-retry); raw exception text is never projected
into an outcome row.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from bt_api_py._contracts.errors import CapabilityNotSupportedError, NormalizedApiError

GATEWAY_COMMAND_WIRE_SCHEMA = "bt_api_gateway.command.v1"
SCHEMA_VERSION = "1.0"
MAX_MESSAGE_BYTES = 1_000_000

__all__ = [
    "CancelCommand",
    "CommandAck",
    "GATEWAY_COMMAND_WIRE_SCHEMA",
    "GatewayWireClient",
    "GatewayWireConfig",
    "GatewayWireError",
    "MAX_MESSAGE_BYTES",
    "OrderCommand",
    "SCHEMA_VERSION",
    "build_intent",
    "canonical_decimal_wire",
    "command_from_envelope",
    "command_to_envelope",
    "outcome_from_ack",
    "outcome_from_dispatch_status",
]


class GatewayWireError(ValueError):
    """A gateway wire DTO cannot be built or parsed (strict, client side)."""


def now_ms() -> int:
    """Current epoch time in milliseconds."""
    return int(time.time() * 1000)


def utc_now_iso() -> str:
    """Current UTC time in ISO-8601 text."""
    return datetime.now(UTC).isoformat()


def canonical_decimal_wire(value: Any, *, optional: bool = False) -> str | None:
    """Encode a finite decimal as stable, exponent-free JSON text."""
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise GatewayWireError("Boolean is not a valid decimal wire value")
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise GatewayWireError("Invalid decimal wire value") from None
    if not decimal_value.is_finite():
        raise GatewayWireError("Decimal wire value must be finite")
    result = format(decimal_value, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if result in {"", "-0"} else result


_INTENT_FIELDS = frozenset(
    {
        "account_id",
        "client_order_id",
        "command_id",
        "command_type",
        "created_at",
        "exchange",
        "extra",
        "idempotency_key",
        "market_type",
        "order_id",
        "order_type",
        "price",
        "query_command_id",
        "reduce_only",
        "request_fingerprint",
        "schema_version",
        "side",
        "size",
        "strategy_id",
        "symbol",
        "time_in_force",
    }
)

_ACK_FIELDS = frozenset(
    {
        "accepted",
        "account_id",
        "command_id",
        "event_time",
        "idempotency_key",
        "order_id",
        "payload",
        "reason",
        "schema_version",
        "sequence_id",
        "status",
        "strategy_id",
    }
)

_ENVELOPE_FIELDS = frozenset({"command", "schema"})


def _reject_unknown_fields(values: Mapping[str, Any], expected: frozenset[str], name: str) -> None:
    supplied = {str(key) for key in values}
    unexpected = supplied - expected - {"_message_type"}
    if unexpected:
        detail = ",".join(sorted(unexpected))
        raise GatewayWireError(f"invalid {name} fields: unknown={detail}")


@dataclass
class OrderCommand:
    """Order or cancel intent sent over the gateway command wire.

    Field set mirrors the retired forwarding ``OrderCommand`` (schema.py
    :129-169) so serialized intent text stays stable across the migration.
    """

    strategy_id: str = ""
    account_id: str = ""
    symbol: str = ""
    side: str = "buy"
    size: str = "0"
    command_type: str = "place_order"
    order_type: str = "market"
    price: str | None = None
    exchange: str = ""
    market_type: str = ""
    time_in_force: str = "GTC"
    reduce_only: bool = False
    client_order_id: str | None = None
    order_id: str | None = None
    idempotency_key: str | None = None
    query_command_id: str | None = None
    request_fingerprint: str = ""
    command_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=utc_now_iso)
    extra: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.command_type = str(self.command_type or "place_order").lower()
        self.size = canonical_decimal_wire(self.size)
        self.price = canonical_decimal_wire(self.price, optional=True)
        if self.idempotency_key is None:
            self.idempotency_key = self.command_id
        if self.client_order_id is None and self.command_type == "place_order":
            self.client_order_id = self.idempotency_key
        if not isinstance(self.extra, Mapping):
            raise GatewayWireError("extra must be a mapping")
        self.extra = {str(key): value for key, value in self.extra.items()}

    def to_dict(self) -> dict[str, Any]:
        """Return the exact JSON-compatible intent mapping (all fields present)."""
        values = asdict(self)
        values["extra"] = dict(self.extra)
        return values

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OrderCommand:
        """Rebuild an intent from its mapping form, rejecting unknown fields."""
        if not isinstance(data, Mapping):
            raise GatewayWireError("order command must be a mapping")
        _reject_unknown_fields(data, _INTENT_FIELDS, "order command")
        values = {name: data[name] for name in _INTENT_FIELDS if name in data}
        values.pop("extra", None)
        extra = dict(data.get("extra") or {})
        try:
            return cls(**values, extra=extra)
        except (TypeError, ValueError) as error:
            if isinstance(error, GatewayWireError):
                raise
            raise GatewayWireError("invalid order command") from error


@dataclass
class CancelCommand(OrderCommand):
    """Cancel intent with the same envelope as :class:`OrderCommand`."""

    command_type: str = "cancel_order"


@dataclass
class CommandAck:
    """Acknowledgement returned by the gateway command wire.

    Field set mirrors the retired forwarding ``CommandAck`` (schema.py
    :215-242).  Missing optional fields fall back to their defaults so acks
    produced with cleaned/partial mappings remain loadable.
    """

    command_id: str = ""
    idempotency_key: str = ""
    accepted: bool = False
    status: str = ""
    account_id: str = ""
    strategy_id: str = ""
    order_id: str | None = None
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    sequence_id: int = 0
    event_time: int = field(default_factory=now_ms)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise GatewayWireError("ack payload must be a mapping")
        self.payload = {str(key): value for key, value in self.payload.items()}

    def to_dict(self) -> dict[str, Any]:
        """Return the exact JSON-compatible ack mapping (all fields present)."""
        values = asdict(self)
        values["payload"] = dict(self.payload)
        return values

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CommandAck:
        """Rebuild an ack from its mapping form, rejecting unknown fields."""
        if not isinstance(data, Mapping):
            raise GatewayWireError("command ack must be a mapping")
        _reject_unknown_fields(data, _ACK_FIELDS, "command ack")
        values = {name: data[name] for name in _ACK_FIELDS if name in data}
        values.pop("payload", None)
        payload = dict(data.get("payload") or {})
        try:
            return cls(**values, payload=payload)
        except (TypeError, ValueError) as error:
            if isinstance(error, GatewayWireError):
                raise
            raise GatewayWireError("invalid command ack") from error


def command_to_envelope(command: OrderCommand) -> dict[str, Any]:
    """Project one intent into the versioned transport envelope.

    The envelope carries the client intent only; durable identity, scope and
    authorization are added by the authenticated server side.
    """
    if not isinstance(command, OrderCommand):
        raise TypeError("OrderCommand is required")
    return {"schema": GATEWAY_COMMAND_WIRE_SCHEMA, "command": command.to_dict()}


def command_from_envelope(value: Mapping[str, Any]) -> OrderCommand:
    """Parse one versioned transport envelope, rejecting unknown fields."""
    if not isinstance(value, Mapping):
        raise GatewayWireError("gateway wire envelope must be a mapping")
    _reject_unknown_fields(value, _ENVELOPE_FIELDS, "envelope")
    supplied = {str(key) for key in value}
    missing = sorted({"command", "schema"} - supplied)
    if missing:
        raise GatewayWireError("invalid envelope fields: missing=" + ",".join(missing))
    if value["schema"] != GATEWAY_COMMAND_WIRE_SCHEMA:
        raise GatewayWireError("unsupported gateway command schema")
    raw_command = value["command"]
    if not isinstance(raw_command, Mapping):
        raise GatewayWireError("gateway command must be a mapping")
    return OrderCommand.from_dict(raw_command)


@dataclass(frozen=True)
class GatewayWireConfig:
    """Client-side replacement for the retired ``ForwardingConfig`` input.

    The endpoints identify the gateway transport; ``dispatch`` is the attached
    transport entry point used by :class:`GatewayWireClient`.  Without a
    dispatch the client stays inert (no socket is opened in this SDK round).
    """

    command_endpoint: str
    market_endpoint: str
    private_endpoint: str
    account_id: str
    strategy_id: str
    market_read_timeout_ms: int = 250
    max_cache_age_ms: int = 5_000
    dispatch: Callable[..., Any] | None = None

    def __post_init__(self) -> None:
        if self.market_read_timeout_ms < 0:
            raise ValueError("market_read_timeout_ms must be non-negative")
        if self.max_cache_age_ms < 0:
            raise ValueError("max_cache_age_ms must be non-negative")


class GatewayWireClient:
    """Thin SDK client over the gateway command wire (durable-router peer).

    The client owns only the client half: it projects the intent into the
    versioned envelope and parses the returned acknowledgement.  Server-side
    durability, authorization and provider dispatch stay on the gateway side.
    """

    def __init__(self, config: GatewayWireConfig) -> None:
        if not isinstance(config, GatewayWireConfig):
            raise TypeError("GatewayWireConfig is required")
        self._config = config

    @property
    def account_id(self) -> str:
        return self._config.account_id

    @property
    def strategy_id(self) -> str:
        return self._config.strategy_id

    def _send_command_sync(self, command: OrderCommand) -> CommandAck:
        """Send one intent and return the parsed acknowledgement.

        A transport failure raises; the SDK caller converts it into an unknown
        outcome (or a definite reject when router status proves rejection).
        """
        envelope = command_to_envelope(command)
        dispatch = self._config.dispatch
        if dispatch is None:
            raise CapabilityNotSupportedError(
                "gateway_wire",
                detail="no transport dispatch is attached to this gateway wire client",
            )
        value = dispatch(envelope)
        if isinstance(value, CommandAck):
            return value
        if isinstance(value, Mapping):
            payload = value.get("ack", value)
            if isinstance(payload, Mapping):
                return CommandAck.from_dict(payload)
        raise GatewayWireError("transport dispatch must return a gateway command ack mapping")

    def command_status(self, command_id: str) -> Any:
        """Read one durable command status row when the transport supports it."""
        probe = getattr(self._config.dispatch, "command_status", None)
        if callable(probe):
            return probe(command_id)
        return None

    def disconnect(self) -> None:
        """Release the client (no persistent resource is held)."""


_NATIVE_INTENT_FIELDS = (
    "position_side",
    "position_id",
    "position_mode",
    "offset",
    "exchange_id",
    "front_id",
    "session_id",
    "order_ref",
)


def _native_intent(operation: str, exchange_name: str, request: Any) -> dict[str, Any]:
    """Map typed position intent fields onto the wire ``extra`` payload.

    Mirrors the retired ZmqBtApiBackend._native_intent semantics: native MT5
    lots map to ``quantity_unit=lots`` and CTP contracts stay integral.
    """
    extra = {
        name: getattr(request, name)
        for name in _NATIVE_INTENT_FIELDS
        if getattr(request, name, None) is not None
    }
    venue = str(exchange_name).partition("___")[0].upper()
    unit = getattr(request, "quantity_unit", None)
    if venue in {"CTP", "MT5"}:
        if unit is not None:
            allowed = {"contracts", "native"} if venue == "CTP" else {"lots", "native"}
            if unit not in allowed:
                raise CapabilityNotSupportedError(
                    operation,
                    detail="native gateway requires explicit native quantity units",
                    definite_reject=operation in {"make_order", "cancel_order"},
                )
            quantity = getattr(request, "quantity", None)
            if (
                venue == "CTP"
                and isinstance(quantity, Decimal)
                and quantity != quantity.to_integral_value()
            ):
                raise NormalizedApiError(
                    operation, "fractional_contracts", definite_reject=True
                )
            extra["quantity_unit"] = "contracts" if venue == "CTP" else "lots"
        return extra
    if extra or unit not in (None, "base"):
        raise CapabilityNotSupportedError(
            operation,
            detail="native position intent is unsupported for this forwarding venue",
            definite_reject=operation in {"make_order", "cancel_order"},
        )
    return {}


def _enum_value(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = getattr(value, "value", value)
    return str(text)


def build_intent(
    operation: str,
    exchange_name: str,
    request: Any,
    *,
    strategy_id: str = "",
    command_id: str | None = None,
) -> OrderCommand:
    """Build the wire intent for one typed write request.

    ``operation`` is ``make_order`` or ``cancel_order``; the field mapping
    mirrors the retired forwarding backend command construction.
    """
    venue = str(exchange_name).partition("___")[0].upper()
    _, separator, market_type = str(exchange_name).partition("___")
    if not separator or not venue or not market_type:
        raise ValueError("exchange_name must be '<EXCHANGE>___<MARKET_TYPE>'")
    account_id = str(getattr(request, "account_id", "") or "")
    symbol = str(getattr(request, "symbol", "") or "")
    client_order_id = getattr(request, "client_order_id", None)
    idempotency_key = getattr(request, "idempotency_key", None) or None
    if operation == "make_order":
        extra = _native_intent(operation, exchange_name, request)
        extra["reduce_only"] = bool(getattr(request, "reduce_only", False))
        idempotency_key = idempotency_key or client_order_id
        return OrderCommand(
            strategy_id=str(strategy_id or ""),
            account_id=account_id,
            symbol=symbol,
            side=_enum_value(getattr(request, "side", None), "buy"),
            size=getattr(request, "quantity", "0"),
            command_type="place_order",
            order_type=_enum_value(getattr(request, "order_type", None), "market"),
            price=getattr(request, "price", None),
            exchange=venue,
            market_type=str(market_type).upper(),
            time_in_force=str(getattr(request, "time_in_force", "") or "GTC"),
            reduce_only=bool(getattr(request, "reduce_only", False)),
            client_order_id=client_order_id,
            idempotency_key=idempotency_key,
            command_id=command_id or str(uuid.uuid4()),
            extra=extra,
        )
    if operation == "cancel_order":
        extra = _native_intent(operation, exchange_name, request)
        order_id = getattr(request, "order_id", None)
        idempotency_key = idempotency_key or (
            f"cancel:{account_id}:{order_id or client_order_id}"
        )
        return OrderCommand(
            strategy_id=str(strategy_id or ""),
            account_id=account_id,
            symbol=symbol,
            command_type="cancel_order",
            client_order_id=client_order_id,
            order_id=order_id,
            idempotency_key=idempotency_key,
            exchange=venue,
            market_type=str(market_type).upper(),
            command_id=command_id or str(uuid.uuid4()),
            extra=extra,
        )
    raise ValueError("wire writes support make_order and cancel_order only")


_OUTCOME_KEYS = (
    "status",
    "execution_unknown",
    "terminal_confirmed",
    "definite_reject",
    "order_id",
    "client_order_id",
)

_DEFINITE_STATUSES = frozenset({"rejected", "expired", "failed"})
_SUCCEEDED_STATUSES = frozenset({"succeeded", "returned_unverified"})
_EXPLICIT_REJECT_STATUSES = frozenset({"rejected", "error"})


def _outcome_row(
    status: str,
    *,
    execution_unknown: bool = False,
    terminal_confirmed: bool = False,
    definite_reject: bool = False,
    order_id: Any = None,
    command: OrderCommand | None = None,
) -> dict[str, Any]:
    client_order_id = getattr(command, "client_order_id", None) if command is not None else None
    return {
        "status": str(status),
        "execution_unknown": bool(execution_unknown),
        "terminal_confirmed": bool(terminal_confirmed),
        "definite_reject": bool(definite_reject),
        "order_id": str(order_id or ""),
        "client_order_id": str(client_order_id or ""),
    }


def _unknown_outcome(command: OrderCommand | None) -> dict[str, Any]:
    """An unverifiable write: not a rejection, never blind-retried."""
    return _outcome_row("submitted", execution_unknown=True, order_id=None, command=command)


def _definite_outcome(command: OrderCommand | None, order_id: Any = None) -> dict[str, Any]:
    return _outcome_row(
        "rejected",
        terminal_confirmed=True,
        definite_reject=True,
        order_id=order_id,
        command=command,
    )


def _normal_outcome(
    command: OrderCommand | None, status: str, order_id: Any = None
) -> dict[str, Any]:
    return _outcome_row(str(status or "accepted"), order_id=order_id, command=command)


def outcome_from_ack(ack: Any, *, command: OrderCommand | None = None) -> dict[str, Any]:
    """Normalize one wire acknowledgement onto the SDK write-contract keys.

    Explicit adapter/unknown evidence in the ack payload wins over the status
    text (unknown precedence); an ack with no acceptance evidence maps to the
    unknown outcome.
    """
    if isinstance(ack, CommandAck):
        value: dict[str, Any] = ack.to_dict()
    elif hasattr(ack, "to_dict"):
        value = dict(ack.to_dict())
    elif isinstance(ack, Mapping):
        value = dict(ack)
    else:
        raise GatewayWireError("command ack must be a mapping or CommandAck")
    payload = value.get("payload")
    payload = dict(payload) if isinstance(payload, Mapping) else {}
    status_text = str(value.get("status") or "").lower()
    order_id = value.get("order_id") or payload.get("order_id")
    if payload.get("execution_unknown") is True or status_text == "unknown":
        return _unknown_outcome(command)
    accepted = value.get("accepted")
    if accepted is True and status_text not in _EXPLICIT_REJECT_STATUSES:
        return _normal_outcome(command, status_text or "accepted", order_id)
    if status_text in _EXPLICIT_REJECT_STATUSES or accepted is False:
        return _definite_outcome(command, order_id)
    return _unknown_outcome(command)


def _dispatch_status_text(status_row: Any) -> str:
    if status_row is None:
        return ""
    status = getattr(status_row, "status", status_row)
    if isinstance(status, Mapping):
        status = status.get("status", "")
    text = getattr(status, "value", status)
    return str(text or "").lower()


def _dispatch_outcome(status_row: Any) -> dict[str, Any]:
    outcome = getattr(status_row, "outcome", None)
    if outcome is None and isinstance(status_row, Mapping):
        outcome = status_row.get("outcome")
    return dict(outcome) if isinstance(outcome, Mapping) else {}


def outcome_from_dispatch_status(
    status_row: Any, *, command: OrderCommand | None = None
) -> dict[str, Any]:
    """Normalize a durable router status row onto the SDK write-contract keys.

    ``status_row`` is the (duck-typed) result of the transport's
    ``command_status`` probe -- e.g. a ``bt_api_gateway`` ``GatewayDispatchResult``.
    Mapping: UNKNOWN / PENDING / DISPATCHING / missing row -> unknown outcome
    (the dispatch may or may not have reached the provider); REJECTED /
    EXPIRED / FAILED -> definite reject; SUCCEEDED / RETURNED_UNVERIFIED ->
    normal acceptance with the outcome payload's order id.
    """
    status_text = _dispatch_status_text(status_row)
    outcome = _dispatch_outcome(status_row)
    order_id = outcome.get("order_id") or outcome.get("id")
    if status_text in _DEFINITE_STATUSES:
        return _definite_outcome(command, order_id)
    if status_text in _SUCCEEDED_STATUSES:
        inner = str(outcome.get("status") or "").lower()
        if outcome.get("execution_unknown") is True or inner == "unknown":
            return _unknown_outcome(command)
        if inner in _EXPLICIT_REJECT_STATUSES:
            return _definite_outcome(command, order_id)
        return _normal_outcome(command, inner or "accepted", order_id)
    return _unknown_outcome(command)
