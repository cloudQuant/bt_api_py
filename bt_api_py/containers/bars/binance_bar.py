from __future__ import annotations

import json
import time
from typing import Any

from bt_api_py.containers.bars.bar import BarData
from bt_api_py.functions.utils import from_dict_get_bool, from_dict_get_float


class BinanceRequestBarData(BarData):
    """Binance REST API bar data container."""

    def __init__(
        self,
        bar_info: Any,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """
        Initialize Binance bar data from REST API.

        Args:
            bar_info: Bar information (JSON string or dict).
            symbol_name: Symbol name.
            asset_type: Asset type.
            has_been_json_encoded: Whether data has been JSON encoded.

        Returns:
            None
        """
        super().__init__(bar_info, has_been_json_encoded)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.server_time: float | None = None
        self.local_update_time = time.time()
        self.bar_data: Any = bar_info if has_been_json_encoded else None
        self.open_time: float | None = None
        self.open_price: float | None = None
        self.high_price: float | None = None
        self.low_price: float | None = None
        self.close_price: float | None = None
        self.volume: float | None = None
        self.close_time: float | None = None
        self.amount: float | None = None
        self.num_trades: float | None = None
        self.taker_buy_base_asset_volume: float | None = None
        self.taker_buy_quote_asset_volume: float | None = None
        self.bar_status: bool | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> BinanceRequestBarData:
        """
        Initialize data from bar_info.

        Returns:
            Self instance for chaining.
        """
        if not self.has_been_json_encoded:
            self.bar_data = json.loads(self.bar_info)
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        if self.bar_data is not None:
            self.open_time = float(self.bar_data[0])
            self.open_price = float(self.bar_data[1])
            self.high_price = float(self.bar_data[2])
            self.low_price = float(self.bar_data[3])
            self.close_price = float(self.bar_data[4])
            self.volume = float(self.bar_data[5])
            self.close_time = self.server_time = float(self.bar_data[6])
            self.amount = float(self.bar_data[7])
            self.num_trades = float(self.bar_data[8])
            self.taker_buy_base_asset_volume = float(self.bar_data[9])
            self.taker_buy_quote_asset_volume = float(self.bar_data[10])
            self.bar_status = True
        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """
        Get all bar data as dictionary.

        Returns:
            Dictionary containing all bar data.
        """
        if not self.has_been_init_data:
            self.init_data()
        if self.all_data is None:
            self.all_data = {
                "server_time": self.server_time,
                "open_time": self.open_time,
                "open_price": self.open_price,
                "high_price": self.high_price,
                "low_price": self.low_price,
                "close_price": self.close_price,
                "volume": self.volume,
                "close_time": self.close_time,
                "amount": self.amount,
                "num_trades": self.num_trades,
                "taker_buy_base_asset_volume": self.taker_buy_base_asset_volume,
                "taker_buy_quote_asset_volume": self.taker_buy_quote_asset_volume,
                "exchange_name": self.exchange_name,
                "local_update_time": self.local_update_time,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "bar_status": True,
            }
        return self.all_data

    def __str__(self) -> str:
        """String representation of bar data."""
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        """Representation of bar data."""
        return self.__str__()

    def get_event_type(self) -> str:
        """
        Get event type.

        Returns:
            Event type string.
        """
        return self.event

    def get_exchange_name(self) -> str:
        """
        Get exchange name.

        Returns:
            Exchange name string.
        """
        return self.exchange_name

    def get_symbol_name(self) -> str:
        """
        Get symbol name.

        Returns:
            Symbol name string.
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """
        Get asset type.

        Returns:
            Asset type string.
        """
        return self.asset_type

    def get_server_time(self) -> float | None:
        """
        Get server timestamp.

        Returns:
            Server timestamp or None.
        """
        return self.server_time

    def get_local_update_time(self) -> float:
        """
        Get local update timestamp.

        Returns:
            Local update timestamp.
        """
        return self.local_update_time

    def get_open_time(self) -> float | None:
        """
        Get open time.

        Returns:
            Open time timestamp or None.
        """
        return self.open_time

    def get_open_price(self) -> float | None:
        """
        Get open price.

        Returns:
            Open price or None.
        """
        return self.open_price

    def get_high_price(self) -> float | None:
        """
        Get high price.

        Returns:
            High price or None.
        """
        return self.high_price

    def get_low_price(self) -> float | None:
        """
        Get low price.

        Returns:
            Low price or None.
        """
        return self.low_price

    def get_close_price(self) -> float | None:
        """
        Get close price.

        Returns:
            Close price or None.
        """
        return self.close_price

    def get_volume(self) -> float | None:
        """
        Get volume.

        Returns:
            Volume or None.
        """
        return self.volume

    def get_amount(self) -> float | None:
        """
        Get amount (quote asset volume).

        Returns:
            Amount or None.
        """
        return self.amount

    def get_close_time(self) -> float | None:
        """
        Get close time.

        Returns:
            Close time timestamp or None.
        """
        return self.close_time

    def get_quote_asset_volume(self) -> None:
        """
        Get quote asset volume.

        Returns:
            Always None for this implementation.
        """
        return

    def get_base_asset_volume(self) -> None:
        """
        Get base asset volume.

        Returns:
            Always None for this implementation.
        """
        return

    def get_num_trades(self) -> float | None:
        """
        Get number of trades.

        Returns:
            Number of trades or None.
        """
        return self.num_trades

    def get_taker_buy_base_asset_volume(self) -> float | None:
        """
        Get taker buy base asset volume.

        Returns:
            Taker buy base asset volume or None.
        """
        return self.taker_buy_base_asset_volume

    def get_taker_buy_quote_asset_volume(self) -> float | None:
        """
        Get taker buy quote asset volume.

        Returns:
            Taker buy quote asset volume or None.
        """
        return self.taker_buy_quote_asset_volume

    def get_bar_status(self) -> bool | None:
        """
        Get bar status.

        Returns:
            True if bar is complete, False otherwise.
        """
        return self.bar_status


class BinanceWssBarData(BarData):
    """Binance WebSocket bar data container."""

    def __init__(
        self,
        bar_info: Any,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """
        Initialize Binance bar data from WebSocket.

        Args:
            bar_info: Bar information (JSON string or dict).
            symbol_name: Symbol name.
            asset_type: Asset type.
            has_been_json_encoded: Whether data has been JSON encoded.

        Returns:
            None
        """
        super().__init__(bar_info)
        self.exchange_name = "BINANCE"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.local_update_time = time.time()
        self.has_been_json_encoded = has_been_json_encoded
        self.bar_data: Any = bar_info["k"] if has_been_json_encoded else None
        self.server_time: float | None = None
        self.open_time: float | None = None
        self.open_price: float | None = None
        self.high_price: float | None = None
        self.low_price: float | None = None
        self.close_price: float | None = None
        self.volume: float | None = None
        self.amount: float | None = None
        self.close_time: float | None = None
        self.num_trades: float | None = None
        self.taker_buy_base_asset_volume: float | None = None
        self.taker_buy_quote_asset_volume: float | None = None
        self.bar_status: bool | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> BinanceWssBarData:
        """
        Initialize data from bar_info.

        Returns:
            Self instance for chaining.
        """
        if not self.has_been_json_encoded:
            self.bar_info = json.loads(self.bar_info)
            self.bar_data = self.bar_info["k"]
            self.has_been_json_encoded = True
        if self.has_been_init_data:
            return self
        self.server_time = from_dict_get_float(self.bar_info, "E")
        self.open_time = from_dict_get_float(self.bar_data, "t")
        self.open_price = from_dict_get_float(self.bar_data, "o")
        self.high_price = from_dict_get_float(self.bar_data, "h")
        self.low_price = from_dict_get_float(self.bar_data, "l")
        self.close_price = from_dict_get_float(self.bar_data, "c")
        self.volume = from_dict_get_float(self.bar_data, "v")
        self.amount = from_dict_get_float(self.bar_data, "q")
        self.close_time = from_dict_get_float(self.bar_data, "T")
        self.num_trades = from_dict_get_float(self.bar_data, "n")
        self.taker_buy_base_asset_volume = from_dict_get_float(self.bar_data, "V")
        self.taker_buy_quote_asset_volume = from_dict_get_float(self.bar_data, "Q")
        self.bar_status = from_dict_get_bool(self.bar_data, "x")
        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """
        Get all bar data as dictionary.

        Returns:
            Dictionary containing all bar data.
        """
        if self.all_data is None:
            self.all_data = {
                "exchange_name": self.exchange_name,
                "symbol_name": self.symbol_name,
                "server_time": self.server_time,
                "local_update_time": self.local_update_time,
                "asset_type": self.asset_type,
                "open_time": self.open_time,
                "open_price": self.open_price,
                "high_price": self.high_price,
                "low_price": self.low_price,
                "close_price": self.close_price,
                "volume": self.volume,
                "amount": self.amount,
                "close_time": self.close_time,
                "num_trades": self.num_trades,
                "taker_buy_base_asset_volume": self.taker_buy_quote_asset_volume,
                "taker_buy_quote_asset_volume": self.taker_buy_quote_asset_volume,
                "bar_status": self.bar_status,
            }
        return self.all_data

    def __str__(self) -> str:
        """String representation of bar data."""
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        """Representation of bar data."""
        return self.__str__()

    def get_event_type(self) -> str:
        """
        Get event type.

        Returns:
            Event type string.
        """
        return self.event

    def get_exchange_name(self) -> str:
        """
        Get exchange name.

        Returns:
            Exchange name string.
        """
        return self.exchange_name

    def get_symbol_name(self) -> str:
        """
        Get symbol name.

        Returns:
            Symbol name string.
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """
        Get asset type.

        Returns:
            Asset type string.
        """
        return self.asset_type

    def get_server_time(self) -> float | None:
        """
        Get server timestamp.

        Returns:
            Server timestamp or None.
        """
        return self.server_time

    def get_local_update_time(self) -> float:
        """
        Get local update timestamp.

        Returns:
            Local update timestamp.
        """
        return self.local_update_time

    def get_open_time(self) -> float | None:
        """
        Get open time.

        Returns:
            Open time timestamp or None.
        """
        return self.open_time

    def get_open_price(self) -> float | None:
        """
        Get open price.

        Returns:
            Open price or None.
        """
        return self.open_price

    def get_high_price(self) -> float | None:
        """
        Get high price.

        Returns:
            High price or None.
        """
        return self.high_price

    def get_low_price(self) -> float | None:
        """
        Get low price.

        Returns:
            Low price or None.
        """
        return self.low_price

    def get_close_price(self) -> float | None:
        """
        Get close price.

        Returns:
            Close price or None.
        """
        return self.close_price

    def get_volume(self) -> float | None:
        """
        Get volume.

        Returns:
            Volume or None.
        """
        return self.volume

    def get_amount(self) -> float | None:
        """
        Get amount (quote asset volume).

        Returns:
            Amount or None.
        """
        return self.amount

    def get_close_time(self) -> float | None:
        """
        Get close time.

        Returns:
            Close time timestamp or None.
        """
        return self.close_time

    def get_quote_asset_volume(self) -> None:
        """
        Get quote asset volume.

        Returns:
            Always None for this implementation.
        """
        return

    def get_base_asset_volume(self) -> None:
        """
        Get base asset volume.

        Returns:
            Always None for this implementation.
        """
        return

    def get_num_trades(self) -> float | None:
        """
        Get number of trades.

        Returns:
            Number of trades or None.
        """
        return self.num_trades

    def get_taker_buy_base_asset_volume(self) -> float | None:
        """
        Get taker buy base asset volume.

        Returns:
            Taker buy base asset volume or None.
        """
        return self.taker_buy_base_asset_volume

    def get_taker_buy_quote_asset_volume(self) -> float | None:
        """
        Get taker buy quote asset volume.

        Returns:
            Taker buy quote asset volume or None.
        """
        return self.taker_buy_quote_asset_volume

    def get_bar_status(self) -> bool | None:
        """
        Get bar status.

        Returns:
            True if bar is complete, False otherwise.
        """
        return self.bar_status
