import time
import json
from bt_api_py.containers.markprices.mark_price import MarkPriceData
from bt_api_py.functions.utils import from_dict_get_float, from_dict_get_string


class OkxMarkPriceData(MarkPriceData):
    """保存标记价格信息"""

    def __init__(self, mark_price_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(mark_price_info, has_been_json_encoded)
        self.exchange_name = "OKX"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.mark_price_data = mark_price_info if has_been_json_encoded else None
        self.all_data = None
        self.mark_price = None
        self.server_time = None
        self.mark_price_symbol_name = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.mark_price_info = json.loads(self.mark_price_info)
            self.mark_price_data = self.mark_price_info["data"][0]
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        if "arg" in self.mark_price_info:
            self.mark_price_symbol_name = from_dict_get_string(self.mark_price_info["arg"], "instId")
        self.server_time = from_dict_get_float(self.mark_price_data, 'ts')
        self.mark_price = from_dict_get_float(self.mark_price_data, 'markPx') \
            if 'markPx' in self.mark_price_data \
            else from_dict_get_float(self.mark_price_data, 'idxPx')
        self.has_been_init_data = True
        return self

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "local_update_time": self.local_update_time,
                "server_time": self.server_time,
                "mark_price": self.mark_price,
                "mark_price_symbol_name": self.mark_price_symbol_name,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()

    def get_exchange_name(self):
        return self.exchange_name

    def get_server_time(self):
        return self.server_time

    def get_local_update_time(self):
        return self.local_update_time

    def get_symbol_name(self):
        return self.symbol_name

    def get_mark_price_symbol_name(self):
        return self.mark_price_symbol_name

    def get_asset_type(self):
        return self.asset_type

    def get_mark_price(self):
        mark_price = self.mark_price_data.get('markPx', None)
        if mark_price is None:
            mark_price = self.mark_price_data.get('idxPx', None)
        return float(mark_price) if mark_price is not None else None
