"""Build spec-collect.json / spec-run1.json / spec-run2.json for REG04 INCR-2 runs.

Adapted from the AFI07 r9 authored spec: same environment invariants, same pinned
sources (2147 r9 pins, all re-verified to match the working tree) plus one new pin
for the new REG04 test file. Repository head refreshed to the current dev head.
"""
import hashlib
import json
from pathlib import Path

OUT = Path(r"D:/temp/it10reg04-incr2-20261016-r1")
R9_SPEC = Path(r"D:/bt_api_py/docs/迭代计划/迭代10-可选组件与可配置通信/evidence/afi07-remainder-owner-group-legs-20261016/qa/r9/spec.json")
NEW_FILE = r"D:\source_code\ai-for-investor\src\backend\tests\test_service_read_reg04_workers.py"
HEAD = "794eab0c26589ccad7ebb49899ac04e75a46d0cb"
FROZEN = r"D:\temp\afi_iter10_approved_target_sdk_sources_20261016_r6_fixed"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


spec = json.loads(R9_SPEC.read_text(encoding="utf-8"))
pins = dict(spec["source_sha256"])
assert len(pins) == 2147, len(pins)
assert NEW_FILE not in pins
pins[NEW_FILE] = digest(NEW_FILE)
assert len(pins) == 2148

base_opts = list(spec["pytest_options"])
run_nodes = [
    "tests/test_service_read_reg04_workers.py::test_reg04_dual_worker_processes_and_strategy_run_share_one_owner",
    "tests/test_service_read_reg04_workers.py::test_reg04_worker_registration_under_live_strategy_claim_keeps_single_active_run",
]


def make(filename, name, outdir, options, nodes):
    s = json.loads(json.dumps(spec))
    s["name"] = name
    s["output"] = str(outdir)
    s["repositories"] = {r"D:\source_code\ai-for-investor": HEAD}
    s["source_sha256"] = pins
    s["pytest_options"] = options
    s["nodes"] = nodes
    path = OUT / filename
    path.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path.name, "pins:", len(s["source_sha256"]), "nodes:", len(nodes))


make("spec-collect.json",
     "iteration10-reg04-afi-workers-single-active-claim-collect-only",
     OUT / "collect-only", base_opts + ["--collect-only"],
     ["tests/test_service_read_reg04_workers.py"])
make("spec-run1.json",
     "iteration10-reg04-afi-workers-single-active-claim-run1",
     OUT / "run1", base_opts, run_nodes)
make("spec-run2.json",
     "iteration10-reg04-afi-workers-single-active-claim-run2",
     OUT / "run2", base_opts, run_nodes)

tars = {}
for name in ("root", "execution", "base", "transport", "monitor", "gateway"):
    p = Path(FROZEN) / (name + ".tar")
    tars[str(p)] = {"size": p.stat().st_size, "sha256": digest(p)}
(OUT / "tars-before.json").write_text(json.dumps(tars, indent=2) + "\n", encoding="utf-8")
print("tars-before.json written; new-file sha256:", pins[NEW_FILE])
