"""Probe B: seal -> old/undeclared/unknown refused; rollback refused post-seal (real files).

Independent adversarial probe; combines and extends the author's assertions.
Exit 0 = all expected; prints JSON summary.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.environ.get("REVIEW_REPO", "D:/bt_api_py"))

from bt_api_py.service_migration_gate import (  # noqa: E402
    MigrationGateError,
    arm_migration_cutover,
    enforce_migration_start,
    read_migration_marker,
    record_migration_first_write,
    rollback_migration_cutover,
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

# pre-seal rollback allowed and removes marker
m1 = arm_migration_cutover(state, source_epoch=OLD, new_epoch=NEW)
if m1.status != "COMMITTED":
    failures.append(f"arm status {m1.status}")
ok = rollback_migration_cutover(state, deployment_epoch=OLD)
if ok is not True:
    failures.append("pre-seal rollback did not return True")
elif os.path.exists(state + ".migration-cutover.json"):
    failures.append("pre-seal rollback left marker behind")
else:
    checks.append("pre-seal rollback True + marker removed")

# re-arm, seal
arm_migration_cutover(state, source_epoch=OLD, new_epoch=NEW)
first = record_migration_first_write(state, fact_kind="dispatch", recorded_at="2026-10-16T00:00:00Z")
second = record_migration_first_write(state, fact_kind="order")
if first is not True or second is not False:
    failures.append(f"seal transitions first={first} second={second}")
else:
    checks.append("seal first=True, second=False (one-way)")
m = read_migration_marker(state)
if m.first_write_fact_kind != "dispatch":
    failures.append(f"first fact not preserved: {m.first_write_fact_kind}")
else:
    checks.append("first fact preserved = dispatch")

marker_file = state + ".migration-cutover.json"
with open(marker_file, "rb") as fh:
    before = hashlib.sha256(fh.read()).hexdigest()

# refused starts: old, None (undeclared), unknown
for epoch, label in ((OLD, "old"), (None, "undeclared-None"), ("unknown", "unknown")):
    try:
        enforce_migration_start(state, deployment_epoch=epoch)
        failures.append(f"sealed start with {label} epoch was NOT refused")
    except MigrationGateError as exc:
        if exc.code != "REVERSE_MIGRATION_REFUSED":
            failures.append(f"sealed start {label}: wrong code {exc.code}")
        else:
            checks.append(f"sealed start {label} refused {exc.code}")

# destination epoch passes
try:
    enforce_migration_start(state, deployment_epoch=NEW)
    checks.append("destination epoch passes")
except MigrationGateError as exc:
    failures.append(f"destination epoch refused: {exc.code}")

# rollback post-seal refused (old, None, unknown)
for epoch, label in ((OLD, "old"), (None, "None"), ("unknown", "unknown")):
    try:
        rollback_migration_cutover(state, deployment_epoch=epoch)
        failures.append(f"post-seal rollback with {label} was NOT refused")
    except MigrationGateError as exc:
        if exc.code != "REVERSE_MIGRATION_REFUSED":
            failures.append(f"post-seal rollback {label}: wrong code {exc.code}")
        else:
            checks.append(f"post-seal rollback {label} refused {exc.code}")

with open(marker_file, "rb") as fh:
    after = hashlib.sha256(fh.read()).hexdigest()
if before != after:
    failures.append("sealed marker mutated by refused attempts")
else:
    checks.append("sealed marker byte-identical after refusals")

with open(state, "rb") as fh:
    if fh.read() != b"old-state":
        failures.append("protected state mutated")
    else:
        checks.append("protected state untouched")

print(json.dumps({"checks": checks, "failures": failures}, indent=1))
sys.exit(1 if failures else 0)
