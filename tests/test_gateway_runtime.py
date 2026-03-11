import queue
import time
import uuid
from collections import deque
from typing import Any

import pytest

from bt_api_py.gateway.client import GatewayClient
from bt_api_py.gateway.adapters import (
    BinanceGatewayAdapter,
    CtpGatewayAdapter,
    IbWebGatewayAdapter,
    OkxGatewayAdapter,
)
from bt_api_py.gateway.adapters.binance_adapter import _normalize_asset_type as bn_normalize
from bt_api_py.gateway.adapters.okx_adapter import _normalize_asset_type as okx_normalize
from bt_api_py.gateway.config import GatewayConfig
from bt_api_py.gateway.models import GatewayTick
from bt_api_py.gateway.protocol import CHANNEL_EVENT, CHANNEL_MARKET
from bt_api_py.gateway.runtime import GatewayRuntime


class FakeGatewayAdapter:
    def __init__(self) -> None:
        self.connected = False
        self.outputs: deque[tuple[str, Any]] = deque()
        self.subscriptions: list[str] = []

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def subscribe_symbols(self, symbols: list[str]) -> dict[str, Any]:
        self.subscriptions.extend(symbols)
        return {"symbols": list(symbols)}

    def get_balance(self) -> dict[str, Any]:
        return {"cash": 1000.0, "equity": 1000.0}

    def get_positions(self) -> list[dict[str, Any]]:
        return [{"instrument": "IF2506.CFFEX", "volume": 1}]

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "order_id": "ord-1",
            "external_order_id": "ord-1",
            **payload,
        }

    def cancel_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "order_id": payload.get("order_id") or "ord-1",
            "status": "canceled",
        }

    def poll_output(self) -> tuple[str, Any] | None:
        if not self.outputs:
            return None
        return self.outputs.popleft()

    def emit(self, channel: str, payload: Any) -> None:
        self.outputs.append((channel, payload))


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.05) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_gateway_runtime_registry_contains_all_exchanges():
    assert GatewayRuntime.get_adapter_class("ctp") is CtpGatewayAdapter
    assert GatewayRuntime.get_adapter_class("IB_WEB") is IbWebGatewayAdapter
    assert GatewayRuntime.get_adapter_class("BINANCE") is BinanceGatewayAdapter
    assert GatewayRuntime.get_adapter_class("binance") is BinanceGatewayAdapter
    assert GatewayRuntime.get_adapter_class("OKX") is OkxGatewayAdapter
    assert GatewayRuntime.get_adapter_class("okx") is OkxGatewayAdapter


def test_gateway_runtime_register_adapter_supports_extension():
    class CustomGatewayAdapter(FakeGatewayAdapter):
        pass

    GatewayRuntime.register_adapter("customx", CustomGatewayAdapter)

    assert GatewayRuntime.get_adapter_class("CUSTOMX") is CustomGatewayAdapter


def test_gateway_runtime_client_ipc_roundtrip(monkeypatch, tmp_path):
    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)
    gateway_base_dir = "/tmp/btgw"
    runtime_name = f"gw-{uuid.uuid4().hex[:8]}"

    config = GatewayConfig.from_kwargs(
        exchange_type="ctp",
        asset_type="future",
        account_id="acc-1",
        gateway_base_dir=gateway_base_dir,
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
    )
    runtime = GatewayRuntime(
        config,
        exchange_type="CTP",
        asset_type="FUTURE",
        account_id="acc-1",
        gateway_base_dir=gateway_base_dir,
        gateway_runtime_name=runtime_name,
    )
    client = GatewayClient(
        exchange_type="CTP",
        asset_type="FUTURE",
        account_id="acc-1",
        gateway_command_endpoint=config.command_endpoint,
        gateway_event_endpoint=config.event_endpoint,
        gateway_market_endpoint=config.market_endpoint,
        gateway_base_dir=gateway_base_dir,
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
        gateway_start_local_runtime=False,
    )

    runtime.start_in_thread()
    try:
        client.connect()

        assert adapter.connected is True
        assert client.get_balance()["cash"] == 1000.0
        assert client.get_positions()[0]["instrument"] == "IF2506.CFFEX"
        assert client.subscribe(["IF2506.CFFEX"]) == {"subscribed": ["IF2506.CFFEX"]}

        order = client.submit_order({"data_name": "IF2506.CFFEX", "volume": 1})
        assert order["order_id"] == "ord-1"
        assert order["data_name"] == "IF2506.CFFEX"

        cancel = client.cancel_order("ord-1", dataname="IF2506.CFFEX")
        assert cancel["status"] == "canceled"

        adapter.emit(
            CHANNEL_MARKET,
            GatewayTick(timestamp=time.time(), symbol="IF2506.CFFEX", price=100.0, volume=2.0),
        )
        assert _wait_until(lambda: client.has_pending_tick("IF2506.CFFEX"))
        tick = client.poll_tick("IF2506.CFFEX")
        assert tick is not None
        assert tick.price == 100.0

        adapter.emit(
            CHANNEL_EVENT,
            {
                "kind": "order",
                "order_id": "ord-1",
                "external_order_id": "ord-1",
                "status": "filled",
            },
        )
        update = None
        deadline = time.time() + 2.0
        while time.time() < deadline:
            update = client.poll_broker_update()
            if update is not None:
                break
            time.sleep(0.05)
        assert update is not None
        assert update["status"] == "filled"
    finally:
        client.disconnect()
        runtime.stop()


