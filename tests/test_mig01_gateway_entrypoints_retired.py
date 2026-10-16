"""Focused offline checks for the retired SDK gateway client entry points."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_gateway_import_and_construction_never_load_forwarding_runtime() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        """
        import importlib
        import json
        import sys

        import bt_api_py
        from bt_api_py._contracts.errors import LegacyForwardingRetiredError

        gateway = importlib.import_module("bt_api_py.gateway")
        client_module = importlib.import_module("bt_api_py.gateway.client")
        package_client = gateway.GatewayClient
        module_client = client_module.GatewayClient
        error = None
        try:
            module_client(command_endpoint="ipc://command", market_endpoint="ipc://market")
        except LegacyForwardingRetiredError as exc:
            error = {"type": type(exc).__name__, "target": exc.target, "detail": exc.detail}
        print(json.dumps({
            "same_class": package_client is module_client,
            "gateway_config": hasattr(gateway, "GatewayConfig"),
            "package_file": bt_api_py.__file__,
            "client_module_file": client_module.__file__,
            "forwarding_modules": sorted(
                name for name in sys.modules if name.startswith("bt_api_py.forwarding")
            ),
            "error": error,
        }))
        """
    )
    env = os.environ.copy()
    env.pop("BT_API_PY_LIGHT_IMPORT", None)
    inherited_python_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(repo_root) + (
        os.pathsep + inherited_python_path if inherited_python_path else ""
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    payload = json.loads(result.stdout)

    assert payload["same_class"] is True
    assert payload["gateway_config"] is True
    assert Path(payload["package_file"]).resolve() == (repo_root / "bt_api_py" / "__init__.py").resolve()
    assert Path(payload["client_module_file"]).resolve() == (
        repo_root / "bt_api_py" / "gateway" / "client.py"
    ).resolve()
    assert payload["forwarding_modules"] == []
    assert payload["error"]["type"] == "LegacyForwardingRetiredError"
    assert payload["error"]["target"] == "bt_api_py.gateway.client.GatewayClient"
    assert "ServiceClientRuntime" in payload["error"]["detail"]


def test_direct_btapi_rejects_legacy_forwarding_config_before_plugin_or_feed_setup(
    monkeypatch,
) -> None:
    from importlib import import_module

    import pytest

    from bt_api_py._contracts.errors import LegacyForwardingRetiredError
    from bt_api_py._contracts.models import ForwardingConfig

    bt_api_module = import_module("bt_api_py.bt_api")
    setup_calls: list[str] = []
    original_init_logger = bt_api_module.BtApi.init_logger
    original_event_bus = bt_api_module.EventBus

    def init_logger(self):
        setup_calls.append("logger")
        return original_init_logger(self)

    def event_bus():
        setup_calls.append("event_bus")
        return original_event_bus()

    monkeypatch.setattr(
        bt_api_module,
        "_ensure_plugins_loaded",
        lambda: setup_calls.append("plugins"),
    )
    monkeypatch.setattr(bt_api_module.BtApi, "init_logger", init_logger)
    monkeypatch.setattr(bt_api_module, "EventBus", event_bus)
    monkeypatch.setattr(
        bt_api_module.BtApi,
        "init_exchange",
        lambda self, settings: setup_calls.append("feeds"),
    )

    legacy_config = ForwardingConfig(
        command_endpoint="tcp://127.0.0.1:5999",
        market_endpoint="tcp://127.0.0.1:5998",
        private_endpoint="tcp://127.0.0.1:5997",
        account_id="paper",
        strategy_id="legacy-strategy",
    )
    with pytest.raises(LegacyForwardingRetiredError) as excinfo:
        bt_api_module.BtApi(
            {"SIM___SPOT": {}},
            debug=False,
            transport_mode="direct",
            forwarding_config=legacy_config,
        )

    assert excinfo.value.target == "forwarding_config"
    assert "ordinary direct/backtest use" in excinfo.value.detail
    assert "ServiceClientRuntime" in excinfo.value.detail
    assert setup_calls == []


def test_direct_btapi_without_legacy_config_keeps_normal_direct_backend(monkeypatch) -> None:
    from importlib import import_module
    import sys

    from bt_api_py import BtApi, TransportMode
    from bt_api_py._direct_backend import DirectBackend

    bt_api_module = import_module("bt_api_py.bt_api")
    plugin_initializations: list[str] = []
    monkeypatch.setattr(bt_api_module, "_plugins_loaded", False)
    monkeypatch.setattr(
        bt_api_module,
        "_initialize_plugin_and_legacy_registrations",
        lambda: plugin_initializations.append("initialized"),
    )
    forwarding_before = {
        name for name in sys.modules if name.startswith("bt_api_py.forwarding")
    }
    api = BtApi(debug=False)
    try:
        assert api.transport_mode is TransportMode.DIRECT
        assert type(api._backend) is DirectBackend
        assert api.exchange_feeds == {}
        assert api.get_request_api("UNKNOWN___SPOT") is None
    finally:
        api.close()
    assert plugin_initializations == ["initialized"]

    forwarding_after = {
        name for name in sys.modules if name.startswith("bt_api_py.forwarding")
    }
    assert forwarding_after == forwarding_before
