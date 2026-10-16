"""MIG01-R2 fixed-source QA runner: re-homed SDK modules + determinism + regression spot-run.

Mirrors the MIG01-R1 runner shape (spec.json / command.json / stdout.log /
stderr.log / junit.xml / exit.txt / source-before.json / source-after.json /
result.json; fresh output dir; SHA-256 source pins and git HEAD guards
before/after), extended to a fixed sequence of pytest runs from spec["runs"]:

1. ``rehomed-r1`` / ``rehomed-r2``: the two re-homed modules (determinism:
   >=2 clean runs), gate-waited on ``ps -W`` before each phase (bounded 120s
   per gate; never two suites at once).
2. ``rehomed-collect``: ``--collect-only`` over the same set.
3. ``targets-and-regression``: the 4 previously-RED target modules (2 re-homed,
   2 gap-report modules that stay RED by design) plus the regression spot-run
   set (legacy forwarding tests + contract modules).  The expected failing set
   (spec["expected_targets_failures"]) is checked explicitly; anything else
   unexpected is recorded and fails the QA.

This round is an increment: it re-homes 2 of the 4 retired-factory test modules
and documents the wire gap for the other 2; it does NOT claim MIG01 acceptance.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# Resident daemons (since Oct 13) are NOT test runners; exclude both their
# git-bash PIDs and their WINPIDs, plus this runner process itself and any
# ai-for-investor venv child.
RESIDENT_IDS = {"128016", "132224", "62480", "66688"}
EXCLUDED_COMMAND_TOKENS = ("ai-for-investor",)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot_python_processes():
    text = subprocess.check_output(["ps", "-W"], text=True, errors="replace")
    rows = []
    for line in text.splitlines():
        if "python" not in line.lower():
            continue
        parts = line.split(None, 7)
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        rows.append({"ps_pid": parts[0], "winpid": parts[3], "command": " ".join(parts[3:])[:220]})
    return rows


def foreign_python_processes():
    rows = []
    for row in snapshot_python_processes():
        if row["ps_pid"] in RESIDENT_IDS or row["winpid"] in RESIDENT_IDS:
            continue
        if row["winpid"] == str(os.getpid()):
            continue
        if any(token in row["command"].lower() for token in EXCLUDED_COMMAND_TOKENS):
            continue
        rows.append(row)
    return rows


def ensure_serialized(label):
    """Bounded (<=120s) wait until no foreign python suite is running."""
    observations = []
    deadline = time.time() + 120.0
    while True:
        foreign = foreign_python_processes()
        observations.append({"utc": now(), "foreign": foreign})
        if not foreign:
            return {"run": label, "clear": True, "observations": observations}
        if time.time() >= deadline:
            observations.append({"utc": now(), "timeout": True})
            raise RuntimeError("serialization gate timeout: " + json.dumps(foreign))
        time.sleep(5.0)


def junit_summary(path):
    root = ET.parse(path).getroot()
    merged = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "failing": []}
    for suite in root.iter("testsuite"):
        merged["tests"] += int(suite.get("tests", "0"))
        merged["failures"] += int(suite.get("failures", "0"))
        merged["errors"] += int(suite.get("errors", "0"))
        merged["skipped"] += int(suite.get("skipped", "0"))
    for case in root.iter("testcase"):
        outcome = None
        for child in case:
            if child.tag in ("failure", "error"):
                outcome = child.tag
                break
            if child.tag == "skipped":
                outcome = "skipped"
                break
        if outcome in ("failure", "error"):
            merged["failing"].append(
                {
                    "id": f"{case.get('classname', '')}::{case.get('name', '')}",
                    "outcome": outcome,
                    "file": case.get("file"),
                }
            )
    merged["passed"] = merged["tests"] - merged["failures"] - merged["errors"] - merged["skipped"]
    merged["failing"].sort(key=lambda item: item["id"])
    return merged


spec_path = Path(sys.argv[1]).resolve(strict=True)
spec = json.loads(spec_path.read_text(encoding="utf-8"))
out = Path(spec["output"])
assert not out.exists(), "A consumed output directory cannot be reused"
assert Path(spec["python"]).is_file()
assert Path(spec["cwd"]).is_dir()
files = spec["source_sha256"]
assert files and all(digest(path) == expected for path, expected in files.items())
heads_before = {}
for repo in spec["repositories"]:
    branch = subprocess.check_output(["git", "-C", repo, "branch", "--show-current"]).decode().strip()
    assert branch == "dev", (repo, branch)
    heads_before[repo] = subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"]).decode().strip()
assert heads_before == spec["repositories"]
env = os.environ.copy()
for key in spec.get("unset_environment", []):
    env.pop(key, None)
env.update(spec["environment"])
assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
out.mkdir(parents=True)

# ---------- pytest runs ----------
(out / "spec.json").write_bytes(spec_path.read_bytes())
(out / "runner.py").write_bytes(Path(__file__).read_bytes())
write_json(out / "source-before.json", files)

runs = []
gates = []
for run_spec in spec["runs"]:
    run_id = run_spec["id"]
    run_dir = out / run_id
    run_dir.mkdir(parents=True)
    gate = ensure_serialized(run_id)
    gates.append(gate)
    argv = [spec["python"], "-m", "pytest", *spec["pytest_options"]]
    if run_spec["mode"] == "collect-only":
        argv.append("--collect-only")
    argv += ["--junitxml=" + str(run_dir / "junit.xml"),
             "--basetemp=" + str(run_dir / "pytest-tmp"),
             *run_spec["nodes"]]
    write_json(run_dir / "command.json", {
        "run": run_id,
        "mode": run_spec["mode"],
        "argv": argv,
        "cwd": spec["cwd"],
        "environment": spec["environment"],
        "unset_environment": spec.get("unset_environment", []),
        "serialization": gate,
        "started_utc": now(),
    })
    started = now()
    print(json.dumps({"run": run_id, "argv": argv}), flush=True)
    with (run_dir / "stdout.log").open("wb") as stdout, (run_dir / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen(argv, cwd=spec["cwd"], env=env, stdout=stdout, stderr=stderr)
        code = process.wait()
    (run_dir / "exit.txt").write_text(str(code) + "\n", encoding="utf-8")
    entry = {
        "id": run_id,
        "mode": run_spec["mode"],
        "nodes": run_spec["nodes"],
        "exit": code,
        "started_utc": started,
        "finished_utc": now(),
        "stdout_sha256": digest(run_dir / "stdout.log"),
        "stderr_sha256": digest(run_dir / "stderr.log"),
    }
    if (run_dir / "junit.xml").is_file():
        entry["junit"] = junit_summary(run_dir / "junit.xml")
    runs.append(entry)
    print(json.dumps({"run": run_id, "exit": code,
                      "junit": {k: v for k, v in entry.get("junit", {}).items() if k != "failing"}}), flush=True)

# ---------- expected-failure adjudication for the targets run ----------
expected = spec.get("expected_targets_failures", {})
targets_run = next((run for run in runs if run["id"] == "targets-and-regression"), None)
adjudication = {"expected_failures": expected, "gap_counts": {}, "unexpected_failures": [], "ok": None}
if targets_run and "junit" in targets_run:
    failing = targets_run["junit"]["failing"]
    matched = set()
    for module_path, count in expected.items():
        token = Path(module_path).stem
        ids = [item["id"] for item in failing if token in item["id"]]
        adjudication["gap_counts"][module_path] = {"expected": count, "actual": len(ids), "ids": sorted(ids)}
        if len(ids) == count:
            matched.update(ids)
    adjudication["unexpected_failures"] = [item for item in failing if item["id"] not in matched]
    adjudication["ok"] = (
        not adjudication["unexpected_failures"]
        and all(entry["expected"] == entry["actual"] for entry in adjudication["gap_counts"].values())
    )

# ---------- determinism pair ----------
det = {"pair": ["rehomed-r1", "rehomed-r2"], "deterministic": None}
by_id = {run["id"]: run for run in runs}
first, second = by_id.get("rehomed-r1"), by_id.get("rehomed-r2")
if first and second and "junit" in first and "junit" in second:
    def finger(run):
        junit = run["junit"]
        return (junit["tests"], junit["failures"], junit["errors"], junit["skipped"],
                [item["id"] for item in junit["failing"]])
    det["fingerprint_r1"] = list(finger(first)[:4])
    det["fingerprint_r2"] = list(finger(second)[:4])
    det["deterministic"] = finger(first) == finger(second)
collect_run = by_id.get("rehomed-collect")
det["collect_exit"] = collect_run["exit"] if collect_run else None

after = {path: digest(path) for path in files}
heads_after = {repo: subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"]).decode().strip()
               for repo in spec["repositories"]}
write_json(out / "source-after.json", after)

clean_runs = [run["id"] for run in runs if run["id"] in ("rehomed-r1", "rehomed-r2", "rehomed-collect")]
clean_ok = all(by_id[run_id]["exit"] == 0 for run_id in clean_runs if run_id in by_id)
qa_ok = bool(clean_ok and adjudication["ok"] and det["deterministic"] and collect_run and collect_run["exit"] == 0)
result = {
    "artifact_id": spec["artifact_id"],
    "pytest_runs": runs,
    "adjudication": adjudication,
    "determinism": det,
    "serialization_gates": gates,
    "sources_unchanged": files == after,
    "heads_before": heads_before,
    "heads_after": heads_after,
    "heads_unchanged": heads_before == heads_after,
    "evidence_level": spec["evidence_level"],
    "finished_utc": now(),
    "qa_ok": qa_ok,
}
write_json(out / "result.json", result)
print(json.dumps({"qa_ok": qa_ok, "runs": {run["id"]: run["exit"] for run in runs},
                  "sources_unchanged": result["sources_unchanged"],
                  "heads_unchanged": result["heads_unchanged"]}, ensure_ascii=False), flush=True)
assert files == after and heads_before == heads_after, "Dependency changed during QA"
raise SystemExit(0 if qa_ok else 1)
