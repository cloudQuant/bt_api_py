"""Reviewer-built spec for the REG04 INCR-2 fresh run (independent of author specs).

Recomputes all pins from the CURRENT working tree, asserts every r9 pin file
matches its r9-recorded hash, adds the new-file pin, and writes a spec whose
output directory does not exist yet.
"""
import hashlib
import json
import subprocess
from pathlib import Path

R9 = Path(r"D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/afi07-remainder-owner-group-legs-20261016/qa/r9/authored-spec-r9.json")
OUTDIR = Path(r"D:/temp/it10reg04-incr2-review-20261016-r1b")
RUN_OUT = OUTDIR / "run-rev1"
NEW_FILE = r"D:\source_code\ai-for-investor\src\backend\tests\test_service_read_reg04_workers.py"
FROZEN = r"D:\temp\afi_iter10_approved_target_sdk_sources_20261016_r6_fixed"
AFI = r"D:\source_code\ai-for-investor"
assert not RUN_OUT.exists(), "output dir must not exist"


def digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


r9 = json.loads(R9.read_text(encoding="utf-8"))
assert len(r9["source_sha256"]) == 2147
pins = {}
changed = []
for key, expected in r9["source_sha256"].items():
    actual = digest(Path(key))
    if actual != expected:
        changed.append(key)
    pins[key] = actual
assert not changed, changed[:10]
pins[NEW_FILE] = digest(Path(NEW_FILE))
assert pins[NEW_FILE] == "e8c35811387873affe184462f4f7568eaacde58f277bf2bca578b78e8f87277a"
assert len(pins) == 2148

head = subprocess.check_output(["git", "-C", AFI, "rev-parse", "HEAD"]).decode().strip()
branch = subprocess.check_output(["git", "-C", AFI, "branch", "--show-current"]).decode().strip()
assert branch == "dev"

spec = {
    "name": "iteration10-reg04-incr2-reviewer-run1",
    "output": str(RUN_OUT),
    "python": r"D:\source_code\ai-for-investor\src\backend\.venv\Scripts\python.exe",
    "cwd": r"D:\source_code\ai-for-investor\src\backend",
    "repositories": {AFI: head},
    "source_sha256": pins,
    "pytest_options": [
        "--noconftest",
        "--import-mode=importlib",
        "-p",
        "no:cacheprovider",
        "-c",
        r"D:\bt_api_py\.codex-tmp\iteration10-approved-target-http-pytest-r3.ini",
        "--rootdir",
        r"D:\source_code\ai-for-investor\src\backend",
        "-q",
        "--tb=short",
    ],
    "nodes": [
        "tests/test_service_read_reg04_workers.py::test_reg04_dual_worker_processes_and_strategy_run_share_one_owner",
        "tests/test_service_read_reg04_workers.py::test_reg04_worker_registration_under_live_strategy_claim_keeps_single_active_run",
    ],
    "environment": {
        "PYTHONPATH": ";".join(
            str(Path(FROZEN) / part)
            for part in ("root", "execution\\src", "base\\src", "transport\\src", "monitor\\src", "gateway\\src")
        ),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    },
    "unset_environment": [
        "PYTEST_ADDOPTS",
        "AFI_DUAL_LIVE_PREFLIGHT_ONLY",
        "PYTEST_PLUGINS",
        "PYTEST_CURRENT_TEST",
        "PYTHONHOME",
        "PYTHONUSERBASE",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
    ],
    "evidence_level": r9["evidence_level"],
}
dst = OUTDIR / "spec-review-run1.json"
dst.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("wrote", dst, "pins:", len(pins), "head:", head)
