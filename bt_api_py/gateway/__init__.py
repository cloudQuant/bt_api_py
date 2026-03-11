from bt_api_py.gateway.config import GatewayConfig

__all__ = ["GatewayClient", "GatewayConfig", "GatewayRuntime"]


def __getattr__(name: str):
    if name == "GatewayClient":
        from bt_api_py.gateway.client import GatewayClient

        return GatewayClient
    if name == "GatewayRuntime":
        from bt_api_py.gateway.runtime import GatewayRuntime

        return GatewayRuntime
    raise AttributeError(name)
