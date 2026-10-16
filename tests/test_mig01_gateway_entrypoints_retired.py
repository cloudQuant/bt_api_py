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
