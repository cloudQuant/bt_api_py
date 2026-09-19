"""Tests for gateway runtime configuration and health."""

from __future__ import annotations

import socket
import time
from typing import Any

from bt_api_base.gateway.config import GatewayConfig
from bt_api_base.gateway.registrar import GatewayRuntimeRegistrar
from bt_api_base.gateway.runtime import GatewayRuntime


class _FakeGatewayAdapter:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = dict(kwargs)
        self.connected = False
        self.disconnected = False
        self.orders: list[dict[str, Any]] = []

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False

    def subscribe_symbols(self, symbols: list[str]) -> dict[str, Any]:
        return {"symbols": list(symbols)}

    def get_balance(self) -> dict[str, Any]:
        return {"cash": 1000.0, "value": 1200.0}

    def get_positions(self) -> list[dict[str, Any]]:
        return [{"instrument": "RB2510", "volume": 1}]

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        order = {"id": "ord-1", "order_id": "ord-1", "status": "accepted", **payload}
        self.orders.append(order)
        return order

    def cancel_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"id": payload.get("order_id"), "status": "canceled"}

    def poll_output(self):
        return None

    def get_session_state(self) -> dict[str, Any]:
        return {
            "auth_state": "authenticated",
            "login_state": "logged_in",
            "front_id": 7,
            "session_id": 8801,
            "trading_day": "20260618",
        }


def _free_tcp_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{sock.getsockname()[1]}"


def setup_function() -> None:
    GatewayRuntimeRegistrar.clear()


def teardown_function() -> None:
    GatewayRuntimeRegistrar.clear()


def test_gateway_config_from_kwargs_allocates_named_runtime() -> None:
    config = GatewayConfig.from_kwargs(
        exchange_type="ctp",
        asset_type="future",
        account_id="test_account",
        runtime_name="ctp-future-test_account",
    )

    assert config.runtime_name == "ctp-future-test_account"
    assert config.exchange_type == "CTP"
    assert config.asset_type == "FUTURE"
    assert config.account_id == "test_account"
    assert config.command_endpoint.startswith("tcp://")
    assert config.event_endpoint.startswith("tcp://")
    assert config.market_endpoint.startswith("tcp://")


def test_gateway_runtime_starts_registered_adapter_and_reports_session_health() -> None:
    GatewayRuntimeRegistrar.register_adapter("CTP", _FakeGatewayAdapter)
    config = GatewayConfig.from_kwargs(
        exchange_type="CTP",
        asset_type="FUTURE",
        account_id="test_account",
        runtime_name="ctp-future-test_account",
        command_endpoint=_free_tcp_endpoint(),
        event_endpoint=_free_tcp_endpoint(),
        market_endpoint=_free_tcp_endpoint(),
        selected_ctp_env="set2_7x24",
        td_address="tcp://td",
        md_address="tcp://md",
    )
    runtime = GatewayRuntime(config)

    runtime.start_in_thread()
    try:
        snapshot = runtime.health.snapshot()
    finally:
        runtime.stop()

    assert snapshot["state"] == "running"
    assert snapshot["is_healthy"] is True
    assert snapshot["market_connection"] == "connected"
    assert snapshot["trade_connection"] == "connected"
    assert snapshot["auth_state"] == "authenticated"
    assert snapshot["login_state"] == "logged_in"
    assert snapshot["front_id"] == 7
    assert snapshot["session_id"] == 8801
    assert snapshot["trading_day"] == "20260618"
    assert snapshot["selected_ctp_env"] == "set2_7x24"
    assert snapshot["td_front"] == "tcp://td"
    assert snapshot["md_front"] == "tcp://md"
    assert snapshot["uptime_sec"] >= 0


def test_gateway_runtime_command_server_uses_adapter_methods() -> None:
    from bt_api_py.forwarding.client import ZmqForwardingClient

    GatewayRuntimeRegistrar.register_adapter("CTP", _FakeGatewayAdapter)
    config = GatewayConfig.from_kwargs(
        exchange_type="CTP",
        asset_type="FUTURE",
        account_id="test_account",
        runtime_name="ctp-future-test_account",
        command_endpoint=_free_tcp_endpoint(),
        event_endpoint=_free_tcp_endpoint(),
        market_endpoint=_free_tcp_endpoint(),
    )
    runtime = GatewayRuntime(config)
    client = ZmqForwardingClient(
        market_endpoint=config.market_endpoint,
        command_endpoint=config.command_endpoint,
        private_endpoint=config.event_endpoint,
        exchange="CTP",
        market_type="FUTURE",
        account_id="test_account",
        strategy_id="bt-test",
        command_timeout_ms=1000,
    )

    runtime.start_in_thread()
    try:
        time.sleep(0.05)
        client.connect()
        balance = client.get_balance()
        order = client.submit_order(
            {
                "data_name": "RB2510",
                "size": 1,
                "price": 3500.0,
                "side": "buy",
                "order_type": "limit",
            }
        )
    finally:
        client.disconnect()
        runtime.stop()

    assert balance["cash"] == 1000.0
    assert order["id"] == "ord-1"
