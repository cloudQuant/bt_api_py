"""Unit tests for Mt5GatewayAdapter and gateway protocol extensions (get_bars, get_symbol_info, get_open_orders)."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bt_api_py.gateway.adapters.base import BaseGatewayAdapter
from bt_api_py.gateway.adapters.mt5_adapter import (
    Mt5GatewayAdapter,
    _MT5_ORDER_STATE_MAP,
    _RETCODE_STATUS,
    _TIMEFRAME_MAP,
)
from bt_api_py.gateway.client import GatewayClient
from bt_api_py.gateway.config import GatewayConfig
from bt_api_py.gateway.models import GatewayTick
from bt_api_py.gateway.protocol import CHANNEL_EVENT, CHANNEL_MARKET
from bt_api_py.gateway.runtime import GatewayRuntime


# ---------------------------------------------------------------------------
# Mock MT5WebClient for unit testing
# ---------------------------------------------------------------------------

@dataclass
class FakeTradeResult:
    retcode: int = 10009
    description: str = "Request completed"
    success: bool = True
    order: int = 12345
    deal: int = 67890
    volume: float = 0.01
    price: float = 1.2345
    bid: float = 1.2344
    ask: float = 1.2346
    comment: str = ""
    request_id: int = 0


@dataclass
class FakeSymbolInfo:
    name: str = "EURUSD"
    symbol_id: int = 1
    digits: int = 5
    description: str = "Euro vs US Dollar"
    path: str = "Forex\\EURUSD"
    trade_calc_mode: int = 0
    basis: str = ""
    sector: str = ""


class FakeMT5WebClient:
    """Mock MT5WebClient for unit testing without real WebSocket."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._connected = False
        self._logged_in = False
        self._subscribed: list[str] = []
        self._symbols: dict[str, FakeSymbolInfo] = {
            "EURUSD": FakeSymbolInfo(name="EURUSD", symbol_id=1, digits=5),
            "XAUUSD": FakeSymbolInfo(name="XAUUSD", symbol_id=2, digits=2),
        }
        self._symbols_by_id: dict[int, FakeSymbolInfo] = {
            s.symbol_id: s for s in self._symbols.values()
        }
        self.symbol_names: list[str] = list(self._symbols.keys())
        self._tick_callback = None
        self._trade_transaction_callback = None
        self._trade_result_callback = None
        self._account_update_callback = None
        self._login_status_callback = None
        self._disconnect_callback = None

    @property
    def is_connected(self):
        return self._connected

    async def connect(self):
        self._connected = True

    async def close(self):
        self._connected = False

    async def login(self, login: int, password: str, **kwargs):
        self._logged_in = True

    async def load_symbols(self):
        pass

    async def subscribe_symbols(self, symbols: list[str]):
        self._subscribed.extend(symbols)

    async def get_account(self) -> dict:
        return {
            "balance": 10000.0,
            "equity": 10050.0,
            "credit": 0.0,
            "currency": "USD",
            "leverage": 100,
        }

    async def get_positions(self) -> list[dict]:
        return [
            {
                "trade_symbol": "EURUSD",
                "position_id": 100,
                "trade_action": 0,
                "trade_volume": 10000,
                "price_open": 1.1050,
                "sl": 1.1000,
                "tp": 1.1100,
                "profit": 25.50,
                "commission": -0.50,
                "storage": -0.10,
                "comment": "test",
            }
        ]

    async def get_orders(self) -> list[dict]:
        return [
            {
                "order_id": 200,
                "trade_symbol": "XAUUSD",
                "trade_type": 2,
                "trade_volume": 100,
                "price_order": 2000.0,
                "sl": 1990.0,
                "tp": 2020.0,
                "order_state": 1,
                "comment": "pending",
            }
        ]

    async def get_rates(self, symbol: str, period_minutes: int, from_ts: int, to_ts: int) -> list[dict]:
        base_time = from_ts
        return [
            {
                "time": base_time + i * period_minutes * 60,
                "open": 1.1000 + i * 0.001,
                "high": 1.1010 + i * 0.001,
                "low": 1.0990 + i * 0.001,
                "close": 1.1005 + i * 0.001,
                "tick_volume": 100 + i,
                "spread": 5,
            }
            for i in range(min(10, (to_ts - from_ts) // (period_minutes * 60) + 1))
        ]

    async def get_full_symbol_info(self, symbol: str) -> dict | None:
        if symbol in self._symbols:
            return {
                "contract_size": 100000,
                "volume_min": 0.01,
                "volume_max": 100.0,
                "volume_step": 0.01,
                "tick_size": 0.00001,
                "digits": self._symbols[symbol].digits,
                "margin_initial": 0.0,
            }
        return None

    def get_symbol_info(self, name: str) -> FakeSymbolInfo | None:
        return self._symbols.get(name)

    async def buy_market(self, symbol, volume, **kwargs) -> FakeTradeResult:
        return FakeTradeResult(retcode=10009, volume=volume, price=1.1050)

    async def sell_market(self, symbol, volume, **kwargs) -> FakeTradeResult:
        return FakeTradeResult(retcode=10009, volume=volume, price=1.1048)

    async def buy_limit(self, symbol, volume, price, **kwargs) -> FakeTradeResult:
        return FakeTradeResult(retcode=10008, volume=volume, price=price)

    async def sell_limit(self, symbol, volume, price, **kwargs) -> FakeTradeResult:
        return FakeTradeResult(retcode=10008, volume=volume, price=price)

    async def buy_stop(self, symbol, volume, price, **kwargs) -> FakeTradeResult:
        return FakeTradeResult(retcode=10008, volume=volume, price=price)

    async def sell_stop(self, symbol, volume, price, **kwargs) -> FakeTradeResult:
        return FakeTradeResult(retcode=10008, volume=volume, price=price)

    async def close_position(self, symbol, position_id, volume, **kwargs) -> FakeTradeResult:
        return FakeTradeResult(retcode=10009, volume=volume, order=position_id)

    async def cancel_pending_order(self, order: int) -> FakeTradeResult:
        return FakeTradeResult(retcode=10009, order=order, description="Order cancelled")

    def on_tick(self, callback):
        self._tick_callback = callback

    def on_trade_transaction(self, callback):
        self._trade_transaction_callback = callback

    def on_trade_result(self, callback):
        self._trade_result_callback = callback

    def on_account_update(self, callback):
        self._account_update_callback = callback

    def on_login_status(self, callback):
        self._login_status_callback = callback

    def on_disconnect(self, callback):
        self._disconnect_callback = callback


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_mt5_client():
    return FakeMT5WebClient()


def _make_adapter(fake_client: FakeMT5WebClient, **overrides) -> Mt5GatewayAdapter:
    """Create an Mt5GatewayAdapter with a fake client, bypassing real connect."""
    kwargs = {
        "login": 12345678,
        "password": "test_pass",
        "exchange_type": "MT5",
        "asset_type": "OTC",
        **overrides,
    }
    adapter = Mt5GatewayAdapter(**kwargs)
    # Inject fake loop + client
    adapter._loop = asyncio.new_event_loop()
    adapter._running = True
    adapter._client = fake_client
    adapter._thread = None

    # Start the loop in a background thread
    import threading
    t = threading.Thread(target=adapter._loop.run_forever, daemon=True)
    t.start()
    adapter._thread = t

    # Register push callbacks (same as _async_connect does)
    fake_client.on_tick(adapter._on_tick_push)
    fake_client.on_trade_transaction(adapter._on_transaction_push)
    fake_client.on_trade_result(adapter._on_trade_result_push)
    fake_client.on_disconnect(adapter._on_ws_disconnect)

    return adapter


def _stop_adapter(adapter: Mt5GatewayAdapter):
    if adapter._loop is not None:
        adapter._loop.call_soon_threadsafe(adapter._loop.stop)
    if adapter._thread is not None:
        adapter._thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# BaseGatewayAdapter default method tests
# ---------------------------------------------------------------------------

class TestBaseGatewayAdapterDefaults:
    """Test that the new optional methods on BaseGatewayAdapter return defaults."""

    def test_get_bars_returns_empty(self):
        class StubAdapter(BaseGatewayAdapter):
            def connect(self): ...
            def disconnect(self): ...
            def subscribe_symbols(self, symbols): return {}
            def get_balance(self): return {}
            def get_positions(self): return []
            def place_order(self, payload): return {}
            def cancel_order(self, payload): return {}

        adapter = StubAdapter()
        assert adapter.get_bars("EURUSD", "M1", 100) == []
        assert adapter.get_symbol_info("EURUSD") == {}
        assert adapter.get_open_orders() == []


# ---------------------------------------------------------------------------
# Mt5GatewayAdapter unit tests
# ---------------------------------------------------------------------------

class TestMt5AdapterSymbolMapping:
    def test_no_mapping(self):
        adapter = Mt5GatewayAdapter(login=1, password="x")
        assert adapter._resolve_symbol("EURUSD") == "EURUSD"

    def test_suffix(self):
        adapter = Mt5GatewayAdapter(login=1, password="x", symbol_suffix="m")
        assert adapter._resolve_symbol("EURUSD") == "EURUSDm"

    def test_manual_map_overrides_suffix(self):
        adapter = Mt5GatewayAdapter(
            login=1, password="x",
            symbol_suffix=".r",
            symbol_map={"XAUUSD": "GOLD.r"},
        )
        assert adapter._resolve_symbol("XAUUSD") == "GOLD.r"
        assert adapter._resolve_symbol("EURUSD") == "EURUSD.r"

    def test_discover_symbol_from_client_names(self):
        client = FakeMT5WebClient()
        client.symbol_names = ["EURUSDm", "XAUUSD.pro"]
        adapter = Mt5GatewayAdapter(login=1, password="x")
        adapter._client = client
        adapter._subscribed_symbols = ["EURUSD", "XAUUSD"]
        assert adapter._resolve_symbol("EURUSD") == "EURUSDm"
        assert adapter._to_standard_symbol("XAUUSD.pro") == "XAUUSD"


class TestMt5AdapterVolumeNormalization:
    def test_basic_normalization(self):
        adapter = Mt5GatewayAdapter(login=1, password="x")
        adapter._symbol_specs["EURUSD"] = {
            "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
        }
        assert adapter._normalize_volume("EURUSD", 0.015) == 0.02
        assert adapter._normalize_volume("EURUSD", 0.005) == 0.01
        assert adapter._normalize_volume("EURUSD", 200.0) == 100.0

    def test_default_when_no_spec(self):
        adapter = Mt5GatewayAdapter(login=1, password="x")
        assert adapter._normalize_volume("UNKNOWN", 0.5) == 0.5


class TestMt5AdapterRetcodeMapping:
    def test_known_codes(self):
        assert _RETCODE_STATUS[10009] == "completed"
        assert _RETCODE_STATUS[10010] == "partial"
        assert _RETCODE_STATUS[10006] == "rejected"
        assert _RETCODE_STATUS[10007] == "canceled"
        assert _RETCODE_STATUS[10008] == "submitted"

    def test_order_state_map(self):
        assert _MT5_ORDER_STATE_MAP[0] == "submitted"   # STARTED
        assert _MT5_ORDER_STATE_MAP[1] == "accepted"     # PLACED
        assert _MT5_ORDER_STATE_MAP[2] == "canceled"     # CANCELED
        assert _MT5_ORDER_STATE_MAP[3] == "partial"      # PARTIAL
        assert _MT5_ORDER_STATE_MAP[4] == "completed"    # FILLED
        assert _MT5_ORDER_STATE_MAP[5] == "rejected"     # REJECTED
        assert _MT5_ORDER_STATE_MAP[6] == "canceled"     # EXPIRED

    def test_trade_result_to_dict(self):
        result = FakeTradeResult(retcode=10009, order=999, deal=888, volume=0.1, price=1.23)
        d = Mt5GatewayAdapter._trade_result_to_dict(result, symbol="EURUSD")
        assert d["status"] == "completed"
        assert d["order_id"] == 999
        assert d["deal"] == 888
        assert d["data_name"] == "EURUSD"


class TestMt5AdapterConnect:
    def test_connect_requires_login(self):
        adapter = Mt5GatewayAdapter(login=0, password="")
        with pytest.raises(ValueError, match="requires 'login' and 'password'"):
            adapter.connect()


class TestMt5AdapterWithFakeClient:
    @pytest.fixture(autouse=True)
    def setup(self, fake_mt5_client):
        self.adapter = _make_adapter(fake_mt5_client)
        self.client = fake_mt5_client
        yield
        _stop_adapter(self.adapter)

    def test_get_balance(self):
        result = self.adapter.get_balance()
        assert result["balance"] == 10000.0
        assert result["equity"] == 10050.0
        assert result["currency"] == "USD"
        assert result["cash"] == 10000.0
        assert result["value"] == 10050.0

    def test_get_positions(self):
        positions = self.adapter.get_positions()
        assert len(positions) == 1
        assert positions[0]["instrument"] == "EURUSD"
        assert positions[0]["direction"] == "buy"
        assert positions[0]["profit"] == 25.50

    def test_get_open_orders(self):
        orders = self.adapter.get_open_orders()
        assert len(orders) == 1
        assert orders[0]["order_id"] == 200
        assert orders[0]["symbol"] == "XAUUSD"

    def test_get_bars(self):
        bars = self.adapter.get_bars("EURUSD", "M1", 5)
        assert len(bars) > 0
        assert "open" in bars[0]
        assert "close" in bars[0]
        assert bars[0]["symbol"] == "EURUSD"
        assert bars[0]["timeframe"] == "M1"

    def test_get_bars_invalid_timeframe(self):
        assert self.adapter.get_bars("EURUSD", "INVALID", 5) == []

    def test_get_symbol_info(self):
        info = self.adapter.get_symbol_info("EURUSD")
        assert info["digits"] == 5
        assert info["contract_size"] == 100000
        assert info["volume_min"] == 0.01

    def test_get_symbol_info_unknown(self):
        info = self.adapter.get_symbol_info("UNKNOWN_SYMBOL")
        assert info == {}

    def test_subscribe_symbols(self):
        result = self.adapter.subscribe_symbols(["EURUSD", "XAUUSD"])
        assert result["symbols"] == ["EURUSD", "XAUUSD"]
        assert "EURUSD" in self.client._subscribed
        assert "XAUUSD" in self.client._subscribed
        assert self.adapter._resolved_symbols["EURUSD"] == "EURUSD"

    def test_place_order_market_buy(self):
        result = self.adapter.place_order({
            "data_name": "EURUSD", "side": "buy", "order_type": "market", "volume": 0.01,
        })
        assert result["status"] == "completed"
        assert result["order_id"] == 12345
        assert result["data_name"] == "EURUSD"

    def test_place_order_market_sell(self):
        result = self.adapter.place_order({
            "data_name": "EURUSD", "side": "sell", "order_type": "market", "volume": 0.01,
        })
        assert result["status"] == "completed"

    def test_place_order_limit(self):
        result = self.adapter.place_order({
            "data_name": "EURUSD", "side": "buy", "order_type": "limit",
            "volume": 0.01, "price": 1.1000,
        })
        assert result["status"] == "submitted"

    def test_place_order_stop(self):
        result = self.adapter.place_order({
            "data_name": "EURUSD", "side": "sell", "order_type": "stop",
            "volume": 0.01, "price": 1.1200,
        })
        assert result["status"] == "submitted"

    def test_place_order_close(self):
        result = self.adapter.place_order({
            "data_name": "EURUSD", "order_type": "close",
            "volume": 0.01, "position_id": 100,
        })
        assert result["status"] == "completed"

    def test_place_order_unsupported_type(self):
        result = self.adapter.place_order({
            "data_name": "EURUSD", "order_type": "exotic", "volume": 0.01,
        })
        assert result["status"] == "error"

    def test_cancel_order(self):
        result = self.adapter.cancel_order({"order_id": 200})
        assert result["status"] == "completed"
        assert result["order_id"] == 200

    def test_cancel_order_missing_id(self):
        result = self.adapter.cancel_order({})
        assert result["status"] == "error"

    def test_tick_push_emits_market_event(self):
        ticks = [
            {"symbol_id": 1, "tick_time": 1700000000.0, "bid": 1.1050, "ask": 1.1052, "tick_volume": 10}
        ]
        self.adapter._on_tick_push(ticks)

        output = self.adapter.poll_output()
        assert output is not None
        channel, payload = output
        assert channel == CHANNEL_MARKET
        assert isinstance(payload, GatewayTick)
        assert payload.exchange == "MT5"
        assert payload.symbol == "EURUSD"
        assert payload.bid_price == 1.1050
        assert payload.ask_price == 1.1052

    def test_tick_push_maps_broker_suffix_symbol_back_to_standard_symbol(self):
        self.client.symbol_names = ["EURUSDm", "XAUUSDm"]
        self.client._symbols["EURUSDm"] = FakeSymbolInfo(name="EURUSDm", symbol_id=11, digits=5)
        self.client._symbols_by_id[11] = self.client._symbols["EURUSDm"]
        self.adapter._subscribed_symbols = ["EURUSD"]
        self.adapter._resolved_symbols["EURUSD"] = "EURUSDm"
        self.adapter._reverse_resolved_symbols["EURUSDm"] = "EURUSD"

        self.adapter._on_tick_push([
            {"symbol": "EURUSDm", "tick_time": 1700000000.0, "bid": 1.2050, "ask": 1.2052, "tick_volume": 8}
        ])

        output = self.adapter.poll_output()
        assert output is not None
        _, payload = output
        assert payload.symbol == "EURUSD"
        assert payload.instrument_id == "EURUSDm"

    def test_trade_result_push_emits_event(self):
        data = {"result": {"retcode": 10009, "order": 999, "deal": 888, "price": 1.23, "volume": 0.1}}
        self.adapter._on_trade_result_push(data)

        output = self.adapter.poll_output()
        assert output is not None
        channel, payload = output
        assert channel == CHANNEL_EVENT
        assert payload["kind"] == "order"
        assert payload["status"] == "completed"
        assert payload["order_id"] == 999

    def test_transaction_push_deal_fill_emits_trade_event(self):
        data = {
            "update_type": 2,
            "deals": [
                {
                    "deal": 5001,
                    "deal_id": "5001",
                    "trade_order": 3001,
                    "trade_symbol": "EURUSD",
                    "trade_action": 0,
                    "trade_volume": 0.01,
                    "price_open": 1.1050,
                    "commission": -0.07,
                    "profit": 0.0,
                    "position_id": 100,
                }
            ],
            "positions": [],
        }
        self.adapter._on_transaction_push(data)

        output = self.adapter.poll_output()
        assert output is not None
        channel, payload = output
        assert channel == CHANNEL_EVENT
        assert payload["kind"] == "trade"
        assert payload["trade_id"] == "5001"
        assert payload["external_order_id"] == "3001"
        assert payload["side"] == "buy"
        assert payload["size"] == 0.01
        assert payload["price"] == 1.1050
        assert payload["data_name"] == "EURUSD"

    def test_transaction_push_order_update_emits_order_event(self):
        data = {
            "update_type": 0,
            "transaction_type": 0,  # add
            "order": {
                "trade_order": 3002,
                "trade_symbol": "XAUUSD",
                "order_type": 2,  # buy limit
                "order_state": 1,  # PLACED
                "price_order": 2350.50,
                "volume_initial": 0.1,
                "volume_current": 0.1,
            },
        }
        self.adapter._on_transaction_push(data)

        output = self.adapter.poll_output()
        assert output is not None
        channel, payload = output
        assert channel == CHANNEL_EVENT
        assert payload["kind"] == "order"
        assert payload["status"] == "accepted"
        assert payload["external_order_id"] == "3002"
        assert payload["data_name"] == "XAUUSD"
        assert payload["side"] == "buy"
        assert payload["price"] == 2350.50

    def test_transaction_push_order_delete_emits_canceled(self):
        data = {
            "update_type": 0,
            "transaction_type": 2,  # delete
            "order": {
                "trade_order": 3003,
                "trade_symbol": "GBPUSD",
                "order_type": 3,  # sell limit
                "order_state": 2,  # CANCELED
                "price_order": 1.2700,
                "volume_initial": 0.05,
                "volume_current": 0.05,
            },
        }
        self.adapter._on_transaction_push(data)

        output = self.adapter.poll_output()
        assert output is not None
        channel, payload = output
        assert channel == CHANNEL_EVENT
        assert payload["kind"] == "order"
        assert payload["status"] == "canceled"

    def test_transaction_push_no_deals_no_event(self):
        data = {"update_type": 2, "deals": [], "positions": []}
        self.adapter._on_transaction_push(data)
        assert self.adapter.poll_output() is None

    def test_disconnect_push_emits_health_event(self):
        self.adapter._on_ws_disconnect()

        output = self.adapter.poll_output()
        assert output is not None
        channel, payload = output
        assert channel == CHANNEL_EVENT
        assert payload["kind"] == "health"
        assert payload["type"] == "disconnected"

    def test_symbol_specs_cached_after_subscribe(self):
        self.adapter.subscribe_symbols(["EURUSD"])
        assert "EURUSD" in self.adapter._symbol_specs
        assert self.adapter._symbol_specs["EURUSD"]["digits"] == 5


# ---------------------------------------------------------------------------
# FakeGatewayAdapter with get_bars/get_symbol_info/get_open_orders for IPC tests
# ---------------------------------------------------------------------------

class FakeGatewayAdapterWithBars:
    """Fake adapter with get_bars/get_symbol_info/get_open_orders for runtime dispatch tests."""

    def __init__(self):
        self.connected = False
        self.outputs: deque[tuple[str, Any]] = deque()

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def subscribe_symbols(self, symbols):
        return {"symbols": list(symbols)}

    def get_balance(self):
        return {"cash": 5000.0, "equity": 5000.0}

    def get_positions(self):
        return []

    def place_order(self, payload):
        return {"order_id": "ord-1", **payload}

    def cancel_order(self, payload):
        return {"status": "canceled"}

    def get_bars(self, symbol, timeframe, count):
        return [
            {"timestamp": 1700000000.0 + i * 60, "open": 1.1 + i * 0.001,
             "high": 1.101 + i * 0.001, "low": 1.099 + i * 0.001,
             "close": 1.1005 + i * 0.001, "volume": 100.0, "symbol": symbol, "timeframe": timeframe}
            for i in range(count)
        ]

    def get_symbol_info(self, symbol):
        if symbol == "EURUSD":
            return {"digits": 5, "contract_size": 100000, "volume_min": 0.01, "volume_step": 0.01}
        return {}

    def get_open_orders(self):
        return [{"order_id": 300, "symbol": "GBPUSD", "type": 2, "volume": 0.05}]

    def poll_output(self):
        if not self.outputs:
            return None
        return self.outputs.popleft()

    def emit(self, channel, payload):
        self.outputs.append((channel, payload))


# ---------------------------------------------------------------------------
# Runtime dispatch tests for new commands
# ---------------------------------------------------------------------------

def _tcp_config_and_client(monkeypatch, adapter, account_id):
    """Helper: create GatewayConfig, Runtime, Client over tcp (Windows-safe)."""
    monkeypatch.setattr(GatewayRuntime, "_create_adapter", lambda self: adapter)
    runtime_name = f"mt5-test-{uuid.uuid4().hex[:8]}"
    config = GatewayConfig.from_kwargs(
        exchange_type="MT5", asset_type="OTC", account_id=account_id,
        transport="tcp", gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10, gateway_command_timeout_sec=2.0,
    )
    runtime = GatewayRuntime(config, exchange_type="MT5", asset_type="OTC", account_id=account_id,
                             transport="tcp", gateway_runtime_name=runtime_name)
    client = GatewayClient(
        exchange_type="MT5", asset_type="OTC", account_id=account_id,
        gateway_command_endpoint=config.command_endpoint,
        gateway_event_endpoint=config.event_endpoint,
        gateway_market_endpoint=config.market_endpoint,
        transport="tcp", gateway_runtime_name=runtime_name,
        gateway_poll_timeout_ms=10, gateway_command_timeout_sec=2.0,
        gateway_start_local_runtime=False,
    )
    return config, runtime, client


def test_runtime_dispatches_get_bars(monkeypatch):
    adapter = FakeGatewayAdapterWithBars()
    _, runtime, client = _tcp_config_and_client(monkeypatch, adapter, "mt5-1")
    runtime.start_in_thread()
    try:
        client.connect()
        bars = client.fetch_bars("EURUSD", "M1", 5)
        assert len(bars) == 5
        assert bars[0]["symbol"] == "EURUSD"
        assert bars[0]["timeframe"] == "M1"
        assert "open" in bars[0]
    finally:
        client.disconnect()
        runtime.stop()


def test_runtime_dispatches_get_symbol_info(monkeypatch):
    adapter = FakeGatewayAdapterWithBars()
    _, runtime, client = _tcp_config_and_client(monkeypatch, adapter, "mt5-2")
    runtime.start_in_thread()
    try:
        client.connect()
        info = client.fetch_symbol_info("EURUSD")
        assert info["digits"] == 5
        assert info["contract_size"] == 100000

        empty = client.fetch_symbol_info("UNKNOWN")
        assert empty == {}
    finally:
        client.disconnect()
        runtime.stop()


def test_runtime_dispatches_get_open_orders(monkeypatch):
    adapter = FakeGatewayAdapterWithBars()
    _, runtime, client = _tcp_config_and_client(monkeypatch, adapter, "mt5-3")
    runtime.start_in_thread()
    try:
        client.connect()
        orders = client.fetch_open_orders()
        assert len(orders) == 1
        assert orders[0]["order_id"] == 300
        assert orders[0]["symbol"] == "GBPUSD"
    finally:
        client.disconnect()
        runtime.stop()


# ---------------------------------------------------------------------------
# MT5 registry test
# ---------------------------------------------------------------------------

def test_mt5_registered_in_adapter_registry():
    """MT5 should be in the registry when pymt5 is available."""
    assert GatewayRuntime.get_adapter_class("MT5") is Mt5GatewayAdapter
    assert GatewayRuntime.get_adapter_class("mt5") is Mt5GatewayAdapter


# ---------------------------------------------------------------------------
# Timeframe map coverage
# ---------------------------------------------------------------------------

def test_timeframe_map_completeness():
    expected = {"M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"}
    assert set(_TIMEFRAME_MAP.keys()) == expected
