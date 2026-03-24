"""CTP 成交数据容器.

对应 CTP 的 CThostFtdcTradeField 结构体.
"""

from __future__ import annotations

from typing import Any

from bt_api_py.containers.trades.trade import TradeData
from bt_api_py.functions.utils import (
    from_dict_get_float,
    from_dict_get_int,
    from_dict_get_string,
)


class CtpTradeData(TradeData):
    """CTP 成交数据容器."""

    def __init__(
        self,
        trade_info: dict[str, Any],
        symbol_name: str | None,
        asset_type: str = "FUTURE",
        has_been_json_encoded: bool = False,
    ) -> None:
        """Initialize CTP trade data.

        Args:
            trade_info: Raw trade data from CTP API
            symbol_name: Symbol name
            asset_type: Asset type (default: "FUTURE")
            has_been_json_encoded: Whether data is already JSON encoded
        """
        super().__init__(trade_info, has_been_json_encoded)
        self.symbol_name = symbol_name
        self.asset_type = asset_type
        self.exchange_name = "CTP"
        self._initialized = False

        self.instrument_id: str | None = None
        self.trade_id_value: str | None = None
        self.order_ref: str | None = None
        self.order_sys_id: str | None = None
        self.direction: str | None = None
        self.offset: str | None = None
        self.price: float | None = None
        self.volume: int | None = None
        self.trade_date: str | None = None
        self.trade_time: str | None = None
        self.exchange_id: str | None = None

        self.trade_fee: float = 0.0
        self.trade_fee_symbol: str = "CNY"
        self._trade_offset: str | None = None

        self._all_data: dict[str, Any] | None = None

    def init_data(self) -> CtpTradeData:
        """Initialize and parse the trade data.

        Returns:
            Self for method chaining
        """
        if self._initialized:
            return self
        info = self.trade_info
        if isinstance(info, dict):
            self.instrument_id = from_dict_get_string(info, "InstrumentID")
            self.trade_id_value = from_dict_get_string(info, "TradeID")
            self.order_ref = from_dict_get_string(info, "OrderRef")
            self.order_sys_id = from_dict_get_string(info, "OrderSysID")
            direction_char = from_dict_get_string(info, "Direction", "0")
            self.direction = "buy" if direction_char == "0" else "sell"
            offset_char = from_dict_get_string(info, "OffsetFlag", "0")
            offset_map = {"0": "open", "1": "close", "3": "close_today", "4": "close_yesterday"}
            self.offset = offset_map.get(offset_char, "open")
            self.price = from_dict_get_float(info, "Price", 0.0)
            self.volume = from_dict_get_int(info, "Volume", 0)
            self.trade_date = from_dict_get_string(info, "TradeDate")
            self.trade_time = from_dict_get_string(info, "TradeTime")
            self.exchange_id = from_dict_get_string(info, "ExchangeID")
        self._initialized = True
        return self

    def get_exchange_name(self) -> str:
        """Get exchange name.

        Returns:
            Exchange name string
        """
        return self.exchange_name

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type string
        """
        return self.asset_type

    def get_symbol_name(self) -> str | None:
        """Get symbol name.

        Returns:
            Symbol name or instrument ID
        """
        return self.instrument_id or self.symbol_name

    def get_server_time(self) -> str | None:
        """Get server time.

        Returns:
            Server time string
        """
        return self.trade_time

    def get_trade_id(self) -> str | None:
        """Get trade ID.

        Returns:
            Trade ID string
        """
        return self.trade_id_value

    def get_order_id(self) -> str | None:
        """Get order ID.

        Returns:
            Order system ID string
        """
        return self.order_sys_id

    def get_client_order_id(self) -> str | None:
        """Get client order ID.

        Returns:
            Client order reference string
        """
        return self.order_ref

    def get_trade_side(self) -> str | None:
        """Get trade side (buy/sell).

        Returns:
            Trade side string
        """
        return self.direction

    def get_trade_offset(self) -> str:
        """Get trade offset.

        Returns:
            Trade offset string (One of:
                "open", "close", "close_today", "close_yesterday"
        """
        return self.offset

    def get_trade_price(self) -> float | None:
        """Get trade price.

        Returns:
            Trade price as float
        """
        return self.price

    def get_trade_volume(self) -> int | None:
        """Get trade volume.

        Returns:
            Trade volume as integer
        """
        return self.volume

    def get_trade_time(self) -> str | None:
        """Get trade time.

        Returns:
            Trade time string
        """
        return self.trade_time

    def get_trade_fee(self) -> float:
        """Get trade fee.

        Note:
            CTP trade data does not contain fee information.

        Returns:
            Trade fee as float (0.0)
        """
        return self.trade_fee

    def get_trade_fee_symbol(self) -> str:
        """Get trade fee symbol.

        Returns:
            Fee currency symbol (e.g., "CNY")
        """
        return self.trade_fee_symbol

    def get_all_data(self) -> dict[str, Any]:
        """Get all trade data as dictionary.

        Returns:
            Dictionary containing all trade data
        """
        if self._all_data is None:
            self._all_data = {
                "exchange_name": self.exchange_name,
                "instrument_id": self.instrument_id,
                "trade_id": self.trade_id_value,
                "order_sys_id": self.order_sys_id,
                "direction": self.direction,
                "offset": self.offset,
                "price": self.price,
                "volume": self.volume,
                "trade_date": self.trade_date,
                "trade_time": self.trade_time,
                "exchange_id": self.exchange_id,
            }
        return self._all_data
