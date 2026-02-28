"""
CTP 账户数据容器
对应 CTP 的 CThostFtdcTradingAccountField 结构体
"""

from bt_api_py.containers.accounts.account import AccountData
from bt_api_py.containers.auto_init_mixin import AutoInitMixin
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class CtpAccountData(AutoInitMixin, AccountData):
    """CTP 账户资金数据"""

    def __init__(
        self, account_info, symbol_name=None, asset_type="FUTURE", has_been_json_encoded=False
    ):
        super().__init__(account_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "CTP"
        self._initialized = False
        # CTP 账户字段
        self.broker_id = None
        self.account_id = None
        self.pre_balance = None  # 上日结存
        self.balance = None  # 动态权益
        self.available = None  # 可用资金
        self.commission = None  # 手续费
        self.frozen_margin = None  # 冻结保证金
        self.curr_margin = None  # 当前保证金
        self.close_profit = None  # 平仓盈亏
        self.position_profit = None  # 持仓盈亏
        self.withdraw = None  # 出金
        self.deposit = None  # 入金
        self.risk_degree = None  # 风险度

    def init_data(self):
        if self._initialized:
            return self
        info = self.account_info
        if isinstance(info, dict):
            self.broker_id = from_dict_get_string(info, "BrokerID")
            self.account_id = from_dict_get_string(info, "AccountID")
            self.pre_balance = from_dict_get_float(info, "PreBalance", 0.0)
            self.balance = from_dict_get_float(info, "Balance", 0.0)
            self.available = from_dict_get_float(info, "Available", 0.0)
            self.commission = from_dict_get_float(info, "Commission", 0.0)
            self.frozen_margin = from_dict_get_float(info, "FrozenMargin", 0.0)
            self.curr_margin = from_dict_get_float(info, "CurrMargin", 0.0)
            self.close_profit = from_dict_get_float(info, "CloseProfit", 0.0)
            self.position_profit = from_dict_get_float(info, "PositionProfit", 0.0)
            self.withdraw = from_dict_get_float(info, "Withdraw", 0.0)
            self.deposit = from_dict_get_float(info, "Deposit", 0.0)
            # 风险度 = 保证金 / 动态权益
            if self.balance and self.balance > 0:
                self.risk_degree = self.curr_margin / self.balance
            else:
                self.risk_degree = 0.0
        self._initialized = True
        return self

    def get_exchange_name(self):
        self._ensure_init()
        return self.exchange_name

    def get_asset_type(self):
        self._ensure_init()
        return self.asset_type

    def get_account_type(self):
        self._ensure_init()
        return self.account_id or "CNY"

    def get_server_time(self):
        return None

    def get_total_wallet_balance(self):
        return self.balance

    def get_margin(self):
        """动态权益"""
        self._ensure_init()
        return self.balance or 0.0

    def get_available_margin(self):
        """可用资金"""
        self._ensure_init()
        return self.available or 0.0

    def get_unrealized_profit(self):
        """持仓盈亏"""
        self._ensure_init()
        return self.position_profit or 0.0

    def get_balances(self):
        return [self]

    def get_positions(self):
        return []

    def get_all_data(self):
        return {
            "exchange_name": self.exchange_name,
            "asset_type": self.asset_type,
            "account_id": self.account_id,
            "pre_balance": self.pre_balance,
            "balance": self.balance,
            "available": self.available,
            "commission": self.commission,
            "frozen_margin": self.frozen_margin,
            "curr_margin": self.curr_margin,
            "close_profit": self.close_profit,
            "position_profit": self.position_profit,
            "risk_degree": self.risk_degree,
        }
