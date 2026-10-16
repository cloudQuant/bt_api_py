"""MIG01-R1 fixed-source QA runner: retired forwarding entry points + residual scan.

Follows the ``iteration10-focused-qa.py`` convention (spec.json / command.json /
stdout.log / stderr.log / junit.xml / exit.txt / source-before.json /
source-after.json / result.json; fresh output dir; SHA-256 source pins and git
HEAD guards before/after).

Phases:
1. ``residual-scan`` (read-only): greps live trees for references to the legacy
   forwarding/gateway surface (``bt_api_py.forwarding``, ``bt_api_py.gateway``,
   ``bt_api_base.gateway``, ``GatewayRuntimeRegistrar``, ``ZmqBtApiBackend``,
   ZMQ transport selection) and inspects build artifacts (dist/*.whl,
   egg-info SOURCES.txt, setup.py, CI workflows).  Writes scan-residual.json,
   scan-artifacts.json, scan-behavior.json.  Never writes to the repo.
2. ``pytest``: the affected unit modules (spec["nodes"]).
3. After-checks: source pins and HEADs must be unchanged.

This round is an increment: it does NOT claim MIG01 acceptance (both legacy
directories are still present; the wheel still packages them).
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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

# ---------- phase 1: residual scan (read-only) ----------
cwd = Path(spec["cwd"])
scan = spec["scan"]
pattern_res = {name: re.compile(pattern) for name, pattern in scan["patterns"].items()}
inventory = {name: {} for name in scan["patterns"]}
files_hit = set()
residual_total = 0
for tree in scan["trees"]:
    root = Path(tree["root"])
    excluded = [Path(item) for item in tree.get("excluded_paths", [])]
    for path in sorted(root.rglob("*.py")):
        if any(path == item or item in path.parents for item in excluded):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            display = str(path.relative_to(cwd)).replace("\\", "/")
        except ValueError:
            display = str(path).replace("\\", "/")
        lines = text.splitlines()
        for name, regex in pattern_res.items():
            hits = [number for number, line in enumerate(lines, 1) if regex.search(line)]
            if hits:
                inventory[name][display] = hits
                residual_total += len(hits)
                files_hit.add(display)
legacy_dirs = {}
for item in scan["legacy_dirs"]:
    path = Path(item)
    legacy_dirs[str(path).replace("\\", "/")] = {
        "exists": path.is_dir(),
        "python_files": len(list(path.rglob("*.py"))) if path.is_dir() else 0,
    }
residual = {
    "trees": scan["trees"],
    "patterns": scan["patterns"],
    "legacy_dirs": legacy_dirs,
    "residual": inventory,
    "residual_total": residual_total,
    "files_with_residuals": sorted(files_hit),
}
write_json(out / "scan-residual.json", residual)

artifacts = {"wheels": [], "egg_info": [], "setup_py": {}, "ci_workflows": []}
for wheel_path in sorted(cwd.glob("dist/*.whl")):
    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()
    legacy_entries = sorted(n for n in names if "forwarding" in n or "gateway" in n)
    artifacts["wheels"].append(
        {
            "path": str(wheel_path.relative_to(cwd)).replace("\\", "/"),
            "total_entries": len(names),
            "legacy_entries": legacy_entries,
            "legacy_entry_count": len(legacy_entries),
        }
    )
for egg_path in (cwd / "bt_api_py.egg-info/SOURCES.txt",
                 cwd / "bt_api/bt_api_base/src/bt_api_base.egg-info/SOURCES.txt"):
    if egg_path.is_file():
        legacy_lines = sorted(
            line.strip()
            for line in egg_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if "forwarding" in line or "gateway" in line
        )
        artifacts["egg_info"].append(
            {
                "path": str(egg_path.relative_to(cwd)).replace("\\", "/"),
                "legacy_lines": legacy_lines,
                "legacy_line_count": len(legacy_lines),
            }
        )
setup_path = cwd / "setup.py"
if setup_path.is_file():
    setup_lines = [
        line.strip()
        for line in setup_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if "find_packages" in line
    ]
    artifacts["setup_py"] = {"find_packages_lines": setup_lines}
for workflow in sorted((cwd / ".github/workflows").glob("*.yml")):
    hits = [
        number
        for number, line in enumerate(workflow.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if "forwarding" in line or "gateway" in line
    ]
    if hits:
        artifacts["ci_workflows"].append(
            {"path": str(workflow.relative_to(cwd)).replace("\\", "/"), "lines": hits}
        )
write_json(out / "scan-artifacts.json", artifacts)

behavior_code = (
    "import json, sys, threading\n"
    "result = {}\n"
    "for key in list(sys.modules):\n"
    "    if key.startswith('bt_api_py.forwarding') or key == 'bt_api_py.gateway':\n"
    "        raise SystemExit('preloaded legacy module: ' + key)\n"
    "import bt_api_py\n"
    "from bt_api_py import BtApi\n"
    "from bt_api_py._contracts import ForwardingConfig\n"
    "from bt_api_py._contracts.errors import LegacyForwardingRetiredError\n"
    "try:\n"
    "    getattr(bt_api_py, 'ForwardingClient')\n"
    "    result['export'] = 'resolved'\n"
    "except LegacyForwardingRetiredError as exc:\n"
    "    result['export'] = {'error': type(exc).__name__, 'target': exc.target}\n"
    "threads_before = threading.active_count()\n"
    "try:\n"
    "    BtApi(debug=False, transport_mode='zmq', forwarding_config=ForwardingConfig('inproc://c', 'inproc://m', 'inproc://p', 'a', 's'))\n"
    "    result['zmq'] = 'constructed'\n"
    "except LegacyForwardingRetiredError as exc:\n"
    "    result['zmq'] = {'error': type(exc).__name__, 'target': exc.target}\n"
    "result['threads_stable'] = threading.active_count() == threads_before\n"
    "result['forwarding_loaded'] = any(k.startswith('bt_api_py.forwarding') for k in sys.modules)\n"
    "result['gateway_shim_loaded'] = 'bt_api_py.gateway' in sys.modules\n"
    "result['zmq_loaded'] = 'zmq' in sys.modules\n"
    "api = BtApi(debug=False)\n"
    "result['direct_mode'] = str(api.transport_mode)\n"
    "print(json.dumps(result))\n"
)
probe = subprocess.run(
    [spec["python"], "-c", behavior_code],
    cwd=spec["cwd"], env=env, capture_output=True, text=True, timeout=180,
)
behavior = {
    "returncode": probe.returncode,
    "stdout": probe.stdout.strip().splitlines()[-1] if probe.returncode == 0 and probe.stdout.strip() else None,
    "stderr_tail": probe.stderr.strip().splitlines()[-3:] if probe.stderr.strip() else [],
}
write_json(out / "scan-behavior.json", behavior)

# ---------- phase 2: fixed pytest argv ----------
argv = [spec["python"], "-m", "pytest", *spec["pytest_options"],
        "--junitxml=" + str(out / "junit.xml"), "--basetemp=" + str(out / "pytest-tmp"),
        *spec["nodes"]]
(out / "spec.json").write_bytes(spec_path.read_bytes())
(out / "runner.py").write_bytes(Path(__file__).read_bytes())
write_json(out / "command.json", {"argv": argv, "cwd": spec["cwd"], "environment": spec["environment"],
                                  "unset_environment": spec.get("unset_environment", []),
                                  "started_utc": now()})
write_json(out / "source-before.json", files)
with (out / "stdout.log").open("wb") as stdout, (out / "stderr.log").open("wb") as stderr:
    process = subprocess.Popen(argv, cwd=spec["cwd"], env=env, stdout=stdout, stderr=stderr)
    write_json(out / "running.json", {"pid": process.pid, "started_utc": now()})
    print(json.dumps({"pid": process.pid, "output": str(out)}), flush=True)
    code = process.wait()
after = {path: digest(path) for path in files}
heads_after = {repo: subprocess.check_output(["git", "-C", repo, "rev-parse", "HEAD"]).decode().strip()
               for repo in spec["repositories"]}
write_json(out / "source-after.json", after)
(out / "exit.txt").write_text(str(code) + "\n", encoding="utf-8")
scan_files = {}
for name in ("scan-residual.json", "scan-artifacts.json", "scan-behavior.json"):
    scan_files[name] = digest(out / name)
result = {"pytest_exit": code, "finished_utc": now(), "source_count": len(files),
          "sources_unchanged": files == after, "heads_before": heads_before, "heads_after": heads_after,
          "heads_unchanged": heads_before == heads_after,
          "stdout_sha256": digest(out / "stdout.log"), "stderr_sha256": digest(out / "stderr.log"),
          "evidence_level": spec["evidence_level"],
          "scan": {"residual_total": residual_total,
                   "files_with_residuals": len(files_hit),
                   "legacy_dirs": legacy_dirs,
                   "wheel_legacy_entries": sum(w["legacy_entry_count"] for w in artifacts["wheels"]),
                   "egg_info_legacy_lines": sum(e["legacy_line_count"] for e in artifacts["egg_info"]),
                   "ci_workflow_files": len(artifacts["ci_workflows"]),
                   "behavior": behavior["returncode"],
                   "scan_files": scan_files}}
write_json(out / "result.json", result)
print(json.dumps(result, ensure_ascii=False), flush=True)
assert files == after and heads_before == heads_after, "Dependency changed during QA"
raise SystemExit(code)
