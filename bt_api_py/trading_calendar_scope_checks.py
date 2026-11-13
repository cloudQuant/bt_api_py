"""Locally ordered, unauthenticated scope-check records for simulation only.

Records and their source observations are immutable by contract. The HEAD is a
cooperating-writer coordination aid, not an authenticated anti-rollback anchor.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from dataclasses import asdict
from datetime import datetime

from bt_api_py import trading_calendar_akshare_observation as observation
from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_mixed_publication as mixed_publication
from bt_api_py import trading_calendar_mixed_simulation_version as mixed
from bt_api_py import trading_calendar_observation_run as runs

_HEAD = "scope-check-head.json"
_PREFIX = "scope-check-batch-"
_LIMIT = 256
_LABEL = {"environment": "SIMULATION_ONLY", "source_authentication": "NOT_PROVEN"}


def _read(fd, chain, name):
    raw = runs._read_entry(fd, chain, name)
    return None if raw is None else storage._unwrap(raw)


def _success(root, evidence, row):
    """Replay the retained observation and materializable template, not a source fact."""
    shown = observation.show(
        root=root,
        observation_id=row["observation_id"],
        run_id=evidence["run_id"],
        filesystem=runs.TRUSTED_LOCAL_POSIX,
    )
    if (
        row["observation_state"] != "COMMITTED"
        or row["observation_status"] != "SUCCESS"
        or row["projection_state"] != "VISIBLE_UNAUTHENTICATED"
        or shown.get("status") != "VISIBLE_UNAUTHENTICATED"
        or shown.get("observation_status") != "SUCCESS"
        or shown.get("data_state") != "DATA_RETAINED"
        or shown.get("requested_venue") != row["target"]["venue"]
        or shown.get("requested_contract") != row["contract"]
        or shown.get("requested_date") != row["requested_date"]
    ):
        raise ValueError("successful observation unavailable")
    stamp = datetime.fromisoformat(shown["captured_at"])
    if stamp.utcoffset() is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("invalid declared UTC")
    template = mixed.build_template(
        root=root,
        candidate_root=root,
        observation_id=row["observation_id"],
        run_id=evidence["run_id"],
        candidate_id=evidence["derived_id"],
        filesystem=runs.TRUSTED_LOCAL_POSIX,
        target=row["target"],
        assumptions=row["assumptions"],
    )
    digest = mixed.template_digest(
        template,
        root=root,
        candidate_root=root,
        filesystem=runs.TRUSTED_LOCAL_POSIX,
        include_trace=True,
    )
    mixed_publication._materialize(
        root, runs.TRUSTED_LOCAL_POSIX, evidence["candidate_id"], template, digest
    )
    return {
        "result_sha256": shown["payload_sha256"],
        "template_sha256": digest,
        "declared_utc": shown["captured_at"],
        "requested_contract": row["contract"],
        "baseline_publication_id": evidence["baseline"]["publication_id"],
        "baseline_version_sha256": evidence["baseline"]["version_sha256"],
    }


def _entries(root, evidence, *, schema):
    entries = []
    for row in evidence["observations"]:
        status = row.get("check_status")
        if status is None:
            status = (
                "SUCCESS"
                if row["observation_state"] == "COMMITTED"
                and row["observation_status"] == "SUCCESS"
                and row["projection_state"] == "VISIBLE_UNAUTHENTICATED"
                else "UNKNOWN"
            )
        if status not in {"SUCCESS", "FAILED", "UNKNOWN"}:
            raise ValueError("invalid scope check status")
        proof = _success(root, evidence, row) if status == "SUCCESS" else None
        entry = {
            "target": row["target"],
            "observation_id": row["observation_id"],
            "check_status": status,
            "observation_status": row["observation_status"],
            "disposition": row.get(
                "disposition", "UPDATED_AKSHARE" if status == "SUCCESS" else "UNAVAILABLE"
            ),
            "success_proof": proof,
        }
        if schema == "bt_api.calendar-scope-check-record/2":
            entry["requested_contract"] = row["contract"]
        entries.append(entry)
    return entries


def _load_locked(root, fd, chain):
    names = []
    with os.scandir(fd) as directory:
        for count, entry in enumerate(directory, 1):
            if count > 10000:
                raise ValueError("scope directory scan limit")
            if entry.name.startswith(_PREFIX) and entry.name.endswith(".json"):
                names.append(entry.name)
                if len(names) > _LIMIT:
                    raise ValueError("scope history limit")
    names.sort()
    head = _read(fd, chain, _HEAD)
    if head is None:
        if names:
            raise ValueError("scope head unavailable")
        return [], None
    body, _ = head
    if (
        type(body) is not dict
        or set(body) != {"schema", "sequence", "record_sha256", "batch_id"}
        or body["schema"] != "bt_api.calendar-scope-check-head/1"
    ):
        raise ValueError("scope head invalid")
    records = []
    for name in names:
        item = _read(fd, chain, name)
        if item is None:
            raise ValueError("scope record missing")
        record, digest = item
        if (
            type(record) is not dict
            or set(record)
            != {
                "schema",
                "environment",
                "source_authentication",
                "batch_id",
                "run_id",
                "evidence_sha256",
                "sequence",
                "previous_sha256",
                "entries",
            }
            or record["schema"]
            not in {"bt_api.calendar-scope-check-record/1", "bt_api.calendar-scope-check-record/2"}
            or record["environment"] != "SIMULATION_ONLY"
            or record["source_authentication"] != "NOT_PROVEN"
            or name != f"{_PREFIX}{record['batch_id']}.json"
        ):
            raise ValueError("scope record invalid")
        evidence = mixed_publication._batch_record(
            root, record["batch_id"], record["evidence_sha256"]
        )
        if record["run_id"] != evidence["run_id"] or record["entries"] != _entries(
            root, evidence, schema=record["schema"]
        ):
            raise ValueError("scope evidence changed")
        records.append((record, digest))
    records.sort(key=lambda item: item[0]["sequence"])
    previous = None
    for sequence, (record, digest) in enumerate(records, 1):
        if record["sequence"] != sequence or record["previous_sha256"] != previous:
            raise ValueError("scope record chain changed")
        previous = digest
    if not records or body != {
        "schema": "bt_api.calendar-scope-check-head/1",
        "sequence": len(records),
        "record_sha256": records[-1][1],
        "batch_id": records[-1][0]["batch_id"],
    }:
        raise ValueError("scope head changed")
    return records, head


def _result(records, targets):
    values = []
    for query in targets:
        if (
            type(query) is not dict
            or set(query) != {"target", "requested_contract"}
            or type(query["target"]) is not dict
            or type(query["requested_contract"]) is not str
            or not query["requested_contract"]
        ):
            raise ValueError("exact scope and requested contract required")
        target = query["target"]
        contract = query["requested_contract"]
        last = None
        for record, _ in records:
            for entry in record["entries"]:
                if (
                    entry["target"] == target
                    and entry["check_status"] == "SUCCESS"
                    and entry["success_proof"]["requested_contract"] == contract
                ):
                    last = {
                        "status": "VISIBLE_UNAUTHENTICATED",
                        "batch_id": record["batch_id"],
                        "observation_id": entry["observation_id"],
                        "source": "AKSHARE",
                        "source_authentication": "NOT_PROVEN",
                        "declared_utc": entry["success_proof"]["declared_utc"],
                        "clock": "CLOCK_UNVERIFIED",
                        "requested_contract": entry["success_proof"]["requested_contract"],
                        "baseline_publication_id": entry["success_proof"][
                            "baseline_publication_id"
                        ],
                        "baseline_version_sha256": entry["success_proof"][
                            "baseline_version_sha256"
                        ],
                        "ordering": "COORDINATED_LOCAL_RECORD_SEQUENCE",
                        "sequence": record["sequence"],
                        "publication": "NOT_INFERRED",
                        "coverage": "DECLARED_SIMULATION_SCOPE_ONLY",
                    }
        values.append(
            {
                "target": target,
                "requested_contract": contract,
                "last_success": last or {"status": "UNKNOWN"},
            }
        )
    return {
        **_LABEL,
        "status": "VISIBLE_UNAUTHENTICATED",
        "scopes": values,
        "venue_last_success": "UNKNOWN",
    }


def record(*, operator, batch_id, evidence_sha256):
    root = operator.root
    try:
        operator._guard()
        with operator._store._writer_lock():
            fds, chain = runs._open_runs(root, create=False)
            try:
                fd = fds[-1]
                records, head = _load_locked(root, fd, chain)
                name = f"{_PREFIX}{batch_id}.json"
                if _read(fd, chain, name) is not None:
                    return {
                        **_LABEL,
                        "status": "UNKNOWN",
                        "reason": "ORIGINAL_BATCH_RECONCILE_FIRST",
                        "batch_id": batch_id,
                    }
                evidence = mixed_publication._batch_record(root, batch_id, evidence_sha256)
                if operator._store._pending_exists() or operator._store._policy_change_pending():
                    raise ValueError("store operation pending")
                policy = operator._store._review_policy_locked()
                operator._check_policy_locked(policy)
                current = operator._store.read_current()
                if asdict(policy) != evidence["policy"] or evidence["baseline"] != {
                    "publication_id": current.publication_id,
                    "publication_sha256": current.publication_sha256,
                    "version_sha256": current.version_sha256,
                }:
                    raise ValueError("batch baseline changed")
                schema = "bt_api.calendar-scope-check-record/2"
                entries = _entries(root, evidence, schema=schema)
                if len(records) >= _LIMIT:
                    raise ValueError("scope history limit")
                body = {
                    "schema": schema,
                    **_LABEL,
                    "batch_id": batch_id,
                    "run_id": evidence["run_id"],
                    "evidence_sha256": evidence_sha256,
                    "sequence": len(records) + 1,
                    "previous_sha256": records[-1][1] if records else None,
                    "entries": entries,
                }
                state, _ = runs._write_once(fd, chain, name, storage._envelope(body))
                if state != "COMMITTED":
                    return {
                        **_LABEL,
                        "status": "UNKNOWN",
                        "reason": "SCOPE_CHECK_RECONCILE_FIRST",
                        "batch_id": batch_id,
                        "record_state": state,
                    }
                saved = _read(fd, chain, name)
                if saved is None or saved[0] != body:
                    raise ValueError("scope record unknown")
                new_head = storage._envelope(
                    {
                        "schema": "bt_api.calendar-scope-check-head/1",
                        "sequence": body["sequence"],
                        "record_sha256": saved[1],
                        "batch_id": batch_id,
                    }
                )
                temp = f".scope-check-head-{uuid.uuid4().hex}.tmp"
                out = None
                try:
                    out = os.open(
                        temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
                    )
                    offset = 0
                    while offset < len(new_head):
                        count = os.write(out, new_head[offset:])
                        if count <= 0:
                            raise OSError("short scope head write")
                        offset += count
                    os.fsync(out)
                    os.close(out)
                    out = None
                    if _read(fd, chain, _HEAD) != head:
                        raise ValueError("scope head raced")
                    os.replace(temp, _HEAD, src_dir_fd=fd, dst_dir_fd=fd)
                    os.fsync(fd)
                    if _read(fd, chain, _HEAD) != storage._unwrap(new_head):
                        raise ValueError("scope head unknown")
                finally:
                    if out is not None:
                        os.close(out)
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(temp, dir_fd=fd)
                records, _ = _load_locked(root, fd, chain)
                return {
                    **_result(
                        records,
                        [
                            {
                                "target": entry["target"],
                                "requested_contract": entry["requested_contract"],
                            }
                            for entry in entries
                        ],
                    ),
                    "record_state": "COMMITTED",
                    "batch_id": batch_id,
                }
            finally:
                runs._close(fds)
    except (KeyboardInterrupt, SystemExit) as exc:
        exc.scope_check_outcome = {
            **_LABEL,
            "status": "UNKNOWN",
            "reason": "SCOPE_CHECK_RECONCILE_FIRST",
            "batch_id": batch_id,
        }
        raise
    except Exception:
        return {
            **_LABEL,
            "status": "UNKNOWN",
            "reason": "SCOPE_CHECK_RECONCILE_FIRST",
            "batch_id": batch_id,
        }


def show(*, operator, targets, batch_id=None):
    try:
        operator._guard()
        with operator._store._readonly_lock():
            fds, chain = runs._open_runs(operator.root, create=False)
            try:
                records, _ = _load_locked(operator.root, fds[-1], chain)
                if batch_id is not None and not any(
                    item[0]["batch_id"] == batch_id for item in records
                ):
                    return {
                        **_LABEL,
                        "status": "UNKNOWN",
                        "reason": "LEGACY_OR_UNRECORDED_BATCH",
                        "batch_id": batch_id,
                    }
                return _result(records, targets)
            finally:
                runs._close(fds)
    except Exception:
        result = {
            **_LABEL,
            "status": "UNKNOWN",
            "reason": "SCOPE_CHECK_HISTORY_UNAVAILABLE",
            "batch_id": batch_id,
        }
        if batch_id and runs._id(batch_id):
            try:
                fds, chain = runs._open_runs(operator.root, create=False)
                try:
                    original = _read(fds[-1], chain, f"{_PREFIX}{batch_id}.json")
                finally:
                    runs._close(fds)
                result["original_record"] = (
                    {"status": "VISIBLE_UNAUTHENTICATED", "record_sha256": original[1]}
                    if original
                    else {"status": "NOT_OBSERVED"}
                )
            except Exception:
                result["original_record"] = {"status": "UNKNOWN"}
        return result
