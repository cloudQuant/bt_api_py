import json
import time
from typing import Any, Self

from bt_api_py.containers.markprices.mark_price import MarkPriceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class OkxMarkPriceData(MarkPriceData):
    """保存标记价格信息"""

    def __init__(
        self,
        mark_price_info: Any,
        symbol_name: Any,
        asset_type: Any,
        has_been_json_encoded: Any = False,
    ) -> None:
        super().__init__(mark_price_info, has_been_json_encoded)
        self.exchange_name = "OKX"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.mark_price_data: dict[str, Any] | None = (
            mark_price_info if has_been_json_encoded else None
        )
        self.all_data: dict[str, Any] | None = None
        self.mark_price: float | None = None
        self.server_time: float | None = None
        self.mark_price_symbol_name: str | None = None
        self.has_been_init_data = False

    def init_data(self) -> Self:
        if not self.has_been_json_encoded:
            raw = self.mark_price_info
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            self.mark_price_info = parsed
            self.mark_price_data = parsed.get("data", [{}])[0] if parsed else None
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        info = self.mark_price_info or {}
        if "arg" in info:
            self.mark_price_symbol_name = from_dict_get_string(info["arg"], "instId")
        data = self.mark_price_data or {}
        self.server_time = from_dict_get_float(data, "ts")
        self.mark_price = (
            from_dict_get_float(data, "markPx")
            if "markPx" in data
            else from_dict_get_float(data, "idxPx")
        )
        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "server_time": self.server_time,
                "mark_price": self.mark_price,
                "mark_price_symbol_name": self.mark_price_symbol_name,
            }
        return self.all_data or {}

    def __str__(self) -> str:
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()

    def get_exchange_name(self) -> str:
        return str(self.exchange_name)

    def get_server_time(self) -> float:
        return self.server_time if self.server_time is not None else 0.0

    def get_local_update_time(self) -> float:
        return self.local_update_time

    def get_symbol_name(self) -> str:
        return str(self.symbol_name)

    def get_mark_price_symbol_name(self) -> str | None:
        return self.mark_price_symbol_name

    def get_asset_type(self) -> str:
        return str(self.asset_type)

    def get_mark_price(self) -> float | None:
        data = self.mark_price_data or {}
        mark_price = data.get("markPx", None)
        if mark_price is None:
            mark_price = data.get("idxPx", None)
        return float(mark_price) if mark_price is not None else None
