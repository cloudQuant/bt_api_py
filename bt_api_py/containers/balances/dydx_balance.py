from typing import Any
import json
import time

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class DydxBalanceData(BalanceData):
    """dYdX 余额数据类."""

    def __init__(
        self, balance_info, symbol_name, asset_type, has_been_json_encoded: bool = False
    ) -> None:
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "DYDX"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.address = None
        self.subaccount_number = None
        self.equity = None
        self.free_collateral = None
        self.open_pnl = None
        self.initial_margin_requirement = None
        self.margin_balance = None
        self.available_margin = None
        self.position_margin = None
        self.account_value = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self) -> None:
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        # 处理子账户信息响应
        if "subaccount" in self.balance_data:
            subaccount = self.balance_data["subaccount"]
            self.equity = from_dict_get_float(subaccount, "equity")
            self.free_collateral = from_dict_get_float(subaccount, "freeCollateral")
            self.open_pnl = from_dict_get_float(subaccount, "openPnl")
            self.initial_margin_requirement = from_dict_get_float(
                subaccount, "initialMarginRequirement"
            )
            self.margin_balance = from_dict_get_float(subaccount, "marginBalance")
            self.available_margin = from_dict_get_float(subaccount, "availableMargin")
            self.position_margin = from_dict_get_float(subaccount, "positionMargin")
            self.account_value = from_dict_get_float(subaccount, "accountValue")
        else:
            # 处理单个币种余额响应
            self.symbol_name = from_dict_get_string(self.balance_data, "symbol")
            self.equity = from_dict_get_float(self.balance_data, "equity")
            self.free_collateral = from_dict_get_float(self.balance_data, "freeCollateral")
            self.open_pnl = from_dict_get_float(self.balance_data, "unrealizedPnl")
            self.available_margin = from_dict_get_float(self.balance_data, "availableMargin")
            self.margin_balance = from_dict_get_float(self.balance_data, "marginBalance")

        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "address": self.address,
                "subaccount_number": self.subaccount_number,
                "equity": self.equity,
                "free_collateral": self.free_collateral,
                "open_pnl": self.open_pnl,
                "initial_margin_requirement": self.initial_margin_requirement,
                "margin_balance": self.margin_balance,
                "available_margin": self.available_margin,
                "position_margin": self.position_margin,
                "account_value": self.account_value,
            }
        return self.all_data

    def __str__(self) -> str:
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        return self.__str__()

    def get_exchange_name(self) -> str:
        """交易所名称."""
        return self.exchange_name

    def get_symbol_name(self) -> str:
        """货币名称."""
        return self.symbol_name

    def get_asset_type(self) -> str:
        """资产类型."""
        return self.asset_type

    def get_server_time(self) -> float:
        """服务器时间戳."""
        return None  # dYdX 响应中没有直接的时间戳

    def get_local_update_time(self) -> float:
        """本地时间戳."""
        return self.local_update_time

    def get_account_id(self) -> Any:
        """账户id (dYdX 使用地址)."""
        return self.address

    def get_account_type(self) -> Any:
        """账户类型."""
        return "PERPETUAL"  # dYdX 主要是永续合约

    def get_fee_tier(self) -> Any:
        """资金费率等级."""
        return None  # 需要单独查询

    def get_max_withdraw_amount(self) -> Any:
        """最大可取资金."""
        return self.free_collateral  # 可用保证金是最大可取金额

    def get_margin(self) -> Any:
        """总的保证金."""
        return self.margin_balance

    def get_used_margin(self) -> Any:
        """总的使用的保证金."""
        return self.position_margin

    def get_maintain_margin(self) -> Any:
        """总的维持资金."""
        return self.initial_margin_requirement

    def get_available_margin(self) -> Any:
        """总的可用保证金."""
        return self.available_margin

    def get_open_order_initial_margin(self) -> Any:
        """开仓订单初始保证金 (包含在 position_margin 中)."""
        return self.position_margin

    def get_position_initial_margin(self) -> Any:
        """持仓初始化保证金."""
        return self.position_margin

    def get_unrealized_profit(self) -> Any:
        """未实现利润."""
        return self.open_pnl

    def get_interest(self) -> Any:
        """获取应计利息."""
        return None  # dYdX 可能没有直接的利息字段

    def get_address(self) -> Any:
        """钱包地址."""
        return self.address

    def get_subaccount_number(self) -> Any:
        """子账户编号."""
        return self.subaccount_number

    def get_equity(self) -> Any:
        """账户权益."""
        return self.equity

    def get_free_collateral(self) -> Any:
        """可用抵押品."""
        return self.free_collateral

    def get_account_value(self) -> Any:
        """账户价值."""
        return self.account_value
