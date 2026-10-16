"""MIG04 pilot: strategy-side isolation check, run inside the strategy venv.

cwd must be OUTSIDE any repository. Verifies:
  - bt_api_py imports and its file lives in the venv site-packages
  - bt_api_gateway / bt_api_execution / bt_api_risk / bt_api_monitor are absent
  - zmq (pyzmq) is absent
Exit 0 only if all isolation facts hold; prints a JSON report.
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import importlib.util
import json
import sys
import sysconfig
from pathlib import Path

report: dict[str, object] = {
    "python": sys.version,
    "executable": sys.executable,
    "prefix": sys.prefix,
    "include_system_site_packages": None,
}

pyvenv = Path(sys.prefix) / "pyvenv.cfg"
if pyvenv.is_file():
    for line in pyvenv.read_text(encoding="utf-8").splitlines():
        if line.lower().startswith("include-system-site-packages"):
            report["include_system_site_packages"] = line.split("=", 1)[1].strip()

import bt_api_py  # noqa: E402

bt_file = Path(bt_api_py.__file__).resolve()
purelib = Path(sysconfig.get_paths()["purelib"]).resolve()
report["bt_api_py_file"] = str(bt_file)
report["bt_api_py_in_site_packages"] = bt_file.is_relative_to(purelib)
report["bt_api_py_version"] = getattr(bt_api_py, "__version__", None)
try:
    report["bt_api_py_dist_version"] = md.version("bt_api_py")
except md.PackageNotFoundError:
    report["bt_api_py_dist_version"] = None

import bt_api_base  # noqa: E402

base_file = Path(bt_api_base.__file__).resolve()
report["bt_api_base_file"] = str(base_file)
report["bt_api_base_in_site_packages"] = base_file.is_relative_to(purelib)

absence_checks = {
    "bt_api_gateway": "bt_api_gateway",
    "bt_api_execution": "bt_api_execution",
    "bt_api_risk": "bt_api_risk",
    "bt_api_monitor": "bt_api_monitor",
    "zmq": "pyzmq",
}
rows: dict[str, dict[str, object]] = {}
for module_name, dist_name in absence_checks.items():
    row: dict[str, object] = {}
    try:
        row["find_spec"] = importlib.util.find_spec(module_name) is not None
    except Exception as error:  # noqa: BLE001 - pilot records raw outcome
        row["find_spec"] = f"{type(error).__name__}: {error}"
    try:
        importlib.import_module(module_name)
        row["import"] = "IMPORTED"
    except Exception as error:  # noqa: BLE001
        row["import"] = f"{type(error).__name__}: {error}"
    try:
        row["distribution"] = md.version(dist_name)
    except md.PackageNotFoundError:
        row["distribution"] = None
    rows[module_name] = row
report["absence_checks"] = rows

print(json.dumps(report, indent=2, sort_keys=True))

isolated = (
    report["bt_api_py_in_site_packages"] is True
    and report["bt_api_base_in_site_packages"] is True
    and report["include_system_site_packages"] == "false"
    and all(rows[name]["import"] != "IMPORTED" for name in absence_checks)
)
sys.exit(0 if isolated else 1)
