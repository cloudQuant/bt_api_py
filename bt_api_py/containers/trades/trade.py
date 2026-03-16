"""交易类，用于确定交易信息的属性和方法，
# 参考接口：https://binance-docs.github.io/apidocs/futures/cn/#060a012f0b
"""

from __future__ import annotations

from typing import Any

from bt_api_py.containers.auto_init_mixin import AutoInitMixin


class TradeData(AutoInitMixin):
    """交易类，用于保存成交信息"""

    def __init__(
        self,
        trade_info: Any,
        has_been_json_encoded: bool = False,
        symbol_name: str | None = None,
        asset_type: str | None = None,
    ) -> None:
        self.event = "TradeEvent"
        self.trade_info = trade_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name: str | None = None
        self.local_update_time: float | None = None
        self.asset_type = asset_type
        self.symbol_name = symbol_name
        self.trade_data: Any = trade_info if has_been_json_encoded else None
        self.server_time: float | None = None
        self.trade_id: str | None = None
        self.trade_symbol_name: str | None = None
        self.order_id: str | None = None
        self.client_order_id: str | None = None
        self.trade_side: str | None = None
        self.trade_offset: str | None = None
        self.trade_price: float | None = None
        self.trade_volume: float | None = None
        self.trade_type: str | None = None
        self.trade_time: float | None = None
        self.trade_fee: float | None = None
        self.trade_fee_symbol: str | None = None
        self.trade_accumulate_volume: float | None = None
        self.all_data: dict[str, Any] | None = None

    def get_event(self) -> str:
        """# 事件类型"""
        return self.event

    def init_data(self) -> TradeData:
        raise NotImplementedError

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "server_time": self.server_time,
                "trade_symbol_name": self.trade_symbol_name,
                "trade_side": self.trade_side,
                "trade_price": self.trade_price,
                "trade_volume": self.trade_volume,
                "trade_type": self.trade_type,
                "trade_time": self.trade_time,
                "trade_fee_symbol": self.trade_fee_symbol,
                "trade_fee": self.trade_fee,
                "client_order_id": self.client_order_id,
                "order_id": self.order_id,
                "trade_id": self.trade_id,
            }
        return self.all_data

    def get_exchange_name(self) -> str:
        """# 交易所名称"""
        raise NotImplementedError

    def get_asset_type(self) -> str | None:
        """# 资产类型"""
        raise NotImplementedError

    def get_symbol_name(self) -> str | None:
        """# symbol名称"""
        raise NotImplementedError

    def get_server_time(self) -> float | None:
        """# 服务器时间戳"""
        raise NotImplementedError

    def get_local_update_time(self) -> float | None:
        """# 本地时间戳"""
        raise NotImplementedError

    def get_trade_id(self) -> str | None:
        """# 交易所返回唯一成交id"""
        raise NotImplementedError

    def get_trade_symbol_name(self) -> str | None:
        """# 返回成交的symbol"""
        raise NotImplementedError

    def get_order_id(self) -> str | None:
        """# 返回下单的id"""
        raise NotImplementedError

    def get_client_order_id(self) -> str | None:
        """# 返回下单的客户自定义Id"""
        raise NotImplementedError

    def get_trade_side(self) -> str | None:
        """# 返回交易的方向"""
        raise NotImplementedError

    def get_trade_offset(self) -> str | None:
        """# offset用于确定是开仓还是平仓"""
        raise NotImplementedError

    def get_trade_price(self) -> float | None:
        """# 成交价格"""
        raise NotImplementedError

    def get_trade_volume(self) -> float | None:
        """# 成交量"""
        raise NotImplementedError

    def get_trade_accumulate_volume(self) -> float | None:
        """# 累计成交量"""
        raise NotImplementedError

    def get_trade_type(self) -> str | None:
        """# 成交类型，maker还是taker"""
        raise NotImplementedError

    def get_trade_time(self) -> float | None:
        """# 成交时间"""
        raise NotImplementedError

    def get_trade_fee(self) -> float | None:
        """# 成交手续费"""
        raise NotImplementedError

    def get_trade_fee_symbol(self) -> str | None:
        """成交手续费币种"""
        raise NotImplementedError

    def __str__(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        raise NotImplementedError
