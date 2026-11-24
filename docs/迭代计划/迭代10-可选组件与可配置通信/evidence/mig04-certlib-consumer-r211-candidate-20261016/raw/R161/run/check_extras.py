"""MIG04 r2: risk extras-gating check, run inside the extras venv.

The venv has bt_api_base + bt_api_risk wheels installed WITHOUT [analytics]
and WITHOUT [ml] (plain requirement names, no extras). Verifies the a1485ee
gating boundary:
  - scikit-learn (ml extra) is genuinely absent as a distribution
  - numpy is present only because bt_api_base core-requires it (recorded via
    importlib.metadata requires-dist; the risk wheel contributes no sklearn)
  - importing the base/admission/service-catalog path loads no
    numpy/sklearn/scipy/joblib into sys.modules
  - in this real venv: AnomalyDetector / RiskEnsembleModel raise an explicit
    ImportError naming the 'ml' extra (sklearn truly absent)
  - under simulated absence of numpy/sklearn (child process with a meta-path
    import blocker) the RiskManager export raises an explicit ImportError
    naming 'analytics' and the two ML exports raise 'ml', while the base
    service path still imports fine
Prints a JSON report; exits 0 only if the whole boundary holds.
"""

from __future__ import annotations

import importlib.metadata as md
import json
import subprocess
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent

report: dict[str, object] = {
    "python": sys.version,
    "executable": sys.executable,
    "prefix": sys.prefix,
}

for dist in ("numpy", "scikit-learn", "scipy", "joblib", "pandas"):
    try:
        report[f"{dist}_installed"] = md.version(dist)
    except md.PackageNotFoundError:
        report[f"{dist}_installed"] = None

for dist in ("bt_api_base", "bt_api_risk"):
    try:
        report[f"{dist}_requires_dist"] = md.requires(dist)
    except md.PackageNotFoundError:  # pragma: no cover - record, do not fail here
        report[f"{dist}_requires_dist"] = None

try:
    metadata = md.metadata("bt_api_risk")
    report["bt_api_risk_provides_extras"] = metadata.get_all("Provides-Extra")
    report["bt_api_risk_direct_url"] = md.distribution("bt_api_risk").read_text("direct_url.json")
except Exception as error:  # noqa: BLE001
    report["bt_api_risk_metadata_error"] = f"{type(error).__name__}: {error}"

# --- base path import (no extras requested) --------------------------------
import bt_api_risk  # noqa: E402

report["bt_api_risk_file"] = str(Path(bt_api_risk.__file__).resolve())

import bt_api_risk.service_component as service_component  # noqa: E402

report["service_component_file"] = str(Path(service_component.__file__).resolve())
role_ports = service_component._RISK_ROLE_PORTS
report["policy_catalog_port"] = sorted(role_ports["policy_catalog"])
report["catalog_classes"] = {
    "RiskReadService": getattr(service_component, "RiskReadService", None) is not None,
    "validate_service_options": callable(
        getattr(service_component, "validate_service_options", None)
    ),
}

from bt_api_risk.core.admission import RiskPolicy  # noqa: E402

report["RiskPolicy_import"] = type(RiskPolicy).__name__

from bt_api_risk import DurableRiskGate  # noqa: E402

report["DurableRiskGate_import"] = type(DurableRiskGate).__name__

from bt_api_risk.order_admission import RiskAdmissionPolicyBinding  # noqa: E402

report["RiskAdmissionPolicyBinding_import"] = type(RiskAdmissionPolicyBinding).__name__

report["base_path_sys_modules"] = {
    name: name in sys.modules for name in ("numpy", "sklearn", "scipy", "joblib")
}


def _request_optional(name: str) -> dict[str, object]:
    try:
        getattr(bt_api_risk, name)
        return {"outcome": "IMPORTED", "import_error": None}
    except ImportError as error:
        return {"outcome": f"ImportError: {error}", "import_error": str(error)}
    except BaseException as error:  # noqa: BLE001
        return {"outcome": f"{type(error).__name__}: {error}", "import_error": None}


optional_names = ("RiskManager", "AnomalyDetector", "RiskEnsembleModel", "RiskAssessor")
report["real_venv_requests"] = {name: _request_optional(name) for name in optional_names}

# --- simulated absence: meta-path blocker in a child process ---------------
_blocker_code = r'''
import json, sys

BLOCKED = {"numpy", "sklearn", "scipy", "joblib"}


class _BlockedExtraImport:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in BLOCKED:
            raise ImportError(
                f"simulated absence: {fullname} is not importable (extra not installed)"
            )
        return None


sys.meta_path.insert(0, _BlockedExtraImport())

import bt_api_risk
import bt_api_risk.service_component
from bt_api_risk.order_admission import RiskAdmissionPolicyBinding

result = {
    "blocked_modules": sorted(BLOCKED),
    "base_path_under_absence": "imported bt_api_risk + service_component + order_admission",
}
for name in ("RiskManager", "AnomalyDetector", "RiskEnsembleModel", "RiskAssessor"):
    try:
        getattr(bt_api_risk, name)
        result[name] = "IMPORTED"
    except ImportError as error:
        result[name] = f"ImportError: {error}"
    except BaseException as error:
        result[name] = f"{type(error).__name__}: {error}"
print(json.dumps(result))
'''
proc = subprocess.run(
    [sys.executable, "-c", _blocker_code],
    capture_output=True,
    text=True,
    cwd=str(OUT_DIR),
)
report["absence_simulation_exit"] = proc.returncode
stdout_lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
report["absence_simulation"] = json.loads(stdout_lines[-1]) if stdout_lines else None
stderr_lines = [line for line in proc.stderr.strip().splitlines() if line.strip()]
report["absence_simulation_stderr_tail"] = stderr_lines[-5:]

print(json.dumps(report, indent=2, sort_keys=True))


def _explicit(entry: object, extra: str) -> bool:
    if not isinstance(entry, dict):
        return False
    error = entry.get("import_error")
    return isinstance(error, str) and f"'{extra}'" in error


real = report["real_venv_requests"]
sim = report["absence_simulation"] or {}

ok = (
    report["scikit-learn_installed"] is None
    and isinstance(report["numpy_installed"], str)
    and not any(report["base_path_sys_modules"].values())
    and report["catalog_classes"]["RiskReadService"] is True
    and _explicit(real["AnomalyDetector"], "ml")
    and _explicit(real["RiskEnsembleModel"], "ml")
    and isinstance(sim.get("RiskManager"), str)
    and "'analytics'" in sim["RiskManager"]
    and isinstance(sim.get("AnomalyDetector"), str)
    and "'ml'" in sim["AnomalyDetector"]
    and isinstance(sim.get("RiskEnsembleModel"), str)
    and "'ml'" in sim["RiskEnsembleModel"]
    and "base_path_under_absence" in sim
)
sys.exit(0 if ok else 1)
