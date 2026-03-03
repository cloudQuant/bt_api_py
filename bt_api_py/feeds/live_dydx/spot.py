from bt_api_py.containers.exchanges.dydx_exchange_data import DydxExchangeDataSwap
from bt_api_py.containers.tickers.dydx_ticker import DydxRequestTickerData
from bt_api_py.containers.orders.dydx_order import DydxOrderData
from bt_api_py.containers.balances.dydx_balance import DydxBalanceData
from bt_api_py.feeds.live_dydx.request_base import DydxRequestData
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class DydxRequestDataSpot(DydxRequestData):
    """dYdX Spot Request Data class"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "swap")
        self.logger_name = kwargs.get("logger_name", "dydx_spot_feed.log")
        self._params = DydxExchangeDataSwap()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

    def get_ticker_spot(self, symbol, extra_data=None, **kwargs):
        """Get spot ticker information"""
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
    def _get_ticker_spot_normalize_function(input_data, extra_data):
        if extra_data is None:
            pass
        status = input_data.get("code", 0) == 0
        if "markets" in input_data:
            markets = input_data["markets"]
            symbol = extra_data.get("symbol_name", "")
            if symbol in markets:
                data = markets[symbol]
                timestamp = float(data.get("snapshotAt", 0))
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

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker information"""
        path, params, extra_data = self.get_ticker_spot(symbol, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    def get_balance_spot(self, address, subaccount_number, extra_data=None, **kwargs):
        """Get spot balance information"""
        request_type = "get_subaccount"
        path = self._params.get_rest_path(request_type)
        path = path.replace("<address>", address).replace("<subaccount_number>", str(subaccount_number))
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
    def _get_balance_spot_normalize_function(input_data, extra_data):
        if extra_data is None:
            pass
        status = True
        subaccount = input_data.get("subaccount", {})
        balance_info = []

        # Extract balance information - check if subaccount has data
        if subaccount:
            balance_info.append({
                "symbol": "USD",
                "equity": float(subaccount.get("equity", 0)),
                "free_collateral": float(subaccount.get("freeCollateral", 0)),
                "available_margin": float(subaccount.get("availableMargin", 0)),
                "position_margin": float(subaccount.get("positionMargin", 0)),
                "margin_balance": float(subaccount.get("marginBalance", 0)),
            })

        data = balance_info
        return data, status

    def get_balance(self, address, subaccount_number, extra_data=None, **kwargs):
        """Get balance information"""
        path, params, extra_data = self.get_balance_spot(address, subaccount_number, extra_data, **kwargs)
        data = self.request(path, params=params, extra_data=extra_data)
        return data

    # ==================== Kline Methods ====================

    def get_kline_spot(self, symbol, period, extra_data=None, **kwargs):
        """Get kline/candlestick data"""
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
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline/candlestick data"""
        status = True
        candles = input_data.get("candles", [])
        kline_info = []

        for candle in candles:
            kline_info.append({
                "timestamp": candle.get("startedAt", ""),
                "open": float(candle.get("open", 0)),
                "high": float(candle.get("high", 0)),
                "low": float(candle.get("low", 0)),
                "close": float(candle.get("close", 0)),
                "volume": float(candle.get("volume", 0)),
            })

        return kline_info, status

    # ==================== OrderBook Methods ====================

    @staticmethod
    def _get_orderbook_normalize_function(input_data, extra_data):
        """Normalize orderbook data"""
        status = True
        if extra_data is None:
            extra_data = {}

        orderbook_info = {
            "symbol": extra_data.get("symbol_name", ""),
            "bids": input_data.get("bids", []),
            "asks": input_data.get("asks", []),
        }
        return [orderbook_info], status

    # ==================== Exchange Info Methods ====================

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange trading rules and symbol information"""
        request_type = "get_exchange_info"
        path = self._params.get_rest_path(request_type)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": DydxRequestDataSpot._get_exchange_info_normalize_function,
            },
        )
        if kwargs is not None:
            extra_data.update(kwargs)
        return path, {}, extra_data

    @staticmethod
    def _get_exchange_info_normalize_function(input_data, extra_data):
        """Normalize exchange info response"""
        status = True
        markets = input_data.get("markets", {})
        exchange_info = []

        for symbol, market_data in markets.items():
            exchange_info.append({
                "symbol": symbol,
                "status": market_data.get("status", "ACTIVE"),
            })

        return exchange_info, status