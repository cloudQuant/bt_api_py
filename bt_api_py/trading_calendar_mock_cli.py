"""Explicit MOCK/SIMULATION_ONLY calendar update orchestration.

This uses only an existing mock run's private temporary or declared root. Its CURRENT is
readable by a normal low-level store if deliberately configured there: this is
not a production isolation or authorization boundary. Reviewers and times are
simulation declarations, and missing or uncertain writes must be inspected by
original ID rather than retried.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import uuid

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py import trading_calendar_review_bundle as review
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication

_LABEL = {
    "data_origin": "MOCK",
    "environment": "SIMULATION_ONLY",
    "source_authentication": "NOT_PROVEN",
}
_SCHEMA = "bt_api.calendar-mock-pending-review/1"


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _emit(value):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True), flush=True)


def allocate(*, candidate_id=None):
    ids = {
        "candidate_id": candidate_id or uuid.uuid4().hex,
        "review_id": uuid.uuid4().hex,
        "publication_id": "MOCK_" + uuid.uuid4().hex,
        "version_id": "MOCK_" + uuid.uuid4().hex,
    }
    if not runs._id(ids["candidate_id"]) or not runs._id(ids["review_id"]):
        raise ValueError("invalid allocated identity")
    if len({ids["candidate_id"], ids["review_id"], ids["publication_id"], ids["version_id"]}) != 4:
        raise ValueError("duplicate allocated identity")
    return ids


def _report(event, **fields):
    _emit({"event": event, **_LABEL, **fields})


def _retain_pending(operator, ids, proposal):
    body = {
        "schema": _SCHEMA,
        **_LABEL,
        "run_id": operator.run_id,
        **ids,
        "proposal": proposal,
        "proposal_sha256": hashlib.sha256(_canonical(proposal)).hexdigest(),
    }
    encoded = storage._envelope(body)
    if len(encoded) > review._OUTPUT_LIMIT:
        return {"status": "NOT_COMMITTED", "reason": "PENDING_REVIEW_TOO_LARGE"}
    try:
        operator._guard()
        fds, chain = review._open(operator.root, False)
    except Exception:
        return {"status": "NOT_COMMITTED", "reason": "PENDING_REVIEW_UNAVAILABLE"}
    lock = None
    attempted = False
    try:
        lock, identity = storage._lock(fds[-1], chain)
        storage._check_lock(fds[-1], chain, lock, identity)
        operator._guard()
        attempted = True
        state = storage._write(
            fds[-1], chain, f"mock-review-{ids['review_id']}.json", encoded, review._OUTPUT_LIMIT
        )
        if state == "COMMITTED":
            observed = _read_pending(operator, ids["review_id"])
            if observed.get("status") != "VISIBLE_UNAUTHENTICATED" or observed.get("body") != body:
                state = "COMMIT_UNKNOWN"
        return {
            "status": state,
            "review_payload_sha256": storage._unwrap(encoded)[1] if state == "COMMITTED" else None,
        }
    except (KeyboardInterrupt, SystemExit) as exc:
        exc.mock_outcome = {
            **_LABEL,
            "operation": "PENDING_REVIEW",
            "run_id": operator.run_id,
            **ids,
            "status": "COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
        }
        raise
    except Exception:
        return {
            "status": "COMMIT_UNKNOWN" if attempted else "NOT_COMMITTED",
            "reason": "PENDING_REVIEW_IO_UNKNOWN",
        }
    finally:
        if lock is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(lock, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                os.close(lock)
        runs._close(fds)


def _read_pending(operator, review_id):
    if not runs._id(review_id):
        return {"status": "UNKNOWN"}
    try:
        operator._guard()
        fds, chain = review._open(operator.root, False)
        try:
            raw = storage._read(
                fds[-1], chain, f"mock-review-{review_id}.json", review._OUTPUT_LIMIT
            )
            if raw is None:
                return {"status": "NOT_OBSERVED"}
            body, digest = storage._unwrap(raw)
            if (
                type(body) is not dict
                or set(body)
                != {
                    "schema",
                    *_LABEL,
                    "run_id",
                    "candidate_id",
                    "review_id",
                    "publication_id",
                    "version_id",
                    "proposal",
                    "proposal_sha256",
                }
                or body["schema"] != _SCHEMA
                or body["run_id"] != operator.run_id
                or body["review_id"] != review_id
                or any(body[key] != value for key, value in _LABEL.items())
                or not runs._id(body["candidate_id"])
                or not runs._id(body["review_id"])
                or type(body["publication_id"]) is not str
                or not body["publication_id"].startswith("MOCK_")
                or type(body["version_id"]) is not str
                or not body["version_id"].startswith("MOCK_")
                or type(body["proposal"]) is not dict
                or body["proposal"].get("candidate_id") != body["candidate_id"]
                or body["proposal"].get("policy", {}).get("mode") != "manual"
                or body["proposal"].get("status") not in {"FIRST_SIMULATION_PUBLICATION", "CHANGED"}
                or hashlib.sha256(_canonical(body["proposal"])).hexdigest()
                != body["proposal_sha256"]
            ):
                raise ValueError("pending review changed")
            operator._guard()
            return {"status": "VISIBLE_UNAUTHENTICATED", "body": body, "payload_sha256": digest}
        finally:
            runs._close(fds)
    except Exception:
        return {"status": "UNKNOWN"}


def _call(function, *, operator, ids, **kwargs):
    try:
        return function(**kwargs)
    except (KeyboardInterrupt, SystemExit) as exc:
        _report(
            "MOCK_INTERRUPTED",
            simulation_root=operator.root,
            run_id=operator.run_id,
            **ids,
            outcome=getattr(exc, "mock_outcome", {"status": "UNKNOWN"}),
        )
        raise


def _result_code(result):
    status = result.get("status")
    if status == "COMMITTED" and result.get("current_visibility") == "VISIBLE":
        return 0
    if status in {"PENDING_REVIEW", "SAME"}:
        return 10
    if status in {"UNKNOWN", "COMMIT_UNKNOWN"}:
        return 25
    return 24


def _finish(operator, ids, published):
    output = {
        **_LABEL,
        "simulation_root": operator.root,
        "run_id": operator.run_id,
        **ids,
        **published,
    }
    if published.get("status") != "COMMITTED":
        return output
    proof = published.get("result")
    if (
        type(proof) is not dict
        or proof.get("visibility") != "COMMITTED"
        or proof.get("fence") != "CLEAR"
        or proof.get("post_commit") != "COMPLETE"
        or published.get("publication") != "SIMULATION_ONLY"
    ):
        output.update(status="COMMIT_UNKNOWN", reason="PUBLICATION_PROOF_INCOMPLETE")
        return output
    try:
        observation = _call(
            operator.verify_publication,
            operator=operator,
            ids=ids,
            publication_id=ids["publication_id"],
        )
        output["current_visibility"] = observation.get("status")
    except Exception:
        output["current_visibility"] = "UNKNOWN"
    if output["current_visibility"] != "VISIBLE":
        output.update(status="COMMIT_UNKNOWN", reason="CURRENT_RECHECK_UNKNOWN")
    return output


def _act(operator, ids, proposal):
    context = {**_LABEL, "simulation_root": operator.root, "run_id": operator.run_id, **ids}
    if proposal.get("status") not in {"FIRST_SIMULATION_PUBLICATION", "SAME", "CHANGED"}:
        return {**context, "status": "UNKNOWN", "reason": "PROPOSAL_UNAVAILABLE"}
    mode = proposal.get("policy", {}).get("mode")
    if mode == "manual" and proposal["status"] != "SAME":
        try:
            saved = _retain_pending(operator, ids, proposal)
        except (KeyboardInterrupt, SystemExit) as exc:
            _report(
                "MOCK_INTERRUPTED",
                simulation_root=operator.root,
                run_id=operator.run_id,
                **ids,
                outcome=getattr(exc, "mock_outcome", {"status": "UNKNOWN"}),
            )
            raise
        if saved.get("status") != "COMMITTED":
            return {
                **context,
                **saved,
                "reason": saved.get("reason", "PENDING_REVIEW_OUTCOME_UNKNOWN"),
            }
        return {
            **context,
            "status": "PENDING_REVIEW",
            "review_payload_sha256": saved["review_payload_sha256"],
            "proposal": proposal,
            "publication": "NOT_ATTEMPTED",
        }
    if mode not in {"auto", "manual"}:
        return {**context, "status": "UNKNOWN", "reason": "POLICY_UNAVAILABLE"}
    published = _call(
        operator.publish,
        operator=operator,
        ids=ids,
        proposal=proposal,
        publication_id=ids["publication_id"],
        version_id=ids["version_id"],
        emit=_emit,
    )
    return _finish(operator, ids, published)


def initial(*, root, run_id, candidate_id, mode, ids):
    operator = MockCalendarPublication(root=root, run_id=run_id)
    initialized = _call(operator.initialize, operator=operator, ids=ids, mode=mode)
    if initialized.get("status") != "INITIALIZED":
        return {
            **_LABEL,
            "simulation_root": root,
            "run_id": run_id,
            **ids,
            "status": "UNKNOWN",
            "reason": "POLICY_INITIALIZATION_UNKNOWN",
            "policy_result": initialized,
        }
    proposal = _call(operator.prepare, operator=operator, ids=ids, candidate_id=candidate_id)
    return _act(operator, ids, proposal)


def update(*, root, run_id, variant):
    operator = MockCalendarPublication(root=root, run_id=run_id)
    ids = allocate()
    prior_ids = {
        run_id,
        operator._audit["capture_id"],
        operator._audit["parent_id"],
        operator._audit["derived_id"],
    }
    if prior_ids.intersection({ids["candidate_id"], ids["review_id"]}):
        raise ValueError("allocated ID collides with run")
    _report("MOCK_UPDATE_IDS_ALLOCATED", simulation_root=root, run_id=run_id, **ids)
    built = _call(
        operator.build_candidate,
        operator=operator,
        ids=ids,
        candidate_id=ids["candidate_id"],
        year=operator._audit["year"],
        variant=variant,
        emit=_emit,
    )
    if built.get("status") != "COMMITTED":
        return {
            **_LABEL,
            "simulation_root": root,
            "run_id": run_id,
            **ids,
            "status": built.get("status", "UNKNOWN"),
            "candidate_result": built,
        }
    proposal = _call(operator.prepare, operator=operator, ids=ids, candidate_id=ids["candidate_id"])
    return _act(operator, ids, proposal)


def confirm(*, root, run_id, review_id, review_sha256, reviewer, confirmed_at):
    operator = MockCalendarPublication(root=root, run_id=run_id)
    pending = _read_pending(operator, review_id)
    if (
        pending.get("status") != "VISIBLE_UNAUTHENTICATED"
        or pending.get("payload_sha256") != review_sha256
    ):
        return {
            **_LABEL,
            "simulation_root": root,
            "run_id": run_id,
            "status": "UNKNOWN",
            "reason": "PENDING_REVIEW_UNAVAILABLE_OR_CHANGED",
            "review_id": review_id,
        }
    body = pending["body"]
    ids = {key: body[key] for key in ("candidate_id", "review_id", "publication_id", "version_id")}
    _report("MOCK_CONFIRM_IDS", simulation_root=root, run_id=run_id, **ids)
    for result in (
        operator.inspect_publication_claim(publication_id=ids["publication_id"]),
        operator.inspect_version_claim(version_id=ids["version_id"]),
    ):
        if result.get("status") != "NOT_OBSERVED":
            return {
                **_LABEL,
                "simulation_root": root,
                "run_id": run_id,
                **ids,
                "status": "UNKNOWN",
                "reason": "ORIGINAL_IDS_RECONCILE_FIRST",
                "claim_observation": result,
            }
    fresh = _call(operator.prepare, operator=operator, ids=ids, candidate_id=ids["candidate_id"])
    if fresh != body["proposal"]:
        return {
            **_LABEL,
            "simulation_root": root,
            "run_id": run_id,
            **ids,
            "status": "NOT_COMMITTED",
            "reason": "REVIEW_STALE_OR_CHANGED",
        }
    published = _call(
        operator.publish,
        operator=operator,
        ids=ids,
        proposal=body["proposal"],
        publication_id=ids["publication_id"],
        version_id=ids["version_id"],
        reviewer=reviewer,
        confirmed_at=confirmed_at,
        emit=_emit,
    )
    return _finish(operator, ids, published)


def show(
    *,
    root,
    run_id,
    review_id=None,
    publication_id=None,
    version_id=None,
    candidate_id=None,
    akshare_observation_id=None,
):
    operator = MockCalendarPublication(root=root, run_id=run_id)
    result = {
        **_LABEL,
        "status": "OBSERVED_UNAUTHENTICATED",
        "run_id": run_id,
        "simulation_root": root,
        "publication": "NOT_AUTHORIZED",
    }
    try:
        operator._guard()
        current = operator._store.read_current()
        result["current"] = {
            "status": "VISIBLE_UNAUTHENTICATED",
            "publication_id": current.publication_id,
            "publication_sha256": current.publication_sha256,
            "version_id": current.version_id,
            "version_sha256": current.version_sha256,
            "coverage_status": current.coverage_status,
            "receipt_consistency": "NOT_CHECKED",
        }
    except Exception:
        result["current"] = {"status": "UNKNOWN", "reason": "CURRENT_UNAVAILABLE"}
    if review_id:
        result["pending_review"] = _read_pending(operator, review_id)
    if publication_id:
        result["publication_claim"] = operator.inspect_publication_claim(
            publication_id=publication_id
        )
        result["publication_visibility"] = operator.verify_publication(
            publication_id=publication_id
        )
        result["first_difference"] = operator.inspect_first_difference(
            publication_id=publication_id
        )
    if version_id:
        result["version_claim"] = operator.inspect_version_claim(version_id=version_id)
    if candidate_id:
        result["proposal"] = operator.prepare(candidate_id=candidate_id)
    if akshare_observation_id:
        from bt_api_py.trading_calendar_mock_akshare import show as show_akshare

        result["akshare_candidate"] = show_akshare(
            root=root,
            run_id=run_id,
            observation_id=akshare_observation_id,
            filesystem=runs.TRUSTED_LOCAL_POSIX,
        )
    return result
