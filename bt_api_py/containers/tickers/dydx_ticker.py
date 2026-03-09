import json
import time

from bt_api_py.containers.tickers.ticker import TickerData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class DydxTickerData(TickerData):
    """保存 dYdX ticker 信息."""

    def __init__(self, ticker_info, symbol_name, asset_type, has_been_json_encoded=False) -> None:
        super().__init__(ticker_info, has_been_json_encoded)
        self.exchange_name = "DYDX"  # 交易所名称
        self.local_update_time = time.time()
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.has_been_init_data = False  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type  # ticker的类型
        self.ticker_data = ticker_info if has_been_json_encoded else None
        self.ticker_symbol_name = None
        self.server_time = None
        self.last_price = None
        self.last_volume = None
        self.high_24h = None
        self.low_24h = None
        self.volume_24h = None
        self.volume_24h_usd = None
        self.funding_rate = None
        self.next_funding_rate = None
        self.next_funding_at = None
        self.mark_price = None
        self.oracle_price = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self) -> "Self":
        raise NotImplementedError

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "ticker_symbol_name": self.ticker_symbol_name,
                "server_time": self.server_time,
                "last_price": self.last_price,
                "last_volume": self.last_volume,
                "high_24h": self.high_24h,
                "low_24h": self.low_24h,
                "volume_24h": self.volume_24h,
                "volume_24h_usd": self.volume_24h_usd,
                "funding_rate": self.funding_rate,
                "next_funding_rate": self.next_funding_rate,
                "next_funding_at": self.next_funding_at,
                "mark_price": self.mark_price,
                "oracle_price": self.oracle_price,
            }
        return self.all_data

    def __str__(self) -> str:
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()

    def get_exchange_name(self) -> str:
        return self.exchange_name

    def get_local_update_time(self) -> float:
        return self.local_update_time

    def get_symbol_name(self) -> str:
        return self.symbol_name

    def get_ticker_symbol_name(self) -> str | None:
        return self.ticker_symbol_name

    def get_asset_type(self) -> str:
        return self.asset_type

    def get_server_time(self) -> float | None:
        return self.server_time

    def get_last_price(self) -> float | None:
        return self.last_price

    def get_last_volume(self) -> float | None:
        return self.last_volume

    def get_high_24h(self) -> float | None:
        return self.high_24h

    def get_low_24h(self) -> float | None:
        return self.low_24h

    def get_volume_24h(self) -> float | None:
        return self.volume_24h

    def get_volume_24h_usd(self):
        return self.volume_24h_usd

    def get_funding_rate(self):
        return self.funding_rate

    def get_next_funding_rate(self):
        return self.next_funding_rate

    def get_next_funding_at(self):
        return self.next_funding_at

    def get_mark_price(self):
        return self.mark_price

    def get_oracle_price(self):
        return self.oracle_price


class DydxWssTickerData(DydxTickerData):
    """保存 WebSocket ticker 信息."""

    def init_data(self) -> "Self":
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # 处理单个 ticker 数据
        if "markets" in self.ticker_data:
            # markets 响应格式
            markets = self.ticker_data["markets"]
            if self.symbol_name in markets:
                market_data = markets[self.symbol_name]
                self.last_price = from_dict_get_float(market_data, "oraclePrice")
                self.volume_24h = from_dict_get_float(market_data, "volume24H")
                self.funding_rate = from_dict_get_float(market_data, "currentFundingRate")
                self.next_funding_at = from_dict_get_string(market_data, "nextFundingRateAt")

        self.has_been_init_data = True
        return self


class DydxRequestTickerData(DydxTickerData):
    """保存 REST API ticker 信息."""

    def init_data(self) -> "Self":
        if not self.has_been_json_encoded:
            self.ticker_data = json.loads(self.ticker_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # 处理 perpetualMarkets 响应
        if "markets" in self.ticker_data:
            markets = self.ticker_data["markets"]
            if self.symbol_name in markets:
                market_data = markets[self.symbol_name]
                self.ticker_symbol_name = self.symbol_name
                self.server_time = from_dict_get_string(market_data, "snapshotAt")
                self.last_price = from_dict_get_float(market_data, "oraclePrice")
                self.volume_24h = from_dict_get_float(market_data, "volume24H")
                self.volume_24h_usd = from_dict_get_float(market_data, "volumeNotional24H")
                self.high_24h = from_dict_get_float(market_data, "high24H")
                self.low_24h = from_dict_get_float(market_data, "low24H")
                self.funding_rate = from_dict_get_float(market_data, "currentFundingRate")
                self.next_funding_at = from_dict_get_string(market_data, "nextFundingRateAt")
                self.mark_price = from_dict_get_float(market_data, "markPrice")
                self.oracle_price = from_dict_get_float(market_data, "oraclePrice")

        self.has_been_init_data = True
        return self
