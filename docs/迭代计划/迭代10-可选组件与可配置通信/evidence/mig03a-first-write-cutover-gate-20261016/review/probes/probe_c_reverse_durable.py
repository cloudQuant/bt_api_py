"""Probe C: accepted reverse migration is durable across a second process.

Parent process seals + records the accepted reverse conversion; a FRESH
interpreter must observe: old epoch allowed, unknown refused, destination
allowed, rollback succeeds (and afterward the gate is fully gone).
Exit 0 = all expected; prints JSON summary.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.environ.get("REVIEW_REPO", "D:/bt_api_py"))

from bt_api_py.service_migration_gate import (  # noqa: E402
    MigrationGateError,
    accept_reverse_migration,
    arm_migration_cutover,
    enforce_migration_start,
    read_migration_marker,
    record_migration_first_write,
)

BASE = sys.argv[1]
os.makedirs(BASE, exist_ok=True)
failures: list[str] = []
checks: list[str] = []

OLD = "0f8d3b6c9a1e4f2d8b7a6c5d4e3f2a10"
NEW = "1a2b3c4d5e6f708192a3b4c5d6e7f801"
state = os.path.join(BASE, "registry.sqlite3")
with open(state, "wb") as fh:
    fh.write(b"old-state")

arm_migration_cutover(state, source_epoch=OLD, new_epoch=NEW)
record_migration_first_write(state, fact_kind="reservation", recorded_at="2026-10-16T00:00:00Z")

# before acceptance the old epoch is refused even in this process
try:
    enforce_migration_start(state, deployment_epoch=OLD)
    failures.append("old epoch allowed before acceptance")
except MigrationGateError as exc:
    checks.append(f"pre-acceptance old refused {exc.code}")

acc = accept_reverse_migration(
    state, accepted_epoch=OLD, reference="reverse-conversion-receipt-0001"
)
if acc.accepted_reverse_epoch != OLD or not acc.sealed:
    failures.append(f"accept result wrong: {acc}")
else:
    checks.append("accept recorded on sealed marker")

# same process: accepted old epoch now passes
try:
    enforce_migration_start(state, deployment_epoch=OLD)
    checks.append("accepted old epoch passes (parent)")
except MigrationGateError as exc:
    failures.append(f"accepted epoch refused in parent: {exc.code}")

# FRESH interpreter: durability of the acceptance
script = "\n".join(
    [
        "import json",
        "from bt_api_py.service_migration_gate import (",
        "    MigrationGateError, enforce_migration_start, rollback_migration_cutover,",
        "    read_migration_marker,",
        ")",
        f"state = {state!r}",
        f"OLD = {OLD!r}",
        f"NEW = {NEW!r}",
        "out = {}",
        "m = read_migration_marker(state)",
        "out['accepted_epoch'] = m.accepted_reverse_epoch",
        "out['sealed'] = m.sealed",
        "for label, epoch in (('old', OLD), ('none', None), ('unknown', 'unknown'), ('dest', NEW)):",
        "    try:",
        "        enforce_migration_start(state, deployment_epoch=epoch)",
        "        out[label] = 'ALLOWED'",
        "    except MigrationGateError as exc:",
        "        out[label] = exc.code",
        "try:",
        "    out['rollback_old'] = rollback_migration_cutover(state, deployment_epoch=OLD)",
        "except MigrationGateError as exc:",
        "    out['rollback_old'] = exc.code",
        "out['marker_gone'] = read_migration_marker(state) is None",
        "print(json.dumps(out))",
    ]
)
env = dict(os.environ)
env["PYTHONPATH"] = os.environ.get("REVIEW_REPO", "D:/bt_api_py") + os.pathsep + env.get("PYTHONPATH", "")
cp = subprocess.run(
    [sys.executable, "-c", script],
    capture_output=True,
    text=True,
    env=env,
    timeout=120,
)
if cp.returncode != 0:
    failures.append(f"fresh interpreter rc={cp.returncode} stderr={cp.stderr[-500:]}")
else:
    out = json.loads(cp.stdout.strip().splitlines()[-1])
    if out.get("accepted_epoch") != OLD or out.get("sealed") is not True:
        failures.append(f"fresh read of acceptance wrong: {out}")
    elif (
        out.get("old") != "ALLOWED"
        or out.get("none") != "REVERSE_MIGRATION_REFUSED"
        or out.get("unknown") != "REVERSE_MIGRATION_REFUSED"
        or out.get("dest") != "ALLOWED"
    ):
        failures.append(f"fresh enforcement wrong: {out}")
    elif out.get("rollback_old") is not True or out.get("marker_gone") is not True:
        failures.append(f"fresh rollback wrong: {out}")
    else:
        checks.append("fresh interpreter: durable acceptance, scoped refusal, rollback True + marker gone")

# third interpreter: fully disarmed again
cp2 = subprocess.run(
    [sys.executable, "-c", script.replace("rollback_migration_cutover(state, deployment_epoch=OLD)", "False"),
     ],
    capture_output=True,
    text=True,
    env=env,
    timeout=120,
)
if cp2.returncode != 0:
    failures.append(f"third interpreter rc={cp2.returncode}")
else:
    out2 = json.loads(cp2.stdout.strip().splitlines()[-1])
    if out2.get("none") != "ALLOWED" or out2.get("unknown") != "ALLOWED" or out2.get("marker_gone") is not True:
        failures.append(f"post-rollback world not gate-free: {out2}")
    else:
        checks.append("after rollback: no marker, all epochs allowed (fully disarmed)")

print(json.dumps({"checks": checks, "failures": failures}, indent=1))
sys.exit(1 if failures else 0)
