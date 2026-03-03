"""
bitFlyer Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.bitflyer_exchange_data import BitflyerExchangeDataSpot
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_bitflyer.request_base import BitflyerRequestData


class BitflyerRequestDataSpot(BitflyerRequestData):
    """bitFlyer Spot Feed for market data."""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_name = kwargs.get("exchange_name", "BITFLYER___SPOT")

    def _normalize_product_code(self, symbol):
        """Normalize symbol to bitFlyer product_code format (e.g., BTC_JPY)."""
        # Convert common formats to bitFlyer format
        symbol = symbol.upper().replace("/", "_").replace("-", "_")
        return symbol

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data."""
        request_type = "get_tick"
        product_code = self._normalize_product_code(symbol)
        path = f"GET /v1/ticker"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_tick_normalize_function,
        })
        return self.request(path, params={"product_code": product_code}, extra_data=extra_data)

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        """Normalize ticker data."""
        if not input_data:
            return [], False
        # bitFlyer returns ticker directly
        if isinstance(input_data, dict) and "product_code" in input_data:
            return [input_data], True
        return [], False

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker."""
        return self._get_tick(symbol, extra_data, **kwargs)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth."""
        request_type = "get_depth"
        product_code = self._normalize_product_code(symbol)
        path = f"GET /v1/board"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        return self.request(path, params={"product_code": product_code}, extra_data=extra_data)

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth data."""
        if not input_data:
            return [], False
        # bitFlyer board has mid_price, bids, asks
        if isinstance(input_data, dict) and "bids" in input_data:
            return [input_data], True
        return [], False

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book."""
        return self._get_depth(symbol, count, extra_data, **kwargs)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data.

        bitFlyer doesn't have a dedicated kline endpoint.
        We use executions to build OHLCV data.
        """
        request_type = "get_kline"
        product_code = self._normalize_product_code(symbol)
        path = f"GET /v1/executions"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "period": period,
            "normalize_function": self._get_kline_normalize_function,
        })
        return self.request(path, params={
            "product_code": product_code,
            "count": count,
        }, extra_data=extra_data)

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline data from executions."""
        if not input_data:
            return [], False
        # bitFlyer returns array of executions
        if isinstance(input_data, list):
            return [input_data], True
        elif isinstance(input_data, dict) and isinstance(input_data.get("data"), list):
            return [input_data["data"]], True
        return [], False

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data."""
        return self._get_kline(symbol, period, count, extra_data, **kwargs)

    def _get_trades(self, symbol, limit=100, extra_data=None, **kwargs):
        """Get recent trades/executions."""
        request_type = "get_trades"
        product_code = self._normalize_product_code(symbol)
        path = f"GET /v1/executions"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_trades_normalize_function,
        })
        return self.request(path, params={
            "product_code": product_code,
            "count": limit,
        }, extra_data=extra_data)

    @staticmethod
    def _get_trades_normalize_function(input_data, extra_data):
        """Normalize trades data."""
        if not input_data:
            return [], False
        # bitFlyer returns array of executions
        if isinstance(input_data, list):
            return [input_data], True
        elif isinstance(input_data, dict) and isinstance(input_data.get("data"), list):
            return [input_data["data"]], True
        return [], False

    def get_trades(self, symbol, limit=100, extra_data=None, **kwargs):
        """Get recent trades."""
        return self._get_trades(symbol, limit, extra_data, **kwargs)

    def _get_markets(self, extra_data=None, **kwargs):
        """Get all available markets."""
        request_type = "get_markets"
        path = f"GET /v1/getmarkets"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_markets_normalize_function,
        })
        return self.request(path, params=None, extra_data=extra_data)

    @staticmethod
    def _get_markets_normalize_function(input_data, extra_data):
        """Normalize markets data."""
        if not input_data:
            return [], False
        # bitFlyer returns array of market objects
        if isinstance(input_data, list):
            return [input_data], True
        return [], False

    def get_markets(self, extra_data=None, **kwargs):
        """Get all markets."""
        return self._get_markets(extra_data, **kwargs)

    def _get_health(self, symbol=None, extra_data=None, **kwargs):
        """Get exchange health status."""
        request_type = "get_health"
        path = f"GET /v1/gethealth"
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "normalize_function": self._get_health_normalize_function,
        })
        params = {}
        if symbol:
            product_code = self._normalize_product_code(symbol)
            params["product_code"] = product_code
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_health_normalize_function(input_data, extra_data):
        """Normalize health data."""
        if not input_data:
            return [], False
        if isinstance(input_data, dict) and "status" in input_data:
            return [input_data], True
        return [], False

    def get_health(self, symbol=None, extra_data=None, **kwargs):
        """Get health status."""
        return self._get_health(symbol, extra_data, **kwargs)

    # Async methods - bitflyer doesn't support native async, so we provide sync wrappers
    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data asynchronously (wrapper around sync)."""
        result = self.get_tick(symbol, extra_data, **kwargs)
        if self.data_queue is not None:
            self.data_queue.put(result)
        return result

    def async_get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth asynchronously (wrapper around sync)."""
        result = self.get_depth(symbol, count, extra_data, **kwargs)
        if self.data_queue is not None:
            self.data_queue.put(result)
        return result

    def async_get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data asynchronously (wrapper around sync)."""
        result = self.get_kline(symbol, period, count, extra_data, **kwargs)
        if self.data_queue is not None:
            self.data_queue.put(result)
        return result
