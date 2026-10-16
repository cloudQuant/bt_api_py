"""Probe D: concurrent arm from two processes.

Part 1 (required): 40 iterations, two simultaneous processes arm the SAME
epoch pair. Invariants: both exit 0; final marker parses via the module and is
COMMITTED with the right epochs; no torn/leftover tmp files.

Part 2 (adversarial): 40 iterations, two simultaneous processes arm
DIFFERENT epoch pairs (racing check-then-write). Records how often the
conflict refusal actually fires vs both-succeed (last-writer-wins TOCTOU).

Exit 0 = part 1 invariants all held (part 2 is informational).
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import threading

sys.path.insert(0, os.environ.get("REVIEW_REPO", "D:/bt_api_py"))

from bt_api_py.service_migration_gate import (  # noqa: E402
    MigrationGateError,
    arm_migration_cutover,
    read_migration_marker,
)

BASE = sys.argv[1]
os.makedirs(BASE, exist_ok=True)
REPO = os.environ.get("REVIEW_REPO", "D:/bt_api_py")

OLD = "0f8d3b6c9a1e4f2d8b7a6c5d4e3f2a10"
NEW = "1a2b3c4d5e6f708192a3b4c5d6e7f801"
OTHER = "ffffffffffffffffffffffffffffffff"

failures: list[str] = []
checks: list[str] = []

ARM_SCRIPT = "\n".join(
    [
        "import json, sys",
        "from bt_api_py.service_migration_gate import MigrationGateError, arm_migration_cutover",
        f"state, src, dst = sys.argv[1], sys.argv[2], sys.argv[3]",
        "try:",
        "    m = arm_migration_cutover(state, source_epoch=src, new_epoch=dst)",
        "    print(json.dumps({'ok': True, 'migration_id': m.migration_id, 'status': m.status}))",
        "except MigrationGateError as exc:",
        "    print(json.dumps({'ok': False, 'code': exc.code}))",
        "except Exception as exc:",
        "    print(json.dumps({'ok': False, 'code': type(exc).__name__ + ': ' + str(exc)}))",
    ]
)

env = dict(os.environ)
env["PYTHONPATH"] = REPO + os.pathsep + env.get("PYTHONPATH", "")


def race(state: str, src_a: str, dst_a: str, src_b: str, dst_b: str) -> tuple[list[dict], int]:
    results: list[str | None] = [None, None]

    def run(idx: int) -> None:
        cp = subprocess.run(
            [sys.executable, "-c", ARM_SCRIPT, state, src_a if idx == 0 else src_b, dst_a if idx == 0 else dst_b],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        results[idx] = cp.stdout.strip().splitlines()[-1] if cp.stdout.strip() else f"RC={cp.returncode} {cp.stderr[-200:]}"

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    parsed = []
    for raw in results:
        try:
            parsed.append(json.loads(raw))
        except Exception:  # noqa: BLE001
            parsed.append({"ok": False, "code": f"UNPARSEABLE:{raw}"})
    return parsed, 0


# ---------------- Part 1: same pair ----------------
same_ok = 0
mismatch_returned_vs_final = 0
for i in range(40):
    d = os.path.join(BASE, f"same-{i:02d}")
    os.makedirs(d, exist_ok=True)
    state = os.path.join(d, "registry.sqlite3")
    with open(state, "wb") as fh:
        fh.write(b"state")
    parsed, _ = race(state, OLD, NEW, OLD, NEW)
    ok_both = all(p.get("ok") for p in parsed)
    if not ok_both:
        failures.append(f"same-{i:02d}: racers not both ok: {parsed}")
        continue
    try:
        m = read_migration_marker(state)
    except MigrationGateError as exc:
        failures.append(f"same-{i:02d}: final marker unreadable {exc.code}")
        continue
    if m is None or m.status != "COMMITTED" or m.source_epoch != OLD or m.destination_epoch != NEW:
        failures.append(f"same-{i:02d}: final marker wrong: {m}")
        continue
    leftovers = sorted(
        os.path.basename(p) for p in glob.glob(os.path.join(d, ".*tmp")) + glob.glob(os.path.join(d, "*.tmp"))
    )
    if leftovers:
        failures.append(f"same-{i:02d}: tmp leftovers {leftovers}")
        continue
    same_ok += 1
    if parsed[0]["migration_id"] != m.migration_id:
        mismatch_returned_vs_final += 1
checks.append(f"same-pair concurrent arm: {same_ok}/40 clean (both ok, single COMMITTED, no tmp leftovers)")

# ---------------- Part 2: conflicting pairs ----------------
both_succeeded = 0
conflict_refused = 0
other = 0
for i in range(40):
    d = os.path.join(BASE, f"diff-{i:02d}")
    os.makedirs(d, exist_ok=True)
    state = os.path.join(d, "registry.sqlite3")
    with open(state, "wb") as fh:
        fh.write(b"state")
    parsed, _ = race(state, OLD, NEW, OLD, OTHER)
    ok_count = sum(1 for p in parsed if p.get("ok"))
    if ok_count == 2:
        both_succeeded += 1
        m = read_migration_marker(state)
        checks.append(
            f"diff-{i:02d}: BOTH arms succeeded; final dst={m.destination_epoch} (last-writer-wins, no conflict fired)"
        )
    elif ok_count == 1:
        conflict_refused += 1
    else:
        other += 1
checks.append(
    f"conflicting-pair concurrent arm: conflict_refused={conflict_refused}/40 both_succeeded={both_succeeded}/40 other={other}/40"
)
checks.append(f"same-pair: racer-returned migration_id mismatched final file in {mismatch_returned_vs_final}/40")

print(json.dumps({"checks": checks, "failures": failures}, indent=1))
sys.exit(1 if failures else 0)
