"""Compatibility forwarding clients for Backtrader gateway integration."""

from __future__ import annotations

from typing import Any

from bt_api_py.gateway.client import GatewayClient


class ZmqForwardingClient(GatewayClient):
    """ZeroMQ forwarding client compatible with the legacy Backtrader store API."""

    def __init__(
        self,
        *,
        market_endpoint: str,
        command_endpoint: str,
        private_endpoint: str | None = None,
        exchange: str = "",
        market_type: str = "",
        account_id: str = "",
        strategy_id: str = "default",
        command_timeout_ms: int = 2000,
        event_cache_size: int = 4096,
        **kwargs: Any,
    ) -> None:
        config = dict(kwargs)
        config.setdefault("gateway_market_endpoint", market_endpoint)
        config.setdefault("gateway_command_endpoint", command_endpoint)
        config.setdefault("gateway_event_endpoint", private_endpoint or "")
        config.setdefault("exchange", exchange)
        config.setdefault("market_type", market_type)
        config.setdefault("account_id", account_id)
        config.setdefault("strategy_id", strategy_id)
        config.setdefault("gateway_command_timeout_sec", max(command_timeout_ms, 1) / 1000.0)
        config.setdefault("gateway_max_events", event_cache_size)
        super().__init__(**config)


class ForwardingClient(GatewayClient):
    """In-process forwarding placeholder with a clear unsupported-bus error.

    The current live gateway path uses :class:`ZmqForwardingClient`.  This class
    keeps the historical import surface available and can delegate to simple bus
    objects that expose a command-style method.
    """

    def __init__(
        self,
        *,
        bus: Any = None,
        exchange: str = "",
        market_type: str = "",
        account_id: str = "",
        strategy_id: str = "default",
        replay: int = 0,
        command_timeout: float = 2.0,
        event_cache_size: int = 4096,
        **kwargs: Any,
    ) -> None:
        config = dict(kwargs)
        config.setdefault("exchange", exchange)
        config.setdefault("market_type", market_type)
        config.setdefault("account_id", account_id)
        config.setdefault("strategy_id", strategy_id)
        config.setdefault("gateway_command_timeout_sec", command_timeout)
        config.setdefault("gateway_max_events", event_cache_size)
        super().__init__(**config)
        self.bus = bus
        self.replay = int(replay or 0)

    def _send_command(self, command: str, payload: dict[str, Any] | None = None) -> Any:
        if self.bus is None:
            return super()._send_command(command, payload)

        command_payload = self._payload_with_strategy(payload)
        self._remember_owned_event_ids(command_payload)
        for method_name in ("send_command", "command", "request", "dispatch"):
            method = getattr(self.bus, method_name, None)
            if callable(method):
                result = method(command, command_payload)
                self._remember_owned_event_ids(result)
                return result
        raise RuntimeError("forwarding bus does not expose a command method")


__all__ = ["ForwardingClient", "ZmqForwardingClient"]
