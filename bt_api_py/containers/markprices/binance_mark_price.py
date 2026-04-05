"""标记价格类，用于确定标记价格的属性和方法."""

from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py._compat import Self
from bt_api_py.containers.markprices.mark_price import MarkPriceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class BinanceMarkPrice(MarkPriceData):
    def __init__(
        self,
        mark_price_info: Any,
        symbol_name: Any,
        asset_type: Any,
        has_been_json_encoded: Any = False,
    ) -> None:
        super().__init__(mark_price_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.mark_price_data = mark_price_info if has_been_json_encoded else None
        self.server_time = None
        self.mark_price_symbol_name = None
        self.mark_price = None
        self.index_price = None
        self.settlement_price = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> Self:
        raise NotImplementedError

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "server_time": self.server_time,
                "mark_price": self.mark_price,
                "index_price": self.index_price,
                "settlement_price": self.settlement_price,
                "mark_price_symbol_name": self.mark_price_symbol_name,
            }
        assert self.all_data is not None
        return self.all_data

    def __str__(self) -> str:
        self.init_data()
        return str(json.dumps(self.get_all_data()))

    def __repr__(self) -> str:
        return self.__str__()

    def get_exchange_name(self) -> str:
        return str(self.exchange_name)

    def get_server_time(self) -> float:
        st = self.server_time
        return float(st) if st is not None else 0.0  # type: ignore[arg-type]

    def get_local_update_time(self) -> float:
        return float(self.local_update_time)

    def get_symbol_name(self) -> str:
        return str(self.symbol_name)

    def get_mark_price_symbol_name(self) -> str:
        return str(self.mark_price_symbol_name) if self.mark_price_symbol_name else ""

    def get_asset_type(self) -> str:
        return str(self.asset_type)

    def get_mark_price(self) -> float | None:
        return self.mark_price

    def get_index_price(self) -> float | None:
        """Get index_price from binance api
        :return: float, index price.
        """
        return self.index_price

    def get_settlement_price(self) -> float | None:
        """Get settlement_price from binance api
        :return: float, settlement price.
        """
        return self.settlement_price


class BinanceRequestMarkPriceData(BinanceMarkPrice):
    """保存标记价格信息."""

    def init_data(self) -> Self:
        if not self.has_been_json_encoded:
            self.mark_price_data = json.loads(self.mark_price_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        assert self.mark_price_data is not None
        self.server_time = from_dict_get_float(self.mark_price_data, "time")
        self.mark_price_symbol_name = from_dict_get_string(self.mark_price_data, "symbol")
        self.mark_price = from_dict_get_float(self.mark_price_data, "markPrice")
        self.index_price = from_dict_get_float(self.mark_price_data, "indexPrice")
        self.settlement_price = from_dict_get_float(self.mark_price_data, "estimatedSettlePrice")
        self.has_been_init_data = True
        return self


class BinanceWssMarkPriceData(BinanceMarkPrice):
    """保存标记价格信息."""

    def init_data(self) -> Self:
        if not self.has_been_json_encoded:
            self.mark_price_data = json.loads(self.mark_price_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        assert self.mark_price_data is not None
        self.server_time = from_dict_get_float(self.mark_price_data, "E")
        self.mark_price_symbol_name = from_dict_get_string(self.mark_price_data, "s")
        self.mark_price = from_dict_get_float(self.mark_price_data, "p")
        self.index_price = from_dict_get_float(self.mark_price_data, "i")
        self.settlement_price = from_dict_get_float(self.mark_price_data, "P")
        self.has_been_init_data = True
        return self
