"""
Poloniex Spot Trading Feed
Implements spot trading functionality for Poloniex exchange.
"""

from bt_api_py.containers.exchanges.poloniex_exchange_data import PoloniexExchangeDataSpot
from bt_api_py.feeds.live_poloniex.request_base import PoloniexRequestData
from bt_api_py.functions.log_message import SpdLogManager


class PoloniexRequestDataSpot(PoloniexRequestData):
    """Poloniex Spot Trading REST API Client."""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "poloniex_spot_feed.log")
        self._params = PoloniexExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

    def _make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """
        Prepare order request parameters.

        Args:
            symbol: Trading symbol
            vol: Order volume
            price: Order price (required for limit orders)
            order_type: Order type (buy-limit, sell-limit, buy-market, sell-market)
            offset: Order offset (open, close) - not used in spot
            post_only: Post-only flag (limit orders only)
            client_order_id: Client-specified order ID
            extra_data: Extra metadata
            **kwargs: Additional parameters

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)

        # Parse order type
        if "-" in order_type:
            side, order_type = order_type.split("-")
        else:
            side = "BUY" if "buy" in order_type.lower() else "SELL"
            order_type = order_type.upper()

        # Build parameters
        params = {
            "symbol": request_symbol,
            "side": side.upper(),
        }

        # Set order type
        order_type_upper = order_type.upper()
        if "MARKET" in order_type_upper:
            params["type"] = "MARKET"
            # Market buy can use amount instead of quantity
            if side.upper() == "BUY" and kwargs.get("amount"):
                params["amount"] = str(kwargs["amount"])
            else:
                params["quantity"] = str(vol)
        elif "LIMIT" in order_type_upper:
            params["type"] = "LIMIT"
            params["quantity"] = str(vol)
            if price is None:
                raise ValueError("Price required for limit orders")
            params["price"] = str(price)
            params["timeInForce"] = kwargs.get("time_in_force", "GTC")
            if post_only:
                params["timeInForce"] = "GTX"  # Post-only (Good Till Crossing)
        elif "LIMIT_MAKER" in order_type_upper:
            params["type"] = "LIMIT_MAKER"
            params["quantity"] = str(vol)
            if price is None:
                raise ValueError("Price required for limit maker orders")
            params["price"] = str(price)
        else:
            params["type"] = order_type_upper
            params["quantity"] = str(vol)

        # Add client order ID if provided
        if client_order_id is not None:
            params["clientOrderId"] = client_order_id

        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": self._make_order_normalize_function,
        })

        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """
        Normalize order creation response.

        Args:
            input_data: Raw API response
            extra_data: Extra metadata

        Returns:
            Tuple of (normalized_data, status)
        """
        status = input_data is not None
        symbol_name = extra_data.get("symbol_name", "")
        asset_type = extra_data.get("asset_type", "SPOT")

        if status and isinstance(input_data, dict):
            # Return single order in list
            return [input_data], True
        elif status and isinstance(input_data, list):
            return input_data, True

        return [], False

    def _cancel_order(
        self,
        symbol,
        order_id=None,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """
        Prepare cancel order request.

        Args:
            symbol: Trading symbol
            order_id: Exchange order ID
            client_order_id: Client order ID
            extra_data: Extra metadata
            **kwargs: Additional parameters

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "cancel_order"

        if order_id:
            path = f"DELETE /orders/{order_id}"
            params = {}
        else:
            path = self._params.get_rest_path("cancel_orders")
            params = {
                "symbol": request_symbol,
            }
            if client_order_id:
                params["clientOrderId"] = client_order_id

        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })

        return path, params, extra_data

    def _query_order(
        self,
        symbol,
        order_id,
        extra_data=None,
        **kwargs,
    ):
        """
        Prepare query order request.

        Args:
            symbol: Trading symbol
            order_id: Exchange order ID
            extra_data: Extra metadata
            **kwargs: Additional parameters

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "query_order"
        path = f"GET /orders/{order_id}"

        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })

        return path, params, extra_data

    def _get_open_orders(
        self,
        symbol=None,
        extra_data=None,
        **kwargs,
    ):
        """
        Prepare get open orders request.

        Args:
            symbol: Trading symbol (optional, if None get all)
            extra_data: Extra metadata
            **kwargs: Additional parameters

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)

        params = {}
        if symbol:
            request_symbol = self._params.get_symbol(symbol)
            params["symbol"] = request_symbol

        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })

        return path, params, extra_data

    def _get_balance(
        self,
        currency=None,
        extra_data=None,
        **kwargs,
    ):
        """
        Prepare get balance request.

        Args:
            currency: Currency code (optional)
            extra_data: Extra metadata
            **kwargs: Additional parameters

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_type = "get_balance"
        path = self._params.get_rest_path(request_type)

        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": currency or "ALL",
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })

        return path, params, extra_data

    def _get_ticker(
        self,
        symbol,
        extra_data=None,
        **kwargs,
    ):
        """
        Prepare get ticker request.

        Args:
            symbol: Trading symbol
            extra_data: Extra metadata
            **kwargs: Additional parameters

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_ticker"
        path = self._params.get_rest_path(request_type).replace("{symbol}", request_symbol)

        params = {}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })

        return path, params, extra_data

    def _get_depth(
        self,
        symbol,
        limit=10,
        extra_data=None,
        **kwargs,
    ):
        """
        Prepare get order book request.

        Args:
            symbol: Trading symbol
            limit: Number of levels (default 10, max 150)
            extra_data: Extra metadata
            **kwargs: Additional parameters

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_orderbook"
        path = self._params.get_rest_path(request_type).replace("{symbol}", request_symbol)

        params = {
            "limit": min(limit, 150),
        }
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })

        return path, params, extra_data

    def _get_kline(
        self,
        symbol,
        period="1m",
        limit=100,
        extra_data=None,
        **kwargs,
    ):
        """
        Prepare get kline request.

        Args:
            symbol: Trading symbol
            period: Kline period (1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 3d, 1w, 1M)
            limit: Number of candles (default 100, max 500)
            extra_data: Extra metadata
            **kwargs: Additional parameters

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type).replace("{symbol}", request_symbol)

        period_map = self._params.get_period(period)

        params = {
            "interval": period_map,
            "limit": min(limit, 500),
        }
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })

        return path, params, extra_data

    def _get_trades(
        self,
        symbol,
        limit=500,
        extra_data=None,
        **kwargs,
    ):
        """
        Prepare get recent trades request.

        Args:
            symbol: Trading symbol
            limit: Number of trades (default 500, max 1000)
            extra_data: Extra metadata
            **kwargs: Additional parameters

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_trades"
        path = self._params.get_rest_path(request_type).replace("{symbol}", request_symbol)

        params = {
            "limit": min(limit, 1000),
        }
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })

        return path, params, extra_data

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        """
        Normalize ticker response.

        Args:
            input_data: Raw API response
            extra_data: Extra metadata

        Returns:
            Tuple of (normalized_data, status)
        """
        status = input_data is not None

        if status and isinstance(input_data, dict):
            # Extract data array if present
            if "data" in input_data:
                return input_data["data"], True
            return [input_data], True
        elif status and isinstance(input_data, list):
            return input_data, True

        return [], False

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """
        Normalize orderbook response.

        Args:
            input_data: Raw API response
            extra_data: Extra metadata

        Returns:
            Tuple of (normalized_data, status)
        """
        status = input_data is not None

        if status and isinstance(input_data, dict):
            # Poloniex returns orderbook with bids/asks
            result = {
                "bids": input_data.get("bids", []),
                "asks": input_data.get("asks", []),
            }
            return [result], True
        elif status and isinstance(input_data, list):
            return input_data, True

        return [], False

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """
        Normalize kline response.

        Args:
            input_data: Raw API response
            extra_data: Extra metadata

        Returns:
            Tuple of (normalized_data, status)
        """
        status = input_data is not None

        if status and isinstance(input_data, list):
            return input_data, True
        elif status and isinstance(input_data, dict):
            if "data" in input_data:
                return input_data["data"], True
            return [input_data], True

        return [], False

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """
        Normalize trades response.

        Args:
            input_data: Raw API response
            extra_data: Extra metadata

        Returns:
            Tuple of (normalized_data, status)
        """
        status = input_data is not None

        if status and isinstance(input_data, list):
            return input_data, True
        elif status and isinstance(input_data, dict):
            if "data" in input_data:
                return input_data["data"], True
            return [input_data], True

        return [], False

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        """
        Normalize balance response.

        Args:
            input_data: Raw API response
            extra_data: Extra metadata

        Returns:
            Tuple of (normalized_data, status)
        """
        status = input_data is not None

        if status and isinstance(input_data, dict):
            if "balances" in input_data:
                return input_data["balances"], True
            return [input_data], True
        elif status and isinstance(input_data, list):
            return input_data, True

        return [], False

    def _get_deals(
        self,
        symbol,
        limit=100,
        extra_data=None,
        **kwargs,
    ):
        """
        Prepare get user trades request.

        Args:
            symbol: Trading symbol
            limit: Number of trades
            extra_data: Extra metadata
            **kwargs: Additional parameters

        Returns:
            Tuple of (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_deals"
        path = self._params.get_rest_path(request_type)

        params = {
            "symbol": request_symbol,
            "limit": min(limit, 1000),
        }
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "exchange_name": self.exchange_name,
            "normalize_function": None,
        })

        return path, params, extra_data
