"""ZMQ transport backend for ``BtApi`` (Task 1.2).

Wraps the forwarding transport so a ``BtApi(transport_mode="zmq")`` instance
presents the same operation boundary as direct mode. LIVE queries surface
transport failures instead of fabricating zero/empty snapshots; CACHE_OK
queries may return a stale cached snapshot with explicit freshness.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from bt_api_py._contracts.cache_policy import CachePolicy
from bt_api_py._contracts.errors import LiveQueryFailedError
from bt_api_py._contracts.models import (
    AccountSnapshot,
    BalanceSnapshot,
    CancelAllRequest,
    CancelOrderRequest,
    Consistency,
    ForwardingConfig,
    Freshness,
    OrderRequest,
    QueryOrderRequest,
)


class ZmqBtApiBackend:
    """Operation backend that routes through a forwarding transport."""

    def __init__(self, config: ForwardingConfig) -> None:
        self._config = config
        self._client: Any = None
        self._cache = CachePolicy()

    def _ensure_client(self) -> Any:
        if self._client is None:
            from bt_api_py.forwarding.client import ZmqForwardingClient

            self._client = ZmqForwardingClient(
                market_endpoint=self._config.market_endpoint,
                command_endpoint=self._config.command_endpoint,
                private_endpoint=self._config.private_endpoint,
                account_id=self._config.account_id,
                strategy_id=self._config.strategy_id,
            )
        return self._client

    def _account_cache_key(self, exchange_name: str) -> str:
        return f"get_account:{exchange_name}"

    def _account_from_cache(self, value: object) -> AccountSnapshot:
        if isinstance(value, AccountSnapshot):
            return value
        payload = value if isinstance(value, dict) else {}
        cash = Decimal(str(payload.get("cash", 0)))
        equity = Decimal(str(payload.get("value", payload.get("equity", 0))))
        available = Decimal(str(payload.get("available_cash", payload.get("cash", 0))))
        return AccountSnapshot(
            id=f"{self._config.account_id}:account",
            account_id=self._config.account_id,
            currency=str(payload.get("currency", "")),
            cash=cash,
            equity=equity,
            margin_used=Decimal("0"),
            available_cash=available,
            freshness=Freshness(
                source="cache",
                observed_at=datetime.now(UTC),
                stale=True,
                stale_reason="transport failure fallback",
            ),
            raw=payload,
        )

    def _account_from_live(self, payload: dict[str, Any]) -> AccountSnapshot:
        cash = Decimal(str(payload.get("available_cash", payload.get("cash", 0))))
        equity = Decimal(str(payload.get("equity", payload.get("value", 0))))
        return AccountSnapshot(
            id=f"{self._config.account_id}:account",
            account_id=self._config.account_id,
            currency=str(payload.get("currency", "")),
            cash=cash,
            equity=equity,
            margin_used=Decimal(str(payload.get("margin_used", 0))),
            available_cash=cash,
            freshness=Freshness(
                source="live",
                observed_at=datetime.now(UTC),
            ),
            raw=payload,
        )

    def get_account(
        self, exchange_name: str, *, consistency: Consistency = Consistency.LIVE
    ) -> AccountSnapshot:
        client = self._ensure_client()
        try:
            result = client.get_balance()
        except Exception as exc:
            if consistency is Consistency.CACHE_OK:
                entry = self._cache.get(self._account_cache_key(exchange_name))
                if entry is not None:
                    return self._account_from_cache(entry.value)
            raise LiveQueryFailedError("get_account", exchange_name, detail=str(exc)) from exc
        self._cache.put(self._account_cache_key(exchange_name), result)
        return self._account_from_live(result)

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

    def get_position(
        self, exchange_name: str, *, consistency: Consistency = Consistency.LIVE
    ) -> Any:
        client = self._ensure_client()
        try:
            return client.get_positions()
        except Exception as exc:
            if consistency is not Consistency.CACHE_OK:
                raise LiveQueryFailedError("get_position", exchange_name, detail=str(exc)) from exc
            raise

    def get_open_orders(
        self, exchange_name: str, *, consistency: Consistency = Consistency.LIVE
    ) -> Any:
        client = self._ensure_client()
        try:
            return client.fetch_open_orders()
        except Exception as exc:
            if consistency is not Consistency.CACHE_OK:
                raise LiveQueryFailedError(
                    "get_open_orders", exchange_name, detail=str(exc)
                ) from exc
            raise

    def get_deals(self, exchange_name: str, *, consistency: Consistency = Consistency.LIVE) -> Any:
        client = self._ensure_client()
        try:
            return client.get_deals()
        except Exception as exc:
            if consistency is not Consistency.CACHE_OK:
                raise LiveQueryFailedError("get_deals", exchange_name, detail=str(exc)) from exc
            raise

    def get_tick(
        self, exchange_name: str, symbol: str, *, consistency: Consistency = Consistency.LIVE
    ) -> Any:
        raise LiveQueryFailedError("get_tick", exchange_name, detail="not routed via ZMQ backend")

    def get_depth(
        self,
        exchange_name: str,
        symbol: str,
        count: int = 10,
        *,
        consistency: Consistency = Consistency.LIVE,
    ) -> Any:
        raise LiveQueryFailedError("get_depth", exchange_name, detail="not routed via ZMQ backend")

    def get_kline(
        self,
        exchange_name: str,
        symbol: str,
        period: str,
        count: int = 500,
        *,
        consistency: Consistency = Consistency.LIVE,
    ) -> Any:
        raise LiveQueryFailedError("get_kline", exchange_name, detail="not routed via ZMQ backend")

    def make_order(self, exchange_name: str, request: OrderRequest) -> Any:
        client = self._ensure_client()
        return client.submit_order(
            {
                "symbol": request.symbol,
                "side": request.side.value,
                "size": float(request.quantity),
                "price": float(request.price) if request.price is not None else 0,
                "order_type": request.order_type.value,
                "client_order_id": request.client_order_id,
            }
        )

    def cancel_order(self, exchange_name: str, request: CancelOrderRequest) -> Any:
        client = self._ensure_client()
        return client.cancel_order(request.order_id or request.client_order_id)

    def cancel_all(self, exchange_name: str, request: CancelAllRequest) -> Any:
        client = self._ensure_client()
        return client.cancel_all(request.symbol)

    def query_order(self, exchange_name: str, request: QueryOrderRequest) -> Any:
        client = self._ensure_client()
        return client.query_order(request.order_id or request.client_order_id)
