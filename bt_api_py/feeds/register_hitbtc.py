"""
HitBTC Feed Registration

This module registers HitBTC exchange feeds with the bt_api_py framework.
Provides registration for both spot trading feeds.

Usage:
    from bt_api_py.feeds.register_hitbtc import register_hitbtc

    # Register HitBTC spot trading
    register_hitbtc()
"""

from bt_api_py.containers.exchanges.hitbtc_exchange_data import HitBtcSpotExchangeData
from bt_api_py.feeds.registry import register
from bt_api_py.feeds.live_hitbtc.spot import HitBtcSpotRequestData


@register("HITBTC_SPOT")
class HitBtcSpotFeedRegistration:
    """HitBTC Spot Trading Feed Registration"""

    @classmethod
    def get_feed_class(cls):
        """Return the HitBTC Spot feed class"""
        return HitBtcSpotRequestData

    @classmethod
    def get_exchange_data_class(cls):
        """Return the HitBTC Spot exchange data class"""
        return HitBtcSpotExchangeData

    @classmethod
    def get_asset_type(cls):
        """Return the asset type"""
        return "SPOT"


# For backward compatibility
@register("HITBTC")
class HitBtcFeedRegistration:
    """Legacy HitBTC Feed Registration (for backward compatibility)"""

    @classmethod
    def get_feed_class(cls):
        """Return the HitBTC feed class"""
        return HitBtcSpotRequestData

    @classmethod
    def get_exchange_data_class(cls):
        """Return the HitBTC exchange data class"""
        return HitBtcSpotExchangeData

    @classmethod
    def get_asset_type(cls):
        """Return the asset type"""
        return "SPOT"


def register_hitbtc():
    """
    Register HitBTC feeds with the framework.

    This function registers all available HitBTC feed implementations:
    - HitBTC Spot Trading

    Returns:
        None
    """
    # Import and register all HitBTC feeds
    from bt_api_py.feeds.registry import _registry

    # Register HitBTC Spot
    _registry["HITBTC_SPOT"] = HitBtcSpotFeedRegistration

    # Register legacy name
    _registry["HITBTC"] = HitBtcFeedRegistration

    print("HitBTC feeds registered successfully!")
    print("Available feeds:")
    print("- HITBTC_SPOT: HitBTC Spot Trading")
    print("- HITBTC: Legacy HitBTC Spot Trading (backward compatibility)")


if __name__ == "__main__":
    register_hitbtc()