# ---------------------------------------------------------------------------
# Binance / OKX adapter unit tests
# ---------------------------------------------------------------------------


class TestBinanceAdapterAssetNormalization:
    def test_swap_default(self):
        assert bn_normalize(None) == "SWAP"
        assert bn_normalize("") == "SWAP"

    def test_swap_explicit(self):
        assert bn_normalize("SWAP") == "SWAP"
        assert bn_normalize("swap") == "SWAP"

    def test_spot(self):
        assert bn_normalize("SPOT") == "SPOT"
        assert bn_normalize("spot") == "SPOT"

    def test_future_maps_to_swap(self):
        assert bn_normalize("FUTURE") == "SWAP"
        assert bn_normalize("FUT") == "SWAP"


class TestOkxAdapterAssetNormalization:
    def test_swap_default(self):
        assert okx_normalize(None) == "SWAP"

    def test_spot(self):
        assert okx_normalize("SPOT") == "SPOT"

    def test_future_maps_to_swap(self):
        assert okx_normalize("FUTURE") == "SWAP"
        assert okx_normalize("FUT") == "SWAP"


def test_binance_adapter_instantiation():
    """BinanceGatewayAdapter can be constructed without network access."""
    adapter = BinanceGatewayAdapter(
        asset_type="SWAP",
        public_key="test_key",
        private_key="test_secret",
    )
    assert adapter.asset_type == "SWAP"
    assert adapter.running is False
    assert adapter.kwargs["exchange_name"] == "BINANCE"
    assert adapter.feed is not None


def test_okx_adapter_instantiation():
    """OkxGatewayAdapter can be constructed without network access."""
    adapter = OkxGatewayAdapter(
        asset_type="SWAP",
        public_key="test_key",
        private_key="test_secret",
        passphrase="test_pass",
    )
    assert adapter.asset_type == "SWAP"
    assert adapter.running is False
    assert adapter.kwargs["exchange_name"] == "OKX"
    assert adapter.feed is not None


def test_binance_adapter_emit_ticker():
    """BinanceGatewayAdapter._emit_ticker produces a GatewayTick on the market channel."""
    from bt_api_py.containers.tickers.binance_ticker import BinanceWssTickerData

    adapter = BinanceGatewayAdapter(
        asset_type="SWAP",
        public_key="k",
        private_key="s",
    )
    ticker_info = {
        "e": "bookTicker",
        "s": "BTCUSDT",
        "E": 1700000000000,
        "b": "42000.0",
        "a": "42001.0",
        "B": "1.5",
        "A": "2.0",
    }
    ticker = BinanceWssTickerData(ticker_info, "BTCUSDT", "SWAP", True)
    adapter._emit_ticker(ticker)

    result = adapter.poll_output()
    assert result is not None
    channel, payload = result
    assert channel == CHANNEL_MARKET
    assert isinstance(payload, GatewayTick)
    assert payload.symbol == "BTCUSDT"
    assert payload.exchange == "BINANCE"
    assert payload.bid_price == 42000.0
    assert payload.ask_price == 42001.0


def test_okx_adapter_emit_ticker():
    """OkxGatewayAdapter._emit_ticker produces a GatewayTick on the market channel."""
    from bt_api_py.containers.tickers.okx_ticker import OkxTickerData

    adapter = OkxGatewayAdapter(
        asset_type="SWAP",
        public_key="k",
        private_key="s",
        passphrase="p",
    )
    ticker_info = {
        "instId": "BTC-USDT-SWAP",
        "ts": "1700000000000",
        "bidPx": "42000.0",
        "askPx": "42001.0",
        "bidSz": "1.5",
        "askSz": "2.0",
        "last": "42000.5",
        "lastSz": "0.1",
    }
    ticker = OkxTickerData(ticker_info, "BTC-USDT-SWAP", "SWAP", True)
    adapter._emit_ticker(ticker)

    result = adapter.poll_output()
    assert result is not None
    channel, payload = result
    assert channel == CHANNEL_MARKET
    assert isinstance(payload, GatewayTick)
    assert payload.symbol == "BTC-USDT-SWAP"
    assert payload.exchange == "OKX"
    assert payload.bid_price == 42000.0
    assert payload.ask_price == 42001.0
    assert payload.price == 42000.5


