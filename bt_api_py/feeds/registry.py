"""
Feed Registry

This module provides a registry system for exchange feeds.
Allows exchange feeds to be registered and discovered by the framework.
"""

from typing import Dict, Type, Any, Callable


# Global registry to store all registered feeds
_registry: Dict[str, Any] = {}


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
    def decorator(cls):
        if name in _registry:
            print(f"Warning: Overriding existing feed registration for '{name}'")

        _registry[name] = cls
        print(f"Registered feed: {name} -> {cls}")
        return cls

    return decorator


def get_feed(name: str) -> Type:
    """
    Get a registered feed class by name.

    Args:
        name: Feed identifier

    Returns:
        Feed class

    Raises:
        KeyError: If feed is not registered
    """
    if name not in _registry:
        raise KeyError(f"Feed '{name}' is not registered")

    return _registry[name]


def get_all_feeds() -> Dict[str, Type]:
    """
    Get all registered feeds.

    Returns:
        Dictionary of feed name to feed class
    """
    return _registry.copy()


def is_registered(name: str) -> bool:
    """
    Check if a feed is registered.

    Args:
        name: Feed identifier

    Returns:
        True if registered, False otherwise
    """
    return name in _registry


def unregister(name: str) -> bool:
    """
    Unregister a feed.

    Args:
        name: Feed identifier

    Returns:
        True if successfully unregistered, False if not registered
    """
    if name in _registry:
        del _registry[name]
        return True
    return False


def clear_registry():
    """Clear all registered feeds."""
    _registry.clear()


def list_feeds():
    """List all registered feeds."""
    print("Registered feeds:")
    for name, cls in _registry.items():
        print(f"  - {name}: {cls}")


# Legacy functions for backward compatibility
def register_feed(name: str, feed_class: Type):
    """Legacy function to register a feed (deprecated)."""
    _registry[name] = feed_class


def get_registry() -> Dict[str, Type]:
    """Legacy function to get registry (deprecated)."""
    return _registry.copy()


# Initialize with built-in exchanges
def initialize_default_feeds():
    """Initialize with default exchanges."""
    try:
        # Import and register default exchanges
        import bt_api_py.feeds.live_binance
        import bt_api_py.feeds.live_okx
        import bt_api_py.feeds.live_hitbtc
    except ImportError:
        pass


# Initialize on import
initialize_default_feeds()