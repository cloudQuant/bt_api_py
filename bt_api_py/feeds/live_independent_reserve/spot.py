"""
Independent Reserve Spot Feed implementation.

Independent Reserve is an Australian cryptocurrency exchange.
Supports both public market data and private trading endpoints.
"""

from bt_api_py.containers.exchanges.independent_reserve_exchange_data import IndependentReserveExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_independent_reserve.request_base import IndependentReserveRequestData
from bt_api_py.functions.utils import update_extra_data


class IndependentReserveRequestDataSpot(IndependentReserveRequestData):
    """Independent Reserve Spot Feed for market data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "INDEPENDENT_RESERVE___SPOT")

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        Independent Reserve uses primaryCurrencyCode and secondaryCurrencyCode
        for symbol identification (e.g., "Xbt" for BTC, "Aud" for AUD).

        Args:
            symbol: Trading pair in format "BTC/AUD" or similar
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_tick"
        path = self._params.rest_paths.get("get_ticker", "GET /Public/GetMarketSummary")

        # Parse symbol into primary and secondary currency codes
        primary_code, secondary_code = self._parse_symbol(symbol)

        params = {
            "primaryCurrencyCode": primary_code,
            "secondaryCurrencyCode": secondary_code,
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "primary_code": primary_code,
                "secondary_code": secondary_code,
                "normalize_function": self._get_tick_normalize_function,
            },
        )

        return path, params, extra_data

    def _parse_symbol(self, symbol):
        """Parse symbol into Independent Reserve format.

        Independent Reserve uses specific currency codes:
        - Xbt for Bitcoin
        - Eth for Ethereum
        - Usdt for Tether
        - Aud for Australian Dollar
        - Usd for US Dollar
        - Nzd for New Zealand Dollar
        - Sgd for Singapore Dollar

        Args:
            symbol: Trading pair in format "BTC/AUD"

        Returns:
            tuple: (primaryCurrencyCode, secondaryCurrencyCode)
        """
        # Currency code mapping for Independent Reserve
        code_map = {
            "BTC": "Xbt",
            "ETH": "Eth",
            "USDT": "Usdt",
            "AUD": "Aud",
            "USD": "Usd",
            "NZD": "Nzd",
            "SGD": "Sgd",
        }

        parts = symbol.split("/")
        if len(parts) != 2:
            # Try other separators
            parts = symbol.split("-")

        if len(parts) == 2:
            base, quote = parts[0].upper(), parts[1].upper()
            primary_code = code_map.get(base, base.capitalize())
            secondary_code = code_map.get(quote, quote.capitalize())
            return primary_code, secondary_code

        # Default fallback
        return "Xbt", "Aud"

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data from Independent Reserve API response.

        Response format:
        {
            "LastPrice": 50000.00,
            "CurrentHighestBidPrice": 49950.00,
            "CurrentLowestOfferPrice": 50050.00,
            "DayVolumeXbt": 123.45,
            "DayVolumeAud": 6172500.00,
            "DayHighestPrice": 51000.00,
            "DayLowestPrice": 49000.00,
            "DayAvgPrice": 50000.00,
            "CreatedTimestamp": "2023-01-01T00:00:00Z",
            ...
        }
        """
        if not input_data:
            return [], False

        data = input_data if isinstance(input_data, dict) else {}

        # Map Independent Reserve fields to standard format
        ticker = {
            "symbol_name": extra_data.get("symbol_name") if extra_data else "UNKNOWN",
            "last_price": data.get("LastPrice"),
            "bid_price": data.get("CurrentHighestBidPrice"),
            "ask_price": data.get("CurrentLowestOfferPrice"),
            "volume_24h": data.get("DayVolumeXbt") or data.get("DayVolumeAud"),
            "high_24h": data.get("DayHighestPrice"),
            "low_24h": data.get("DayLowestPrice"),
            "vwap": data.get("DayAvgPrice"),
            "timestamp": data.get("CreatedTimestamp"),
        }

        return [ticker], bool(data)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker.

        Args:
            symbol: Trading pair (e.g., "BTC/AUD", "ETH/USD")
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with ticker data
        """
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth.

        Args:
            symbol: Trading pair
            count: Number of levels (may not be supported)
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_depth"
        path = self._params.rest_paths.get("get_depth", "GET /Public/GetOrderBook")

        # Parse symbol into primary and secondary currency codes
        primary_code, secondary_code = self._parse_symbol(symbol)

        params = {
            "primaryCurrencyCode": primary_code,
            "secondaryCurrencyCode": secondary_code,
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "primary_code": primary_code,
                "secondary_code": secondary_code,
                "normalize_function": self._get_depth_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth data from Independent Reserve API response.

        Response format:
        {
            "BuyOrders": [
                {"Price": 49950.00, "Volume": 1.5},
                ...
            ],
            "SellOrders": [
                {"Price": 50050.00, "Volume": 2.0},
                ...
            ]
        }
        """
        if not input_data:
            return [], False

        data = input_data if isinstance(input_data, dict) else {}

        # Map to standard format, lowercase keys for standard access
        raw_bids = data.get("BuyOrders", [])
        raw_asks = data.get("SellOrders", [])
        bids = [{k.lower() if isinstance(k, str) else k: v for k, v in item.items()} for item in raw_bids] if raw_bids else []
        asks = [{k.lower() if isinstance(k, str) else k: v for k, v in item.items()} for item in raw_asks] if raw_asks else []
        depth = {
            "symbol_name": extra_data.get("symbol_name") if extra_data else "UNKNOWN",
            "bids": bids,
            "asks": asks,
        }

        return [depth], bool(data)

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book.

        Args:
            symbol: Trading pair
            count: Number of levels
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with order book data
        """
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information (available trading pairs).

        Returns list of valid primary and secondary currency codes.

        Args:
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_exchange_info"

        # Get valid primary currencies
        path = self._params.rest_paths.get("get_markets", "GET /Public/GetValidPrimaryCurrencyCodes")

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "normalize_function": self._get_exchange_info_normalize_function,
            },
        )

        return path, {}, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info from Independent Reserve API response.

        Returns a list of available trading pairs/currencies.
        """
        if not input_data:
            return [], False

        currencies = input_data if isinstance(input_data, list) else []

        exchange_info = {
            "currencies": currencies,
            "timestamp": extra_data.get("timestamp") if extra_data else None,
        }

        return [exchange_info], bool(currencies)

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time.

        Independent Reserve doesn't have a dedicated server time endpoint,
        so we use the ticker endpoint timestamp as a proxy.

        Returns:
            RequestData with server time info
        """
        path = "GET /Public/GetValidPrimaryCurrencyCodes"
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_server_time",
                "normalize_function": None,
            },
        )
        return self.request(path, {}, extra_data=extra_data)

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange information.

        Args:
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with exchange information
        """
        path, params, extra_data = self._get_exchange_info(extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def get_recent_trades(self, symbol, count=50, extra_data=None, **kwargs):
        """Get recent trades for a symbol.

        Args:
            symbol: Trading pair
            count: Number of trades
            extra_data: Extra data
            **kwargs: Additional parameters

        Returns:
            RequestData with recent trades
        """
        request_type = "get_recent_trades"
        path = self._params.rest_paths.get("get_recent_trades", "GET /Public/GetRecentTrades")

        # Parse symbol into primary and secondary currency codes
        primary_code, secondary_code = self._parse_symbol(symbol)

        params = {
            "primaryCurrencyCode": primary_code,
            "secondaryCurrencyCode": secondary_code,
            "numberOfRecentTradesToRetrieve": count,
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "normalize_function": self._get_recent_trades_normalize_function,
            },
        )

        return self.request(path, params, extra_data=extra_data)

    @staticmethod
    def _get_recent_trades_normalize_function(input_data, extra_data):
        """Normalize recent trades response."""
        if not input_data:
            return [], False

        trades = input_data if isinstance(input_data, list) else []

        return [trades], bool(trades)

    def _make_order(self, symbol, vol, price, order_type="buy-limit", extra_data=None, **kwargs):
        """Generate parameters for placing an order.

        Args:
            symbol: Trading pair (e.g., "BTC/AUD")
            vol: Order volume
            price: Order price
            order_type: Order type (buy-limit, sell-limit, buy-market, sell-market)
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "make_order"

        # Determine order side and type
        side = "LimitBid" if order_type.startswith("buy") else "LimitOffer"
        if "market" in order_type.lower():
            path = self._params.rest_paths.get("place_market_order", "POST /Private/PlaceMarketOrder")
        else:
            path = self._params.rest_paths.get("place_limit_order", "POST /Private/PlaceLimitOrder")

        # Parse symbol
        primary_code, secondary_code = self._parse_symbol(symbol)

        params = {
            "primaryCurrencyCode": primary_code,
            "secondaryCurrencyCode": secondary_code,
            "volume": vol,
        }

        # Only add price for limit orders
        if "limit" in order_type.lower() and price:
            params["price"] = price

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "order_type": order_type,
                "normalize_function": self._make_order_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """Normalize make order response."""
        if not input_data:
            return [], False

        data = input_data if isinstance(input_data, dict) else {}

        return [data], bool(data)

    def _cancel_order(self, order_id, symbol=None, extra_data=None, **kwargs):
        """Generate parameters for canceling an order.

        Args:
            order_id: Order ID to cancel
            symbol: Trading pair (optional, for validation)
            extra_data: Extra data to attach to response
            **kwargs: Additional parameters

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "cancel_order"
        path = self._params.rest_paths.get("cancel_order", "POST /Private/CancelOrder")

        params = {
            "orderGuid": order_id,
        }

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "order_id": order_id,
                "symbol_name": symbol,
                "normalize_function": self._cancel_order_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        """Normalize cancel order response."""
        if not input_data:
            return [], False

        data = input_data if isinstance(input_data, dict) else {}

        return [data], bool(data)
