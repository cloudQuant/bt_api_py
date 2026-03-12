"""HTX USDT-M Linear Swap Trading Feed

Uses api.hbdm.com with /linear-swap-api/ and /linear-swap-ex/ prefixes.
Symbol format: BTC-USDT (uppercase, dash-separated).
Market data uses 'contract_code' parameter instead of 'symbol'.
"""

from typing import Any

from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataUsdtSwap
from bt_api_py.feeds.live_htx.request_base import HtxRequestData
from bt_api_py.feeds.live_htx.spot import HtxAccountWssDataSpot, HtxMarketWssDataSpot
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class HtxRequestDataUsdtSwap(HtxRequestData):
    """HTX USDT-M Linear Swap REST API feed.

    HTX derivatives use different parameter names than spot:
    - 'contract_code' instead of 'symbol' for market data
    - POST body instead of query params for private endpoints
    """

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "USDT_SWAP")
        self.logger_name = kwargs.get("logger_name", "htx_usdt_swap_feed.log")
        self._params = HtxExchangeDataUsdtSwap()
        self.request_logger = get_logger("request")
        self.async_logger = get_logger("async_request")

    # ==================== Market Data ====================

    def _get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker data for a contract.

        Args:
            symbol: Contract code (e.g., BTC-USDT)
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_ticker"
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        params = {"contract_code": request_symbol}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker data for a contract."""
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data (standard interface alias)."""
        return self.get_ticker(symbol, extra_data=extra_data, **kwargs)

    def _get_depth(self, symbol, depth_type="step0", extra_data=None, **kwargs):
        """Get orderbook depth for a contract."""
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        params = {"contract_code": request_symbol, "type": depth_type}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_depth(self, symbol, count=None, depth_type="step0", extra_data=None, **kwargs):
        """Get orderbook depth for a contract."""
        path, params, extra_data = self._get_depth(symbol, depth_type, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_kline(self, symbol, period="1min", count=150, extra_data=None, **kwargs):
        """Get kline data for a contract."""
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        request_period = self._params.get_period(period)
        params = {
            "contract_code": request_symbol,
            "period": request_period,
            "size": count,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_kline(self, symbol, period="1min", count=150, extra_data=None, **kwargs):
        """Get kline data for a contract."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time."""
        path = self._params.get_rest_path("get_server_time")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_server_time",
                "symbol_name": "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return self.request(path, params={}, extra_data=extra_data)

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get contract info (exchange info)."""
        path = self._params.get_rest_path("get_contract")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_exchange_info",
                "symbol_name": "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return self.request(path, params={}, extra_data=extra_data)

    def get_symbols(self, extra_data=None, **kwargs):
        """Get contract list (alias for get_exchange_info)."""
        return self.get_exchange_info(extra_data=extra_data, **kwargs)

    # ==================== Account ====================

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info."""
        path = self._params.get_rest_path("get_account")
        body = {}
        if symbol:
            body["contract_code"] = self._params.get_symbol(symbol)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_account",
                "symbol_name": symbol or "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def get_accounts(self, extra_data=None, **kwargs):
        """Get account info (alias)."""
        return self.get_account(extra_data=extra_data, **kwargs)

    def get_balance(self, symbol=None, account_id=None, extra_data=None, **kwargs):
        """Get account balance."""
        aid = account_id if account_id is not None else symbol
        return self.get_account(symbol=aid, extra_data=extra_data, **kwargs)

    def get_position(self, symbol=None, extra_data=None, **kwargs):
        """Get position info."""
        path = self._params.get_rest_path("get_position")
        body = {}
        if symbol:
            body["contract_code"] = self._params.get_symbol(symbol)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_position",
                "symbol_name": symbol or "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)

    # ==================== Trade ====================

    def _make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="limit",
        direction="buy",
        offset="open",
        lever_rate=1,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Create an order."""
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        body = {
            "contract_code": request_symbol,
            "volume": int(vol),
            "direction": direction,
            "offset": offset,
            "lever_rate": lever_rate,
            "order_price_type": order_type,
        }
        if price is not None:
            body["price"] = float(price)
        if client_order_id:
            body["client_order_id"] = client_order_id
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return path, body, extra_data

    def make_order(
        self,
        symbol,
        volume,
        price=None,
        order_type="limit",
        direction="buy",
        offset="open",
        post_only=False,
        lever_rate=1,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Create an order."""
        path, body, extra_data = self._make_order(
            symbol,
            volume,
            price,
            order_type,
            direction,
            offset,
            lever_rate,
            client_order_id,
            extra_data,
            **kwargs,
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def _cancel_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        """Cancel an order."""
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)
        body = {"order_id": str(order_id) if order_id is not None else ""}
        if symbol:
            body["contract_code"] = self._params.get_symbol(symbol)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return path, body, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel an order."""
        path, body, extra_data = self._cancel_order(
            symbol=symbol, order_id=order_id, extra_data=extra_data, **kwargs
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order details."""
        path = self._params.get_rest_path("query_order")
        body = {"order_id": str(order_id)}
        if symbol:
            body["contract_code"] = self._params.get_symbol(symbol)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "query_order",
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders."""
        path = self._params.get_rest_path("get_open_orders")
        body = {}
        if symbol:
            body["contract_code"] = self._params.get_symbol(symbol)
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_open_orders",
                "symbol_name": symbol or "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)


class HtxMarketWssDataUsdtSwap(HtxMarketWssDataSpot):
    """HTX USDT Swap Market WebSocket data feed."""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        kwargs.setdefault("exchange_data", HtxExchangeDataUsdtSwap())
        kwargs.setdefault("asset_type", "USDT_SWAP")
        super().__init__(data_queue, **kwargs)


class HtxAccountWssDataUsdtSwap(HtxAccountWssDataSpot):
    """HTX USDT Swap Account WebSocket data feed."""

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        kwargs.setdefault("exchange_data", HtxExchangeDataUsdtSwap())
        kwargs.setdefault("asset_type", "USDT_SWAP")
        super().__init__(data_queue, **kwargs)
