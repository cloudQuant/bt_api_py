"""
Feed Registry

This module provides a backward-compatible registry facade for exchange feeds.
Prefer `bt_api_py.registry.ExchangeRegistry` for new code.
"""

from collections.abc import Callable

from bt_api_py.logging_factory import get_logger
from bt_api_py.registry import ExchangeRegistry

__all__ = [
    "register",
    "get_feed",
    "get_all_feeds",
    "is_registered",
    "unregister",
    "clear_registry",
    "list_feeds",
    "register_feed",
    "get_registry",
    "initialize_default_feeds",
]

_logger = get_logger("registry")
_legacy_registry: dict[str, type] = {}


def register(name: str) -> Callable:
    """
    Decorator to register an exchange feed with the framework.

    Args:
        name: Unique identifier for the feed (e.g., "BINANCE_SPOT", "OKX_SWAP")

    Returns:
        Decorator function

    Example:
        @register("BINANCE_SPOT")
        class BinanceSpotFeed:
            pass
    """

    def decorator(cls: type) -> type:
        existing = ExchangeRegistry.get_feed_class(name)
        if existing is not None and existing is not cls:
            _logger.warning(f"Overriding existing feed registration for '{name}'")

        ExchangeRegistry.register_feed(name, cls)
        _legacy_registry[name] = cls
        _logger.debug(f"Registered feed: {name} -> {cls.__name__}")
        return cls

    return decorator


def get_feed(name: str) -> type:
    """
    Get a registered feed class by name.

    Args:
        name: Feed identifier

    Returns:
        Feed class

    Raises:
        KeyError: If feed is not registered
    """
    feed_class = ExchangeRegistry.get_feed_class(name)
    if feed_class is None:
        raise KeyError(f"Feed '{name}' is not registered")

    return feed_class


def get_all_feeds() -> dict[str, type]:
    """
    Get all registered feeds.

    Returns:
        Dictionary of feed name to feed class
    """
    return ExchangeRegistry.get_feed_classes()


def is_registered(name: str) -> bool:
    """
    Check if a feed is registered.

    Args:
        name: Feed identifier

    Returns:
        True if registered, False otherwise
    """
    return ExchangeRegistry.get_feed_class(name) is not None


def unregister(name: str) -> bool:
    """
    Unregister a feed.

    Args:
        name: Feed identifier

    Returns:
        True if successfully unregistered, False if not registered
    """
    _legacy_registry.pop(name, None)
    return ExchangeRegistry.unregister_feed(name)


def clear_registry():
    """Clear all registered feeds."""
    _legacy_registry.clear()
    ExchangeRegistry.clear()


def list_feeds():
    """List all registered feeds."""
    for name, cls in get_all_feeds().items():
        _logger.info(f"Registered feed: {name} -> {cls.__name__}")


# Legacy functions for backward compatibility
def register_feed(name: str, feed_class: type):
    """Legacy function to register a feed (deprecated)."""
    ExchangeRegistry.register_feed(name, feed_class)
    _legacy_registry[name] = feed_class


def get_registry() -> dict[str, type]:
    """Legacy function to get registry (deprecated)."""
    return get_all_feeds()


def initialize_default_feeds():
    """Explicitly initialize the legacy default feeds."""
    try:
        import bt_api_py.exchange_registers.register_binance
        import bt_api_py.exchange_registers.register_hitbtc
        import bt_api_py.exchange_registers.register_okx  # noqa: F401
    except ImportError as exc:
        _logger.warning(f"Failed to initialize default feeds: {exc}")

    return get_all_feeds()
