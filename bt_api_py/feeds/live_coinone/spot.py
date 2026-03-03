"""
Coinone Spot Feed implementation.
"""

from bt_api_py.containers.exchanges.coinone_exchange_data import CoinoneExchangeDataSpot
from bt_api_py.containers.tickers.coinone_ticker import CoinoneRequestTickerData
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.live_coinone.request_base import CoinoneRequestData


class CoinoneRequestDataSpot(CoinoneRequestData):
    """Coinone Spot Feed for market data."""

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
        self.exchange_name = kwargs.get("exchange_name", "COINONE___SPOT")
        self.asset_type = kwargs.get("asset_type", "SPOT")

    def _parse_symbol(self, symbol):
        """Parse symbol into quote and target currency.
        Coinone uses format like KRW-BTC.
        """
        if "-" in symbol:
            parts = symbol.split("-")
            if len(parts) == 2:
                return parts[0], parts[1]
        # Default to KRW as quote currency
        return "KRW", symbol

    def _get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data parameters.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_tick"
        quote_currency, target_currency = self._parse_symbol(symbol)
        path = f"GET /public/v2/ticker_new/{quote_currency}/{target_currency}"
        params = None
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "normalize_function": self._get_tick_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_tick_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        symbol_name = extra_data.get("symbol_name", "")
        asset_type = extra_data.get("asset_type", "SPOT")

        # Coinone API returns {result: "success", tickers: [{...}]}
        # We need to extract the ticker data from the nested structure
        ticker_data = None
        if isinstance(input_data, dict):
            if "tickers" in input_data and isinstance(input_data["tickers"], list):
                # Get the first ticker from the tickers array
                if input_data["tickers"]:
                    ticker_data = input_data["tickers"][0]
            else:
                ticker_data = input_data
        elif isinstance(input_data, list) and input_data:
            ticker_data = input_data[0]

        if ticker_data:
            data = [CoinoneRequestTickerData(ticker_data, symbol_name, asset_type, True)]
        else:
            data = []
        return data, True

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get symbol ticker.

        Returns:
            RequestData: Response from the API
        """
        path, params, extra_data = self._get_tick(symbol, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book depth parameters.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_depth"
        quote_currency, target_currency = self._parse_symbol(symbol)
        path = f"GET /public/v2/orderbook/{quote_currency}/{target_currency}"
        params = {"size": count}
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_depth_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        depth = input_data
        return [depth], depth is not None

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get order book.

        Returns:
            RequestData: Response from the API
        """
        path, params, extra_data = self._get_depth(symbol, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)

    def _get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline/candlestick data parameters.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_kline"
        quote_currency, target_currency = self._parse_symbol(symbol)
        path = f"GET /public/v2/chart/{quote_currency}/{target_currency}"
        params = {
            "interval": self._params.get_period(period),
            "limit": count,
        }
        extra_data = extra_data or {}
        extra_data.update({
            "request_type": request_type,
            "symbol_name": symbol,
            "normalize_function": self._get_kline_normalize_function,
        })
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        if not input_data:
            return [], False
        klines = input_data
        return [klines], klines is not None

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data.

        Returns:
            RequestData: Response from the API
        """
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params, extra_data=extra_data)
