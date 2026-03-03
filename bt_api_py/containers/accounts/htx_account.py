"""HTX Account Data Container"""

import json
import time

from bt_api_py.containers.accounts.account import AccountData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class HtxSpotRequestAccountData(AccountData):
    """HTX REST API account data."""

    def __init__(self, account_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "HTX"
        self.account_type = "SPOT"
        self.symbol_name = symbol_name
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.account_data = account_info if has_been_json_encoded else None
        self.account_id = None
        self.available_margin = None
        self.used_margin = None
        self.all_data = None
        self.has_been_init_data = False

    def init_data(self):
        """Initialize account data from HTX response.

        HTX account response format:
        {
            "status": "ok",
            "data": [
                {
                    "id": 123456,
                    "type": "spot",
                    "subtype": "",
                    "state": "working"
                }
            ]
        }
        """
        if self.has_been_init_data:
            return

        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)

        # Extract account data
        data = self.account_data.get("data", [])
        if isinstance(data, list) and len(data) > 0:
            account = data[0]
            self.account_id = str(from_dict_get_float(account, "id"))

        self.has_been_init_data = True

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "account_type": self.account_type,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "account_id": self.account_id,
                "available_margin": self.available_margin,
                "used_margin": self.used_margin,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        return self.exchange_name

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_local_update_time(self):
        return self.local_update_time

    def get_account_id(self):
        self.init_data()
        return self.account_id

    def get_account_type(self):
        return self.account_type

    def get_available_margin(self):
        return self.available_margin

    def get_used_margin(self):
        return self.used_margin
