"""FeedBrokerAdapter: a broker adapter backed by a direct-mode BtApi (Task 3.2)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast

from bt_api_py._contracts.models import (
    OrderRequest as ContractOrderRequest,
)
from bt_api_py._contracts.models import (
    OrderType as ContractOrderType,
)
from bt_api_py._contracts.models import (
    Side as ContractSide,
)
from bt_api_py.brokers.base import BrokerAdapter
from bt_api_py.brokers.types import (
    AccountSnapshot,
    BrokerCapabilities,
    CancelOrderRequest,
    OrderRequest,
    OrderSide,
    OrderSnapshot,
    OrderStatus,
    PositionSnapshot,
)


class FeedBrokerAdapter(BrokerAdapter):
    """Broker adapter backed by a ``BtApi(transport_mode="direct")`` instance.

    The gateway constructs a single direct-mode BtApi that owns exchange
    credentials and feeds; this adapter exposes its account/positions/orders/
    quote operations to the order router without creating a second public
    client (U-03).
    """

    def __init__(self, bt_api: Any, *, exchange_name: str, account_id: str = "paper") -> None:
        self._bt_api = bt_api
        self._exchange_name = exchange_name
        self.account_id = account_id
        self.connected = False

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def disconnect(self) -> bool:
        self.connected = False
        return True

    async def health(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "adapter": "feed_bridge",
            "exchange_name": self._exchange_name,
            "account_id": self.account_id,
        }

    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            supports_market_data=True,
            supports_order_submit=True,
            supports_order_cancel=True,
            supports_positions=True,
            supports_account=True,
        )

    async def list_accounts(self) -> list[AccountSnapshot]:
        return [AccountSnapshot(account_id=self.account_id, cash=0.0, equity=0.0)]

    async def get_account(self, account_id: str) -> AccountSnapshot:
        result = self._bt_api.get_account(self._exchange_name)
        return self._to_account_snapshot(result, account_id)

    async def list_positions(self, account_id: str) -> list[PositionSnapshot]:
        result = self._bt_api.get_position(self._exchange_name)
        return self._to_position_snapshots(result, account_id)

    async def list_orders(self, account_id: str) -> list[OrderSnapshot]:
        result = self._bt_api.get_open_orders(self._exchange_name)
        return self._to_order_snapshots(result, account_id)

    async def place_order(self, request: OrderRequest) -> OrderSnapshot:
        contract = self._to_contract_request(request)
        result = self._bt_api.make_order(self._exchange_name, contract)
        return self._to_order_snapshot(result, request)

    async def cancel_order(self, request: CancelOrderRequest) -> OrderSnapshot:
        result = self._bt_api.cancel_order(self._exchange_name, request.symbol, request.order_id)
        return self._to_order_snapshot(result, request)

    async def get_quote(self, symbol: str) -> dict[str, Any]:
        result = self._bt_api.get_tick(self._exchange_name, symbol)
        if isinstance(result, dict):
            return {"symbol": symbol, "price": result.get("price", 0.0)}
        return {"symbol": symbol, "price": getattr(result, "price", 0.0)}

    @staticmethod
    def _to_contract_request(request: OrderRequest) -> ContractOrderRequest:
        side = ContractSide(str(request.side))
        order_type = ContractOrderType(str(request.order_type))
        price = Decimal(str(request.price)) if request.price is not None else None
        return ContractOrderRequest(
            symbol=request.symbol,
            side=side,
            order_type=order_type,
            quantity=Decimal(str(request.quantity)),
            price=price,
            account_id=request.account_id,
            client_order_id=request.client_order_id
            or f"legacy-{request.idempotency_key or 'auto'}",
        )

    @staticmethod
    def _to_account_snapshot(result: Any, account_id: str) -> AccountSnapshot:
        if isinstance(result, AccountSnapshot):
            return result
        payload: dict[str, Any] = result if isinstance(result, dict) else {}
        return AccountSnapshot(
            account_id=payload.get("account_id") or account_id,
            currency=str(payload.get("currency", "CNY")),
            cash=float(payload.get("cash") or 0.0),
            equity=float(payload.get("equity") or payload.get("value") or 0.0),
            available_cash=float(payload.get("available_cash") or payload.get("cash") or 0.0),
        )

    @staticmethod
    def _to_position_snapshots(result: Any, account_id: str) -> list[PositionSnapshot]:
        if result is None:
            return []
        if isinstance(result, dict):
            result = result.get("positions", [])
        snapshots: list[PositionSnapshot] = []
        for item in result:
            payload: dict[str, Any] = item if isinstance(item, dict) else {}
            snapshots.append(
                PositionSnapshot(
                    account_id=payload.get("account_id") or account_id,
                    symbol=str(payload.get("symbol", "")),
                    quantity=float(payload.get("quantity") or payload.get("size") or 0.0),
                    average_price=float(payload.get("average_price") or 0.0),
                )
            )
        return snapshots

    @staticmethod
    def _to_order_snapshots(result: Any, account_id: str) -> list[OrderSnapshot]:
        if result is None:
            return []
        if isinstance(result, dict):
            result = result.get("orders", [])
        snapshots: list[OrderSnapshot] = []
        for item in result:
            payload: dict[str, Any] = item if isinstance(item, dict) else {}
            snapshots.append(
                OrderSnapshot(
                    order_id=str(payload.get("order_id", "")),
                    account_id=payload.get("account_id") or account_id,
                    symbol=str(payload.get("symbol", "")),
                    side=cast("OrderSide", str(payload.get("side", "buy"))),
                    quantity=float(payload.get("quantity") or 0.0),
                    status=cast("OrderStatus", str(payload.get("status", "submitted"))),
                )
            )
        return snapshots

    @staticmethod
    def _to_order_snapshot(result: Any, request: Any) -> OrderSnapshot:
        payload: dict[str, Any] = result if isinstance(result, dict) else {}
        return OrderSnapshot(
            order_id=str(payload.get("order_id", payload.get("id", ""))),
            account_id=request.account_id,
            symbol=str(payload.get("symbol", request.symbol)),
            side=cast("OrderSide", str(getattr(request, "side", "buy"))),
            quantity=float(getattr(request, "quantity", 0.0)),
            status=cast("OrderStatus", str(payload.get("status", "submitted"))),
        )
