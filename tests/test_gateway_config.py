from bt_api_py.gateway.config import GatewayConfig
from bt_api_py.gateway.models import GatewayTick
from bt_api_py.gateway.protocol import dumps_message, loads_message


def test_gateway_config_builds_ipc_endpoints():
    config = GatewayConfig.from_kwargs(exchange_type="ctp", asset_type="future", account_id="acc-1")
    assert config.exchange_type == "CTP"
    assert config.asset_type == "FUTURE"
    assert config.command_endpoint.startswith("ipc://")
    assert config.event_endpoint.startswith("ipc://")
    assert config.market_endpoint.startswith("ipc://")


def test_gateway_protocol_roundtrip_tick_payload():
    tick = GatewayTick(timestamp=1.0, symbol="IF2506.CFFEX", price=100.0, volume=2.0)
    payload = loads_message(dumps_message(tick.to_dict()))
    assert payload["symbol"] == "IF2506.CFFEX"
    assert payload["price"] == 100.0
    assert payload["volume"] == 2.0
