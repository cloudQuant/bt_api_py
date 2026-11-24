"""MIG04 pilot: consumer checks, run inside the consumers venv.

The venv has bt_api_base + bt_api_py + bt_api_binance + backtrader wheels
installed (all built from pinned git archives). Verifies:
  - import backtrader works after wheel install
  - the bt_api.plugins entry point for binance loads (provider wheel)
  - importing the binance_penetration/certlib package from the backtrader
    SOURCE extraction: exact exception is recorded (a gap, not a fix request)
Prints a JSON report; exits 0 if backtrader imports and every attempted
action produced a recorded outcome, exits 1 only on harness-level breakage.
"""

from __future__ import annotations

import importlib
import importlib.metadata as md
import json
import sys
import traceback
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent
source_certlib_root = (
    OUT_DIR / "src" / "backtrader" / "examples" / "live" / "000_live_certification" / "binance_penetration"
)

report: dict[str, object] = {
    "python": sys.version,
    "executable": sys.executable,
    "prefix": sys.prefix,
}

import backtrader  # noqa: E402

report["backtrader_file"] = str(Path(backtrader.__file__).resolve())
report["backtrader_version"] = getattr(backtrader, "__version__", None)

entry_points = md.entry_points(group="bt_api.plugins")
report["bt_api_plugins_entry_points"] = sorted(ep.name for ep in entry_points)
for ep in entry_points:
    if ep.name == "binance":
        try:
            loaded = ep.load()
            report["binance_entry_point_load"] = f"ok: {loaded!r}"
        except BaseException as error:  # noqa: BLE001
            report["binance_entry_point_load"] = f"{type(error).__name__}: {error}"

try:
    import bt_api_binance  # noqa: E402

    report["bt_api_binance_file"] = str(Path(bt_api_binance.__file__).resolve())
    report["bt_api_binance_version"] = md.version("bt_api_binance")
except BaseException as error:  # noqa: BLE001
    report["bt_api_binance_import"] = f"{type(error).__name__}: {error}"

report["certlib_source_root"] = str(source_certlib_root)
report["certlib_source_root_exists"] = source_certlib_root.is_dir()
sys.path.insert(0, str(source_certlib_root))
try:
    certlib = importlib.import_module("certlib")
    report["certlib_import"] = f"IMPORTED {certlib.__file__}"
except BaseException as error:  # noqa: BLE001 - the exact exception is the evidence
    report["certlib_import"] = f"{type(error).__name__}: {error}"
    report["certlib_traceback_tail"] = traceback.format_exc().splitlines()[-8:]

print(json.dumps(report, indent=2, sort_keys=True))
sys.exit(0 if report.get("backtrader_file") else 1)
