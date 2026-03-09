import json
import time

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class UpbitTickerData(TickerData):
    """保存 Upbit ticker 信息."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False) -> None:
        # Set raw_data first
        if isinstance(ticker_info, str):
            self.raw_data = ticker_info
        else:
            self.raw_data = json.dumps(ticker_info)

        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "UPBIT"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.server_time = None
        self.bid_price = None
        self.ask_price = None
        self.bid_volume = None
        self.ask_volume = None
        self.last_price = None
        self.last_volume = None
        self.high_price = None
        self.low_price = None
        self.open_price = None
        self.prev_close_price = None
        self.change = None
        self.change_rate = None
        self.timestamp = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self) -> "Self":
        """初始化 ticker 数据."""
        try:
            if not self.has_been_json_encoded:
                self.ticker_data = json.loads(self.raw_data)

            # 基础信息
            self.ticker_symbol_name = from_dict_get_string(self.ticker_data, "market")
            self.server_time = from_dict_get_float(self.ticker_data, "timestamp") or time.time()

            # 价格信息
            self.last_price = from_dict_get_float(self.ticker_data, "trade_price")
            self.open_price = from_dict_get_float(self.ticker_data, "opening_price")
            self.high_price = from_dict_get_float(self.ticker_data, "high_price")
            self.low_price = from_dict_get_float(self.ticker_data, "low_price")
            self.prev_close_price = from_dict_get_float(self.ticker_data, "prev_closing_price")

            # 买卖价格和数量
            self.bid_price = from_dict_get_float(self.ticker_data, "bid_price")
            self.ask_price = from_dict_get_float(self.ticker_data, "ask_price")
            self.bid_volume = from_dict_get_float(self.ticker_data, "bid_size")
            self.ask_volume = from_dict_get_float(self.ticker_data, "ask_size")

            # 成交量
            self.last_volume = from_dict_get_float(self.ticker_data, "trade_volume")

            # 涨跌幅
            self.change = from_dict_get_string(self.ticker_data, "change")  # "RISE", "FALL", "EVEN"
            self.change_rate = from_dict_get_float(self.ticker_data, "signed_change_rate")

            # 时间戳
            if "trade_date_utc" in self.ticker_data and "trade_time_utc" in self.ticker_data:
                # Combine date and time
                date_str = self.ticker_data["trade_date_utc"]
                time_str = self.ticker_data["trade_time_utc"]
                time_str = f"{date_str} {time_str}"
                try:
                    import datetime

                    self.timestamp = datetime.datetime.strptime(
                        time_str, "%Y-%m-%d %H:%M:%S"
                    ).timestamp()
                except (ValueError, KeyError):
                    self.timestamp = time.time()

            self.has_been_init_data = True

        except Exception as e:
            print(f"Error initializing Upbit ticker data: {e}")

    def get_exchange_name(self) -> str:
        """获取交易所名称."""
        return self.exchange_name

    def get_symbol_name(self) -> str:
        """获取交易对名称."""
        return self.symbol_name

    def get_last_price(self) -> float | None:
        """获取最新价格."""
        if not self.has_been_init_data:
            self.init_data()
        return self.last_price

    def get_all_data(self) -> dict[str, Any]:
        """获取所有 ticker 数据."""
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "ticker_symbol_name": self.ticker_symbol_name,
                "server_time": self.server_time,
                "last_price": self.last_price,
                "open_price": self.open_price,
                "high_price": self.high_price,
                "low_price": self.low_price,
                "prev_close_price": self.prev_close_price,
                "bid_price": self.bid_price,
                "ask_price": self.ask_price,
                "bid_volume": self.bid_volume,
                "ask_volume": self.ask_volume,
                "last_volume": self.last_volume,
                "change": self.change,
                "change_rate": self.change_rate,
                "timestamp": self.timestamp,
            }
        return self.all_data

    def __str__(self) -> str:
        """字符串表示."""
        if not self.has_been_init_data:
            self.init_data()

        return (
            f"UpbitTicker(symbol={self.symbol_name}, "
            f"last={self.last_price}, "
            f"bid={self.bid_price}, ask={self.ask_price}, "
            f"change={self.change_rate:.4f}%)"
        )
