"""Binance Grid Trading API - Grid trading interface request class.

Implements all REST API requests related to Binance grid trading, including:
- Futures grid order creation and cancellation
- Futures grid order query
- Futures grid position query
- Futures grid income query
"""

from __future__ import annotations

from typing import Any

from bt_api_py.containers.exchanges.binance_exchange_data import BinanceExchangeDataGrid
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class BinanceRequestDataGrid(BinanceRequestData):
    """Binance Grid Trading API request class.

    Handles all grid trading related requests.
    """

    def __init__(self, data_queue: Any, **kwargs: Any) -> None:
        """Initialize Binance grid request data handler.

        Args:
            data_queue: Queue for storing data.
            **kwargs: Additional keyword arguments including:
                - exchange_data: Exchange data instance
                - exchange_name: Exchange name (default: "binance_grid")
                - asset_type: Asset type (default: "GRID")
                - logger_name: Logger name (default: "binance_grid_feed.log")

        """
        kwargs.setdefault("exchange_data", BinanceExchangeDataGrid())
        kwargs.setdefault("exchange_name", "binance_grid")
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "GRID")
        self.logger_name = kwargs.get("logger_name", "binance_grid_feed.log")
        self._params = kwargs["exchange_data"]
        self.request_logger = get_logger("binance_grid_feed")
        self.async_logger = get_logger("binance_grid_feed")

    def _futures_grid_new_order(
        self,
        symbol: str,
        upper_price: float,
        lower_price: float,
        grid_quantity: int,
        base_quantity: float,
        interval_type: str | None = None,
        extra_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Build request parameters for creating a futures grid order.

        Args:
            symbol: Trading pair symbol.
            upper_price: Upper price limit of the grid.
            lower_price: Lower price limit of the grid.
            grid_quantity: Number of grid levels.
            base_quantity: Quantity per grid level.
            interval_type: Grid interval type (e.g., GEOMETRIC, ARITHMETIC).
            extra_data: Additional data for the request.
            **kwargs: Additional keyword arguments.

        Returns:
            Tuple containing (path, params, extra_data).

        """
        request_type = "futures_grid_new_order"
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        params = {
            "pair": request_symbol,
            "upperPrice": upper_price,
            "lowerPrice": lower_price,
            "gridNumber": grid_quantity,
            "investAmount": base_quantity,
        }
        if interval_type is not None:
            params["intervalType"] = interval_type
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def futures_grid_new_order(
        self,
        symbol: str,
        upper_price: float,
        lower_price: float,
        grid_quantity: int,
        base_quantity: float,
        interval_type: str | None = None,
        extra_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> RequestData:
        """Create a new futures grid order.

        Args:
            symbol: Trading pair symbol.
            upper_price: Upper price limit of the grid.
            lower_price: Lower price limit of the grid.
            grid_quantity: Number of grid levels.
            base_quantity: Quantity per grid level.
            interval_type: Grid interval type (e.g., GEOMETRIC, ARITHMETIC).
            extra_data: Additional data for the request.
            **kwargs: Additional keyword arguments.

        Returns:
            RequestData containing the order creation result.

        """
        path, params, extra_data = self._futures_grid_new_order(
            symbol=symbol,
            upper_price=upper_price,
            lower_price=lower_price,
            grid_quantity=grid_quantity,
            base_quantity=base_quantity,
            interval_type=interval_type,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _futures_grid_cancel_order(
        self,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
        extra_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Build request parameters for cancelling a futures grid order.

        Args:
            symbol: Trading pair symbol.
            order_id: Grid order ID to cancel.
            client_order_id: Client order ID to cancel.
            extra_data: Additional data for the request.
            **kwargs: Additional keyword arguments.

        Returns:
            Tuple containing (path, params, extra_data).

        """
        request_type = "futures_grid_cancel_order"
        path = self._params.get_rest_path(request_type)
        request_symbol = self._params.get_symbol(symbol)
        params = {
            "pair": request_symbol,
        }
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id is not None:
            params["clientOrderId"] = client_order_id
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def futures_grid_cancel_order(
        self,
        symbol: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
        extra_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> RequestData:
        """Cancel a futures grid order.

        Args:
            symbol: Trading pair symbol.
            order_id: Grid order ID to cancel.
            client_order_id: Client order ID to cancel.
            extra_data: Additional data for the request.
            **kwargs: Additional keyword arguments.

        Returns:
            RequestData containing the cancellation result.

        """
        path, params, extra_data = self._futures_grid_cancel_order(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_futures_grid_orders(
        self,
        symbol: str | None = None,
        order_id: str | None = None,
        status: str | None = None,
        extra_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Build request parameters for querying futures grid orders.

        Args:
            symbol: Trading pair symbol.
            order_id: Grid order ID to query.
            status: Grid order status to filter.
            extra_data: Additional data for the request.
            **kwargs: Additional keyword arguments.

        Returns:
            Tuple containing (path, params, extra_data).

        """
        request_type = "get_futures_grid_orders"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
            params["pair"] = request_symbol
        if order_id is not None:
            params["orderId"] = order_id
        if status is not None:
            params["status"] = status
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_futures_grid_orders(
        self,
        symbol: str | None = None,
        order_id: str | None = None,
        status: str | None = None,
        extra_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> RequestData:
        """Query futures grid orders.

        Args:
            symbol: Trading pair symbol.
            order_id: Grid order ID to query.
            status: Grid order status to filter.
            extra_data: Additional data for the request.
            **kwargs: Additional keyword arguments.

        Returns:
            RequestData containing the query result.

        """
        path, params, extra_data = self._get_futures_grid_orders(
            symbol=symbol, order_id=order_id, status=status, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_futures_grid_position(
        self,
        symbol: str | None = None,
        extra_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Build request parameters for querying futures grid position.

        Args:
            symbol: Trading pair symbol.
            extra_data: Additional data for the request.
            **kwargs: Additional keyword arguments.

        Returns:
            Tuple containing (path, params, extra_data).

        """
        request_type = "get_futures_grid_position"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
            params["pair"] = request_symbol
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_futures_grid_position(
        self,
        symbol: str | None = None,
        extra_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> RequestData:
        """Query futures grid position.

        Args:
            symbol: Trading pair symbol.
            extra_data: Additional data for the request.
            **kwargs: Additional keyword arguments.

        Returns:
            RequestData containing the position query result.

        """
        path, params, extra_data = self._get_futures_grid_position(
            symbol=symbol, extra_data=extra_data, **kwargs
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data

    def _get_futures_grid_income(
        self,
        symbol: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int | None = None,
        extra_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        """Build request parameters for querying futures grid income.

        Args:
            symbol: Trading pair symbol.
            start_time: Start timestamp in milliseconds.
            end_time: End timestamp in milliseconds.
            limit: Number of results to return.
            extra_data: Additional data for the request.
            **kwargs: Additional keyword arguments.

        Returns:
            Tuple containing (path, params, extra_data).

        """
        request_type = "get_futures_grid_income"
        path = self._params.get_rest_path(request_type)
        params: dict[str, Any] = {}
        if symbol is not None:
            request_symbol = self._params.get_symbol(symbol)
            params["pair"] = request_symbol
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        if limit is not None:
            params["limit"] = limit
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def get_futures_grid_income(
        self,
        symbol: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int | None = None,
        extra_data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> RequestData:
        """Query futures grid income.

        Args:
            symbol: Trading pair symbol.
            start_time: Start timestamp in milliseconds.
            end_time: End timestamp in milliseconds.
            limit: Number of results to return.
            extra_data: Additional data for the request.
            **kwargs: Additional keyword arguments.

        Returns:
            RequestData containing the income query result.

        """
        path, params, extra_data = self._get_futures_grid_income(
            symbol=symbol,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            extra_data=extra_data,
            **kwargs,
        )
        data = self.request(path, params=params, extra_data=extra_data, is_sign=True)
        return data
