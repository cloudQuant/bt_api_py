"""Module documentation"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = "1.0"
MAX_MESSAGE_BYTES = 1_000_000


class ForwardingError(RuntimeError):
    """Raised when a forwarding command or event cannot be processed."""


def now_ms() -> int:
    """now_ms function"""
    return int(time.time() * 1000)


def utc_now_iso() -> str:
    """utc_now_iso function"""
    return datetime.now(UTC).isoformat()


def normalize_market_symbol(symbol: Any) -> str:
    """normalize_market_symbol function"""
    return str(symbol or "").replace("/", "-")


def market_topic(exchange: str, market_type: str, symbol: str, event_type: str) -> str:
    """market_topic function"""
    exchange = str(exchange or "UNKNOWN").upper()
    market_type = str(market_type or "SPOT").upper()
    symbol = normalize_market_symbol(symbol)
    event_type = str(event_type or "event").lower()
    return f"md.{exchange}.{market_type}.{symbol}.{event_type}"


def private_topic(kind: str, account_id: str = "", strategy_id: str = "") -> str:
    """private_topic function"""
    kind = str(kind or "event").lower()
    if strategy_id:
        return f"strategy.{strategy_id}.{kind}"
    return f"acct.{account_id}.{kind}"


def _clean(value: Any) -> Any:
    if is_dataclass(value):
        return _clean(asdict(value))  # type: ignore[arg-type]  # is_dataclass 已确认是实例
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    return value


def _ensure_message_size(data: bytes) -> None:
    if len(data) > MAX_MESSAGE_BYTES:
        raise ForwardingError(
            f"Forwarding message exceeds maximum size: {len(data)} > {MAX_MESSAGE_BYTES} bytes"
        )


@dataclass
class MarketEvent:
    """Normalized market event published by the forwarding layer."""

    event_type: str
    exchange: str
    market_type: str
    symbol: str
    payload: dict[str, Any] = field(default_factory=dict)
    sequence_id: int = 0
    event_time: int = 0
    receive_time: int = 0
    source: str = ""
    schema_version: str = SCHEMA_VERSION
    topic: str = ""

    def __post_init__(self) -> None:
        if not self.event_time:
            self.event_time = now_ms()
        if not self.receive_time:
            self.receive_time = now_ms()
        if not self.topic:
            self.topic = market_topic(self.exchange, self.market_type, self.symbol, self.event_type)

    def to_dict(self) -> dict[str, Any]:
        """to_dict method"""
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MarketEvent:
        """from_dict method"""
        values = dict(data)
        values.pop("_message_type", None)
        return cls(**values)


@dataclass
class OrderCommand:
    """Order or query command sent to the central order router."""

    strategy_id: str
    account_id: str
    symbol: str = ""
    side: str = "buy"
    size: float = 0.0
    command_type: str = "place_order"
    order_type: str = "market"
    price: float | None = None
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
        if self.idempotency_key is None:
            self.idempotency_key = self.command_id
        if self.client_order_id is None and self.command_type == "place_order":
            self.client_order_id = self.idempotency_key
        if not self.request_fingerprint:
            self.request_fingerprint = command_request_fingerprint(self)

    def to_dict(self) -> dict[str, Any]:
        """to_dict method"""
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OrderCommand:
        """from_dict method"""
        values = dict(data)
        values.pop("_message_type", None)
        return cls(**values)


def command_request_fingerprint(command: OrderCommand) -> str:
    """Return a canonical, non-secret request-intent fingerprint.

    Transport envelope fields such as the generated command id and creation
    time are deliberately excluded.  The digest may be persisted or compared,
    while raw request material (including any opaque ``extra`` values) is
    never written to a receipt merely for idempotency checking.
    """
    intent = {
        "account_id": command.account_id,
        "client_order_id": command.client_order_id,
        "command_type": command.command_type,
        "exchange": command.exchange,
        "extra": command.extra,
        "market_type": command.market_type,
        "order_id": command.order_id,
        "order_type": command.order_type,
        "price": command.price,
        "query_command_id": command.query_command_id,
        "reduce_only": bool(command.reduce_only),
        "side": command.side,
        "size": command.size,
        "strategy_id": command.strategy_id,
        "symbol": command.symbol,
        "time_in_force": command.time_in_force,
    }
    encoded = json.dumps(_clean(intent), default=str, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass
class CancelCommand(OrderCommand):
    """Cancel command with the same envelope as OrderCommand."""

    command_type: str = "cancel_order"


@dataclass
class CommandAck:
    """Acknowledgement returned by the order router."""

    command_id: str
    idempotency_key: str
    accepted: bool
    status: str
    account_id: str = ""
    strategy_id: str = ""
    order_id: str | None = None
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    sequence_id: int = 0
    event_time: int = field(default_factory=now_ms)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """to_dict method"""
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CommandAck:
        """from_dict method"""
        values = dict(data)
        values.pop("_message_type", None)
        return cls(**values)


@dataclass
class PrivateEvent:
    """Private account, order, trade or error event."""

    event_type: str
    account_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    strategy_id: str = ""
    client_order_id: str = ""
    order_ref: str = ""
    external_order_id: str = ""
    order_sys_id: str = ""
    trade_id: str = ""
    id_source: str = ""
    raw_fields: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    sequence_id: int = 0
    event_time: int = field(default_factory=now_ms)
    schema_version: str = SCHEMA_VERSION
    topic: str = ""

    def __post_init__(self) -> None:
        self._populate_payload_contract_fields()
        if not self.topic:
            self.topic = private_topic(self.event_type, self.account_id, self.strategy_id)

    def _populate_payload_contract_fields(self) -> None:
        self.payload.setdefault("account_id", self.account_id)
        if self.strategy_id:
            self.payload.setdefault("strategy_id", self.strategy_id)
        for key in (
            "client_order_id",
            "order_ref",
            "external_order_id",
            "order_sys_id",
            "trade_id",
            "id_source",
            "trace_id",
        ):
            value = getattr(self, key)
            if value not in (None, ""):
                self.payload.setdefault(key, value)
        if self.raw_fields:
            self.payload.setdefault("raw_fields", dict(self.raw_fields))

    def to_dict(self) -> dict[str, Any]:
        """to_dict method"""
        return _clean(asdict(self))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PrivateEvent:
        """from_dict method"""
        values = dict(data)
        values.pop("_message_type", None)
        return cls(**values)


_MESSAGE_TYPES: dict[str, Callable[[Mapping[str, Any]], Any]] = {
    "market_event": MarketEvent.from_dict,
    "order_command": OrderCommand.from_dict,
    "command_ack": CommandAck.from_dict,
    "private_event": PrivateEvent.from_dict,
}


def serialize_message(message: Any) -> bytes:
    """serialize_message function"""
    if isinstance(message, MarketEvent):
        payload = message.to_dict()
        payload["_message_type"] = "market_event"
    elif isinstance(message, OrderCommand):
        payload = message.to_dict()
        payload["_message_type"] = "order_command"
    elif isinstance(message, CommandAck):
        payload = message.to_dict()
        payload["_message_type"] = "command_ack"
    elif isinstance(message, PrivateEvent):
        payload = message.to_dict()
        payload["_message_type"] = "private_event"
    elif isinstance(message, Mapping):
        payload = dict(message)
    else:
        raise ForwardingError(f"Unsupported forwarding message type: {type(message)!r}")

    try:
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ForwardingError(
            f"Forwarding message payload is not JSON serializable: {exc}"
        ) from exc
    _ensure_message_size(encoded)
    return encoded


def deserialize_message(data: bytes | str | Mapping[str, Any]) -> Any:
    """deserialize_message function"""
    if isinstance(data, Mapping):
        payload = dict(data)
    else:
        try:
            encoded = data if isinstance(data, bytes) else data.encode("utf-8")
            _ensure_message_size(encoded)
            payload = json.loads(encoded.decode("utf-8"))
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ForwardingError(f"Invalid forwarding message payload: {exc}") from exc

    if not isinstance(payload, dict):
        raise ForwardingError(
            f"Forwarding message payload must be an object, got {type(payload)!r}"
        )

    message_type = payload.get("_message_type")
    if not message_type:
        if "command_id" in payload and "accepted" in payload:
            message_type = "command_ack"
        elif "command_type" in payload:
            message_type = "order_command"
        elif str(payload.get("topic", "")).startswith("md."):
            message_type = "market_event"
        elif "account_id" in payload and "event_type" in payload:
            message_type = "private_event"

    factory = _MESSAGE_TYPES.get(str(message_type or ""))
    if factory is None:
        raise ForwardingError(f"Unknown forwarding message type: {message_type!r}")
    return factory(payload)
