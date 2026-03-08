import json
import time

from bt_api_py.containers.balances.balance import BalanceData


class BitfinexBalanceData(BalanceData):
    """保存 Bitfinex 余额信息"""

    def __init__(self, balance_info, asset_type, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "BITFINEX"  # 交易所名称
        self.local_update_time = time.time()  # 本地时间戳
        self.asset_type = asset_type  # 余额类型
        self.balance_data = balance_info if has_been_json_encoded else None
        self.all_data = None  # Initialize all_data
        self.wallet_type = None
        self.currency = None
        self.balance = None
        self.unsettled_interest = None
        self.balance_available = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.balance_data = json.loads(self.balance_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        if isinstance(self.balance_data, list) and len(self.balance_data) >= 5:
            # Bitfinex 余额格式:
            # [WALLET_TYPE, CURRENCY, BALANCE, UNSETTLED_INTEREST, BALANCE_AVAILABLE]
            self.wallet_type = str(self.balance_data[0]) if self.balance_data[0] is not None else ""
            self.currency = str(self.balance_data[1]) if self.balance_data[1] is not None else ""
            self.balance = float(self.balance_data[2]) if self.balance_data[2] is not None else 0.0
            self.unsettled_interest = (
                float(self.balance_data[3]) if self.balance_data[3] is not None else 0.0
            )
            self.balance_available = (
                float(self.balance_data[4]) if self.balance_data[4] is not None else 0.0
            )

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "wallet_type": self.wallet_type,
                "currency": self.currency,
                "balance": self.balance,
                "unsettled_interest": self.unsettled_interest,
                "balance_available": self.balance_available,
                "balance_used": self.balance - self.balance_available
                if self.balance is not None and self.balance_available is not None
                else 0,
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

    def get_asset_type(self):
        return self.asset_type

    def get_wallet_type(self):
        """获取钱包类型"""
        return self.wallet_type

    def get_currency(self):
        """获取货币"""
        return self.currency

    def get_balance(self):
        """获取总余额"""
        return self.balance

    def get_unsettled_interest(self):
        """获取未结利息"""
        return self.unsettled_interest

    def get_balance_available(self):
        """获取可用余额"""
        return self.balance_available

    def get_balance_used(self):
        """获取已用余额"""
        if self.balance is not None and self.balance_available is not None:
            return self.balance - self.balance_available
        return 0

    def get_balance_percentage(self):
        """获取余额使用百分比"""
        if self.balance is None or self.balance == 0:
            return 0
        return (self.get_balance_used() / self.balance) * 100

    def is_sufficient(self, amount):
        """检查余额是否足够"""
        if self.balance_available is None:
            return False
        return self.balance_available >= amount

    def get_total_value(self, price_map=None):
        """获取总价值（如果提供价格映射）

        Args:
            price_map: 价格字典，格式 {currency: price}

        Returns:
            float: 总价值
        """
        if price_map and self.currency in price_map:
            return self.balance * price_map[self.currency]
        return self.balance


class BitfinexSpotRequestBalanceData(BitfinexBalanceData):
    """保存 Bitfinex 现货余额信息"""

    pass  # 现货余额格式与基础格式相同


class BitfinexWssBalanceData(BitfinexBalanceData):
    """保存 Bitfinex WebSocket 余额信息"""

    pass  # WebSocket 余额格式与 REST API 相同
