import json
import time

from bt_api_py.containers.accounts.account import AccountData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HyperliquidSpotWssAccountData(AccountData):
    """保存Hyperliquid现货WebSocket账户数据"""

    def __init__(self, account_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "HYPERLIQUID"
        self.local_update_time = time.time()
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.account_data = account_info if has_been_json_encoded else None
        self.user_address = None
        self.account_value = None
        self.total_margin_used = None
        self.initial_margin = None
        self.positions = []
        self.balances = []
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """初始化账户数据"""
        if self.has_been_init_data:
            return

        try:
            if not self.has_been_json_encoded:
                self.account_data = json.loads(self.account_info)

            # Process account data
            if isinstance(self.account_data, dict):
                self.user_address = from_dict_get_string(self.account_data, "user")
                self.account_value = from_dict_get_float(self.account_data, "accountValue")
                self.total_margin_used = from_dict_get_float(self.account_data, "totalMarginUsed")
                self.initial_margin = from_dict_get_float(self.account_data, "initialMargin")

                # Process asset positions
                if "assetPositions" in self.account_data:
                    for position in self.account_data["assetPositions"]:
                        self.positions.append(position)

                # Process balances
                if "balances" in self.account_data:
                    for balance in self.account_data["balances"]:
                        self.balances.append(balance)

            self.has_been_init_data = True

        except Exception as e:
            print(f"Error initializing Hyperliquid account data: {e}")

    def get_all_data(self):
        """获取所有账户数据"""
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "user_address": self.user_address,
                "account_value": self.account_value,
                "total_margin_used": self.total_margin_used,
                "initial_margin": self.initial_margin,
                "positions": self.positions,
                "balances": self.balances,
            }
        return self.all_data

    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_user_address(self):
        return self.user_address

    def get_account_value(self):
        return self.account_value

    def get_total_margin_used(self):
        return self.total_margin_used

    def get_initial_margin(self):
        return self.initial_margin

    def get_positions(self):
        return self.positions

    def get_balances(self):
        return self.balances

    def __str__(self):
        return f"HyperliquidSpotWssAccountData(user={self.user_address}, value={self.account_value})"