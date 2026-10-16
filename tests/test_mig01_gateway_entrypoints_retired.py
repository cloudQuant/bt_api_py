"""The retired forwarding runtimes are absent; supported SDK entry points remain."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path


def _source_environment(repository: Path) -> dict[str, str]:
    environment = os.environ.copy()
    inherited = environment.get("PYTHONPATH", "").split(os.pathsep)
    base_src = repository / "bt_api" / "bt_api_base" / "src"
    paths: list[str] = []
    seen: set[str] = set()
    for raw_path in [str(repository), str(base_src), *inherited]:
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = repository / path
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized not in seen:
            seen.add(normalized)
            paths.append(str(path))
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    environment["BT_API_PY_LIGHT_IMPORT"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def test_retired_runtime_modules_are_absent_after_normal_package_import() -> None:
    repository = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        """
        import importlib.util
        import json
        import sys
        from pathlib import Path

        import bt_api_py
        import bt_api_py.runtime_plugins as runtime_plugins

        root = Path(bt_api_py.__file__).resolve().parent
        names = (
            "bt_api_py.forwarding",
            "bt_api_py.gateway",
            "bt_api_py._gateway_wire",
            "bt_api_py.runtime_plugins.gateway_dispatch",
        )
        modules = {name: importlib.util.find_spec(name) is not None for name in names}
        print(json.dumps({
            "package_file": str(Path(bt_api_py.__file__).resolve()),
            "runtime_plugins_file": str(Path(runtime_plugins.__file__).resolve()),
            "modules": modules,
            "loaded": sorted(name for name in sys.modules if name in names),
        }))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=repository,
        env=_source_environment(repository),
        timeout=15,
    )
    payload = json.loads(result.stdout)

    assert Path(payload["package_file"]) == (repository / "bt_api_py" / "__init__.py").resolve()
    assert (
        Path(payload["runtime_plugins_file"])
        == (repository / "bt_api_py" / "runtime_plugins" / "__init__.py").resolve()
    )
    assert payload["modules"] == {
        "bt_api_py.forwarding": False,
        "bt_api_py.gateway": False,
        "bt_api_py._gateway_wire": False,
        "bt_api_py.runtime_plugins.gateway_dispatch": False,
    }
    assert payload["loaded"] == []
    assert not (repository / "bt_api_py" / "forwarding").exists()
    assert not (repository / "bt_api_py" / "gateway").exists()


def test_direct_btapi_rejects_legacy_forwarding_config_before_setup(monkeypatch) -> None:
    from importlib import import_module

    import pytest

    from bt_api_py._contracts.errors import LegacyForwardingRetiredError
    from bt_api_py._contracts.models import ForwardingConfig

    bt_api_module = import_module("bt_api_py.bt_api")
    setup_calls: list[str] = []
    monkeypatch.setattr(
        bt_api_module,
        "_ensure_plugins_loaded",
        lambda: setup_calls.append("plugins"),
    )
    monkeypatch.setattr(
        bt_api_module.BtApi,
        "init_logger",
        lambda _self: setup_calls.append("logger"),
    )
    monkeypatch.setattr(
        bt_api_module.BtApi,
        "init_exchange",
        lambda _self, _settings: setup_calls.append("feeds"),
    )
    monkeypatch.setattr(
        bt_api_module,
        "EventBus",
        lambda: setup_calls.append("event_bus"),
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
    assert "ServiceExecutionSession" in excinfo.value.detail
    assert setup_calls == []


def test_direct_btapi_rejects_gateway_wire_config_before_setup(monkeypatch) -> None:
    import pytest

    from bt_api_py import BtApi
    from bt_api_py import bt_api as bt_api_module
    from bt_api_py._contracts.errors import LegacyForwardingRetiredError

    setup_calls: list[str] = []
    monkeypatch.setattr(
        bt_api_module,
        "_ensure_plugins_loaded",
        lambda: setup_calls.append("plugins"),
    )
    monkeypatch.setattr(
        bt_api_module.BtApi,
        "init_logger",
        lambda _self: setup_calls.append("logger"),
    )
    monkeypatch.setattr(
        bt_api_module.BtApi,
        "init_exchange",
        lambda _self, _settings: setup_calls.append("feeds"),
    )
    monkeypatch.setattr(
        bt_api_module,
        "EventBus",
        lambda: setup_calls.append("event_bus"),
    )

    with pytest.raises(LegacyForwardingRetiredError) as excinfo:
        BtApi({"SIM___SPOT": {}}, debug=False, gateway_wire_config=object())

    assert excinfo.value.target == "gateway_wire_config"
    assert "ServiceExecutionSession" in excinfo.value.detail
    assert "service.order_entry" in excinfo.value.detail
    assert setup_calls == []


def test_direct_btapi_without_legacy_inputs_keeps_normal_backend(monkeypatch) -> None:
    import sys

    from bt_api_py import BtApi, TransportMode
    from bt_api_py import bt_api as bt_api_module
    from bt_api_py._direct_backend import DirectBackend

    plugin_initializations: list[str] = []
    monkeypatch.setattr(bt_api_module, "_plugins_loaded", False)
    monkeypatch.setattr(
        bt_api_module,
        "_initialize_plugins",
        lambda: plugin_initializations.append("initialized"),
    )
    legacy_modules_before = {
        name
        for name in sys.modules
        if name.startswith(("bt_api_py.forwarding", "bt_api_py.gateway"))
        or name in {"bt_api_py._gateway_wire", "bt_api_py.runtime_plugins.gateway_dispatch"}
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
    legacy_modules_after = {
        name
        for name in sys.modules
        if name.startswith(("bt_api_py.forwarding", "bt_api_py.gateway"))
        or name in {"bt_api_py._gateway_wire", "bt_api_py.runtime_plugins.gateway_dispatch"}
    }
    assert legacy_modules_after == legacy_modules_before == set()
