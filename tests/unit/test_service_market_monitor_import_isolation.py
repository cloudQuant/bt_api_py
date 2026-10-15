"""Verify the opt-in SDK relay imports without any domain service package."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ISOLATED_IMPORT = r"""
import importlib.abc
import importlib.util
import json
import sys
from pathlib import Path

sdk_root = Path(sys.argv[1])
base_source = Path(sys.argv[2])
sys.path.insert(0, str(base_source))
sys.path.insert(0, str(sdk_root))

blocked_roots = {
    "bt_api_monitor",
    "bt_api_gateway",
    "bt_api_execution",
    "bt_api_risk",
}
attempted = []

class DomainImportBlocked(ImportError):
    pass

class DomainImportBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        del path, target
        if any(fullname == root or fullname.startswith(root + ".") for root in blocked_roots):
            attempted.append(fullname)
            raise DomainImportBlocked(fullname)
        return None

sys.meta_path.insert(0, DomainImportBlocker())
try:
    module = importlib.import_module("bt_api_py.service_market_monitor")
except DomainImportBlocked as error:
    print(json.dumps({"blocked_import": str(error), "attempted": attempted}))
    raise

module_origin_matches_checkout = Path(module.__file__).resolve() == (
    sdk_root / "bt_api_py" / "service_market_monitor.py"
).resolve()
loaded = sorted(
    name
    for name in sys.modules
    if any(name == root or name.startswith(root + ".") for root in blocked_roots)
)
report = {
    "module_imported": "bt_api_py.service_market_monitor" in sys.modules,
    "module_origin_matches_checkout": module_origin_matches_checkout,
    "attempted_domain_imports": attempted,
    "loaded_domain_modules": loaded,
}
print(json.dumps(report, sort_keys=True))
assert report["module_imported"]
assert module_origin_matches_checkout
assert not attempted
assert not loaded
"""


def test_service_market_monitor_import_isolated_from_domain_packages() -> None:
    sdk_root = Path(__file__).resolve().parents[2]
    base_source = sdk_root / "bt_api" / "bt_api_base" / "src"
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            _ISOLATED_IMPORT,
            str(sdk_root),
            str(base_source),
        ],
        cwd=sdk_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, (
        f"isolated import failed: exit={completed.returncode}; stderr={completed.stderr!r}"
    )
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report == {
        "module_imported": True,
        "module_origin_matches_checkout": True,
        "attempted_domain_imports": [],
        "loaded_domain_modules": [],
    }
