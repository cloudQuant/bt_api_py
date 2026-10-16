"""Tests for the gateway config builder and retired runtime boundary."""

from __future__ import annotations

from typing import Any

import pytest
from bt_api_base.exceptions import ConfigurationError

from bt_api_base.gateway.config import GatewayConfig
from bt_api_base.gateway.registrar import GatewayRuntimeRegistrar
from bt_api_base.gateway.runtime import GatewayRuntime


class _FakeGatewayAdapter:
    constructions = 0

    def __init__(self, **kwargs: Any) -> None:
        type(self).constructions += 1
        self.kwargs = dict(kwargs)


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


def test_gateway_runtime_fails_closed_even_with_manual_adapter_registration() -> None:
    _FakeGatewayAdapter.constructions = 0
    GatewayRuntimeRegistrar.register_adapter("CTP", _FakeGatewayAdapter)
    config = GatewayConfig(
        exchange_type="CTP",
        asset_type="FUTURE",
        account_id="test_account",
        runtime_name="ctp-future-test_account",
        command_endpoint="inproc://command",
        event_endpoint="inproc://event",
        market_endpoint="inproc://market",
        selected_ctp_env="set2_7x24",
        td_address="tcp://td",
        md_address="tcp://md",
    )
    with pytest.raises(ConfigurationError) as exc_info:
        GatewayRuntime(config)

    assert "ServiceHost" in str(exc_info.value)
    assert "service.order_entry" in str(exc_info.value)
    assert _FakeGatewayAdapter.constructions == 0
