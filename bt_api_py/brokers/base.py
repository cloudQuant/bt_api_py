"""Module-level docstring."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from bt_api_py.brokers.errors import BrokerError, BrokerErrorCode
from bt_api_py.brokers.types import (
    AccountSnapshot,
    BrokerCapabilities,
    BrokerEvent,
    CancelOrderRequest,
    OrderRequest,
    OrderSnapshot,
    PositionSnapshot,
)


class BrokerAdapter(ABC):
    """Class BrokerAdapter"""

    @abstractmethod
    async def connect(self) -> bool:
        """connect method"""
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> bool:
        """disconnect method"""
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """health method"""
        raise NotImplementedError

    @abstractmethod
    def capabilities(self) -> BrokerCapabilities:
        """capabilities method"""
        raise NotImplementedError

    @abstractmethod
    async def list_accounts(self) -> list[AccountSnapshot]:
        """list_accounts method"""
        raise NotImplementedError

    @abstractmethod
    async def get_account(self, account_id: str) -> AccountSnapshot:
        """get_account method"""
        raise NotImplementedError

    @abstractmethod
    async def list_positions(self, account_id: str) -> list[PositionSnapshot]:
        """list_positions method"""
        raise NotImplementedError

    @abstractmethod
    async def list_orders(self, account_id: str) -> list[OrderSnapshot]:
        """list_orders method"""
        raise NotImplementedError

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderSnapshot:
        """place_order method"""
        raise NotImplementedError

    @abstractmethod
    async def cancel_order(self, request: CancelOrderRequest) -> OrderSnapshot:
        """cancel_order method"""
        raise NotImplementedError

    @abstractmethod
    async def get_quote(self, symbol: str) -> dict[str, Any]:
        """get_quote method"""
        raise NotImplementedError

    async def stream_events(self) -> AsyncIterator[tuple[BrokerEvent, dict[str, Any]]]:
        """stream_events method"""
        if False:
            yield BrokerEvent.ERROR, {}

    @staticmethod
    def not_supported(message: str) -> BrokerError:
        """not_supported method"""
        return BrokerError(BrokerErrorCode.NOT_SUPPORTED, message, retryable=False)