def test_binance_adapter_dispatch_routes_ticker():
    """_dispatch_item routes BinanceWssTickerData to market channel."""
    from bt_api_py.containers.tickers.binance_ticker import BinanceWssTickerData

    adapter = BinanceGatewayAdapter(
        asset_type="SWAP",
        public_key="k",
        private_key="s",
    )
    ticker_info = {"e": "bookTicker", "s": "ETHUSDT", "E": 1700000000000,
                   "b": "2000.0", "a": "2001.0", "B": "10", "A": "20"}
    ticker = BinanceWssTickerData(ticker_info, "ETHUSDT", "SWAP", True)
    adapter._dispatch_item(ticker)

    result = adapter.poll_output()
    assert result is not None
    assert result[0] == CHANNEL_MARKET
    assert result[1].symbol == "ETHUSDT"


def test_okx_adapter_dispatch_routes_ticker():
    """_dispatch_item routes OkxTickerData to market channel."""
    from bt_api_py.containers.tickers.okx_ticker import OkxTickerData

    adapter = OkxGatewayAdapter(
        asset_type="SWAP",
        public_key="k",
        private_key="s",
        passphrase="p",
    )
    ticker_info = {"instId": "ETH-USDT-SWAP", "ts": "1700000000000",
                   "bidPx": "2000.0", "askPx": "2001.0", "bidSz": "10",
                   "askSz": "20", "last": "2000.5", "lastSz": "1"}
    ticker = OkxTickerData(ticker_info, "ETH-USDT-SWAP", "SWAP", True)
    adapter._dispatch_item(ticker)

    result = adapter.poll_output()
    assert result is not None
    assert result[0] == CHANNEL_MARKET
    assert result[1].symbol == "ETH-USDT-SWAP"


def test_backtrader_gateway_wrapper_integration(monkeypatch, tmp_path):
    """Verify the backtrader CtpGatewayClientWrapper works end-to-end with GatewayRuntime.

    This simulates the path backtrader's BtApiStore/BtApiFeed/BtApiBroker would take:
    subscribe -> supports_live_ticks -> poll_tick -> place_order -> poll_broker_update.
    """
    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)
    runtime_name = f"bt-{uuid.uuid4().hex[:8]}"

    config = GatewayConfig.from_kwargs(
        exchange_type="ctp",
        asset_type="future",
        account_id="bt-acc",
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
    )
    runtime = GatewayRuntime(
        config, exchange_type="CTP", asset_type="FUTURE", account_id="bt-acc",
        gateway_base_dir="/tmp/btgw", gateway_runtime_name=runtime_name,
    )
    client = GatewayClient(
        exchange_type="CTP", asset_type="FUTURE", account_id="bt-acc",
        gateway_command_endpoint=config.command_endpoint,
        gateway_event_endpoint=config.event_endpoint,
        gateway_market_endpoint=config.market_endpoint,
        gateway_base_dir="/tmp/btgw", gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10, gateway_command_timeout_sec=1.0,
        gateway_start_local_runtime=False,
    )
    runtime.start_in_thread()
    try:
        client.connect()

        # 1. subscribe (as backtrader's CtpGatewayClientWrapper.subscribe does)
        result = client.subscribe(["rb2510"])
        assert "rb2510" in result["subscribed"]

        # 2. supports_live_ticks — must reflect subscribed set (was broken before fix)
        assert client.supports_live_ticks("rb2510") is True
        assert client.supports_live_ticks("ag2512") is False

        # 3. get_balance / get_positions (as broker/store would call)
        balance = client.get_balance()
        assert balance["cash"] == 1000.0
        positions = client.get_positions()
        assert len(positions) > 0

        # 4. Adapter emits a tick — verify poll_tick receives it
        adapter.emit(
            CHANNEL_MARKET,
            GatewayTick(timestamp=time.time(), symbol="rb2510", price=3800.0, volume=5.0),
        )
        assert _wait_until(lambda: client.has_pending_tick("rb2510"))
        tick = client.poll_tick("rb2510")
        assert tick is not None
        assert tick.symbol == "rb2510"
        assert tick.price == 3800.0
        # After drain, no more ticks
        assert client.poll_tick("rb2510") is None

        # 5. place_order (as broker would call via submit_order)
        order = client.submit_order({
            "data_name": "rb2510", "volume": 1, "direction": "buy",
            "price": 3800.0, "order_type": "limit",
        })
        assert order["order_id"] == "ord-1"
        assert order["data_name"] == "rb2510"

        # 6. Adapter emits order event — verify poll_broker_update receives it
        adapter.emit(CHANNEL_EVENT, {
            "kind": "order",
            "order_id": "ord-1",
            "external_order_id": "ord-1",
            "status": "submitted",
            "data_name": "rb2510",
        })
        update = None
        deadline = time.time() + 2.0
        while time.time() < deadline:
            update = client.poll_broker_update()
            if update is not None:
                break
            time.sleep(0.05)
        assert update is not None
        assert update["kind"] == "order"
        assert update["status"] == "submitted"

        # 7. Adapter emits trade event — verify broker receives fill
        adapter.emit(CHANNEL_EVENT, {
            "kind": "trade",
            "order_id": "ord-1",
            "external_order_id": "ord-1",
            "status": "filled",
            "fill_price": 3800.0,
            "fill_volume": 1,
        })
        fill = None
        deadline = time.time() + 2.0
        while time.time() < deadline:
            fill = client.poll_broker_update()
            if fill is not None:
                break
            time.sleep(0.05)
        assert fill is not None
        assert fill["kind"] == "trade"
        assert fill["fill_price"] == 3800.0

        # 8. cancel_order
        cancel = client.cancel_order("ord-1", dataname="rb2510")
        assert cancel["status"] == "canceled"

    finally:
        client.disconnect()
        runtime.stop()


