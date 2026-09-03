"""ZMQ implementation of the public :class:`BtApi` operation boundary.

The gateway intentionally exposes bounded event-cache reads rather than
pretending that a streaming market transport is a synchronous REST service.
``LIVE`` waits for an event newer than the call's observed sequence;
``CACHE_OK`` returns only a still-valid cached event and marks it stale.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from bt_api_py._contracts.cache_policy import CacheEntry, CachePolicy
from bt_api_py._contracts.errors import LiveQueryFailedError, StaleDataUnavailableError
from bt_api_py._contracts.models import (
    AccountSnapshot,
    BalanceSnapshot,
    CancelAllRequest,
    CancelOrderRequest,
    CommandStatus,
    Consistency,
    DepthSnapshot,
    FillSnapshot,
    ForwardingConfig,
    Freshness,
    KlineSnapshot,
    OrderRequest,
    OrderSnapshot,
    OrderType,
    PositionSnapshot,
    QueryOrderRequest,
    Side,
    TickerSnapshot,
)
from bt_api_py.forwarding.schema import CommandAck, MarketEvent, OrderCommand, PrivateEvent


class ZmqBtApiBackend:
    """Operation backend that routes reads and commands through forwarding."""

    def __init__(self, config: ForwardingConfig) -> None:
        self._config = config
        # ``_client`` remains a supported test/embedded-client injection seam.
        # Production clients are scoped by exchange/market.
        self._client: Any = None
        self._clients: dict[tuple[str, str], Any] = {}
        self._cache = CachePolicy()

    @staticmethod
    def _scope(exchange_name: str) -> tuple[str, str]:
        parts = str(exchange_name).split("___")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise ValueError("forwarding exchange_name must be '<EXCHANGE>___<MARKET_TYPE>'")
        return parts[0].upper(), parts[1].upper()

    def _ensure_client(self, exchange_name: str) -> Any:
        exchange, market_type = self._scope(exchange_name)
        if not self._clients and self._client is not None:
            return self._client
        scope = (exchange, market_type)
        client = self._clients.get(scope)
        if client is None:
            from bt_api_py.forwarding.client import ZmqForwardingClient

            client = ZmqForwardingClient(
                market_endpoint=self._config.market_endpoint,
                command_endpoint=self._config.command_endpoint,
                private_endpoint=self._config.private_endpoint,
                exchange=exchange,
                market_type=market_type,
                account_id=self._config.account_id,
                strategy_id=self._config.strategy_id,
            )
            self._clients[scope] = client
            self._client = client
        return client

    def _cache_key(self, operation: str, exchange_name: str) -> str:
        return f"{operation}:{exchange_name}:{self._config.account_id}:{self._config.strategy_id}"

    @staticmethod
    def _observed_at(timestamp_ms: int) -> datetime:
        return datetime.fromtimestamp(timestamp_ms / 1000.0, UTC)

    def _event_freshness(
        self,
        event: MarketEvent | PrivateEvent,
        *,
        source: str,
        stale: bool,
        stale_reason: str | None = None,
    ) -> Freshness:
        return Freshness(
            source=source,
            observed_at=self._observed_at(event.event_time),
            stale=stale,
            stale_reason=stale_reason,
        )

    @staticmethod
    def _cache_freshness(entry: CacheEntry) -> Freshness:
        return Freshness(
            source="cache",
            observed_at=entry.freshness.observed_at,
            stale=True,
            stale_reason="CACHE_OK fallback after live transport failure",
        )

    def _check_market_scope(self, exchange_name: str, event: MarketEvent) -> None:
        exchange, market_type = self._scope(exchange_name)
        if event.exchange.upper() != exchange or event.market_type.upper() != market_type:
            raise LiveQueryFailedError(
                "market_event_scope",
                exchange_name,
                detail=(
                    f"received {event.exchange}___{event.market_type}, "
                    f"expected {exchange}___{market_type}"
                ),
            )

    def _event_is_fresh(self, event: MarketEvent | PrivateEvent) -> bool:
        age_ms = (datetime.now(UTC) - self._observed_at(event.event_time)).total_seconds() * 1000
        return age_ms <= self._config.max_cache_age_ms

    def _latest_market_event(
        self,
        client: Any,
        exchange_name: str,
        symbol: str,
        event_type: str,
        consistency: Consistency,
    ) -> tuple[MarketEvent, Freshness]:
        current = client.peek_market_event(symbol, event_type)
        operation = f"get_{'depth' if event_type == 'orderbook' else event_type}"
        if consistency is Consistency.CACHE_OK:
            if current is None or not self._event_is_fresh(current):
                raise StaleDataUnavailableError(
                    operation,
                    exchange_name,
                    detail=(
                        f"symbol={symbol}; cache missing or older than "
                        f"{self._config.max_cache_age_ms}ms"
                    ),
                )
            self._check_market_scope(exchange_name, current)
            return current, self._event_freshness(
                current,
                source="cache",
                stale=True,
                stale_reason="CACHE_OK requested",
            )

        after_sequence_id = current.sequence_id if current is not None else 0
        event = client.wait_for_next_market_event(
            symbol,
            event_type,
            after_sequence_id=after_sequence_id,
            timeout_ms=self._config.market_read_timeout_ms,
        )
        if event is None:
            raise LiveQueryFailedError(
                operation,
                exchange_name,
                detail=(
                    f"symbol={symbol}; timeout_ms={self._config.market_read_timeout_ms}; "
                    "no event newer than the call baseline"
                ),
            )
        self._check_market_scope(exchange_name, event)
        return event, self._event_freshness(event, source="live", stale=False)

    @staticmethod
    def _decimal(value: Any, default: str = "0") -> Decimal:
        if value is None or value == "":
            value = default
        return Decimal(str(value))

    @classmethod
    def _levels(cls, raw_levels: Any) -> tuple[tuple[Decimal, Decimal], ...]:
        levels: list[tuple[Decimal, Decimal]] = []
        for level in raw_levels or []:
            if isinstance(level, dict):
                price = level.get("price")
                quantity = level.get("size", level.get("quantity", level.get("volume")))
            else:
                price, quantity = level[0], level[1]
            levels.append((cls._decimal(price), cls._decimal(quantity)))
        return tuple(levels)

    def get_tick(
        self, exchange_name: str, symbol: str, *, consistency: Consistency = Consistency.LIVE
    ) -> TickerSnapshot:
        event, freshness = self._latest_market_event(
            self._ensure_client(exchange_name), exchange_name, symbol, "tick", consistency
        )
        payload = dict(event.payload)
        return TickerSnapshot(
            id=f"{exchange_name}:{symbol}:tick:{event.sequence_id}",
            symbol=symbol,
            last_price=self._decimal(payload.get("last_price", payload.get("price"))),
            freshness=freshness,
            raw=payload,
        )

    def get_depth(
        self,
        exchange_name: str,
        symbol: str,
        count: int = 10,
        *,
        consistency: Consistency = Consistency.LIVE,
    ) -> DepthSnapshot:
        event, freshness = self._latest_market_event(
            self._ensure_client(exchange_name), exchange_name, symbol, "orderbook", consistency
        )
        payload = dict(event.payload)
        return DepthSnapshot(
            id=f"{exchange_name}:{symbol}:depth:{event.sequence_id}",
            symbol=symbol,
            bids=self._levels(payload.get("bids"))[:count],
            asks=self._levels(payload.get("asks"))[:count],
            freshness=freshness,
            raw=payload,
        )

    def get_kline(
        self,
        exchange_name: str,
        symbol: str,
        period: str,
        count: int = 500,
        *,
        consistency: Consistency = Consistency.LIVE,
    ) -> KlineSnapshot:
        del count  # Latest-event semantics deliberately return one latest bar.
        event, freshness = self._latest_market_event(
            self._ensure_client(exchange_name), exchange_name, symbol, "bar", consistency
        )
        payload = dict(event.payload)
        event_period = payload.get("period")
        if event_period not in (None, "", period):
            error = (
                StaleDataUnavailableError
                if consistency is Consistency.CACHE_OK
                else LiveQueryFailedError
            )
            raise error(
                "get_kline",
                exchange_name,
                detail=f"symbol={symbol}; event period={event_period!r}, requested={period!r}",
            )
        return KlineSnapshot(
            id=f"{exchange_name}:{symbol}:bar:{event.sequence_id}",
            symbol=symbol,
            period=period,
            open=self._decimal(payload.get("open")),
            high=self._decimal(payload.get("high")),
            low=self._decimal(payload.get("low")),
            close=self._decimal(payload.get("close")),
            volume=self._decimal(payload.get("volume")),
            freshness=freshness,
            raw=payload,
        )

    def _account_from_payload(
        self, payload: dict[str, Any], freshness: Freshness
    ) -> AccountSnapshot:
        cash = self._decimal(payload.get("cash", payload.get("available_cash")))
        equity = self._decimal(payload.get("equity", payload.get("value", cash)))
        available = self._decimal(payload.get("available_cash", cash))
        return AccountSnapshot(
            id=f"{self._config.account_id}:account",
            account_id=self._config.account_id,
            currency=str(payload.get("currency", "")),
            cash=cash,
            equity=equity,
            margin_used=self._decimal(payload.get("margin_used")),
            available_cash=available,
            freshness=freshness,
            raw=payload,
        )

    def _private_cache_event(
        self, events: Iterable[PrivateEvent], operation: str, exchange_name: str
    ) -> list[PrivateEvent]:
        cached = list(events)
        if not cached or any(not self._event_is_fresh(event) for event in cached):
            raise StaleDataUnavailableError(
                operation,
                exchange_name,
                detail=f"cache missing or older than {self._config.max_cache_age_ms}ms",
            )
        return cached

    def get_account(
        self, exchange_name: str, *, consistency: Consistency = Consistency.LIVE
    ) -> AccountSnapshot:
        client = self._ensure_client(exchange_name)
        key = self._cache_key("get_account", exchange_name)
        if consistency is Consistency.CACHE_OK:
            event = client.latest_account_event()
            if isinstance(event, PrivateEvent) and self._event_is_fresh(event):
                return self._account_from_payload(
                    dict(event.payload),
                    self._event_freshness(
                        event, source="cache", stale=True, stale_reason="CACHE_OK requested"
                    ),
                )
            entry = self._cache.get_within_age(key, self._config.max_cache_age_ms)
            # Preserve the earlier in-process cache key for callers that had
            # already populated it before scoped keys were introduced.
            if entry is None:
                entry = self._cache.get_within_age(
                    f"get_account:{exchange_name}", self._config.max_cache_age_ms
                )
            if entry is not None:
                return self._account_from_payload(dict(entry.value), self._cache_freshness(entry))
            raise StaleDataUnavailableError(
                "get_account", exchange_name, detail="cache missing or expired"
            )
        try:
            payload = client.get_balance(allow_cached_failure=False)
        except Exception as exc:
            raise LiveQueryFailedError("get_account", exchange_name, detail=str(exc)) from exc
        self._cache.put(key, dict(payload))
        return self._account_from_payload(
            dict(payload), Freshness(source="live", observed_at=datetime.now(UTC))
        )

    def get_balance(
        self, exchange_name: str, *, consistency: Consistency = Consistency.LIVE
    ) -> list[BalanceSnapshot]:
        account = self.get_account(exchange_name, consistency=consistency)
        return [
            BalanceSnapshot(
                id=f"{account.account_id}:balance",
                account_id=account.account_id,
                currency=account.currency,
                available=account.available_cash,
                frozen=Decimal("0"),
                freshness=account.freshness,
                raw=dict(account.raw),
            )
        ]

    def _positions_from_payloads(
        self, payloads: Iterable[dict[str, Any]], freshness: Freshness
    ) -> list[PositionSnapshot]:
        return [
            PositionSnapshot(
                id=f"{self._config.account_id}:position:{item.get('symbol', '')}",
                account_id=self._config.account_id,
                symbol=str(item.get("symbol", "")),
                quantity=self._decimal(item.get("quantity", item.get("size"))),
                average_price=self._decimal(item.get("average_price", item.get("price"))),
                freshness=freshness,
                raw=dict(item),
            )
            for item in payloads
        ]

    @staticmethod
    def _side(value: Any) -> Side:
        try:
            return Side(str(value).lower())
        except ValueError:
            return Side.BUY

    @staticmethod
    def _order_type(value: Any) -> OrderType:
        try:
            return OrderType(str(value).lower())
        except ValueError:
            return OrderType.MARKET

    def _orders_from_payloads(
        self, payloads: Iterable[dict[str, Any]], freshness: Freshness
    ) -> list[OrderSnapshot]:
        return [
            OrderSnapshot(
                id=f"{self._config.account_id}:order:{item.get('order_id', '')}",
                order_id=str(item.get("order_id", item.get("external_order_id", ""))),
                account_id=self._config.account_id,
                symbol=str(item.get("symbol", "")),
                side=self._side(item.get("side")),
                order_type=self._order_type(item.get("order_type")),
                quantity=self._decimal(item.get("quantity", item.get("size"))),
                status=str(item.get("status", "")),
                price=(
                    self._decimal(item["price"]) if item.get("price") not in (None, "") else None
                ),
                filled_quantity=self._decimal(item.get("filled_quantity", item.get("filled"))),
                freshness=freshness,
                raw=dict(item),
            )
            for item in payloads
        ]

    def _fills_from_payloads(
        self, payloads: Iterable[dict[str, Any]], freshness: Freshness
    ) -> list[FillSnapshot]:
        return [
            FillSnapshot(
                id=f"{self._config.account_id}:fill:{item.get('trade_id', '')}",
                fill_id=str(item.get("trade_id", item.get("fill_id", ""))),
                order_id=str(item.get("order_id", item.get("external_order_id", ""))),
                account_id=self._config.account_id,
                symbol=str(item.get("symbol", "")),
                side=self._side(item.get("side")),
                quantity=self._decimal(item.get("quantity", item.get("size"))),
                price=self._decimal(item.get("price", item.get("average_price"))),
                fee=self._decimal(item.get("fee")),
                freshness=freshness,
                raw=dict(item),
            )
            for item in payloads
        ]

    def _private_read(
        self,
        operation: str,
        exchange_name: str,
        consistency: Consistency,
        *,
        client_method: str,
        event_method: str,
        mapper: Any,
    ) -> Any:
        client = self._ensure_client(exchange_name)
        if consistency is Consistency.CACHE_OK:
            events = self._private_cache_event(
                getattr(client, event_method)(), operation, exchange_name
            )
            return mapper(
                [dict(event.payload) for event in events],
                self._event_freshness(
                    events[-1], source="cache", stale=True, stale_reason="CACHE_OK requested"
                ),
            )
        try:
            payloads = getattr(client, client_method)(allow_cached_failure=False)
        except Exception as exc:
            raise LiveQueryFailedError(operation, exchange_name, detail=str(exc)) from exc
        return mapper(list(payloads), Freshness(source="live", observed_at=datetime.now(UTC)))

    def get_position(
        self, exchange_name: str, *, consistency: Consistency = Consistency.LIVE
    ) -> list[PositionSnapshot]:
        return self._private_read(
            "get_position",
            exchange_name,
            consistency,
            client_method="get_positions",
            event_method="latest_position_events",
            mapper=self._positions_from_payloads,
        )

    def get_open_orders(
        self, exchange_name: str, *, consistency: Consistency = Consistency.LIVE
    ) -> list[OrderSnapshot]:
        return self._private_read(
            "get_open_orders",
            exchange_name,
            consistency,
            client_method="fetch_open_orders",
            event_method="latest_order_events",
            mapper=self._orders_from_payloads,
        )

    def get_deals(
        self, exchange_name: str, *, consistency: Consistency = Consistency.LIVE
    ) -> list[FillSnapshot]:
        return self._private_read(
            "get_deals",
            exchange_name,
            consistency,
            client_method="get_deals",
            event_method="latest_fill_events",
            mapper=self._fills_from_payloads,
        )

    def _command(self, exchange_name: str, **values: Any) -> OrderCommand:
        exchange, market_type = self._scope(exchange_name)
        return OrderCommand(
            strategy_id=str(values.pop("strategy_id", self._config.strategy_id)),
            account_id=str(values.pop("account_id", self._config.account_id)),
            exchange=exchange,
            market_type=market_type,
            **values,
        )

    def make_order(self, exchange_name: str, request: OrderRequest) -> CommandAck:
        client = self._ensure_client(exchange_name)
        command = self._command(
            exchange_name,
            symbol=request.symbol,
            side=request.side.value,
            size=float(request.quantity),
            command_type="place_order",
            order_type=request.order_type.value,
            price=float(request.price) if request.price is not None else None,
            time_in_force=request.time_in_force,
            reduce_only=request.reduce_only,
            client_order_id=request.client_order_id,
            idempotency_key=request.idempotency_key or request.client_order_id,
            account_id=request.account_id,
        )
        return client._send_command_sync(command)

    def cancel_order(self, exchange_name: str, request: CancelOrderRequest) -> CommandAck:
        client = self._ensure_client(exchange_name)
        return client._send_command_sync(
            self._command(
                exchange_name,
                command_type="cancel_order",
                symbol=request.symbol,
                order_id=request.order_id or request.client_order_id,
                idempotency_key=request.idempotency_key
                or f"cancel:{request.account_id}:{request.order_id or request.client_order_id}",
                account_id=request.account_id,
            )
        )

    def cancel_all(self, exchange_name: str, request: CancelAllRequest) -> CommandAck:
        client = self._ensure_client(exchange_name)
        return client._send_command_sync(
            self._command(
                exchange_name,
                command_type="cancel_all",
                symbol=request.symbol or "",
                idempotency_key=request.idempotency_key
                or f"cancel-all:{request.account_id}:{request.symbol or '*'}",
                account_id=request.account_id,
            )
        )

    def query_order(self, exchange_name: str, request: QueryOrderRequest) -> CommandAck:
        client = self._ensure_client(exchange_name)
        return client._send_command_sync(
            self._command(
                exchange_name,
                command_type="query_order",
                symbol=request.symbol,
                order_id=request.order_id or request.client_order_id,
                idempotency_key=(
                    f"query:{request.account_id}:{request.order_id or request.client_order_id}"
                ),
                account_id=request.account_id,
            )
        )

    def get_command_status(self, exchange_name: str, command_id: str) -> CommandStatus:
        return self._ensure_client(exchange_name).get_command_status(command_id)

    def get_capabilities(self, exchange_name: str) -> dict[str, bool]:
        """Report only forwarding operations with a concrete implementation."""
        del exchange_name
        return {
            "get_tick": True,
            "get_depth": True,
            "get_kline": True,
            "get_account": True,
            "get_balance": True,
            "get_position": True,
            "get_open_orders": True,
            "get_deals": True,
            "make_order": True,
            "cancel_order": True,
            "cancel_all": True,
            "query_order": True,
            "get_command_status": True,
            "get_trades": False,
        }
