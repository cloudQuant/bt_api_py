from __future__ import annotations

from typing import Any, Self

from bt_api_py.containers.auto_init_mixin import AutoInitMixin


class BalanceData(AutoInitMixin):
    """
    用于保存账户的余额.

    用于存储和访问账户余额信息的基类。
    """

    def __init__(self, balance_info: Any, has_been_json_encoded: bool = False) -> None:
        """
        Initialize balance data.

        Args:
            balance_info: Balance information dictionary.
            has_been_json_encoded: Whether data has been JSON encoded.

        Returns:
            None
        """
        self.event = "BalanceEvent"
        self.balance_info = balance_info
        self.has_been_json_encoded = has_been_json_encoded

    def get_event(self) -> str:
        """
        Get event type.

        Returns:
            Event type string.
        """
        return self.event

    def init_data(self) -> None | Self:
        """
        Initialize data from balance_info.

        Returns:
            Self for method chaining, or None.

        Raises:
            NotImplementedError: Must be implemented by subclass.
        """
        raise NotImplementedError

    def get_all_data(self) -> dict[str, Any]:
        """
        Get all balance data as dictionary.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Dictionary containing all balance data.
        """
        raise NotImplementedError

    def get_exchange_name(self) -> str:
        """
        Get exchange name.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Exchange name string.
        """
        raise NotImplementedError

    def get_asset_type(self) -> str | None:
        """
        Get asset type.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Asset type string.
        """
        raise NotImplementedError

    def get_server_time(self) -> float | None:
        """
        Get server timestamp.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Server timestamp or None if not available.
        """
        raise NotImplementedError

    def get_local_update_time(self) -> float | None:
        """
        Get local update timestamp.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Local update timestamp.
        """
        raise NotImplementedError

    def get_account_id(self) -> str | None:
        """
        Get account ID.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Account ID string or None.
        """
        raise NotImplementedError

    def get_account_type(self) -> str | None:
        """
        Get account type.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Account type string or None.
        """
        raise NotImplementedError

    def get_fee_tier(self) -> int | str | None:
        """
        Get fee tier level.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Fee tier level (int or str) or None.
        """
        raise NotImplementedError

    def get_max_withdraw_amount(self) -> float | None:
        """
        Get maximum withdrawable amount.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Maximum withdrawable amount or None.
        """
        raise NotImplementedError

    def get_margin(self) -> float | None:
        """
        Get total margin.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Total margin amount or None.
        """
        raise NotImplementedError

    def get_used_margin(self) -> float | None:
        """
        Get used margin.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Used margin amount or None.
        """
        raise NotImplementedError

    def get_maintain_margin(self) -> float | None:
        """
        Get maintenance margin.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Maintenance margin amount or None.
        """
        raise NotImplementedError

    def get_available_margin(self) -> float | None:
        """
        Get available margin.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Available margin amount or None.
        """
        raise NotImplementedError

    def get_open_order_initial_margin(self) -> float | None:
        """
        Get open order initial margin.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Open order initial margin amount or None.
        """
        raise NotImplementedError

    def get_open_order_maintenance_margin(self) -> float | None:
        """
        Get open order maintenance margin.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Open order maintenance margin amount or None.
        """
        raise NotImplementedError

    def get_unrealized_profit(self) -> float | None:
        """
        Get unrealized profit.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Unrealized profit amount or None.
        """
        raise NotImplementedError

    def get_interest(self) -> float | None:
        """
        Get interest amount.

        Raises:
            NotImplementedError: Must be implemented by subclass.

        Returns:
            Interest amount or None.
        """
        raise NotImplementedError

    def __str__(self) -> str:
        raise NotImplementedError

    def __repr__(self) -> str:
        raise NotImplementedError
