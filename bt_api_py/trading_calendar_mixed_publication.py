"""Retain a replayable mixed simulation candidate; no account authority."""

from __future__ import annotations

import contextlib
import fcntl
import os

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_mixed_simulation_version as mixed
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_review_bundle as review

_SCHEMA = "bt_api.calendar-mixed-candidate/1"
_ALIGNMENT = "REPEAT_OVER_MOCK_OWNER_DAY_MAPPING"


def build_once(*, root, filesystem, candidate_id, template, evidence_sha256):
    """Write once under a caller-preallocated ID; inspect the ID on uncertainty."""
    if not runs._id(candidate_id):
        return {"status": "NOT_COMMITTED", "reason": "INVALID_ID"}
    try:
        result = mixed.materialize_template(
            template,
            root=root,
            candidate_root=root,
            filesystem=filesystem,
            alignment=_ALIGNMENT,
            expected_evidence_sha256=evidence_sha256,
            candidate_id=candidate_id,
        )
    except Exception:
        return {"status": "NOT_COMMITTED", "reason": "INVALID_MIXED_INPUT"}
    candidate = review.build_once(
        root=root,
        filesystem=filesystem,
        candidate_id=candidate_id,
        input_data=result["input"],
        emit=lambda _: None,
        propagate_interrupt=True,
    )
    if candidate.get("status") != "COMMITTED":
        return {"status": candidate.get("status", "UNKNOWN"), "reason": "CANDIDATE_RECONCILE_FIRST"}
    body = {
        "schema": _SCHEMA,
        "environment": "SIMULATION_ONLY",
        "candidate_id": candidate_id,
        "template": template,
        "evidence_sha256": evidence_sha256,
        "provenance": result["provenance"],
    }
    attempted = False
    fds = []
    lock = None
    try:
        fds, chain = review._open(root, False)
        lock, identity = storage._lock(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, identity)
        attempted = True
        state = storage._write(
            fds[-1],
            chain,
            f"mock-mixed-{candidate_id}.json",
            storage._envelope(body),
            review._OUTPUT_LIMIT,
        )
        return {"status": state, "candidate_state": "COMMITTED", "candidate_id": candidate_id}
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return {
            "status": "UNKNOWN" if attempted else "NOT_COMMITTED",
            "candidate_state": "COMMITTED",
            "reason": "MIXED_EVIDENCE_RECONCILE_FIRST",
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


def read(*, root, filesystem, candidate_id):
    """Read and fully replay retained inputs; raise on any unavailable proof."""
    if not runs._id(candidate_id):
        raise ValueError("invalid candidate")
    fds, chain = review._open(root, False)
    try:
        raw = storage._read(fds[-1], chain, f"mock-mixed-{candidate_id}.json", review._OUTPUT_LIMIT)
    finally:
        runs._close(fds)
    if raw is None:
        raise ValueError("mixed evidence unavailable")
    body, _ = storage._unwrap(raw)
    if (
        type(body) is not dict
        or set(body)
        != {"schema", "environment", "candidate_id", "template", "evidence_sha256", "provenance"}
        or body["schema"] != _SCHEMA
        or body["environment"] != "SIMULATION_ONLY"
        or body["candidate_id"] != candidate_id
    ):
        raise ValueError("invalid mixed evidence")
    materialized = mixed.materialize_template(
        body["template"],
        root=root,
        candidate_root=root,
        filesystem=filesystem,
        alignment=_ALIGNMENT,
        expected_evidence_sha256=body["evidence_sha256"],
        candidate_id=candidate_id,
    )
    shown = review.show(root=root, candidate_id=candidate_id, filesystem=filesystem)
    if (
        shown.get("status") != "VISIBLE_UNAUTHENTICATED"
        or shown["bundle"]["input"] != materialized["input"]
        or body["provenance"] != materialized["provenance"]
    ):
        raise ValueError("mixed candidate or provenance changed")
    return shown, body["provenance"]
