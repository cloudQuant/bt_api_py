"""
OKX Open Interest Data Container.
"""

from __future__ import annotations

import json
import time

from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class OkxOpenInterestData:
    """OKX open interest data from open-interest channel.

    Represents the total number of outstanding derivative contracts.
    """

    def __init__(self, open_interest_info, symbol_name, asset_type, has_been_json_encoded=False):
        self.event = "OpenInterestEvent"
        self.open_interest_info = open_interest_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name = "OKX"
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.symbol_name = symbol_name
        self.open_interest_data = open_interest_info if has_been_json_encoded else None
        self.has_been_init_data = False

        # Data fields
        self.server_time = None
        self.open_interest_symbol_name = None
        self.open_interest = None
        self.open_interest_ccy = None
        self.all_data = None

    def init_data(self):
        if not self.has_been_json_encoded:
            self.open_interest_data = json.loads(self.open_interest_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.open_interest_data = self.open_interest_data
        self.server_time = from_dict_get_float(self.open_interest_data, "ts")
        self.open_interest_symbol_name = from_dict_get_string(self.open_interest_data, "instId")
        self.open_interest = from_dict_get_float(self.open_interest_data, "oi")
        self.open_interest_ccy = from_dict_get_string(self.open_interest_data, "oiCcy")

        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "server_time": self.server_time,
                "open_interest_symbol_name": self.open_interest_symbol_name,
                "open_interest": self.open_interest,
                "open_interest_ccy": self.open_interest_ccy,
            }
        return self.all_data

    def get_event(self):
        return self.event

    def get_exchange_name(self):
        return self.exchange_name

    def get_local_update_time(self):
        return self.local_update_time

    def get_symbol_name(self):
        return self.symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_server_time(self):
        return self.server_time

    def get_open_interest_symbol_name(self):
        return self.open_interest_symbol_name

    def get_open_interest(self):
        return self.open_interest

    def get_open_interest_ccy(self):
        return self.open_interest_ccy

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()
