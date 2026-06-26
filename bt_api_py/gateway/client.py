from __future__ import annotations

import collections
import json
import time
import uuid
from typing import Any

DEFAULT_MAX_TICKS_PER_SYMBOL = 1_000
DEFAULT_MAX_EVENTS = 1_000
DEFAULT_DRAIN_MAX_MESSAGES = 250
DEFAULT_SOCKET_RCVHWM = 1_000
DEFAULT_MAX_OWNED_EVENT_IDS = 4_096
_ALL_SYMBOLS = frozenset({"*", "all", "ALL"})
_STRATEGY_ID_KEYS = (
    "strategy_id",
    "strategyId",
    "gateway_strategy_id",
    "gatewayStrategyId",
    "owner_strategy_id",
    "ownerStrategyId",
)
_TICK_SYMBOL_KEYS = (
    "symbol",
    "instrument",
    "instrument_id",
    "InstrumentID",
    "data_name",
    "dataname",
    "instId",
    "contract",
    "ticker",
)
_PRIVATE_EVENT_IDENTITY_KEYS = (
    "request_id",
    "requestId",
    "command_request_id",
    "commandRequestId",
    "client_order_id",
    "clientOrderId",
    "c",
    "C",
    "newClientOrderId",
    "origClientOrderId",
    "orderLinkId",
    "origOrderLinkId",
    "order_ref",
    "orderRef",
    "OrderRef",
    "bt_order_ref",
    "btOrderRef",
    "ctp_order_ref",
    "ctpOrderRef",
    "venue_order_id",
    "venueOrderId",
    "external_order_id",
    "externalOrderId",
    "ordId",
    "order_id",
    "orderId",
    "i",
    "OrderID",
    "order_sys_id",
    "orderSysId",
    "OrderSysID",
    "clOrdId",
    "origClOrdId",
    "trade_id",
    "tradeId",
    "TradeID",
    "deal_id",
    "dealId",
    "DealID",
    "exec_id",
    "execId",
    "execID",
    "ExecID",
    "execution_id",
    "executionId",
    "executionID",
    "fill_id",
    "fillId",
    "id",
)
_ORDER_EVENT_NAMES = frozenset(
    {
        "order",
        "orders",
        "order_update",
        "orderupdate",
        "order_status",
        "order_event",
        "order_created",
        "order_accepted",
        "order_submitted",
        "order_cancelled",
        "order_canceled",
        "order_rejected",
        "execution_report",
        "executionreport",
    }
)
_TRADE_EVENT_NAMES = frozenset(
    {
        "trade",
        "trades",
        "trade_update",
        "trade_event",
        "fill",
        "fills",
        "fill_update",
        "execution",
        "execution_update",
        "deal",
        "deal_update",
    }
)
_ERROR_EVENT_NAMES = frozenset(
    {
        "error",
        "errors",
        "order_error",
        "trade_error",
        "cancel_error",
        "cancel_reject",
        "cancel_rejected",
        "reject",
        "rejected",
    }
)


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip()


class GatewayClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = dict(kwargs)
        self.command_endpoint = str(kwargs.get("gateway_command_endpoint") or "").strip()
        self.event_endpoint = str(kwargs.get("gateway_event_endpoint") or "").strip()
        self.market_endpoint = str(kwargs.get("gateway_market_endpoint") or "").strip()
        self.strategy_id = str(
            kwargs.get("strategy_id") or kwargs.get("gateway_strategy_id") or "default"
        ).strip()
        self.drop_unowned_events = bool(kwargs.get("gateway_drop_unowned_events", True))
        self.command_timeout_sec = float(kwargs.get("gateway_command_timeout_sec") or 10.0)
        self.max_ticks_per_symbol = _positive_int(
            kwargs.get("gateway_max_ticks_per_symbol")
            or kwargs.get("max_ticks_per_symbol"),
            DEFAULT_MAX_TICKS_PER_SYMBOL,
        )
        self.max_events = _positive_int(
            kwargs.get("gateway_max_events") or kwargs.get("max_events"),
            DEFAULT_MAX_EVENTS,
        )
        self.max_owned_event_ids = _positive_int(
            kwargs.get("gateway_max_owned_event_ids")
            or kwargs.get("max_owned_event_ids"),
            DEFAULT_MAX_OWNED_EVENT_IDS,
        )
        self.drain_max_messages = _positive_int(
            kwargs.get("gateway_drain_max_messages")
            or kwargs.get("drain_max_messages"),
            DEFAULT_DRAIN_MAX_MESSAGES,
        )
        self.socket_rcvhwm = _positive_int(
            kwargs.get("gateway_socket_rcvhwm") or kwargs.get("socket_rcvhwm"),
            DEFAULT_SOCKET_RCVHWM,
        )
        self._connected = False
        self._context = None
        self._market_socket = None
        self._event_socket = None
        self.subscribed: set[str] = set()
        self._tick_queues: dict[str, collections.deque[dict[str, Any]]] = collections.defaultdict(
            self._new_tick_queue
        )
        self._event_queue: collections.deque[dict[str, Any]] = collections.deque(
            maxlen=self.max_events
        )
        self._owned_event_ids: set[str] = set()
        self._owned_event_id_order: collections.deque[str] = collections.deque(
            maxlen=self.max_owned_event_ids
        )

    def _new_tick_queue(self) -> collections.deque[dict[str, Any]]:
        return collections.deque(maxlen=self.max_ticks_per_symbol)

    def connect(self) -> None:
        if not self.command_endpoint:
            raise RuntimeError("gateway_command_endpoint is required")
        try:
            import zmq
        except ImportError as exc:
            raise RuntimeError("pyzmq is required for GatewayClient") from exc
        self._context = zmq.Context.instance()
        if self.market_endpoint and self._market_socket is None:
            self._market_socket = self._context.socket(zmq.SUB)
            self._market_socket.setsockopt(zmq.SUBSCRIBE, b"")
            self._market_socket.setsockopt(zmq.RCVHWM, self.socket_rcvhwm)
            self._market_socket.setsockopt(zmq.RCVTIMEO, 0)
            self._market_socket.connect(self.market_endpoint)
        if self.event_endpoint and self._event_socket is None:
            self._event_socket = self._context.socket(zmq.SUB)
            self._event_socket.setsockopt(zmq.SUBSCRIBE, b"")
            self._event_socket.setsockopt(zmq.RCVHWM, self.socket_rcvhwm)
            self._event_socket.setsockopt(zmq.RCVTIMEO, 0)
            self._event_socket.connect(self.event_endpoint)
        self._connected = True

    def disconnect(self) -> None:
        for sock in (self._market_socket, self._event_socket):
            if sock is not None:
                sock.close(linger=0)
        self._market_socket = None
        self._event_socket = None
        self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected:
            self.connect()

    def _send_command(self, command: str, payload: dict[str, Any] | None = None) -> Any:
        self._ensure_connected()
        import zmq

        command_payload = self._payload_with_strategy(payload)
        ctx = zmq.Context.instance()
        timeout_ms = int(max(self.command_timeout_sec, 0.1) * 1000)
        attempts = int(self.kwargs.get("gateway_command_retries") or 3)
        attempts = max(attempts, 1)
        response = None
        for attempt in range(attempts):
            sock = ctx.socket(zmq.DEALER)
            sock.setsockopt(zmq.IDENTITY, uuid.uuid4().hex.encode("utf-8"))
            sock.setsockopt(zmq.SNDTIMEO, timeout_ms)
            sock.setsockopt(zmq.RCVTIMEO, timeout_ms)
            request_id = uuid.uuid4().hex
            self._remember_owned_event_ids(
                {
                    "request_id": request_id,
                    "command": command,
                    **command_payload,
                }
            )
            try:
                sock.connect(self.command_endpoint)
                request = {
                    "request_id": request_id,
                    "command": command,
                    "payload": command_payload,
                }
                sock.send(
                    json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
                )
                response = json.loads(sock.recv().decode("utf-8"))
                break
            except zmq.Again:
                if attempt >= attempts - 1:
                    raise
                time.sleep(min(0.5 * (attempt + 1), 2.0))
            finally:
                sock.close(linger=0)
        if response is None:
            raise RuntimeError(f"gateway command timed out: {command}")
        if not isinstance(response, dict):
            raise RuntimeError(f"invalid gateway response for {command}: {response!r}")
        if response.get("status") != "ok":
            raise RuntimeError(str(response.get("error") or f"gateway command failed: {command}"))
        data = response.get("data")
        self._remember_owned_event_ids(data)
        return data

    def _command(self, command: str, payload: dict[str, Any] | None = None) -> Any:
        return self._send_command(command, payload)

    def _payload_with_strategy(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        if self.strategy_id:
            data.setdefault("strategy_id", self.strategy_id)
        return data

    def subscribe(self, symbols: str | list[str]) -> dict[str, Any]:
        symbol_list = [symbols] if isinstance(symbols, str) else list(symbols)
        symbol_list = [_normalize_symbol(symbol) for symbol in symbol_list]
        symbol_list = [symbol for symbol in symbol_list if symbol]
        result = self._send_command("subscribe", {"symbols": symbol_list}) or {}
        self.subscribed.update(symbol_list)
        return result

    def get_balance(self) -> dict[str, Any]:
        return self._send_command("get_balance", {}) or {}

    def get_account(self) -> dict[str, Any]:
        return self.get_balance()

    def get_positions(self) -> list[dict[str, Any]]:
        return self._send_command("get_positions", {}) or []

    def fetch_open_orders(self) -> list[dict[str, Any]]:
        return self._send_command("get_open_orders", {}) or []

    def fetch_bars(self, symbol: str, timeframe: str = "M1", count: int = 200) -> list[dict[str, Any]]:
        return (
            self._send_command(
                "get_bars",
                {"symbol": symbol, "timeframe": timeframe, "count": count},
            )
            or []
        )

    def fetch_symbol_info(self, symbol: str) -> dict[str, Any]:
        return self._send_command("get_symbol_info", {"symbol": symbol}) or {}

    def submit_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._send_command("place_order", dict(payload or {})) or {}

    def cancel_order(self, order_ref: str, dataname: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"order_ref": order_ref}
        symbol = _normalize_symbol(dataname)
        if symbol:
            payload.update(
                {
                    "dataname": symbol,
                    "data_name": symbol,
                    "symbol": symbol,
                    "instrument": symbol,
                }
            )
        elif dataname is not None:
            payload["dataname"] = dataname
        return self._send_command(
            "cancel_order",
            payload,
        ) or {}

    def poll_broker_update(self) -> dict[str, Any] | None:
        if self._event_queue:
            return self._event_queue.popleft()
        self._drain_events()
        if self._event_queue:
            return self._event_queue.popleft()
        return None

    def poll_tick(self, symbol: str) -> dict[str, Any] | None:
        symbol_key = str(symbol or "")
        queue = self._tick_queues.get(symbol_key)
        if queue:
            return queue.popleft()
        self._drain_market()
        queue = self._tick_queues.get(symbol_key)
        if queue:
            return queue.popleft()
        return None

    def get_next_tick(self, symbol: str) -> dict[str, Any] | None:
        return self.poll_tick(symbol)

    def has_pending_tick(self, symbol: str) -> bool:
        queue = self._tick_queues.get(str(symbol or ""))
        if queue:
            return True
        self._drain_market()
        queue = self._tick_queues.get(str(symbol or ""))
        return bool(queue)

    def supports_live_ticks(self, _symbol: str | None = None) -> bool:
        return bool(self.market_endpoint)

    def supports_live_streaming(self, _symbol: str | None = None) -> bool:
        return bool(self.market_endpoint)

    def _drain_market(self) -> None:
        if self._market_socket is None:
            return
        self._drain_socket(
            self._market_socket,
            self._store_tick,
            max_messages=self.drain_max_messages,
        )

    def _drain_events(self) -> None:
        if self._event_socket is None:
            return
        self._drain_socket(
            self._event_socket,
            self._store_event,
            max_messages=self.drain_max_messages,
        )

    @staticmethod
    def _drain_socket(
        sock: Any,
        callback: Any,
        *,
        max_messages: int = DEFAULT_DRAIN_MAX_MESSAGES,
    ) -> None:
        import zmq

        drained = 0
        limit = max(int(max_messages or 0), 0)
        deadline = time.monotonic() + 0.05
        while time.monotonic() < deadline:
            if limit and drained >= limit:
                return
            try:
                raw = sock.recv(flags=zmq.NOBLOCK)
            except zmq.Again:
                return
            drained += 1
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                callback(payload)

    def _store_tick(self, payload: dict[str, Any]) -> None:
        keys = [_normalize_symbol(payload.get(key)) for key in _TICK_SYMBOL_KEYS]
        keys = [key for key in keys if key]
        keys = list(dict.fromkeys(keys))
        if not keys:
            return
        if self.subscribed and not (self.subscribed & _ALL_SYMBOLS):
            keys = [key for key in keys if key in self.subscribed]
            if not keys:
                return
        for key in keys:
            self._tick_queues[key].append(payload)

    def _store_event(self, payload: dict[str, Any]) -> None:
        payload = self._normalize_broker_event_payload(payload)
        if not self._event_belongs_to_strategy(payload):
            return
        self._event_queue.append(payload)

    @classmethod
    def _normalize_broker_event_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return payload
        normalized_kind = cls._normalized_event_kind(payload)
        if not normalized_kind:
            return payload
        if payload.get("kind") == normalized_kind:
            return payload
        normalized = dict(payload)
        normalized["kind"] = normalized_kind
        return normalized

    @classmethod
    def _normalized_event_kind(cls, payload: dict[str, Any]) -> str:
        okx_channel_kind = cls._normalized_okx_channel_kind(payload)
        if okx_channel_kind:
            return okx_channel_kind
        topic_kind = cls._normalized_topic_kind(payload)
        if topic_kind:
            return topic_kind

        for key in ("kind", "type", "event_type", "event", "e"):
            value = payload.get(key)
            if value in (None, ""):
                continue
            text = cls._normalize_event_name(value)
            if text in {"execution_report", "executionreport"}:
                return "trade" if cls._raw_binance_event_has_fill(payload) else "order"
            if text == "order_trade_update":
                return "trade" if cls._raw_binance_event_has_fill(payload) else "order"
            if text in _ORDER_EVENT_NAMES:
                return "order"
            if text in _TRADE_EVENT_NAMES:
                return "trade"
            if text in _ERROR_EVENT_NAMES or "error" in text or "reject" in text:
                return "error"
        return ""

    @staticmethod
    def _normalize_event_name(value: Any) -> str:
        text = str(value or "").strip()
        out = []
        previous_is_lower_or_digit = False
        for char in text:
            if char.isalnum():
                if char.isupper() and previous_is_lower_or_digit:
                    out.append("_")
                out.append(char.lower())
                previous_is_lower_or_digit = char.islower() or char.isdigit()
            else:
                if out and out[-1] != "_":
                    out.append("_")
                previous_is_lower_or_digit = False
        normalized = "".join(out).strip("_")
        compact = normalized.replace("_", "")
        if compact == "executionreport":
            return "execution_report"
        if compact == "ordertradeupdate":
            return "order_trade_update"
        return normalized

    @classmethod
    def _normalized_topic_kind(cls, payload: dict[str, Any]) -> str:
        topic = cls._normalize_event_name(payload.get("topic"))
        if not topic:
            return ""
        if "execution" in topic or "fill" in topic or "trade" in topic:
            return "trade"
        if "order" in topic:
            return "order"
        if "error" in topic or "reject" in topic:
            return "error"
        return ""

    @classmethod
    def _normalized_okx_channel_kind(cls, payload: dict[str, Any]) -> str:
        arg = payload.get("arg")
        if not isinstance(arg, dict):
            return ""
        channel = cls._normalize_event_name(arg.get("channel"))
        if not channel:
            return ""
        if "fills" in channel:
            return "trade"
        if "orders" in channel or channel == "order":
            return "trade" if cls._raw_okx_channel_has_fill(payload) else "order"
        return ""

    @classmethod
    def _raw_binance_event_has_fill(cls, payload: dict[str, Any]) -> bool:
        details = payload.get("o") if isinstance(payload.get("o"), dict) else payload
        execution_type = str(details.get("x") or "").strip().upper()
        if execution_type == "TRADE":
            return True
        trade_id = details.get("t")
        return str(trade_id or "").strip() not in {"", "0", "-1"}

    @classmethod
    def _raw_okx_channel_has_fill(cls, payload: dict[str, Any]) -> bool:
        data = payload.get("data")
        rows = data if isinstance(data, list) else [data]
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key in ("tradeId", "trade_id", "fillSz", "fill_sz", "fillPx", "fill_px"):
                value = row.get(key)
                if value not in (None, "", "0", 0):
                    return True
        return False

    def _event_belongs_to_strategy(self, payload: dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        strategy_id = self._payload_strategy_id(payload, include_details=False)
        if strategy_id:
            return not self.strategy_id or strategy_id == self.strategy_id
        strategy_id = self._payload_strategy_id(payload, include_details=True)
        if strategy_id:
            return not self.strategy_id or strategy_id == self.strategy_id

        if self._event_matches_owned_identity(payload):
            return True

        if not self.drop_unowned_events:
            return True
        kind = str(payload.get("kind") or "").strip().lower()
        if kind not in {"order", "trade", "error"}:
            return True
        return not self._event_has_order_identity(payload)

    @classmethod
    def _payload_strategy_id(
        cls,
        payload: dict[str, Any],
        *,
        include_details: bool = True,
    ) -> str:
        for key in _STRATEGY_ID_KEYS:
            value = payload.get(key)
            if value not in (None, ""):
                return str(value).strip()
        if include_details:
            details = payload.get("details")
            if isinstance(details, dict):
                return cls._payload_strategy_id(details, include_details=False)
        return ""

    @classmethod
    def _event_has_order_identity(cls, payload: Any) -> bool:
        if isinstance(payload, (list, tuple, set)):
            return any(cls._event_has_order_identity(item) for item in payload)
        if not isinstance(payload, dict):
            return False
        for key in _PRIVATE_EVENT_IDENTITY_KEYS:
            if payload.get(key) not in (None, ""):
                return True
        details = payload.get("details")
        if isinstance(details, (dict, list, tuple, set)):
            return cls._event_has_order_identity(details)
        data = payload.get("data")
        if isinstance(data, (dict, list, tuple, set)):
            return cls._event_has_order_identity(data)
        return False

    @classmethod
    def _event_identity_values(cls, payload: Any) -> set[str]:
        if isinstance(payload, (list, tuple, set)):
            values: set[str] = set()
            for item in payload:
                values.update(cls._event_identity_values(item))
            return values
        if not isinstance(payload, dict):
            return set()
        values: set[str] = set()
        for key in _PRIVATE_EVENT_IDENTITY_KEYS:
            value = payload.get(key)
            if value in (None, ""):
                continue
            values.add(str(value).strip())
        details = payload.get("details")
        if isinstance(details, dict):
            values.update(cls._event_identity_values(details))
        data = payload.get("data")
        if isinstance(data, (dict, list, tuple, set)):
            values.update(cls._event_identity_values(data))
        return {value for value in values if value}

    def _remember_owned_event_ids(self, payload: Any) -> None:
        for value in self._event_identity_values(payload):
            if value in self._owned_event_ids:
                continue
            if len(self._owned_event_id_order) == self._owned_event_id_order.maxlen:
                expired = self._owned_event_id_order.popleft()
                self._owned_event_ids.discard(expired)
            self._owned_event_ids.add(value)
            self._owned_event_id_order.append(value)

    def _event_matches_owned_identity(self, payload: dict[str, Any]) -> bool:
        return bool(self._event_identity_values(payload) & self._owned_event_ids)
