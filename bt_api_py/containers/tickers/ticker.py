"""Ticker class for defining ticker attributes and methods."""

from __future__ import annotations

from typing import Any

from bt_api_py.containers.auto_init_mixin import AutoInitMixin


class TickerData(AutoInitMixin):
    """保存ticker信息."""

    def __init__(self, ticker_info: Any, has_been_json_encoded: bool = False) -> None:
        self.event = "TickerEvent"
        self.ticker_info = ticker_info
        self.has_been_json_encoded = has_been_json_encoded

    def init_data(self) -> TickerData:
        """Initialize ticker data from raw response.

        Returns:
            Self instance for method chaining.

        """
        raise NotImplementedError

    def get_all_data(self) -> dict[str, Any]:
        """Get all ticker data as dictionary.

        Returns:
            Dictionary containing all ticker data.

        """
        raise NotImplementedError

    def get_event(self) -> str:
        """Get event type.

        Returns:
            Event type string.

        """
        return self.event

    def get_exchange_name(self) -> str:
        """Get exchange name.

        Returns:
            Exchange name string.

        """
        raise NotImplementedError

    def get_local_update_time(self) -> float:
        """Get local update time.

        Returns:
            Local update timestamp.

        """
        raise NotImplementedError

    def get_symbol_name(self) -> str:
        """Get symbol name.

        Returns:
            Symbol name string.

        """
        raise NotImplementedError

    def get_ticker_symbol_name(self) -> str | None:
        """Get ticker symbol name from response.

        Returns:
            Ticker symbol name or None.

        """
        raise NotImplementedError

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type string.

        """
        raise NotImplementedError

    def get_server_time(self) -> float | None:
        """Get server time.

        Returns:
            Server timestamp or None.

        """
        raise NotImplementedError

    def get_bid_price(self) -> float | None:
        """Get bid price.

        Returns:
            Bid price or None.

        """
        raise NotImplementedError

    def get_ask_price(self) -> float | None:
        """Get ask price.

        Returns:
            Ask price or None.

        """
        raise NotImplementedError

    def get_bid_volume(self) -> float | None:
        """Get bid volume.

        Returns:
            Bid volume or None.

        """
        raise NotImplementedError

    def get_ask_volume(self) -> float | None:
        """Get ask volume.

        Returns:
            Ask volume or None.

        """
        raise NotImplementedError

    def get_last_price(self) -> float | None:
        """Get last price.

        Returns:
            Last price or None.

        """
        raise NotImplementedError

    def get_last_volume(self) -> float | None:
        """Get last volume.

        Returns:
            Last volume or None.

        """
        raise NotImplementedError

    def __str__(self) -> str:
        """String representation.

        Returns:
            String representation of ticker data.

        """
        raise NotImplementedError

    def __repr__(self) -> str:
        """Repr representation.

        Returns:
            String representation of ticker data.

        """
        raise NotImplementedError
