from __future__ import annotations

import queue
from abc import ABC, abstractmethod
from typing import Any

from bt_api_py.logging_factory import get_logger


class BaseGatewayAdapter(ABC):
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = dict(kwargs)
        self.output_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.logger = get_logger("gateway")

    @abstractmethod
    def connect(self) -> None:
        ...

    @abstractmethod
    def disconnect(self) -> None:
        ...

    @abstractmethod
    def subscribe_symbols(self, symbols: list[str]) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_balance(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_positions(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    @abstractmethod
    def cancel_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        ...

    def get_bars(self, symbol: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        """Fetch historical OHLCV bars. Optional — default returns empty list."""
        return []

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        """Fetch contract/symbol specifications. Optional — default returns empty dict."""
        return {}

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Fetch current pending orders. Optional — default returns empty list."""
        return []

    def poll_output(self) -> tuple[str, Any] | None:
        try:
            return self.output_queue.get_nowait()
        except queue.Empty:
            return None

    def emit(self, channel: str, payload: Any) -> None:
        self.output_queue.put((channel, payload))
