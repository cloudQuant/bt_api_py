"""HTX Bar/Kline Data Container."""

import json
import time
from typing import Any

from bt_api_py.containers.bars.bar import BarData
from bt_api_py.functions.utils import from_dict_get_float


class HtxRequestBarData(BarData):
    """HTX REST API kline/bar data container.

    This class handles bar (candlestick/kline) data from HTX (Huobi) exchange's REST API.
    It parses and stores bar information including OHLC prices, volume, and trade counts.

    Attributes:
        exchange_name: Exchange identifier ("HTX").
        symbol_name: Trading symbol name.
        asset_type: Asset type for the bar data.
        server_time: Server timestamp.
        local_update_time: Local timestamp of last update.
        bar_data: Parsed bar data dictionary.
        open_time: Bar open timestamp.
        open_price: Bar open price.
        high_price: Bar high price.
        low_price: Bar low price.
        close_price: Bar close price.
        volume: Bar volume.
        amount: Bar amount.
        num_trades: Number of trades in the bar.
        bar_status: Bar status.
        all_data: Cached dictionary of all bar data.
        has_been_init_data: Flag indicating if data has been initialized.
    """

    def __init__(
        self,
        bar_info: dict[str, Any] | str,
        symbol_name: str,
        asset_type: str,
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize HTX bar data container.

        Args:
            bar_info: Bar information from HTX API (dict or JSON string).
            symbol_name: Trading symbol name.
            asset_type: Asset type for the bar data.
            has_been_json_encoded: Whether bar_info is already JSON encoded.
        """
        super().__init__(bar_info, has_been_json_encoded)
        self.exchange_name = "HTX"
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.server_time: float | None = None
        self.local_update_time = time.time()
        self.bar_data: dict[str, Any] | None = bar_info if has_been_json_encoded else None
        self.open_time: float | None = None
        self.open_price: float | None = None
        self.high_price: float | None = None
        self.low_price: float | None = None
        self.close_price: float | None = None
        self.volume: float | None = None
        self.amount: float | None = None
        self.num_trades: float | None = None
        self.bar_status: bool | None = None
        self.all_data: dict[str, Any] | None = None
        self.has_been_init_data = False

    def init_data(self) -> "HtxRequestBarData":
        """Initialize bar data from HTX response.

        Parses the HTX kline response format:
        {
            "id": 1234567890,      # timestamp in seconds
            "open": 50000,
            "close": 50500,
            "low": 49000,
            "high": 51000,
            "amount": 1234.56,     # trade amount (base currency)
            "vol": 61728350,       # trade volume (quote currency)
            "count": 10000         # number of trades
        }

        Returns:
            Self for method chaining.
        """
        if self.has_been_init_data:
            return self

        if not self.has_been_json_encoded:
            self.bar_data = (
                json.loads(self.bar_info) if isinstance(self.bar_info, str) else self.bar_info
            )
            self.has_been_json_encoded = True

        if self.bar_data:
            self.open_time = from_dict_get_float(self.bar_data, "id")
            self.server_time = self.open_time
            self.open_price = from_dict_get_float(self.bar_data, "open")
            self.high_price = from_dict_get_float(self.bar_data, "high")
            self.low_price = from_dict_get_float(self.bar_data, "low")
            self.close_price = from_dict_get_float(self.bar_data, "close")
            self.amount = from_dict_get_float(self.bar_data, "amount")
            self.volume = from_dict_get_float(self.bar_data, "vol")
            self.num_trades = from_dict_get_float(self.bar_data, "count")
            self.bar_status = True

        self.has_been_init_data = True
        return self

    def get_all_data(self) -> dict[str, Any]:
        """Get all bar data as a dictionary.

        Returns:
            Dictionary containing all bar information including server time,
            open time, OHLC prices, volume, amount, and trade counts.
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
                "amount": self.amount,
                "num_trades": self.num_trades,
                "exchange_name": self.exchange_name,
                "local_update_time": self.local_update_time,
                "symbol_name": self.symbol_name,
                "asset_type": self.asset_type,
                "bar_status": self.bar_status,
            }
        return self.all_data

    def __str__(self) -> str:
        """Return string representation of bar data.

        Returns:
            JSON string of all bar data.
        """
        self.init_data()
        return json.dumps(self.get_all_data())

    def __repr__(self) -> str:
        """Return representation of bar data.

        Returns:
            Same as __str__.
        """
        return self.__str__()

    def get_exchange_name(self) -> str:
        """Get exchange name.

        Returns:
            Exchange identifier "HTX".
        """
        return self.exchange_name

    def get_local_update_time(self) -> float:
        """Get local update timestamp.

        Returns:
            Local timestamp when data was last updated.
        """
        return self.local_update_time

    def get_symbol_name(self) -> str:
        """Get symbol name.

        Returns:
            Trading symbol name.
        """
        return self.symbol_name

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type for the bar data.
        """
        return self.asset_type

    def get_server_time(self) -> float | None:
        """Get server timestamp.

        Returns:
            Server timestamp or None if not initialized.
        """
        return self.server_time

    def get_open_time(self) -> float | None:
        """Get bar open timestamp.

        Returns:
            Bar open timestamp or None if not initialized.
        """
        self.init_data()
        return self.open_time

    def get_open_price(self) -> float | None:
        """Get bar open price.

        Returns:
            Bar open price or None if not initialized.
        """
        self.init_data()
        return self.open_price

    def get_high_price(self) -> float | None:
        """Get bar high price.

        Returns:
            Bar high price or None if not initialized.
        """
        self.init_data()
        return self.high_price

    def get_low_price(self) -> float | None:
        """Get bar low price.

        Returns:
            Bar low price or None if not initialized.
        """
        self.init_data()
        return self.low_price

    def get_close_price(self) -> float | None:
        """Get bar close price.

        Returns:
            Bar close price or None if not initialized.
        """
        self.init_data()
        return self.close_price

    def get_volume(self) -> float | None:
        """Get bar volume.

        Returns:
            Bar volume or None if not initialized.
        """
        self.init_data()
        return self.volume

    def get_amount(self) -> float | None:
        """Get bar amount.

        Returns:
            Bar amount or None if not initialized.
        """
        self.init_data()
        return self.amount

    def get_num_trades(self) -> float | None:
        """Get number of trades in the bar.

        Returns:
            Number of trades or None if not initialized.
        """
        self.init_data()
        return self.num_trades

    def get_bar_status(self) -> bool | None:
        """Get bar status.

        Returns:
            Bar status or None if not initialized.
        """
        self.init_data()
        return self.bar_status
