from bt_api_py.gateway.config import GatewayConfig

__all__ = [
    "ConnectionState",
    "GatewayClient",
    "GatewayConfig",
    "GatewayHealth",
    "GatewayProcess",
    "GatewayRuntime",
    "GatewayState",
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
    if name == "GatewayHealth":
        from bt_api_py.gateway.health import GatewayHealth

        return GatewayHealth
    if name == "GatewayState":
        from bt_api_py.gateway.health import GatewayState

        return GatewayState
    if name == "ConnectionState":
        from bt_api_py.gateway.health import ConnectionState

        return ConnectionState
    if name == "GatewayProcess":
        from bt_api_py.gateway.process import GatewayProcess

        return GatewayProcess
    raise AttributeError(name)
