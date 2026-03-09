"""
IB 持仓数据容器
对应 IB TWS API 的 Position
"""

from typing import Any
from bt_api_py.containers.positions.position import PositionData


class IbPositionData(PositionData):
    """IB 持仓数据"""

    def __init__(
        self,
        position_info: Any,
        symbol_name: Any = None,
        asset_type: Any = "STK",
        has_been_json_encoded: Any = False,
    ) -> None:
        super().__init__(position_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "IB"
        self._initialized = False
        self.account = None
        self.contract_symbol = None
        self.sec_type = None
        self.position_val = None
        self.avg_cost = None
        self.market_price_val = None
        self.market_value = None
        self.unrealized_pnl_val = None
        self.realized_pnl_val = None
        self.currency = None

    def init_data(self) -> None:
        if self._initialized:
            return self
        info = self.position_info
        if isinstance(info, dict):
            self.account = info.get("account", "")
            self.contract_symbol = info.get("symbol", self.symbol_name)
            self.sec_type = info.get("secType", self.asset_type)
            self.position_val = float(info.get("position", 0))
            self.avg_cost = float(info.get("avgCost", 0))
            self.market_price_val = float(info.get("marketPrice", 0))
            self.market_value = float(info.get("marketValue", 0))
            self.unrealized_pnl_val = float(info.get("unrealizedPNL", 0))
            self.realized_pnl_val = float(info.get("realizedPNL", 0))
            self.currency = info.get("currency", "USD")
        self._initialized = True
        return self

    def get_exchange_name(self) -> None:
        return self.exchange_name

    def get_asset_type(self) -> None:
        return self.sec_type or self.asset_type

    def get_symbol_name(self) -> None:
        return self.contract_symbol or self.symbol_name

    def get_position_volume(self) -> None:
        return self.position_val or 0

    def get_avg_price(self) -> None:
        return self.avg_cost or 0.0

    def get_mark_price(self) -> None:
        return self.market_price_val

    def get_liquidation_price(self) -> None:
        return None

    def get_initial_margin(self) -> None:
        return 0.0

    def get_maintain_margin(self) -> None:
        return 0.0

    def get_position_unrealized_pnl(self) -> None:
        return self.unrealized_pnl_val or 0.0

    def get_position_funding_value(self) -> None:
        return 0.0

    def get_all_data(self) -> None:
        return {
            "exchange_name": self.exchange_name,
            "account": self.account,
            "symbol": self.contract_symbol,
            "sec_type": self.sec_type,
            "position": self.position_val,
            "avg_cost": self.avg_cost,
            "market_price": self.market_price_val,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl_val,
            "realized_pnl": self.realized_pnl_val,
            "currency": self.currency,
        }