def test_binance_adapter_ipc_roundtrip_with_fake(monkeypatch, tmp_path):
    """BinanceGatewayAdapter via GatewayRuntime IPC roundtrip using FakeGatewayAdapter."""
    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)
    runtime_name = f"bn-{uuid.uuid4().hex[:8]}"

    config = GatewayConfig.from_kwargs(
        exchange_type="BINANCE",
        asset_type="SWAP",
        account_id="test-bn",
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
    )
    runtime = GatewayRuntime(config, exchange_type="BINANCE", asset_type="SWAP",
                             account_id="test-bn", gateway_base_dir="/tmp/btgw",
                             gateway_runtime_name=runtime_name)
    client = GatewayClient(
        exchange_type="BINANCE", asset_type="SWAP", account_id="test-bn",
        gateway_command_endpoint=config.command_endpoint,
        gateway_event_endpoint=config.event_endpoint,
        gateway_market_endpoint=config.market_endpoint,
        gateway_base_dir="/tmp/btgw", gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10, gateway_command_timeout_sec=1.0,
        gateway_start_local_runtime=False,
    )
    runtime.start_in_thread()
    try:
        client.connect()
        assert client.get_balance()["cash"] == 1000.0
        result = client.subscribe(["BTCUSDT"])
        assert "BTCUSDT" in result["subscribed"]
    finally:
        client.disconnect()
        runtime.stop()


def test_okx_adapter_ipc_roundtrip_with_fake(monkeypatch, tmp_path):
    """OkxGatewayAdapter via GatewayRuntime IPC roundtrip using FakeGatewayAdapter."""
    adapter = FakeGatewayAdapter()
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)
    runtime_name = f"okx-{uuid.uuid4().hex[:8]}"

    config = GatewayConfig.from_kwargs(
        exchange_type="OKX",
        asset_type="SWAP",
        account_id="test-okx",
        gateway_base_dir="/tmp/btgw",
        gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10,
        gateway_command_timeout_sec=1.0,
    )
    runtime = GatewayRuntime(config, exchange_type="OKX", asset_type="SWAP",
                             account_id="test-okx", gateway_base_dir="/tmp/btgw",
                             gateway_runtime_name=runtime_name)
    client = GatewayClient(
        exchange_type="OKX", asset_type="SWAP", account_id="test-okx",
        gateway_command_endpoint=config.command_endpoint,
        gateway_event_endpoint=config.event_endpoint,
        gateway_market_endpoint=config.market_endpoint,
        gateway_base_dir="/tmp/btgw", gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10, gateway_command_timeout_sec=1.0,
        gateway_start_local_runtime=False,
    )
    runtime.start_in_thread()
    try:
        client.connect()
        assert client.get_balance()["cash"] == 1000.0
        result = client.subscribe(["BTC-USDT-SWAP"])
        assert "BTC-USDT-SWAP" in result["subscribed"]
    finally:
        client.disconnect()
        runtime.stop()
