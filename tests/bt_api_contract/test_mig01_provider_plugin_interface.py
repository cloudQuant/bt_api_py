from __future__ import annotations

import ast
import importlib
from pathlib import Path

from bt_api_base.registry import ExchangeRegistry


_PROVIDERS = (
    "binance",
    "bitget",
    "bybit",
    "coinbase",
    "ctp",
    "dydx",
    "gateio",
    "htx",
    "hyperliquid",
    "ib_web",
    "kraken",
    "mexc",
    "mt5",
    "okx",
)
_RETIRED_RUNTIME_NAMES = frozenset(
    {
        "GatewayRuntimeRegistrar",
        "register_adapter",
        "register_runtime",
        "runtime_factory",
    }
)


def test_provider_plugins_use_only_the_exchange_registry_contract() -> None:
    sdk_root = Path(__file__).resolve().parents[2]

    for provider in _PROVIDERS:
        source_path = (
            sdk_root
            / "bt_api"
            / f"bt_api_{provider}"
            / "src"
            / f"bt_api_{provider}"
            / "plugin.py"
        )
        source = source_path.read_text(encoding="utf-8")
        module = ast.parse(source, filename=str(source_path))
        registrations = [
            node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "register_plugin"
        ]
        assert len(registrations) == 1, provider
        registration = registrations[0]
        assert [arg.arg for arg in registration.args.posonlyargs + registration.args.args] == [
            "registry"
        ], provider
        assert registration.args.vararg is None, provider
        assert registration.args.kwarg is None, provider
        assert not registration.args.defaults, provider
        assert not registration.args.kw_defaults, provider

        referenced_names = {
            node.id for node in ast.walk(module) if isinstance(node, ast.Name)
        }
        referenced_attributes = {
            node.attr for node in ast.walk(module) if isinstance(node, ast.Attribute)
        }
        assert not (
            _RETIRED_RUNTIME_NAMES & (referenced_names | referenced_attributes)
        ), provider
        assert "gateway.adapter" not in source.lower(), provider


def test_coinbase_and_mexc_registration_is_isolated_and_has_no_import_side_effect() -> None:
    global_registry = ExchangeRegistry._get_default()
    with global_registry._lock:
        previous = (
            dict(global_registry._feed_classes),
            dict(global_registry._exchange_data_classes),
            dict(global_registry._balance_handlers),
            {name: dict(rows) for name, rows in global_registry._stream_classes.items()},
        )

    providers = (
        (
            "coinbase",
            "COINBASE___SPOT",
            "bt_api_coinbase.exchange_registers.register_coinbase",
        ),
        ("mexc", "MEXC___SPOT", "bt_api_mexc.registry_registration"),
    )
    try:
        global_registry.clear()
        for provider, exchange_name, registrar_module_name in providers:
            registrar_module = importlib.import_module(registrar_module_name)
            importlib.reload(registrar_module)
            assert global_registry.list_exchanges() == [], provider

            plugin_module = importlib.import_module(f"bt_api_{provider}.plugin")
            isolated_registry = ExchangeRegistry.create_isolated()
            info = plugin_module.register_plugin(isolated_registry)

            assert info.supported_exchanges == (exchange_name,), provider
            assert isolated_registry.list_exchanges() == [exchange_name], provider
            assert isolated_registry.get_feed_class(exchange_name) is not None, provider
            assert isolated_registry.get_exchange_data_class(exchange_name) is not None, provider
            assert isolated_registry.get_balance_handler(exchange_name) is not None, provider
            assert global_registry.list_exchanges() == [], provider
    finally:
        global_registry.clear()
        feeds, exchange_data, balance_handlers, streams = previous
        for exchange_name, feed_class in feeds.items():
            global_registry.register_feed(exchange_name, feed_class)
        for exchange_name, data_class in exchange_data.items():
            global_registry.register_exchange_data(exchange_name, data_class)
        for exchange_name, handler in balance_handlers.items():
            global_registry.register_balance_handler(exchange_name, handler)
        for exchange_name, stream_rows in streams.items():
            for stream_type, stream_class in stream_rows.items():
                global_registry.register_stream(exchange_name, stream_type, stream_class)
