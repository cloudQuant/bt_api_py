"""Request Data Container."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Optional

NormalizeResult = tuple[Any, Optional[bool]]
NormalizeFunction = Callable[[Any, dict[str, Any]], NormalizeResult]


class RequestData:
    """Request data container for API requests.

    This class handles request data for API calls, including input data,
    extra data, status, and normalization functions.

    Attributes:
        event: Event type identifier.
        input_data: Input data for the request.
        extra_data: Additional data for the request.
        data: Processed data list.
        status: Request status.
        normalize_func: Function to normalize input data.
        local_update_time: Local timestamp of last update.
        exchange_name: Exchange identifier.
        symbol_name: Trading symbol name.
        asset_type: Asset type.
        request_type: Request type identifier.
        has_been_init_data: Flag indicating if data has been initialized.
    """

    def __init__(
        self,
        data: Any,
        extra_data: dict[str, Any],
        status: bool = False,
        normalize_func: NormalizeFunction | None = None,
    ) -> None:
        """Initialize request data container.

        Args:
            data: Input data for the request.
            extra_data: Additional data for the request.
            status: Request status, defaults to False.
            normalize_func: Function to normalize input data, defaults to None.
        """
        self.event: str = "RequestEvent"
        self.input_data = data
        self.extra_data = extra_data
        self.data: Any = []
        self.status: bool | None = status
        self.normalize_func = normalize_func
        self.local_update_time = time.time()
        self.exchange_name: str = str(extra_data.get("exchange_name", ""))
        _sn = extra_data.get("symbol_name")
        self.symbol_name: str | None = str(_sn) if _sn else None
        _at = extra_data.get("asset_type")
        self.asset_type: str | None = str(_at) if _at else None
        self.request_type: str = str(extra_data.get("request_type", ""))
        self.has_been_init_data = False

    def init_data(self) -> None:
        """Initialize and parse request data.

        Processes the input data using the normalize function if provided,
        otherwise uses the input data directly.
        """
        normalize_func = self.normalize_func or self.extra_data.get("normalize_function", None)
        if normalize_func is None:
            self.data = self.input_data
            self.status = None
        else:
            self.data, self.status = normalize_func(self.input_data, self.extra_data)

        self.has_been_init_data = True

    def set_data(self, data: Any) -> None:
        """Set data to request.

        Args:
            data: Data to set.
        """
        self.data = data

    def set_status(self, status: bool = True) -> None:
        """Set status of request.

        Args:
            status: Request status, defaults to True.
        """
        self.status = status

    def get_event(self) -> str:
        """Get event type from request data info.

        Returns:
            Event type string.
        """
        return self.event

    def get_request_type(self) -> str:
        """Get request type from request data info.

        Returns:
            Request type string.
        """
        return self.request_type

    def get_input_data(self) -> Any:
        """Get input data from request data info.

        Returns:
            Input data (str or dict).
        """
        return self.input_data

    def get_extra_data(self) -> dict[str, Any]:
        """Get extra data from request data info.

        Returns:
            Extra data dictionary.
        """
        return self.extra_data

    def get_data(self) -> Any:
        """Get data from request data info.

        Initializes data if not already done.

        Returns:
            Processed request data.
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.data

    def get_status(self) -> bool | None:
        """Get status of request.

        Initializes data if not already done.

        Returns:
            bool: True or False, None if not initialized.
        """
        if not self.has_been_init_data:
            self.init_data()
        return self.status

    def get_exchange_name(self) -> str:
        """Get exchange name.

        Returns:
            Exchange identifier.
        """
        return self.exchange_name

    def get_symbol_name(self) -> str:
        """Get symbol name.

        Returns:
            Trading symbol name.
        """
        return self.symbol_name or ""

    def get_asset_type(self) -> str:
        """Get asset type.

        Returns:
            Asset type.
        """
        return self.asset_type or ""
