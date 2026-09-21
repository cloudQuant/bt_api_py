"""Offline contracts for best-effort Hyperliquid example diagnostics."""

import importlib.util
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

_SENSITIVE_ERROR = (
    "test-private-key-do-not-log /tmp/sensitive-hyperliquid.yaml "
    "configuration-content=must-not-appear"
)


@pytest.fixture
def example_modules(monkeypatch):
    """Load the example with fake dependencies so tests cannot perform I/O."""
    bt_api_py = ModuleType("bt_api_py")
    bt_api_py.__path__ = []
    feeds = ModuleType("bt_api_py.feeds")
    feeds.__path__ = []
    live_hyperliquid = ModuleType("bt_api_py.feeds.live_hyperliquid")
    functions = ModuleType("bt_api_py.functions")
    functions.__path__ = []
    log_message = ModuleType("bt_api_py.functions.log_message")
    config_loader = ModuleType("bt_api_py.config_loader")
    eth_account = ModuleType("eth_account")

    class NeverUsedRequestData:
        pass

    class NeverUsedWebsocketData:
        pass

    class NeverUsedLogManager:
        pass

    class LiveHyperliquidModule(ModuleType):
        HyperliquidRequestDataSpot: type[NeverUsedRequestData]
        HyperliquidMarketWssDataSpot: type[NeverUsedWebsocketData]

    class LogMessageModule(ModuleType):
        SpdLogManager: type[NeverUsedLogManager]

    class FeedsModule(ModuleType):
        __path__: list[str]
        live_hyperliquid: LiveHyperliquidModule

    class FunctionsModule(ModuleType):
        __path__: list[str]
        log_message: LogMessageModule

    class ConfigLoaderModule(ModuleType):
        load_exchange_config: Callable[[str], object]

    class BtApiPyModule(ModuleType):
        __path__: list[str]
        feeds: FeedsModule
        functions: FunctionsModule
        config_loader: ConfigLoaderModule

    live_hyperliquid = LiveHyperliquidModule("bt_api_py.feeds.live_hyperliquid")
    live_hyperliquid.HyperliquidRequestDataSpot = NeverUsedRequestData
    live_hyperliquid.HyperliquidMarketWssDataSpot = NeverUsedWebsocketData
    log_message = LogMessageModule("bt_api_py.functions.log_message")
    log_message.SpdLogManager = NeverUsedLogManager
    feeds = FeedsModule("bt_api_py.feeds")
    feeds.__path__ = []
    feeds.live_hyperliquid = live_hyperliquid
    functions = FunctionsModule("bt_api_py.functions")
    functions.__path__ = []
    functions.log_message = log_message
    config_loader = ConfigLoaderModule("bt_api_py.config_loader")

    def unexpected_config_load(_path: str) -> object:
        raise AssertionError("load_exchange_config must be replaced before use")

    config_loader.load_exchange_config = unexpected_config_load
    bt_api_py = BtApiPyModule("bt_api_py")
    bt_api_py.__path__ = []
    bt_api_py.feeds = feeds
    bt_api_py.functions = functions
    bt_api_py.config_loader = config_loader

    fake_modules = {
        "bt_api_py": bt_api_py,
        "bt_api_py.feeds": feeds,
        "bt_api_py.feeds.live_hyperliquid": live_hyperliquid,
        "bt_api_py.functions": functions,
        "bt_api_py.functions.log_message": log_message,
        "bt_api_py.config_loader": config_loader,
        "eth_account": eth_account,
    }
    for name, fake_module in fake_modules.items():
        monkeypatch.setitem(sys.modules, name, fake_module)

    source_path = (
        Path(__file__).resolve().parents[1]
        / "examples/network_tests/integration/test_hyperliquid_integration.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_test_hyperliquid_integration_under_test", source_path
    )
    assert spec is not None and spec.loader is not None
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)

    return example, live_hyperliquid, config_loader


