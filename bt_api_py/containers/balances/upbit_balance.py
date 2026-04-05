from __future__ import annotations

import json
import time

from bt_api_py.containers.balances.balance import BalanceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string
from bt_api_py.logging_factory import get_logger

logger = get_logger("container")


class UpbitBalanceData(BalanceData):
    """保存 Upbit 余额信息"""

    def __init__(self, balance_info, currency, asset_type, has_been_json_encoded=False):
        super().__init__(balance_info, has_been_json_encoded)
        self.exchange_name = "UPBIT"
        self.local_update_time = time.time()
        self.currency = currency
        self.asset_type = asset_type
        self.balance_data = balance_info if has_been_json_encoded else None
        self.balance = None
        self.locked = None
        self.avg_buy_price = None
        self.avg_buy_price_modified = None
        self.unit_currency = None
        self.currency_name = None
        self.status = None
        self.all_data: dict | None = None
        self.has_been_init_data = False

    def init_data(self):
        """初始化余额数据"""
        try:
            if not self.has_been_json_encoded:
                self.balance_data = json.loads(self.raw_data)

            # 基础余额信息
            self.balance = from_dict_get_float(self.balance_data, "balance")
            self.locked = from_dict_get_float(self.balance_data, "locked")
            self.avg_buy_price = from_dict_get_float(self.balance_data, "avg_buy_price")
            self.avg_buy_price_modified = from_dict_get_string(
                self.balance_data, "avg_buy_price_modified"
            )

            # 币种信息
            self.currency_name = from_dict_get_string(self.balance_data, "currency_name")
            self.unit_currency = from_dict_get_string(self.balance_data, "unit_currency")

            # 状态信息
            self.status = from_dict_get_string(self.balance_data, "status")

            # 计算可用余额
            self.available = (
                self.balance - self.locked
                if self.balance and self.locked is not None
                else self.balance
            )

            self.has_been_init_data = True

        except Exception as e:
            logger.error(f"Error initializing Upbit balance data: {e}", exc_info=True)

    def get_all_data(self):
        """获取所有余额数据"""
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "currency": self.currency,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "balance": self.balance,
                "locked": self.locked,
                "available": self.available,
                "avg_buy_price": self.avg_buy_price,
                "avg_buy_price_modified": self.avg_buy_price_modified,
                "currency_name": self.currency_name,
                "unit_currency": self.unit_currency,
                "status": self.status,
            }
        return self.all_data

    def total_balance(self):
        """获取总余额（可用 + 锁定）"""
        return self.balance

    def available_balance(self):
        """获取可用余额"""
        return self.available

    def locked_balance(self):
        """获取锁定余额"""
        return self.locked

    def value_in_currency(self, currency_rate):
        """计算余额在指定货币下的价值"""
        if self.balance is None:
            return 0.0

        if self.unit_currency == currency_rate:
            return self.balance

        if currency_rate and isinstance(currency_rate, (int, float)) and currency_rate > 0:
            return self.balance * currency_rate

        return 0.0

    def __str__(self):
        """字符串表示"""
        if not self.has_been_init_data:
            self.init_data()

        return (
            f"UpbitBalance(currency={self.currency}, "
            f"total={self.balance:.8f}, "
            f"available={self.available:.8f}, "
            f"locked={self.locked:.8f})"
        )
