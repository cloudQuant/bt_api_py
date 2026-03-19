import sys
import zlib

import bt_api_py.gateway.config as gateway_config
from bt_api_py.gateway.config import GatewayConfig
from bt_api_py.gateway.models import GatewayTick
from bt_api_py.gateway.protocol import dumps_message, loads_message


def test_gateway_config_builds_local_endpoints():
    config = GatewayConfig.from_kwargs(exchange_type="ctp", asset_type="future", account_id="acc-1")
    assert config.exchange_type == "CTP"
    assert config.asset_type == "FUTURE"
    expected_prefix = "tcp://" if sys.platform.startswith("win") else "ipc://"
    assert config.command_endpoint.startswith(expected_prefix)
    assert config.event_endpoint.startswith(expected_prefix)
    assert config.market_endpoint.startswith(expected_prefix)


def test_gateway_protocol_roundtrip_tick_payload():
    tick = GatewayTick(timestamp=1.0, symbol="IF2506.CFFEX", price=100.0, volume=2.0)
    payload = loads_message(dumps_message(tick.to_dict()))
    assert payload["symbol"] == "IF2506.CFFEX"
    assert payload["price"] == 100.0
    assert payload["volume"] == 2.0


def test_gateway_config_tcp_base_port_avoids_simple_runtime_name_collisions():
    config_a = GatewayConfig.from_kwargs(
        exchange_type="mt5",
        asset_type="otc",
        account_id="acc-a",
        transport="tcp",
        gateway_runtime_name="ab",
    )
    config_b = GatewayConfig.from_kwargs(
        exchange_type="mt5",
        asset_type="otc",
        account_id="acc-b",
        transport="tcp",
        gateway_runtime_name="ba",
    )

    assert config_a.command_endpoint != config_b.command_endpoint
    assert config_a.event_endpoint != config_b.event_endpoint
    assert config_a.market_endpoint != config_b.market_endpoint


def test_gateway_config_tcp_base_port_varies_by_xdist_worker(monkeypatch):
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    config_a = GatewayConfig.from_kwargs(
        exchange_type="mt5",
        asset_type="otc",
        account_id="acc-a",
        transport="tcp",
        gateway_runtime_name="mt5-test-same",
    )

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    config_b = GatewayConfig.from_kwargs(
        exchange_type="mt5",
        asset_type="otc",
        account_id="acc-a",
        transport="tcp",
        gateway_runtime_name="mt5-test-same",
    )

    assert config_a.command_endpoint != config_b.command_endpoint
    assert config_a.event_endpoint != config_b.event_endpoint
    assert config_a.market_endpoint != config_b.market_endpoint


def test_gateway_config_tcp_base_port_probes_forward_on_hash_collision(monkeypatch):
    monkeypatch.setattr(gateway_config, "_TCP_PORT_ASSIGNMENTS", {})
    monkeypatch.setattr(gateway_config, "_TCP_RESERVED_BASE_PORTS", set())
    monkeypatch.setattr(zlib, "crc32", lambda data: 1234)
    config_a = GatewayConfig.from_kwargs(
        exchange_type="mt5",
        asset_type="otc",
        account_id="acc-a",
        transport="tcp",
        gateway_runtime_name="mt5-test-a",
    )
    config_b = GatewayConfig.from_kwargs(
        exchange_type="mt5",
        asset_type="otc",
        account_id="acc-b",
        transport="tcp",
        gateway_runtime_name="mt5-test-b",
    )

    assert config_a.command_endpoint != config_b.command_endpoint
    assert config_a.event_endpoint != config_b.event_endpoint
    assert config_a.market_endpoint != config_b.market_endpoint


def test_gateway_config_tcp_base_port_skips_busy_port_triplets(monkeypatch):
    monkeypatch.setattr(gateway_config, "_TCP_PORT_ASSIGNMENTS", {})
    monkeypatch.setattr(gateway_config, "_TCP_RESERVED_BASE_PORTS", set())
    monkeypatch.setattr(zlib, "crc32", lambda data: 1234)
    busy_base_port = 32000 + (1234 % 10000) * 3
    monkeypatch.setattr(
        gateway_config,
        "_tcp_port_triplet_available",
        lambda base_port, host="127.0.0.1": base_port != busy_base_port,
    )

    config = GatewayConfig.from_kwargs(
        exchange_type="mt5",
        asset_type="otc",
        account_id="acc-busy",
        transport="tcp",
        gateway_runtime_name="mt5-test-busy",
    )

    assert config.command_endpoint == f"tcp://127.0.0.1:{busy_base_port + 3}"
    assert config.event_endpoint == f"tcp://127.0.0.1:{busy_base_port + 4}"
    assert config.market_endpoint == f"tcp://127.0.0.1:{busy_base_port + 5}"
