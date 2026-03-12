from datetime import datetime
from typing import Any

from bt_api_py.containers.exchanges.dydx_exchange_data import DydxExchangeDataSwap
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_dydx.request_base import DydxRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class DydxRequestDataSpot(DydxRequestData):
    """dYdX Spot Request Data class."""

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "swap")
        self.logger_name = kwargs.get("logger_name", "dydx_spot_feed.log")
        self._params = DydxExchangeDataSwap()
        self.request_logger = get_logger("dydx_spot_feed")
        self.async_logger = get_logger("dydx_spot_feed")

    def get_ticker_spot(
        self, symbol: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get spot ticker information."""
        request_symbol = self._params.get_symbol(symbol) if symbol is not None else ""
        request_type = "get_ticker"
        params = {
            "instId": request_symbol,
        }
        path = self._params.get_rest_path(request_type)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": request_symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": DydxRequestDataSpot._get_ticker_spot_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_ticker_spot_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[list[float] | None, bool]:
        def _normalize_timestamp(value: Any) -> float:
            if value is None:
                return 0.0
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    try:
                        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        return 0.0
            return 0.0

        if extra_data is None:
            pass
        status = input_data.get("code", 0) == 0
        if "markets" in input_data:
            markets = input_data["markets"]
            symbol = extra_data.get("symbol_name", "")
            if symbol in markets:
                data = markets[symbol]
                timestamp = _normalize_timestamp(data.get("snapshotAt", 0))
                ticker_info = [
                    timestamp,
                    float(data.get("oraclePrice", 0)),
                    float(data.get("markPrice", 0)),
                    float(data.get("lastPrice", 0)),
                    float(data.get("volume24H", 0)),
                    float(data.get("high24H", 0)),
                    float(data.get("low24H", 0)),
                    float(data.get("volumeNotional24H", 0)),
                    float(data.get("openInterest24H", 0)),
                ]
                return ticker_info, status
        return None, False

    def get_ticker(self, symbol: Any, extra_data: Any = None, **kwargs: Any) -> RequestData:
        """Get ticker information."""
        path, params, extra_data = self.get_ticker_spot(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def get_balance_spot(
        self, address: Any, subaccount_number: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, None, dict[str, Any]]:
        """Get spot balance information."""
        request_type = "get_subaccount"
        path = self._params.get_rest_path(request_type)
        path = path.replace("<address>", address).replace(
            "<subaccount_number>", str(subaccount_number)
        )
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": DydxRequestDataSpot._get_balance_spot_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, None, extra_data

    @staticmethod
    def _get_balance_spot_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        if extra_data is None:
            pass
        status = True
        subaccount = input_data.get("subaccount", {})
        balance_info = []

        # Extract balance information - check if subaccount has data
        if subaccount:
            balance_info.append(
                {
                    "symbol": "USD",
                    "equity": float(subaccount.get("equity", 0)),
                    "free_collateral": float(subaccount.get("freeCollateral", 0)),
                    "available_margin": float(subaccount.get("availableMargin", 0)),
                    "position_margin": float(subaccount.get("positionMargin", 0)),
                    "margin_balance": float(subaccount.get("marginBalance", 0)),
                }
            )

        data = balance_info
        return data, status

    def get_balance(self, symbol: Any = None, extra_data: Any = None, **kwargs: Any) -> RequestData:
        """Get balance information."""
        address = kwargs.pop("address", self.address or "")
        subaccount_number = kwargs.pop("subaccount_number", self.subaccount_number)
        path, params, extra_data = self.get_balance_spot(
            address, subaccount_number, extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    # ==================== Kline Methods ====================

    def get_kline_spot(
        self, symbol: Any, period: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Get kline/candlestick data."""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_candles"
        path = self._params.get_rest_path(request_type)
        path = path.replace("<symbol>", request_symbol)

        # Convert period format
        exchange_period = self._params.get_period(period)

        params = {
            "resolution": exchange_period,
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": request_symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "period": period,
                "normalize_function": DydxRequestDataSpot._get_kline_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        """Normalize kline/candlestick data."""
        status = True
        candles = input_data.get("candles", [])
        kline_info = [
            {
                "timestamp": c.get("startedAt", ""),
                "open": float(c.get("open", 0)),
                "high": float(c.get("high", 0)),
                "low": float(c.get("low", 0)),
                "close": float(c.get("close", 0)),
                "volume": float(c.get("volume", 0)),
            }
            for c in candles
        ]

        return kline_info, status

    # ==================== OrderBook Methods ====================

    @staticmethod
    def _get_orderbook_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[dict[str, Any], bool]:
        """Normalize orderbook data."""
        status = True
        if extra_data is None:
            extra_data = {}

        orderbook_info = {
            "symbol": extra_data.get("symbol_name", ""),
            "bids": input_data.get("bids", []),
            "asks": input_data.get("asks", []),
        }
        return orderbook_info, status

    # ==================== Exchange Info Methods ====================

    @staticmethod
    def _get_exchange_info_normalize_function(
        input_data: Any, extra_data: Any
    ) -> tuple[list[dict[str, Any]], bool]:
        """Normalize exchange info response."""
        status = True
        markets = input_data.get("markets", {})
        exchange_info = []

        for symbol, market_data in markets.items():
            exchange_info.append(
                {
                    "symbol": symbol,
                    "status": market_data.get("status", "ACTIVE"),
                }
            )

        return exchange_info, status

    # ── Standard Interface: make_order ──────────────────────────────

    def _make_order(
        self,
        symbol: Any,
        volume: Any,
        price: Any,
        order_type: Any,
        offset: Any = "open",
        post_only: Any = False,
        client_order_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Prepare order request parameters. Returns (path, body, extra_data).
        Note: dYdX V4 trading requires wallet signature (on-chain tx).
        This method prepares the request data structure.
        """
        if extra_data is None:
            extra_data = {}
        request_symbol = self._params.get_symbol(symbol)
        side = kwargs.get("side", "BUY")
        body = {
            "market": request_symbol,
            "side": side.upper(),
            "type": order_type.upper() if isinstance(order_type, str) else "LIMIT",
            "size": str(volume),
            "price": str(price) if price else "0",
            "postOnly": post_only,
        }
        if client_order_id:
            body["clientId"] = client_order_id
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": request_symbol,
                "asset_type": self.asset_type,
                "request_type": "make_order",
                "side": side,
                "quantity": volume,
                "price": price,
                "order_type": order_type,
            }
        )
        return "POST /v4/orders", body, extra_data

    def make_order(
        self,
        symbol: Any,
        volume: Any,
        price: Any,
        order_type: Any,
        offset: Any = "open",
        post_only: Any = False,
        client_order_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> RequestData:
        """Place order following standard Feed interface. Returns RequestData."""
        path, body, extra_data = self._make_order(
            symbol,
            volume,
            price,
            order_type,
            offset,
            post_only,
            client_order_id,
            extra_data,
            **kwargs,
        )
        return self.request(path, body=body, extra_data=extra_data)

    # ── Standard Interface: cancel_order ────────────────────────────

    def _cancel_order(
        self, symbol: Any, order_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, None, dict[str, Any]]:
        """Prepare cancel order request. Returns (path, body, extra_data)."""
        if extra_data is None:
            extra_data = {}
        request_symbol = self._params.get_symbol(symbol)
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": request_symbol,
                "asset_type": self.asset_type,
                "request_type": "cancel_order",
                "order_id": order_id,
            }
        )
        return f"DELETE /v4/orders/{order_id}", None, extra_data

    def cancel_order(
        self,
        symbol: Any = None,
        order_id: Any = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> RequestData:
        """Cancel order following standard Feed interface. Returns RequestData."""
        path, body, extra_data = self._cancel_order(
            symbol or kwargs.get("symbol"),
            order_id or kwargs.get("order_id"),
            extra_data,
            **kwargs,
        )
        return self.request(path, body=body, extra_data=extra_data)

    # ── Standard Interface: query_order ─────────────────────────────

    def _query_order(
        self, symbol: Any, order_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Prepare query order request. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        request_symbol = self._params.get_symbol(symbol)
        path = self._params.get_rest_path("get_orders")
        params = {"orderId": order_id}
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": request_symbol,
                "asset_type": self.asset_type,
                "request_type": "query_order",
                "order_id": order_id,
            }
        )
        return path, params, extra_data

    def query_order(
        self, symbol: Any, order_id: Any, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        """Query order status. Returns RequestData."""
        path, params, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ── Standard Interface: get_open_orders ─────────────────────────

    def _get_open_orders(
        self, symbol: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Prepare open orders request. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("get_orders")
        params = {"status": "OPEN"}
        if symbol:
            params["market"] = self._params.get_symbol(symbol)
        address = kwargs.get("address", self.address or "")
        if address:
            params["address"] = address
        subaccount_number = kwargs.get("subaccount_number", self.subaccount_number)
        if subaccount_number is not None:
            params["subaccountNumber"] = subaccount_number
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_open_orders",
            }
        )
        return path, params, extra_data

    def get_open_orders(
        self, symbol: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        """Get open orders. Returns RequestData."""
        path, params, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ── Standard Interface: get_account ─────────────────────────────

    def _get_account(
        self, symbol: Any = "ALL", extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Prepare account request. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        address = kwargs.get("address", self.address or "")
        subaccount_number = kwargs.get("subaccount_number", self.subaccount_number)
        path = self._params.get_rest_path("get_subaccount")
        path = path.replace("<placeholder>", address, 1)
        path = path.replace("<placeholder>", str(subaccount_number), 1)
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "request_type": "get_account",
            }
        )
        return path, {}, extra_data

    def get_account(
        self, symbol: Any = "ALL", extra_data: Any = None, **kwargs: Any
    ) -> RequestData:
        """Get account info. Returns RequestData."""
        path, params, extra_data = self._get_account(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ── Standard Interface: get_balance (standard signature) ────────

    def _get_balance_std(
        self, symbol: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Prepare balance request with standard signature. Returns (path, params, extra_data)."""
        if extra_data is None:
            extra_data = {}
        address = kwargs.get("address", self.address or "")
        subaccount_number = kwargs.get("subaccount_number", self.subaccount_number)
        path = self._params.get_rest_path("get_subaccount")
        path = path.replace("<placeholder>", address, 1)
        path = path.replace("<placeholder>", str(subaccount_number), 1)
        extra_data.update(
            {
                "exchange_name": self.exchange_name,
                "symbol_name": symbol or "",
                "asset_type": self.asset_type,
                "request_type": "get_balance",
                "normalize_function": DydxRequestDataSpot._get_balance_spot_normalize_function,
            }
        )
        return path, {}, extra_data
