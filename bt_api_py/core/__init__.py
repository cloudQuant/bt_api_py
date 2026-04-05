"""
Modern architecture for bt_api_py with dependency injection and microservices design.
"""

from __future__ import annotations

from .async_context import AsyncContextManager
from .dependency_injection import Container, DIContainer, injectable, singleton
from .interfaces import (
    IAsyncExchangeAdapter,
    ICache,
    IConnectionManager,
    IEventBus,
    IExchangeAdapter,
    IRateLimiter,
)
from .services import (
    AccountService,
    CacheService,
    ConnectionService,
    EventService,
    MarketDataService,
    RateLimitService,
    TradingService,
)

__all__ = [
    "Container",
    "ConnectionService",
    "MarketDataService",
    "TradingService",
    "AccountService",
    "EventService",
    "CacheService",
    "RateLimitService",
    "IExchangeAdapter",
    "IAsyncExchangeAdapter",
    "IConnectionManager",
    "IEventBus",
    "ICache",
    "IRateLimiter",
    "injectable",
    "singleton",
    "DIContainer",
    "AsyncContextManager",
]