def _assert_only_exception_type_was_logged(caplog, exception_name):
    records = [
        record
        for record in caplog.records
        if record.name == "_test_hyperliquid_integration_under_test"
    ]

    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    assert records[0].getMessage() == (
        f"Suppressed Hyperliquid integration exception type: {exception_name}"
    )
    assert "test-private-key-do-not-log" not in caplog.text
    assert "/tmp/sensitive-hyperliquid.yaml" not in caplog.text
    assert "configuration-content" not in caplog.text


def test_market_data_failure_is_best_effort_and_logs_only_exception_type(
    example_modules, monkeypatch, caplog
):
    class FailingRequestData:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_all_mids(self):
            raise RuntimeError(_SENSITIVE_ERROR)

    integration, _hyperliquid_feed, _config_loader = example_modules
    monkeypatch.setattr(integration, "HyperliquidRequestDataSpot", FailingRequestData)
    caplog.set_level(logging.DEBUG, logger=integration.__name__)

    assert integration.test_market_data_queries() is None

    _assert_only_exception_type_was_logged(caplog, "RuntimeError")


def test_authenticated_setup_failure_is_best_effort_and_logs_only_type(
    example_modules, monkeypatch, caplog
):
    class FailingRequestData:
        def __init__(self, *_args, **_kwargs):
            raise PermissionError(_SENSITIVE_ERROR)

    integration, _hyperliquid_feed, _config_loader = example_modules
    monkeypatch.setattr(integration, "HyperliquidRequestDataSpot", FailingRequestData)
    caplog.set_level(logging.DEBUG, logger=integration.__name__)

    assert integration.test_authenticated_queries() is None

    _assert_only_exception_type_was_logged(caplog, "PermissionError")


def test_authenticated_inner_suppress_blocks_remain_silent(example_modules, monkeypatch, caplog):
    calls = []

    class FailingRequestData:
        def __init__(self, *_args, **_kwargs):
            pass

        def get_clearinghouse_state(self):
            calls.append("clearinghouse")
            raise ValueError(_SENSITIVE_ERROR)

        def place_order(self, **_kwargs):
            calls.append("place_order")
            raise RuntimeError(_SENSITIVE_ERROR)

    integration, _hyperliquid_feed, _config_loader = example_modules
    monkeypatch.setattr(integration, "HyperliquidRequestDataSpot", FailingRequestData)
    caplog.set_level(logging.DEBUG, logger=integration.__name__)

    assert integration.test_authenticated_queries() is None

    assert calls == ["clearinghouse", "place_order"]
    assert not [record for record in caplog.records if record.name == integration.__name__]


def test_websocket_example_failure_is_best_effort_and_logs_only_type(
    example_modules, monkeypatch, caplog
):
    class FailingWebsocketData:
        def __init__(self, *_args, **_kwargs):
            pass

        def subscribe_ticker(self, _symbol):
            raise OSError(_SENSITIVE_ERROR)

    integration, hyperliquid_feed, _config_loader = example_modules
    monkeypatch.setattr(hyperliquid_feed, "HyperliquidMarketWssDataSpot", FailingWebsocketData)
    caplog.set_level(logging.DEBUG, logger=integration.__name__)

    assert integration.test_websocket_subscription() is None

    _assert_only_exception_type_was_logged(caplog, "OSError")


def test_config_loading_failure_is_best_effort_and_logs_only_type(
    example_modules, monkeypatch, caplog
):
    def fail_loading(_path):
        raise FileNotFoundError(_SENSITIVE_ERROR)

    integration, _hyperliquid_feed, config_loader = example_modules
    monkeypatch.setattr(config_loader, "load_exchange_config", fail_loading)
    caplog.set_level(logging.DEBUG, logger=integration.__name__)

    assert integration.test_config_loading() is None

    _assert_only_exception_type_was_logged(caplog, "FileNotFoundError")
