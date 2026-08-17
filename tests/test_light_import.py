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
