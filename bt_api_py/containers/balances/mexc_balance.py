import json
import time

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class MexcBalanceData(BalanceData):
    """保存余额信息"""

    def __init__(self, balance_info, symbol_name=None, asset_type=None, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "MEXC"  # 交易所名称
        self.local_update_time = time.time()  # 本地时间戳
        self.symbol_name = symbol_name
        self.asset_type = asset_type  # 余额的类型
        self.balance_data = balance_info if has_been_json_encoded else None
        self.asset = None
        self.free = None  # 可用余额
        self.locked = None  # 冻结余额
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            if isinstance(self.balance_info, str):
                self.balance_data = json.loads(self.balance_info)
            else:
                self.balance_data = self.balance_info

        if self.balance_data:
            self.asset = from_dict_get_string(self.balance_data, "asset")
            self.free = from_dict_get_float(self.balance_data, "free", 0.0)
            self.locked = from_dict_get_float(self.balance_data, "locked", 0.0)

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "asset": self.asset,
                "free": self.free,
                "locked": self.locked,
                "total": self.free + self.locked,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_asset(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.asset

    def get_free(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.free

    def get_locked(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.locked

    def get_total(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.free + self.locked

    def get_available(self):
        """获取可用余额"""
        if not self.has_been_init_data:
            self.init_data()
        return self.free

    def get_frozen(self):
        """获取冻结余额"""
        if not self.has_been_init_data:
            self.init_data()
        return self.locked

    def is_zero(self):
        """检查余额是否为0"""
        return self.free == 0.0 and self.locked == 0.0

    def has_available(self):
        """检查是否有可用余额"""
        return self.free > 0

    def has_frozen(self):
        """检查是否有冻结余额"""
        return self.locked > 0


class MexcRequestBalanceData(MexcBalanceData):
    """保存请求返回的余额信息"""

    def __init__(self, balance_info, symbol_name=None, asset_type=None, has_been_json_encoded=False):
        super().__init__(balance_info, symbol_name, asset_type, has_been_json_encoded)

    def init_data(self):
        if not self.has_been_json_encoded:
            if isinstance(self.balance_info, str):
                self.balance_data = json.loads(self.balance_info)
            else:
                self.balance_data = self.balance_info

        if self.balance_data:
            self.asset = from_dict_get_string(self.balance_data, "asset")
            self.free = from_dict_get_float(self.balance_data, "free", 0.0)
            self.locked = from_dict_get_float(self.balance_data, "locked", 0.0)

        self.has_been_init_data = True
        return self


class MexcAccountData:
    """保存账户信息"""

    def __init__(self, account_info, asset_type="SPOT"):
        self.exchange_name = "MEXC"
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.account_data = account_info
        self.maker_commission = 0.0
        self.taker_commission = 0.0
        self.buyer_commission = 0.0
        self.seller_commission = 0.0
        self.can_trade = False
        self.can_withdraw = False
        self.can_deposit = False
        self.balances = []
        self.account_type = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_init_data and self.account_data:
            self.maker_commission = self.account_data.get("makerCommission", 0.0)
            self.taker_commission = self.account_data.get("takerCommission", 0.0)
            self.buyer_commission = self.account_data.get("buyerCommission", 0.0)
            self.seller_commission = self.account_data.get("sellerCommission", 0.0)
            self.can_trade = self.account_data.get("canTrade", False)
            self.can_withdraw = self.account_data.get("canWithdraw", False)
            self.can_deposit = self.account_data.get("canDeposit", False)
            self.account_type = self.account_data.get("accountType")

            # 处理余额列表
            for balance in self.account_data.get("balances", []):
                if float(balance.get("free", 0)) > 0 or float(balance.get("locked", 0)) > 0:
                    balance_data = MexcRequestBalanceData(
                        balance,
                        asset_type=self.asset_type
                    )
                    self.balances.append(balance_data)

            self.has_been_init_data = True
        return self

    def get_all_data(self):
        self.init_data()
        return {
            "exchange_name": self.exchange_name,
            "asset_type": self.asset_type,
            "local_update_time": self.local_update_time,
            "maker_commission": self.maker_commission,
            "taker_commission": self.taker_commission,
            "buyer_commission": self.buyer_commission,
            "seller_commission": self.seller_commission,
            "can_trade": self.can_trade,
            "can_withdraw": self.can_withdraw,
            "can_deposit": self.can_deposit,
            "account_type": self.account_type,
            "balances": [balance.get_all_data() for balance in self.balances],
        }

    def __str__(self):
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        return self.exchange_name

    def get_asset_type(self):
        return self.asset_type

    def get_local_update_time(self):
        return self.local_update_time

    def get_maker_commission(self):
        return self.maker_commission

    def get_taker_commission(self):
        return self.taker_commission

    def get_buyer_commission(self):
        return self.buyer_commission

    def get_seller_commission(self):
        return self.seller_commission

    def get_can_trade(self):
        return self.can_trade

    def get_can_withdraw(self):
        return self.can_withdraw

    def get_can_deposit(self):
        return self.can_deposit

    def get_account_type(self):
        return self.account_type

    def get_balances(self):
        """获取所有余额列表"""
        return self.balances

    def get_balance_by_asset(self, asset):
        """根据资产获取余额"""
        for balance in self.balances:
            if balance.get_asset() == asset:
                return balance
        return None

    def get_total_balance_by_asset(self, asset):
        """根据资产获取总余额"""
        balance = self.get_balance_by_asset(asset)
        return balance.get_total() if balance else 0.0

    def get_available_balance_by_asset(self, asset):
        """根据资产获取可用余额"""
        balance = self.get_balance_by_asset(asset)
        return balance.get_available() if balance else 0.0

    def get_frozen_balance_by_asset(self, asset):
        """根据资产获取冻结余额"""
        balance = self.get_balance_by_asset(asset)
        return balance.get_frozen() if balance else 0.0

    def get_usd_value(self, asset_price_map):
        """计算账户总USDT价值"""
        total_value = 0.0
        for balance in self.balances:
            asset = balance.get_asset()
            amount = balance.get_total()

            if asset == "USDT":
                total_value += amount
            elif asset in asset_price_map:
                total_value += amount * asset_price_map[asset]

        return total_value

    def has_sufficient_balance(self, asset, amount):
        """检查是否有足够余额"""
        available_balance = self.get_available_balance_by_asset(asset)
        return available_balance >= amount
