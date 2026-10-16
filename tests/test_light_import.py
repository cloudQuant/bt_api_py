import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path


def _source_environment(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    inherited = env.get("PYTHONPATH", "").split(os.pathsep)
    base_src = repo_root / "bt_api" / "bt_api_base" / "src"
    paths: list[str] = []
    seen: set[str] = set()
    for raw_path in (str(repo_root), str(base_src), *inherited):
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = repo_root / path
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized not in seen:
            seen.add(normalized)
            paths.append(str(path))
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["BT_API_PY_LIGHT_IMPORT"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def test_light_import_skips_gateway_unneeded_provider_modules():
    repo_root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        """
        import json
        import sys

        import bt_api_py
        import bt_api_py.runtime_plugins as runtime_plugins

        heavy = [
            name
            for name in sys.modules
            if name.startswith(("bt_api_py.ctp", "bt_api_ctp", "pandas", "numpy", "scipy", "pyarrow"))
        ]
        print(json.dumps({
            "btapi_bound": "BtApi" in vars(bt_api_py),
            "package_file": str(bt_api_py.__file__),
            "runtime_plugins_file": str(runtime_plugins.__file__),
            "retired_modules": sorted(
                name
                for name in sys.modules
                if name.startswith(("bt_api_py.forwarding", "bt_api_py.gateway"))
                or name in {
                    "bt_api_py._gateway_wire",
                    "bt_api_py.runtime_plugins.gateway_dispatch",
                }
            ),
            "heavy": heavy,
        }))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=_source_environment(repo_root),
        cwd=repo_root,
        timeout=15,
    )
    payload = json.loads(result.stdout)

    assert payload["btapi_bound"] is False
    assert (
        Path(payload["package_file"]).resolve()
        == (repo_root / "bt_api_py" / "__init__.py").resolve()
    )
    assert (
        Path(payload["runtime_plugins_file"]).resolve()
        == (repo_root / "bt_api_py" / "runtime_plugins" / "__init__.py").resolve()
    )
    assert payload["retired_modules"] == []
    assert payload["heavy"] == []


def test_simnow_top_level_exports_load_only_when_requested():
    repo_root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(
        """
        import json
        import sys

        import bt_api_py
        before = "bt_api_py.ctp_simnow_execution" in sys.modules
        from bt_api_py import CtpSimNowExecutionAdapter, OFFICIAL_SET1_PROFILES
        print(json.dumps({
            "before": before,
            "after": "bt_api_py.ctp_simnow_execution" in sys.modules,
            "package_file": str(bt_api_py.__file__),
            "adapter_module": CtpSimNowExecutionAdapter.__module__,
            "profiles": sorted(OFFICIAL_SET1_PROFILES),
        }))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=_source_environment(repo_root),
        cwd=repo_root,
        timeout=15,
    )
    payload = json.loads(result.stdout)

    assert payload == {
        "before": False,
        "after": True,
        "package_file": str(repo_root / "bt_api_py" / "__init__.py"),
        "adapter_module": "bt_api_py.ctp_simnow_execution",
        "profiles": ["set1_group1", "set1_group2"],
    }
