"""R5 supplementary probe v2 (NOT the mission-exact env): demonstrate the
designed in-process ``legacy_direct_execution_error`` guard in the retired
live run.py ``main()``.

Notes on the environment quirks this driver works around (all captured raw
in the sibling evidence folders):
- The target module's fence computes a wrong repository root and inserts
  ``D:/source_code`` on sys.path (see ../fence-path-math.txt).
- With ``D:/source_code`` on sys.path before the checkout root, ``import
  backtrader`` resolves to the checkout directory itself as a PEP-420
  namespace package, shadowing the real ``backtrader/backtrader`` package
  (this caused the v1 driver failure captured in driver.stderr.txt).
- This driver therefore imports the real ``backtrader`` package first (so
  sys.modules caches it), then execs the target module by file path, then
  calls the module-level ``main()`` and prints the redacted error payload.

Read-only: imports modules, calls main(), prints JSON, exits.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(r"D:/source_code/backtrader")
TARGET = REPO_ROOT / "examples/unified/007_ctp/ctp_bbroker_5s_examples/live/run.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import backtrader  # noqa: F401,E402  (cache the real package before the fence shadow)

spec = importlib.util.spec_from_file_location("r5_probe_bbroker_5s_live_v2", TARGET)
module = importlib.util.module_from_spec(spec)
import_failure = None
try:
    spec.loader.exec_module(module)
except Exception as exc:  # noqa: BLE001 - raw capture is the point
    import_failure = {"type": type(exc).__name__, "error": str(exc)}

result = {
    "module_import_failure": import_failure,
    "main_defined": hasattr(module, "main"),
}
if result["main_defined"]:
    try:
        module.main()
        result["main_returned_without_raising"] = True
    except Exception as exc:  # noqa: BLE001
        as_dict = getattr(exc, "as_dict", None)
        result["main_raised_type"] = type(exc).__name__
        result["main_raised_error"] = as_dict() if callable(as_dict) else str(exc)

print(json.dumps(result, ensure_ascii=False, sort_keys=True))
raise SystemExit(0)
