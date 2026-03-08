import time

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HitBtcRequestBalanceData(BalanceData):
    """保存HitBTC账户余额信息"""

    def __init__(self, balance_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "HITBTC"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        # Always store balance_info for init_data() to parse
        self.balance_data = balance_info
        self.currency = None
        self.available = None
        self.reserved = None
        self.timestamp = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        if self.has_been_init_data:
            return

        if self.balance_data is None:
            return

        # 提取数据
        self.currency = from_dict_get_string(self.balance_data, "currency")
        self.available = from_dict_get_float(self.balance_data, "available")
        self.reserved = from_dict_get_float(self.balance_data, "reserved")
        self.timestamp = from_dict_get_float(self.balance_data, "timestamp")

        self.has_been_init_data = True

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "currency": self.currency,
                "available": self.available,
                "reserved": self.reserved,
                "timestamp": self.timestamp,
            }
        return self.all_data

    def get_exchange_name(self):
        """Get exchange name."""
        return self.exchange_name

    def get_asset_type(self):
        """Get asset type."""
        return self.asset_type

    def get_server_time(self):
        """Get server time."""
        return self.timestamp

    def get_local_update_time(self):
        """Get local update time."""
        return self.local_update_time

    def get_currency(self):
        """Get currency."""
        return self.currency

    def get_total(self):
        """获取总余额"""
        return (self.available or 0) + (self.reserved or 0)

    def get_free(self):
        """获取可用余额"""
        return self.available or 0

    def get_used(self):
        """获取已用余额"""
        return self.reserved or 0

    def is_zero(self):
        """判断是否为零余额"""
        total = self.get_total()
        return abs(total) < 1e-8

    def __str__(self):
        return (
            f"HITBTC Balance {self.currency}: Available={self.available}, Reserved={self.reserved}"
        )

    def __repr__(self):
        return f"<HitBtcBalanceData {self.currency} {self.get_total()}>"
