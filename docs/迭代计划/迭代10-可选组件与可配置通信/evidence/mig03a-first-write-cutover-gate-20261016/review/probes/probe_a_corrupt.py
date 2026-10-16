"""Probe A: corrupt/foreign/truncated marker must fail closed CUTOVER_MARKER_UNREADABLE.

Independent adversarial probe. Writes real files, uses the module API only.
Exit 0 = all corruptions behave fail-closed; prints JSON summary.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.environ.get("REVIEW_REPO", "D:/bt_api_py"))

from bt_api_py.service_migration_gate import (  # noqa: E402
    MigrationGateError,
    accept_reverse_migration,
    arm_migration_cutover,
    enforce_migration_start,
    read_migration_marker,
    record_migration_first_write,
    rollback_migration_cutover,
)

BASE = sys.argv[1]
os.makedirs(BASE, exist_ok=True)
results = []
failures = []

OLD = "0f8d3b6c9a1e4f2d8b7a6c5d4e3f2a10"
NEW = "1a2b3c4d5e6f708192a3b4c5d6e7f801"


def expect_unreadable(label: str, state: str) -> None:
    calls = {
        "read": lambda: read_migration_marker(state),
        "enforce": lambda: enforce_migration_start(state, deployment_epoch=OLD),
        "enforce_none": lambda: enforce_migration_start(state, deployment_epoch=None),
        "arm": lambda: arm_migration_cutover(state, source_epoch=OLD, new_epoch=NEW),
        "rollback": lambda: rollback_migration_cutover(state, deployment_epoch=OLD),
        "first_write": lambda: record_migration_first_write(state, fact_kind="dispatch"),
        "accept": lambda: accept_reverse_migration(state, accepted_epoch=OLD, reference="r"),
    }
    for name, call in calls.items():
        try:
            call()
            failures.append(f"{label}/{name}: expected CUTOVER_MARKER_UNREADABLE, got success")
        except MigrationGateError as exc:
            if exc.code != "CUTOVER_MARKER_UNREADABLE":
                failures.append(f"{label}/{name}: wrong code {exc.code}")
            else:
                results.append(f"{label}/{name}: {exc.code}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label}/{name}: leaked {type(exc).__name__}: {exc}")


def write_and_probe(label: str, content: str | None, *, make_dir: bool = False) -> None:
    d = os.path.join(BASE, label)
    os.makedirs(d, exist_ok=True)
    state = os.path.join(d, "registry.sqlite3")
    with open(state, "wb") as fh:
        fh.write(b"state")
    marker = state + ".migration-cutover.json"
    if make_dir:
        os.makedirs(marker, exist_ok=True)
    else:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(content or "")
    expect_unreadable(label, state)


# 1. empty file
write_and_probe("empty-file", "")
# 2. truncated JSON
write_and_probe("truncated-json", "{")
# 3. valid JSON but non-dict
write_and_probe("json-array", "[1,2,3]")
# 4. JSON null
write_and_probe("json-null", "null")
# 5. wrong schema_version
write_and_probe(
    "schema-mismatch",
    json.dumps(
        {
            "schema_version": 99,
            "migration_id": "a" * 32,
            "source_epoch": OLD,
            "destination_epoch": NEW,
            "status": "COMMITTED",
            "first_write": None,
            "accepted_reverse_migration": None,
        }
    ),
)
# 6. unknown status
write_and_probe(
    "unknown-status",
    json.dumps(
        {
            "schema_version": 1,
            "migration_id": "a" * 32,
            "source_epoch": OLD,
            "destination_epoch": NEW,
            "status": "TAMPERED",
            "first_write": None,
            "accepted_reverse_migration": None,
        }
    ),
)
# 7. SEALED but first_write null (inconsistent document)
write_and_probe(
    "sealed-without-first-write",
    json.dumps(
        {
            "schema_version": 1,
            "migration_id": "a" * 32,
            "source_epoch": OLD,
            "destination_epoch": NEW,
            "status": "SEALED",
            "first_write": None,
            "accepted_reverse_migration": None,
        }
    ),
)
# 8. COMMITTED but first_write present (inconsistent document)
write_and_probe(
    "committed-with-first-write",
    json.dumps(
        {
            "schema_version": 1,
            "migration_id": "a" * 32,
            "source_epoch": OLD,
            "destination_epoch": NEW,
            "status": "COMMITTED",
            "first_write": {"fact_kind": "dispatch", "recorded_at": "2026-10-16T00:00:00Z"},
            "accepted_reverse_migration": None,
        }
    ),
)
# 9. accepted_reverse == destination (would neuter the gate if read as valid)
write_and_probe(
    "accepted-reverse-equals-destination",
    json.dumps(
        {
            "schema_version": 1,
            "migration_id": "a" * 32,
            "source_epoch": OLD,
            "destination_epoch": NEW,
            "status": "SEALED",
            "first_write": {"fact_kind": "dispatch", "recorded_at": "2026-10-16T00:00:00Z"},
            "accepted_reverse_migration": {"accepted_epoch": NEW, "reference": "x"},
        }
    ),
)
# 10. marker path is a directory
write_and_probe("marker-is-directory", None, make_dir=True)

# 11. truncate a formerly-valid sealed marker (bit rot after seal)
d = os.path.join(BASE, "truncated-after-seal")
os.makedirs(d, exist_ok=True)
state = os.path.join(d, "registry.sqlite3")
with open(state, "wb") as fh:
    fh.write(b"state")
arm_migration_cutover(state, source_epoch=OLD, new_epoch=NEW)
record_migration_first_write(state, fact_kind="dispatch", recorded_at="2026-10-16T00:00:00Z")
marker = state + ".migration-cutover.json"
with open(marker, "rb") as fh:
    blob = fh.read()
with open(marker, "wb") as fh:
    fh.write(blob[: len(blob) // 2])
expect_unreadable("truncated-after-seal", state)

# 12. valid sealed marker still readable + gate still refuses old epoch (control)
d = os.path.join(BASE, "control-valid-sealed")
os.makedirs(d, exist_ok=True)
state = os.path.join(d, "registry.sqlite3")
with open(state, "wb") as fh:
    fh.write(b"state")
arm_migration_cutover(state, source_epoch=OLD, new_epoch=NEW)
record_migration_first_write(state, fact_kind="cursor", recorded_at="2026-10-16T00:00:00Z")
m = read_migration_marker(state)
if m is None or not m.sealed:
    failures.append("control-valid-sealed: marker not sealed/readable")
try:
    enforce_migration_start(state, deployment_epoch=OLD)
    failures.append("control-valid-sealed: old epoch not refused")
except MigrationGateError as exc:
    if exc.code != "REVERSE_MIGRATION_REFUSED":
        failures.append(f"control-valid-sealed: wrong code {exc.code}")
    else:
        results.append(f"control-valid-sealed: {exc.code}")

print(json.dumps({"checks": len(results), "failures": failures}, indent=1))
sys.exit(1 if failures else 0)
