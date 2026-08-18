"""Tests for the safe-by-default gateway configuration policy (Task 0.2).

Before the P0 authentication work (Iteration 4), the forwarding gateway must
default to read-only loopback/IPC. Remote TCP or write-enabled operation needs
an explicit, opt-in safe policy; otherwise the configuration is rejected.
"""

from __future__ import annotations

import pytest

from bt_api_py.gateway.config import GatewayConfig
from bt_api_py.gateway.safety import GatewaySafetyError, is_loopback_or_ipc


def test_gateway_rejects_remote_or_write_enabled_config_without_explicit_safe_policy() -> None:
    with pytest.raises(GatewaySafetyError):
        GatewayConfig(command_endpoint="tcp://0.0.0.0:7002", enable_trading=True)


def test_gateway_defaults_to_read_only_loopback_mode() -> None:
    config = GatewayConfig.local_defaults()
    assert config.enable_trading is False
    assert config.is_loopback_or_ipc is True


def test_gateway_rejects_non_loopback_without_allow_remote() -> None:
    with pytest.raises(GatewaySafetyError):
        GatewayConfig(
            command_endpoint="tcp://0.0.0.0:7002",
            market_endpoint="tcp://0.0.0.0:7001",
            private_endpoint="tcp://0.0.0.0:7003",
        )


def test_gateway_allows_remote_when_explicitly_authorized_but_stays_read_only() -> None:
    config = GatewayConfig(
        command_endpoint="tcp://0.0.0.0:7002",
        market_endpoint="tcp://0.0.0.0:7001",
        private_endpoint="tcp://0.0.0.0:7003",
        allow_remote=True,
    )
    assert config.enable_trading is False
    assert config.is_loopback_or_ipc is False


def test_gateway_rejects_shared_private_endpoint_by_default() -> None:
    with pytest.raises(GatewaySafetyError):
        GatewayConfig(
            command_endpoint="tcp://127.0.0.1:7002",
            market_endpoint="tcp://127.0.0.1:7001",
            private_endpoint=None,
        )


def test_is_loopback_or_ipc_endpoint_classification() -> None:
    assert is_loopback_or_ipc("tcp://127.0.0.1:7001")
    assert is_loopback_or_ipc("tcp://localhost:7001")
    assert is_loopback_or_ipc("ipc:///tmp/gateway.ipc")
    assert not is_loopback_or_ipc("tcp://0.0.0.0:7001")
    assert not is_loopback_or_ipc("tcp://192.168.1.10:7001")


def test_zmq_runtime_rejects_write_without_explicit_policy() -> None:
    from bt_api_py.brokers.mock import MockBrokerAdapter
    from bt_api_py.forwarding.service import ZmqForwardingRuntime

    with pytest.raises(GatewaySafetyError):
        ZmqForwardingRuntime(
            MockBrokerAdapter(),
            market_endpoint="tcp://127.0.0.1:7001",
            command_endpoint="tcp://127.0.0.1:7002",
            private_endpoint="tcp://127.0.0.1:7003",
            enable_trading=True,
        )
