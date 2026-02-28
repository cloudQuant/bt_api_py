"""OKX Account Greeks data container."""

import json
import time

from bt_api_py.containers.greeks.greeks import GreeksData
from bt_api_py.functions.utils import from_dict_get_float


class OkxAccountGreeksData(GreeksData):
    """OKX account Greeks data container.

    WebSocket channel: account-greeks
    Pushes account-level Greeks for options portfolio.

    Example data:
    {
        "arg": {"channel": "account-greeks"},
        "data": [{
            "gtD": "0",
            "gtG": "0",
            "gtT": "0",
            "gtV": "0",
            "bsD": "0",
            "bsG": "0",
            "bsT": "0",
            "bsV": "0",
            "uTime": "1700000000000"
        }]
    }
    """

    def __init__(self, greeks_info, symbol_name, asset_type, has_been_json_encoded=False):
        super().__init__(greeks_info, has_been_json_encoded)
        self.exchange_name = "OKX"
        self.symbol_name = symbol_name or "ANY"
        self.local_update_time = time.time()
        self.asset_type = asset_type
        self.greeks_data = greeks_info if has_been_json_encoded else None
        # PA (Price Adjoint) Greeks
        self.pa_delta = None
        self.pa_gamma = None
        self.pa_theta = None
        self.pa_vega = None
        # BS (Black-Scholes) Greeks
        self.bs_delta = None
        self.bs_gamma = None
        self.bs_theta = None
        self.bs_vega = None
        self.update_time = None
        self.has_been_init_data = False

    def init_data(self):
        if not self.has_been_json_encoded:
            self.greeks_data = json.loads(self.greeks_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self

        self.pa_delta = from_dict_get_float(self.greeks_data, "gtD")
        self.pa_gamma = from_dict_get_float(self.greeks_data, "gtG")
        self.pa_theta = from_dict_get_float(self.greeks_data, "gtT")
        self.pa_vega = from_dict_get_float(self.greeks_data, "gtV")
        self.bs_delta = from_dict_get_float(self.greeks_data, "bsD")
        self.bs_gamma = from_dict_get_float(self.greeks_data, "bsG")
        self.bs_theta = from_dict_get_float(self.greeks_data, "bsT")
        self.bs_vega = from_dict_get_float(self.greeks_data, "bsV")
        self.update_time = from_dict_get_float(self.greeks_data, "uTime")
        self.server_time = self.update_time
        self.has_been_init_data = True
        return self

    def get_exchange_name(self):
        return self.exchange_name

    def get_asset_type(self):
        return self.asset_type

    def get_symbol_name(self):
        return self.symbol_name

    def get_server_time(self):
        if not self.has_been_init_data:
            self.init_data()
        return self.server_time

    def get_local_update_time(self):
        return self.local_update_time

    def get_pa_delta(self):
        """PA Delta (Price Adjoint Delta)."""
        if not self.has_been_init_data:
            self.init_data()
        return self.pa_delta

    def get_pa_gamma(self):
        """PA Gamma (Price Adjoint Gamma)."""
        if not self.has_been_init_data:
            self.init_data()
        return self.pa_gamma

    def get_pa_theta(self):
        """PA Theta (Price Adjoint Theta)."""
        if not self.has_been_init_data:
            self.init_data()
        return self.pa_theta

    def get_pa_vega(self):
        """PA Vega (Price Adjoint Vega)."""
        if not self.has_been_init_data:
            self.init_data()
        return self.pa_vega

    def get_bs_delta(self):
        """BS Delta (Black-Scholes Delta)."""
        if not self.has_been_init_data:
            self.init_data()
        return self.bs_delta

    def get_bs_gamma(self):
        """BS Gamma (Black-Scholes Gamma)."""
        if not self.has_been_init_data:
            self.init_data()
        return self.bs_gamma

    def get_bs_theta(self):
        """BS Theta (Black-Scholes Theta)."""
        if not self.has_been_init_data:
            self.init_data()
        return self.bs_theta

    def get_bs_vega(self):
        """BS Vega (Black-Scholes Vega)."""
        if not self.has_been_init_data:
            self.init_data()
        return self.bs_vega

    def get_update_time(self):
        """Update timestamp."""
        if not self.has_been_init_data:
            self.init_data()
        return self.update_time

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
                "pa_delta": self.pa_delta,
                "pa_gamma": self.pa_gamma,
                "pa_theta": self.pa_theta,
                "pa_vega": self.pa_vega,
                "bs_delta": self.bs_delta,
                "bs_gamma": self.bs_gamma,
                "bs_theta": self.bs_theta,
                "bs_vega": self.bs_vega,
                "update_time": self.update_time,
            }
        return self.all_data

    def __str__(self):
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self):
        return self.__str__()
