"""
IB K线数据容器
对应 IB TWS API 的 BarData (reqHistoricalData / reqRealTimeBars)
"""

from bt_api_py.containers.bars.bar import BarData


class IbBarData(BarData):
    """IB K线数据"""

    def __init__(
        self,
        bar_info: Any,
        symbol_name: Any = None,
        asset_type: Any = "STK",
        has_been_json_encoded: Any = False,
    ) -> None:
        super().__init__(bar_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "IB"
        self._initialized = False
        self.date_val = None
        self.open_val = None
        self.high_val = None
        self.low_val = None
        self.close_val = None
        self.volume_val = None
        self.wap_val = None  # 加权平均价
        self.bar_count = None  # 交易笔数

    def init_data(self) -> None:
        if self._initialized:
            return self
        info = self.bar_info
        if isinstance(info, dict):
            self.date_val = info.get("date", "")
            self.open_val = float(info.get("open", 0))
            self.high_val = float(info.get("high", 0))
            self.low_val = float(info.get("low", 0))
            self.close_val = float(info.get("close", 0))
            self.volume_val = int(info.get("volume", 0))
            self.wap_val = float(info.get("wap", 0))
            self.bar_count = int(info.get("barCount", 0))
        self._initialized = True
        return self

    def get_exchange_name(self) -> None:
        return self.exchange_name

    def get_symbol_name(self) -> None:
        return self.symbol_name

    def get_asset_type(self) -> None:
        return self.asset_type

    def get_server_time(self) -> None:
        return self.date_val

    def get_open_time(self) -> None:
        return self.date_val

    def get_open_price(self) -> None:
        return self.open_val

    def get_high_price(self) -> None:
        return self.high_val

    def get_low_price(self) -> None:
        return self.low_val

    def get_close_price(self) -> None:
        return self.close_val

    def get_volume(self) -> None:
        return self.volume_val

    def get_amount(self) -> None:
        return None

    def get_close_time(self) -> None:
        return self.date_val

    def get_bar_status(self) -> None:
        return True

    def get_num_trades(self) -> None:
        return self.bar_count

    def get_wap(self) -> None:
        """加权平均价"""
        return self.wap_val

    def get_all_data(self) -> None:
        return {
            "exchange_name": self.exchange_name,
            "symbol_name": self.symbol_name,
            "date": self.date_val,
            "open": self.open_val,
            "high": self.high_val,
            "low": self.low_val,
            "close": self.close_val,
            "volume": self.volume_val,
            "wap": self.wap_val,
            "bar_count": self.bar_count,
        }
