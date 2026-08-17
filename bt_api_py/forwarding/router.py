"""Module documentation"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from typing import Any

from bt_api_base.logging_factory import get_logger

from bt_api_py.brokers.base import BrokerAdapter
from bt_api_py.brokers.errors import BrokerError
from bt_api_py.brokers.types import CancelOrderRequest, OrderRequest
from bt_api_py.forwarding.memory import InMemoryForwardingBus
from bt_api_py.forwarding.schema import CommandAck, OrderCommand, PrivateEvent
from bt_api_py.forwarding.state import SQLiteStateStore

logger = get_logger("forwarding.router")

_VALID_SIDES = frozenset({"buy", "sell"})
_VALID_ORDER_TYPES = frozenset({"limit", "market"})


def _normalize_side(value: Any) -> str:
    side = str(value).strip().lower()
    if side not in _VALID_SIDES:
        raise ValueError(f"invalid side {value!r}: must be 'buy' or 'sell'")
    return side


def _normalize_order_type(value: Any) -> str:
    order_type = str(value).strip().lower()
    if order_type not in _VALID_ORDER_TYPES:
        raise ValueError(f"invalid order_type {value!r}: must be 'limit' or 'market'")
    return order_type


@dataclass
class RiskRuleSet:
    """Class RiskRuleSet"""
    allowed_accounts: set[str] | None = None
    allowed_symbols: set[str] | None = None
    max_order_size: float | None = None
    kill_switch: bool = False


class OrderRouter:
    """Central account/order gateway for forwarded trading commands."""

    def __init__(
        self,
        adapter: BrokerAdapter,
        *,
        bus: InMemoryForwardingBus | None = None,
        risk_rules: RiskRuleSet | None = None,
        state_store: SQLiteStateStore | None = None,
    ) -> None:
        """__init__ method"""
        self.adapter = adapter
        self.bus = bus
        self.risk_rules = risk_rules or RiskRuleSet()
        self.state_store = state_store
        self._acks_by_idempotency_key: dict[str, CommandAck] = {}
        self._sequence_id = 0
        if self.bus is not None:
            self.bus.set_command_handler(self.handle_command)

    async def connect(self) -> bool:
        """connect method"""
        return await self.adapter.connect()

    async def disconnect(self) -> bool:
        """disconnect method"""
        return await self.adapter.disconnect()

    async def health(self) -> dict[str, Any]:
        """health method"""
        try:
            adapter_health = await self.adapter.health()
        except Exception as exc:
            adapter_health = {
                "connected": False,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        return {
            "adapter": adapter_health,
            "cached_ack_count": len(self._acks_by_idempotency_key),
            "sequence_id": self._sequence_id,
            "state_store_enabled": self.state_store is not None,
            "bus_attached": self.bus is not None,
            "risk": {
                "allowed_account_count": (
                    None
                    if self.risk_rules.allowed_accounts is None
                    else len(self.risk_rules.allowed_accounts)
                ),
                "allowed_symbol_count": (
                    None
                    if self.risk_rules.allowed_symbols is None
                    else len(self.risk_rules.allowed_symbols)
                ),
                "max_order_size": self.risk_rules.max_order_size,
                "kill_switch": self.risk_rules.kill_switch,
            },
        }

    async def handle_command(self, command: OrderCommand) -> CommandAck:
        """handle_command method"""
        command_type = str(command.command_type or "").lower()
        if command_type == "place_order":
            return await self.place_order(command)
        if command_type == "cancel_order":
            return await self.cancel_order(command)
        if command_type == "get_account":
            account = await self.adapter.get_account(command.account_id)
            return self._ack(command, True, "ok", payload=_snapshot_to_dict(account))
        if command_type == "list_positions":
            positions = await self.adapter.list_positions(command.account_id)
            return self._ack(
                command,
                True,
                "ok",
                payload={"positions": [_snapshot_to_dict(item) for item in positions]},
            )
        if command_type == "list_orders":
            orders = await self.adapter.list_orders(command.account_id)
            return self._ack(
                command,
                True,
                "ok",
                payload={"orders": [_snapshot_to_dict(item) for item in orders]},
            )
        return self._reject(command, f"unsupported command_type: {command.command_type}")

    async def place_order(self, command: OrderCommand) -> CommandAck:
        """place_order method"""
        cached = self._get_cached_ack(command)
        if cached is not None:
            return cached

        risk_error = self._validate_order(command)
        if risk_error:
            ack = self._reject(command, risk_error)
            self._remember_ack(ack)
            return ack

        try:
            side = _normalize_side(command.side)
            order_type = _normalize_order_type(command.order_type)
        except ValueError as exc:
            ack = self._reject(command, str(exc), payload={"error_code": "INVALID_PARAM"})
            self._publish_error(command, str(exc), error_code="INVALID_PARAM")
            return ack  # 注意:不 _remember_ack,输入错误是调用方问题,不占幂等表

        request = OrderRequest(
            account_id=command.account_id,
            symbol=command.symbol,
            side=side,
            quantity=float(command.size),
            order_type=order_type,
            price=command.price,
            client_order_id=command.client_order_id,
            idempotency_key=command.idempotency_key,
            extra=dict(command.extra or {}),
        )

        try:
            order = await self.adapter.place_order(request)
        except BrokerError as exc:
            ack = self._reject(command, str(exc), payload={"error_code": str(exc.code)})
            if not exc.retryable:
                self._remember_ack(ack)
            self._publish_error(command, str(exc), error_code=str(exc.code))
            return ack
        except Exception as exc:
            ack = self._reject(command, str(exc), payload={"error_code": type(exc).__name__})
            self._publish_error(command, str(exc), error_code=type(exc).__name__)
            return ack

        payload = _snapshot_to_dict(order)
        ack = self._ack(
            command,
            True,
            str(payload.get("status") or "accepted"),
            order_id=str(payload.get("order_id") or ""),
            payload=payload,
        )
        self._remember_ack(ack)
        self._publish_order_update(command, payload)
        if str(payload.get("status") or "").lower() == "filled":
            self._publish_trade_update(command, payload)
        await self._publish_account_state(command.account_id, command.strategy_id)
        return ack

    async def cancel_order(self, command: OrderCommand) -> CommandAck:
        """cancel_order method"""
        cached = self._get_cached_ack(command)
        if cached is not None:
            return cached

        if not command.order_id:
            ack = self._reject(command, "cancel_order requires order_id")
            self._remember_ack(ack)
            return ack

        try:
                order = await self.adapter.cancel_order(
                CancelOrderRequest(
                    account_id=command.account_id,
                    order_id=str(command.order_id),
                    symbol=command.symbol or None,
                    idempotency_key=command.idempotency_key,
                )
            )
        except BrokerError as exc:
            ack = self._reject(command, str(exc), payload={"error_code": str(exc.code)})
            if not exc.retryable:
                self._remember_ack(ack)
            self._publish_error(command, str(exc), error_code=str(exc.code))
            return ack
        except Exception as exc:
            ack = self._reject(command, str(exc), payload={"error_code": type(exc).__name__})
            self._publish_error(command, str(exc), error_code=type(exc).__name__)
            return ack

        payload = _snapshot_to_dict(order)
        ack = self._ack(
            command,
            True,
            str(payload.get("status") or "cancelled"),
            order_id=str(payload.get("order_id") or command.order_id),
            payload=payload,
        )
        self._remember_ack(ack)
        self._publish_order_update(command, payload)
        return ack

    def _get_cached_ack(self, command: OrderCommand) -> CommandAck | None:
        key = str(command.idempotency_key)
        cached = self._acks_by_idempotency_key.get(key)
        if cached is not None:
            return cached
        if self.state_store is None:
            return None
        cached = self.state_store.get_command_ack(key)
        if cached is not None:
            self._acks_by_idempotency_key[key] = cached
        return cached

    def _remember_ack(self, ack: CommandAck) -> None:
        self._acks_by_idempotency_key[str(ack.idempotency_key)] = ack
        if self.state_store is not None:
            self.state_store.save_command_ack(ack)

    def _validate_order(self, command: OrderCommand) -> str | None:
        rules = self.risk_rules
        if rules.kill_switch:
            return "order router kill switch is enabled"
        if rules.allowed_accounts is not None and command.account_id not in rules.allowed_accounts:
            return f"account is not allowed: {command.account_id}"
        if rules.allowed_symbols is not None and command.symbol not in rules.allowed_symbols:
            return f"symbol is not allowed: {command.symbol}"
        if not command.account_id:
            return "account_id is required"
        if not command.strategy_id:
            return "strategy_id is required"
        if not command.symbol:
            return "symbol is required"
        if float(command.size or 0.0) <= 0.0:
            return "size must be positive"
        if rules.max_order_size is not None and abs(float(command.size)) > rules.max_order_size:
            return f"size exceeds max_order_size: {rules.max_order_size}"
        return None

    def _ack(
        self,
        command: OrderCommand,
        accepted: bool,
        status: str,
        *,
        order_id: str | None = None,
        reason: str = "",
        payload: dict[str, Any] | None = None,
    ) -> CommandAck:
        self._sequence_id += 1
        return CommandAck(
            command_id=command.command_id,
            idempotency_key=str(command.idempotency_key or command.command_id),
            accepted=accepted,
            status=status,
            account_id=command.account_id,
            strategy_id=command.strategy_id,
            order_id=order_id,
            reason=reason,
            payload=dict(payload or {}),
            sequence_id=self._sequence_id,
        )

    def _reject(
        self,
        command: OrderCommand,
        reason: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> CommandAck:
        return self._ack(command, False, "rejected", reason=reason, payload=payload)

    def _publish_order_update(self, command: OrderCommand, payload: dict[str, Any]) -> None:
        event_payload = _order_event_payload(command, payload)
        self._publish_private_event(
            PrivateEvent(
                event_type="orders",
                account_id=command.account_id,
                strategy_id=command.strategy_id,
                payload=event_payload,
                client_order_id=str(event_payload.get("client_order_id") or ""),
                order_ref=str(event_payload.get("order_ref") or ""),
                external_order_id=str(event_payload.get("external_order_id") or ""),
                order_sys_id=str(event_payload.get("order_sys_id") or ""),
                id_source=str(event_payload.get("id_source") or ""),
                raw_fields=dict(event_payload.get("raw_fields") or {}),
            )
        )

    def _publish_trade_update(self, command: OrderCommand, payload: dict[str, Any]) -> None:
        trade_payload = _trade_event_payload(command, payload)
        self._publish_private_event(
            PrivateEvent(
                event_type="trades",
                account_id=command.account_id,
                strategy_id=command.strategy_id,
                payload=trade_payload,
                client_order_id=str(trade_payload.get("client_order_id") or ""),
                order_ref=str(trade_payload.get("order_ref") or ""),
                external_order_id=str(trade_payload.get("external_order_id") or ""),
                order_sys_id=str(trade_payload.get("order_sys_id") or ""),
                trade_id=str(trade_payload.get("trade_id") or ""),
                id_source=str(trade_payload.get("id_source") or ""),
                raw_fields=dict(trade_payload.get("raw_fields") or {}),
            )
        )

    def _publish_error(self, command: OrderCommand, message: str, *, error_code: str = "") -> None:
        self._publish_private_event(
            PrivateEvent(
                event_type="errors",
                account_id=command.account_id,
                strategy_id=command.strategy_id,
                payload={
                    "kind": "error",
                    "command_id": command.command_id,
                    "error_code": error_code,
                    "error_msg": message,
                },
            )
        )

    async def _publish_account_state(self, account_id: str, strategy_id: str) -> None:
        try:
            account = await self.adapter.get_account(account_id)
            self._publish_private_event(
                PrivateEvent(
                    event_type="balances",
                    account_id=account_id,
                    strategy_id=strategy_id,
                    payload={"kind": "account", **_snapshot_to_dict(account)},
                )
            )
            positions = await self.adapter.list_positions(account_id)
            for position in positions:
                self._publish_private_event(
                    PrivateEvent(
                        event_type="positions",
                        account_id=account_id,
                        strategy_id=strategy_id,
                        payload={"kind": "position", **_snapshot_to_dict(position)},
                    )
                )
        except Exception as exc:
            logger.warning(
                "Failed to publish account state after order command: "
                f"account_id={account_id}, strategy_id={strategy_id}, "
                f"error_type={type(exc).__name__}, error={exc}"
            )
            return

    def _publish_private_event(self, event: PrivateEvent) -> None:
        if self.state_store is not None:
            self.state_store.save_private_event(event)
        if self.bus is not None:
            self.bus.publish_private(event)


def _snapshot_to_dict(snapshot: Any) -> dict[str, Any]:
    if is_dataclass(snapshot):
        data = asdict(snapshot)
    elif isinstance(snapshot, dict):
        data = dict(snapshot)
    else:
        data = dict(getattr(snapshot, "__dict__", {}) or {})

    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _order_event_payload(command: OrderCommand, payload: dict[str, Any]) -> dict[str, Any]:
    raw_fields = dict(payload.get("raw_fields") or payload.get("details") or {})
    order_sys_id = _first_text(payload, "order_sys_id", "OrderSysID")
    external_order_id = _first_text(payload, "external_order_id", "order_id")
    if not external_order_id and order_sys_id:
        external_order_id = order_sys_id
    id_source = str(payload.get("id_source") or ("exchange" if external_order_id else "local_pending"))
    order_ref = _first_text(payload, "order_ref", "OrderRef") or str(command.client_order_id or "")
    client_order_id = _first_text(payload, "client_order_id") or str(command.client_order_id or "")
    symbol = _first_text(payload, "symbol", "data_name", "instrument") or command.symbol
    return {
        "kind": "order",
        **payload,
        "account_id": payload.get("account_id") or command.account_id,
        "strategy_id": payload.get("strategy_id") or command.strategy_id,
        "client_order_id": client_order_id,
        "order_ref": order_ref,
        "external_order_id": external_order_id,
        "order_sys_id": order_sys_id or external_order_id,
        "symbol": symbol,
        "data_name": payload.get("data_name") or symbol,
        "side": payload.get("side") or command.side,
        "size": payload.get("quantity", payload.get("size", command.size)),
        "filled": payload.get("filled_quantity", payload.get("filled", 0.0)),
        "price": payload.get("average_price", payload.get("price", command.price)),
        "id_source": id_source,
        "raw_fields": raw_fields,
    }


def _trade_event_payload(command: OrderCommand, payload: dict[str, Any]) -> dict[str, Any]:
    raw_fields = dict(payload.get("raw_fields") or payload.get("details") or {})
    order_sys_id = _first_text(payload, "order_sys_id", "OrderSysID")
    external_order_id = _first_text(payload, "external_order_id", "order_id")
    if not external_order_id and order_sys_id:
        external_order_id = order_sys_id
    trade_id = _first_text(payload, "trade_id", "TradeID")
    id_source = str(payload.get("id_source") or ("exchange" if trade_id else "local_simulated"))
    if not trade_id:
        trade_id = f"{external_order_id or command.command_id}-fill"
    order_ref = _first_text(payload, "order_ref", "OrderRef") or str(command.client_order_id or "")
    client_order_id = _first_text(payload, "client_order_id") or str(command.client_order_id or "")
    symbol = _first_text(payload, "symbol", "data_name", "instrument") or command.symbol
    return {
        "kind": "trade",
        **payload,
        "account_id": payload.get("account_id") or command.account_id,
        "strategy_id": payload.get("strategy_id") or command.strategy_id,
        "client_order_id": client_order_id,
        "trade_id": trade_id,
        "order_id": payload.get("order_id"),
        "external_order_id": external_order_id,
        "order_sys_id": order_sys_id or external_order_id,
        "order_ref": order_ref,
        "symbol": symbol,
        "data_name": payload.get("data_name") or symbol,
        "side": payload.get("side") or command.side,
        "size": payload.get("filled_quantity", payload.get("size", command.size)),
        "price": payload.get("average_price", payload.get("price", command.price)),
        "status": payload.get("status", "filled"),
        "id_source": id_source,
        "raw_fields": raw_fields,
    }
