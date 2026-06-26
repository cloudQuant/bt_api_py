import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_light_import_skips_gateway_unneeded_provider_modules():
    repo_root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        """
        import json
        import sys

        import bt_api_py
        from bt_api_py.gateway.client import GatewayClient

        heavy = [
            name
            for name in sys.modules
            if name.startswith(("bt_api_py.ctp", "bt_api_ctp", "pandas", "numpy", "scipy", "pyarrow"))
        ]
        print(json.dumps({
            "btapi_bound": "BtApi" in vars(bt_api_py),
            "gateway_client": GatewayClient.__name__,
            "heavy": heavy,
        }))
        """
    )
    env = os.environ.copy()
    env["BT_API_PY_LIGHT_IMPORT"] = "1"
    env["PYTHONPATH"] = str(repo_root)

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(result.stdout)

    assert payload["gateway_client"] == "GatewayClient"
    assert payload["btapi_bound"] is False
    assert payload["heavy"] == []


def test_forwarding_import_exposes_zmq_gateway_client():
    from bt_api_py.forwarding import ForwardingClient, ZmqForwardingClient

    client = ZmqForwardingClient(
        market_endpoint="ipc://market",
        command_endpoint="ipc://command",
        private_endpoint="ipc://event",
        strategy_id="unit-1",
        command_timeout_ms=2500,
        event_cache_size=17,
    )

    assert client.market_endpoint == "ipc://market"
    assert client.command_endpoint == "ipc://command"
    assert client.event_endpoint == "ipc://event"
    assert client.strategy_id == "unit-1"
    assert client.command_timeout_sec == 2.5
    assert client.max_events == 17
    assert ForwardingClient.__name__ == "ForwardingClient"


def test_forwarding_bus_commands_include_strategy_id():
    from bt_api_py.forwarding import ForwardingClient

    class FakeBus:
        def __init__(self) -> None:
            self.calls = []

        def send_command(self, command, payload):
            self.calls.append((command, payload))
            return {"ok": True}

    bus = FakeBus()
    client = ForwardingClient(bus=bus, strategy_id="unit-1")

    assert client._send_command("place_order", {"symbol": "XAUUSD"}) == {"ok": True}
    assert bus.calls == [("place_order", {"symbol": "XAUUSD", "strategy_id": "unit-1"})]


def test_forwarding_bus_response_registers_owned_order_identity():
    from bt_api_py.forwarding import ForwardingClient

    class FakeBus:
        def send_command(self, _command, _payload):
            return {"orderId": "venue-1", "clientOrderId": "client-1"}

    client = ForwardingClient(bus=FakeBus(), strategy_id="unit-1")

    client._send_command("place_order", {"symbol": "XAUUSD", "client_order_id": "client-1"})
    client._store_event({"kind": "trade", "orderId": "venue-1"})
    client._store_event({"kind": "trade", "orderId": "other"})

    assert list(client._event_queue) == [{"kind": "trade", "orderId": "venue-1"}]
