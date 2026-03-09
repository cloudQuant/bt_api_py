"""
IB 账户数据容器
对应 IB TWS API 的 AccountSummary / AccountValue
"""

from typing import Any

from bt_api_py.containers.accounts.account import AccountData


class IbAccountData(AccountData):
    """IB 账户数据"""

    def __init__(
        self,
        account_info: Any,
        symbol_name: Any = None,
        asset_type: Any = "STK",
        has_been_json_encoded: Any = False,
    ) -> None:
        super().__init__(account_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "IB"
        self._initialized = False
        self.account_id = None
        self.net_liquidation = None  # 净清算价值
        self.total_cash_value = None  # 总现金
        self.buying_power = None  # 购买力
        self.gross_position_value = None  # 总持仓价值
        self.maintenance_margin = None  # 维持保证金
        self.available_funds = None  # 可用资金
        self.unrealized_pnl = None  # 未实现盈亏
        self.realized_pnl = None  # 已实现盈亏
        self.currency = None

    def init_data(self) -> None:
        if self._initialized:
            return self
        info = self.account_info
        if isinstance(info, dict):
            self.account_id = info.get("AccountID", info.get("account", ""))
            self.net_liquidation = float(info.get("NetLiquidation", 0))
            self.total_cash_value = float(info.get("TotalCashValue", 0))
            self.buying_power = float(info.get("BuyingPower", 0))
            self.gross_position_value = float(info.get("GrossPositionValue", 0))
            self.maintenance_margin = float(info.get("MaintMarginReq", 0))
            self.available_funds = float(info.get("AvailableFunds", 0))
            self.unrealized_pnl = float(info.get("UnrealizedPnL", 0))
            self.realized_pnl = float(info.get("RealizedPnL", 0))
            self.currency = info.get("Currency", "USD")
        self._initialized = True
        return self

    def get_exchange_name(self) -> None:
        return self.exchange_name

    def get_asset_type(self) -> None:
        return self.asset_type

    def get_account_type(self) -> None:
        return self.currency or "USD"

    def get_server_time(self) -> None:
        return None

    def get_total_wallet_balance(self) -> None:
        return self.net_liquidation

    def get_margin(self) -> None:
        return self.net_liquidation or 0.0

    def get_available_margin(self) -> None:
        return self.available_funds or 0.0

    def get_unrealized_profit(self) -> None:
        return self.unrealized_pnl or 0.0

    def get_balances(self) -> None:
        return [self]

    def get_positions(self) -> None:
        return []

    def get_all_data(self) -> None:
        return {
            "exchange_name": self.exchange_name,
            "account_id": self.account_id,
            "net_liquidation": self.net_liquidation,
            "total_cash_value": self.total_cash_value,
            "buying_power": self.buying_power,
            "available_funds": self.available_funds,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "currency": self.currency,
        }
