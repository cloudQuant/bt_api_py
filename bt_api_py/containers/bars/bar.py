# """
# K线类
# 用于确定k线数据的属性和方法
# Bar的数据推送和请求频率不是特别高，传入的数据直接使用json格式
# """

from typing import Any

from bt_api_py.containers.auto_init_mixin import AutoInitMixin


class BarData(AutoInitMixin):
    def __init__(self, bar_info: Any, has_been_json_encoded: bool = False) -> None:
        self.event = "BarEvent"
        self.bar_info = bar_info
        self.has_been_json_encoded = has_been_json_encoded

    def init_data(self) -> "BarData":
        raise NotImplementedError

    def get_event(self) -> str:
        return self.event

    def get_exchange_name(self) -> str:
        raise NotImplementedError

    def get_symbol_name(self) -> str:
        raise NotImplementedError

    def get_asset_type(self) -> str:
        raise NotImplementedError

    def get_server_time(self) -> float | int | None:
        raise NotImplementedError

    def get_local_update_time(self) -> float | int | None:
        raise NotImplementedError

    def get_open_time(self) -> float | int:
        raise NotImplementedError

    def get_open_price(self) -> float | int:
        raise NotImplementedError

    def get_high_price(self) -> float | int:
        raise NotImplementedError

    def get_low_price(self) -> float | int:
        raise NotImplementedError

    def get_close_price(self) -> float | int:
        raise NotImplementedError

    def get_volume(self) -> float | int:
        raise NotImplementedError

    def get_amount(self) -> float | int:
        raise NotImplementedError

    def get_close_time(self) -> float | int:
        raise NotImplementedError

    def get_quote_asset_volume(self) -> float | int:
        raise NotImplementedError

    def get_base_asset_volume(self) -> float | int:
        raise NotImplementedError

    def get_num_trades(self) -> int:
        raise NotImplementedError

    def get_taker_buy_base_asset_volume(self) -> float | int:
        raise NotImplementedError

    def get_taker_buy_quote_asset_volume(self) -> float | int:
        raise NotImplementedError

    def get_bar_status(self) -> bool | int:
        raise NotImplementedError

    def get_all_data(self) -> Any:
        raise NotImplementedError

    def __str__(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        raise NotImplementedError
