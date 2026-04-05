"""Greeks data container base class."""

from __future__ import annotations


class GreeksData:
    """Base class for account Greeks data."""

    def __init__(self, greeks_info, has_been_json_encoded=False):
        self.event = "AccountGreeksEvent"
        self.greeks_info = greeks_info
        self.has_been_json_encoded = has_been_json_encoded
        self.exchange_name = None
        self.local_update_time = None
        self.asset_type = None
        self.symbol_name = None
        self.greeks_data = greeks_info if has_been_json_encoded else None
        self.server_time = None
        self.all_data = None

    def get_event(self):
        """Event type."""
        return self.event

    def init_data(self):
        raise NotImplementedError

    def get_all_data(self):
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "server_time": self.server_time,
            }
        return self.all_data

    def get_exchange_name(self):
        """Exchange name."""
        raise NotImplementedError

    def get_asset_type(self):
        """Asset type."""
        raise NotImplementedError

    def get_symbol_name(self):
        """Symbol name."""
        raise NotImplementedError

    def get_server_time(self):
        """Server timestamp."""
        raise NotImplementedError

    def get_local_update_time(self):
        """Local timestamp."""
        raise NotImplementedError

    def __str__(self):
        raise NotImplementedError

    def __repr__(self):
        return self.__str__()
