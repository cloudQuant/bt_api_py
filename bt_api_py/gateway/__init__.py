from bt_api_py.gateway.config import GatewayConfig

__all__ = [
    "GatewayClient",
    "GatewayConfig",
    "GatewayRuntime",
    "OrderIdentityMap",
    "OrderRefAllocator",
    "SubscriptionManager",
    "TickWriter",
]


def __getattr__(name: str):
    if name == "GatewayClient":
        from bt_api_py.gateway.client import GatewayClient

        return GatewayClient
    if name == "GatewayRuntime":
        from bt_api_py.gateway.runtime import GatewayRuntime

        return GatewayRuntime
    if name == "SubscriptionManager":
        from bt_api_py.gateway.subscription_manager import SubscriptionManager

        return SubscriptionManager
    if name == "OrderIdentityMap":
        from bt_api_py.gateway.order_identity_map import OrderIdentityMap

        return OrderIdentityMap
    if name == "OrderRefAllocator":
        from bt_api_py.gateway.order_ref_allocator import OrderRefAllocator

        return OrderRefAllocator
    if name == "TickWriter":
        from bt_api_py.gateway.storage.tick_writer import TickWriter

        return TickWriter
    raise AttributeError(name)
