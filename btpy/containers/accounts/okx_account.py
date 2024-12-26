import time
import json
from btpy.containers.accounts.account import AccountData
from btpy.containers.balances.okx_balance import OkxBalanceData
from btpy.functions.utils import from_dict_get_float


class OkxAccountData(AccountData):
    def __init__(self, account_info, symbol_name, asset_type, has_been_json_encoded=False):
        super(OkxAccountData, self).__init__(account_info, has_been_json_encoded)
        self.exchange_name = "OKX"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type
        self.account_data = account_info if has_been_json_encoded else None
        self.balances = None
        self.total_wallet_balance = None
        self.total_unrealized_profit = None
        self.total_open_order_initial_margin = None
        self.total_maintain_margin = None
        self.total_margin = None
        self.total_used_margin = None
        self.server_time = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.account_data, "uTime")
        self.total_margin = from_dict_get_float(self.account_data, "totalEq")
        self.total_used_margin = from_dict_get_float(self.account_data, "imr")
        self.total_maintain_margin = from_dict_get_float(self.account_data, "mmr")
        self.total_open_order_initial_margin = from_dict_get_float(self.account_data, "ordFroz")
        self.total_unrealized_profit = from_dict_get_float(self.account_data, "upl")
        self.balances = [OkxBalanceData(i, self.get_symbol_name(), self.get_asset_type(), True)
                         for i in self.account_data["details"]]
        self.total_wallet_balance = from_dict_get_float(self.account_data, 'totalEq')
        self.has_been_init_data = True
        return self

    def get_exchange_name(self):
        """# 交易所名称"""
        return self.exchange_name

    def get_symbol_name(self):
        """# 货币名称"""
        return self.symbol_name

    def get_asset_type(self):
        """# 资产类型"""
        return self.asset_type

    def get_server_time(self):
        """# 服务器时间戳"""
        # print("self.account_data", self.account_data)
        return self.server_time

    def get_local_update_time(self):
        """# 本地时间戳"""
        return self.local_update_time

    def get_account_id(self):
        """# 账户id"""
        return None

    def get_account_type(self):
        """# 账户类型"""
        return None

    def get_can_deposit(self):
        """# 是否可以存钱"""
        return None

    def get_can_trade(self):
        """# 是否可以交易"""
        return None

    def get_can_withdraw(self):
        """# 是否可以取钱"""
        return None

    def get_fee_tier(self):
        """# 资金费率等级"""
        return None

    def get_max_withdraw_amount(self):
        """# 最大可取资金"""
        return None

    def get_total_margin(self):
        """# 总的保证金"""
        return self.total_margin

    def get_total_used_margin(self):
        """# 总的使用的保证金"""
        return self.total_used_margin

    def get_total_maintain_margin(self):
        """# 总的维持资金"""
        return self.total_maintain_margin

    def get_total_available_margin(self):
        """# 总的可用保证金"""
        return self.get_total_margin() - self.get_total_used_margin()

    def get_total_open_order_initial_margin(self):
        """# 总的开仓订单初始保证金"""
        return self.total_open_order_initial_margin

    def get_total_position_initial_margin(self):
        """# 总的持仓初始化保证金"""
        return None

    def get_total_unrealized_profit(self):
        """# 总的未实现利润 """
        return self.total_unrealized_profit

    def get_total_wallet_balance(self):
        """# 总的钱包余额, 以美金计价"""
        return self.total_wallet_balance

    def get_balances(self):
        """# 资产,账户余额，可用，冻结"""
        return self.balances

    def get_positions(self):
        """# 获取持仓数据"""
        return None

    def get_spot_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_spot_taker_commission_rate(self):
        # taker佣金费率
        return None

    def get_future_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_future_taker_commission_rate(self):
        # taker佣金费率
        return None

    def get_option_maker_commission_rate(self):
        # maker佣金费率
        return None

    def get_option_taker_commission_rate(self):
        # taker佣金费率
        return None

    def get_all_data(self):
        if not self.has_been_init_data:
            self.init_data()
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "server_time": self.server_time,
                "local_update_time": self.local_update_time,
                "total_margin": self.total_margin,
                "total_used_margin": self.total_used_margin,
                "total_maintain_margin": self.total_maintain_margin,
                "total_open_order_initial_margin": self.total_open_order_initial_margin,
                "total_unrealized_profit": self.total_unrealized_profit,
                "balances": [OkxBalanceData(i, self.get_symbol_name(), self.get_asset_type(), True)
                             for i in self.account_data["details"]],
                "total_wallet_balance": self.total_wallet_balance

            }
        return self.all_data

    def __str__(self):
        self.init_data()
        self.all_data = self.get_all_data()
        return str(self.all_data)

    def __repr__(self):
        return self.__str__()
