import json
import time

from bt_api_py.containers.accounts.account import AccountData


class BitfinexSpotRequestAccountData(AccountData):
    """Bitfinex Spot Request Account Data"""

    def __init__(self, account_info, asset_type, has_been_json_encoded=False):
        super().__init__(account_info, has_been_json_encoded)
        self.exchange_name = "BITFINEX"
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.account_data = account_info if has_been_json_encoded else None
        self.account_id = None
        self.account_type = None
        self.currency = None
        self.balance = None
        self.available = None
        self.timestamp = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.account_data = json.loads(self.account_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        if isinstance(self.account_data, dict):
            self.account_id = self.account_data.get("id")
            self.account_type = self.account_data.get("type")
            self.currency = self.account_data.get("currency")
            self.balance = self.account_data.get("balance")
            self.available = self.account_data.get("available")
            self.timestamp = self.account_data.get("timestamp")

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.init_data()
            self.all_data = {
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "account_id": self.account_id,
                "account_type": self.account_type,
                "currency": self.currency,
                "balance": self.balance,
                "available": self.available,
                "timestamp": self.timestamp,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()


class BitfinexSpotWssAccountData(BitfinexSpotRequestAccountData):
    """Bitfinex Spot WebSocket Account Data"""

    pass  # Same structure as request data
