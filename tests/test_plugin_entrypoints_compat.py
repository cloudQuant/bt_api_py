from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

from bt_api_base.gateway.registrar import GatewayRuntimeRegistrar
from bt_api_base.plugins.loader import PluginLoader
from bt_api_base.registry import ExchangeRegistry


@dataclass
class _EntryPoint:
    name: str
    module: str
    attr: str = "register_plugin"

    def load(self) -> Any:
        return getattr(import_module(self.module), self.attr)


def setup_function() -> None:
    ExchangeRegistry.clear()
    GatewayRuntimeRegistrar.clear()


def teardown_function() -> None:
    ExchangeRegistry.clear()
    GatewayRuntimeRegistrar.clear()


def test_legacy_exchange_entrypoints_load_with_current_plugin_protocol(monkeypatch):
    entry_points = [
        _EntryPoint("buda", "bt_api_buda.plugin"),
        _EntryPoint("btcturk", "bt_api_btcturk.plugin"),
        _EntryPoint("bitvavo", "bt_api_bitvavo.plugin"),
        _EntryPoint("bitfinex", "bt_api_bitfinex.plugin"),
    ]
    loader = PluginLoader(ExchangeRegistry, GatewayRuntimeRegistrar)
    monkeypatch.setattr(loader, "_discover_entry_points", lambda group: entry_points)

    loader.load_all()

    assert loader.failed == {}
    assert set(loader.loaded) == {
        "bt_api_buda",
        "bt_api_btcturk",
        "bt_api_bitvavo",
        "bt_api_bitfinex",
    }
    assert ExchangeRegistry.get_feed_class("BUDA___SPOT") is not None
    assert ExchangeRegistry.get_feed_class("BTCTURK___SPOT") is not None
    assert ExchangeRegistry.get_feed_class("BITVAVO___SPOT") is not None
    assert ExchangeRegistry.get_feed_class("BITFINEX___SPOT") is not None
