"""Tests for gateway/config.py."""

import sys

import pytest

from bt_api_py.gateway import config as gateway_config
from bt_api_py.gateway.config import GatewayConfig


class TestGatewayConfig:
    """Tests for GatewayConfig dataclass."""

    def test_init(self):
        """Test initialization."""
        config = GatewayConfig(exchange_type="binance", asset_type="spot", account_id="test")
        assert config.exchange_type == "BINANCE"
        assert config.asset_type == "SPOT"
        assert config.account_id == "test"

    def test_defaults(self):
        """Test default values."""
        config = GatewayConfig(exchange_type="binance", asset_type="spot", account_id="test")
        # On Windows, transport defaults to "tcp" instead of "ipc"
        if sys.platform.startswith("win"):
            assert config.transport == "tcp"
        else:
            assert config.transport == "ipc"

    def test_runtime_name_normalization(self):
        config = GatewayConfig(exchange_type="okx", asset_type="swap", account_id="User 01/alpha")
        assert config.runtime_name == "okx-swap-user-01-alpha"

    def test_from_kwargs_uses_aliases(self):
        config = GatewayConfig.from_kwargs(
            provider_exchange="ctp",
            user_id="investor-1",
            zmq_transport="tcp",
            gateway_command_timeout_sec="9.5",
            gateway_startup_timeout_sec="12.5",
            gateway_poll_timeout_ms="250",
        )
        assert config.exchange_type == "CTP"
        assert config.account_id == "investor-1"
        assert config.transport == "tcp"
        assert config.command_timeout_sec == 9.5
        assert config.startup_timeout_sec == 12.5
        assert config.poll_timeout_ms == 250

    def test_to_dict_contains_expected_fields(self):
        config = GatewayConfig(exchange_type="binance", asset_type="spot", account_id="test")
        data = config.to_dict()
        assert data["exchange_type"] == "BINANCE"
        assert data["asset_type"] == "SPOT"
        assert data["account_id"] == "test"
        # On Windows, transport is tcp, on other platforms it's ipc
        if sys.platform.startswith("win"):
            assert data["command_endpoint"].startswith("tcp://")
        else:
            assert data["command_endpoint"].startswith("ipc://")

    def test_build_endpoints_for_ipc_creates_runtime_directory(self, tmp_path):
        # Skip on Windows since IPC is automatically converted to TCP
        if sys.platform.startswith("win"):
            pytest.skip("IPC transport not supported on Windows")

        config = GatewayConfig(
            exchange_type="binance",
            asset_type="spot",
            account_id="acct",
            transport="ipc",
            base_dir=str(tmp_path),
        )
        runtime_root = tmp_path / config.runtime_name
        assert runtime_root.exists()
        assert config.command_endpoint == f"ipc://{runtime_root / 'command.sock'}"
        assert config.event_endpoint == f"ipc://{runtime_root / 'event.sock'}"
        assert config.market_endpoint == f"ipc://{runtime_root / 'market.sock'}"

    def test_build_endpoints_for_tcp(self, monkeypatch):
        gateway_config._TCP_PORT_ASSIGNMENTS.clear()
        gateway_config._TCP_RESERVED_BASE_PORTS.clear()
        monkeypatch.setattr(
            gateway_config, "_tcp_port_triplet_available", lambda base_port, host="127.0.0.1": True
        )
        config = GatewayConfig(
            exchange_type="okx",
            asset_type="swap",
            account_id="acct",
            transport="tcp",
        )
        assert config.command_endpoint.startswith("tcp://127.0.0.1:")
        base_port = int(config.command_endpoint.rsplit(":", 1)[1])
        assert config.event_endpoint == f"tcp://127.0.0.1:{base_port + 1}"
        assert config.market_endpoint == f"tcp://127.0.0.1:{base_port + 2}"

    def test_tcp_base_port_reuses_existing_assignment(self, monkeypatch):
        gateway_config._TCP_PORT_ASSIGNMENTS.clear()
        gateway_config._TCP_RESERVED_BASE_PORTS.clear()
        monkeypatch.setattr(
            gateway_config, "_tcp_port_triplet_available", lambda base_port, host="127.0.0.1": True
        )
        first = GatewayConfig(
            exchange_type="okx", asset_type="swap", account_id="acct", transport="tcp"
        )
        second = GatewayConfig(
            exchange_type="okx", asset_type="swap", account_id="acct", transport="tcp"
        )
        assert first.command_endpoint == second.command_endpoint

    def test_tcp_base_port_raises_when_no_ports_available(self, monkeypatch):
        gateway_config._TCP_PORT_ASSIGNMENTS.clear()
        gateway_config._TCP_RESERVED_BASE_PORTS.clear()
        monkeypatch.setattr(
            gateway_config, "_tcp_port_triplet_available", lambda base_port, host="127.0.0.1": False
        )
        config = GatewayConfig(
            exchange_type="okx",
            asset_type="swap",
            account_id="acct",
            transport="tcp",
            command_endpoint="preset://command",
            event_endpoint="preset://event",
            market_endpoint="preset://market",
        )
        with pytest.raises(RuntimeError):
            config._tcp_base_port()

    def test_windows_forces_tcp_transport(self, monkeypatch, tmp_path):
        monkeypatch.setattr(gateway_config.sys, "platform", "win32")
        gateway_config._TCP_PORT_ASSIGNMENTS.clear()
        gateway_config._TCP_RESERVED_BASE_PORTS.clear()
        monkeypatch.setattr(
            gateway_config, "_tcp_port_triplet_available", lambda base_port, host="127.0.0.1": True
        )
        config = GatewayConfig(
            exchange_type="binance",
            asset_type="spot",
            account_id="acct",
            transport="ipc",
            base_dir=str(tmp_path),
        )
        assert config.transport == "tcp"
