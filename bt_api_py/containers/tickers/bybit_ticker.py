import time

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float


class BybitTickerData(TickerData):
    """保存 Bybit ticker 信息"""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "BYBIT"  # 交易所名称
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type  # ticker的类型
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.server_time = None
        self.bid_price = None
        self.ask_price = None
        self.bid_volume = None
        self.ask_volume = None
        self.last_price = None
        self.last_volume = None
        self.high_price_24h = None
        self.low_price_24h = None
        self.volume_24h = None
        self.turnover_24h = None
        self.price_change_24h = None
        self.price_change_percent_24h = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """初始化 ticker 数据"""
        if self.has_been_init_data or self.ticker_data is None:
            return self

        try:
            result = self.ticker_data.get("result", {})
            list_data = result.get("list", [])

            if not list_data:
                return

            ticker = list_data[0]  # Bybit 返回列表，取第一个

            # 基本信息设置
            self.ticker_symbol_name = ticker.get("symbol")
            self.server_time = ticker.get("time", 0)

            # 价格信息
            self.last_price = from_dict_get_float(ticker, "lastPrice")
            self.bid_price = from_dict_get_float(ticker, "bid1Price")
            self.ask_price = from_dict_get_float(ticker, "ask1Price")

            # 量信息
            self.bid_volume = from_dict_get_float(ticker, "bid1Size")
            self.ask_volume = from_dict_get_float(ticker, "ask1Size")
            self.last_volume = from_dict_get_float(ticker, "volume24h")

            # 24小时统计
            self.high_price_24h = from_dict_get_float(ticker, "highPrice24h")
            self.low_price_24h = from_dict_get_float(ticker, "lowPrice24h")
            self.volume_24h = from_dict_get_float(ticker, "volume24h")
            self.turnover_24h = from_dict_get_float(ticker, "turnover24h")

            # 价格变化
            prev_price_24h = from_dict_get_float(ticker, "prevPrice24h")
            if prev_price_24h and self.last_price:
                self.price_change_24h = self.last_price - prev_price_24h
                self.price_change_percent_24h = (self.price_change_24h / prev_price_24h) * 100

            self.has_been_init_data = True

        except Exception as e:
            print(f"Error parsing Bybit ticker data: {e}")
            self.has_been_init_data = False
        return self

    def get_all_data(self):
        """获取所有 ticker 数据"""
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "ticker_symbol_name": self.ticker_symbol_name,
                "server_time": self.server_time,
                "last_price": self.last_price,
                "bid_price": self.bid_price,
                "ask_price": self.ask_price,
                "bid_volume": self.bid_volume,
                "ask_volume": self.ask_volume,
                "last_volume": self.last_volume,
                "high_price_24h": self.high_price_24h,
                "low_price_24h": self.low_price_24h,
                "volume_24h": self.volume_24h,
                "turnover_24h": self.turnover_24h,
                "price_change_24h": self.price_change_24h,
                "price_change_percent_24h": self.price_change_percent_24h,
            }
        return self.all_data

    def __str__(self):
        """返回 ticker 的字符串表示"""
        self.init_data()
        return (
            f"BybitTicker(symbol={self.symbol_name}, "
            f"last={self.last_price}, "
            f"bid={self.bid_price}, "
            f"ask={self.ask_price}, "
            f"volume={self.volume_24h})"
        )


class BybitSpotTickerData(BybitTickerData):
    """Bybit 现货 ticker 数据"""

    def __init__(self, ticker_info, symbol_name, has_been_json_encoded=False):
        super().__init__(ticker_info, symbol_name, "spot", has_been_json_encoded)


class BybitSwapTickerData(BybitTickerData):
    """Bybit 期货/swap ticker 数据"""

    def __init__(self, ticker_info, symbol_name, has_been_json_encoded=False):
        super().__init__(ticker_info, symbol_name, "swap", has_been_json_encoded)
