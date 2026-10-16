"""R5 supplementary probe (NOT the mission-exact env): call the retired
live run.py ``main()`` in-process and print its redacted RuntimeConfigError
payload.

The target module's own fence computes a wrong repository root (see
../fence-path-math.txt), so this driver deliberately inserts the checkout
root on sys.path to let the module-level import resolve; only then can the
designed ``legacy_direct_execution_error`` guard in ``main()`` be observed.

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

spec = importlib.util.spec_from_file_location("r5_probe_bbroker_5s_live", TARGET)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

try:
    module.main()
except Exception as exc:  # noqa: BLE001 - raw capture is the point
    payload = {"raised_type": type(exc).__name__}
    as_dict = getattr(exc, "as_dict", None)
    payload["error"] = as_dict() if callable(as_dict) else str(exc)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    raise SystemExit(2)

print(json.dumps({"raised_type": None, "note": "main() returned without raising"}, ensure_ascii=False))
raise SystemExit(0)
