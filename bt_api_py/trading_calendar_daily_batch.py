"""One explicitly configured offline-testable SIMULATION_ONLY daily batch.

No scheduler or account integration. AKShare requests are made only for scopes
explicitly supplied by the caller; a request date is not an effective date.
"""

from __future__ import annotations

import copy
import math
import os
import re
import time
import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime

from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_mixed_publication as mixed_publication
from bt_api_py import trading_calendar_mixed_simulation_version as mixed
from bt_api_py import trading_calendar_mock_akshare as akshare
from bt_api_py import trading_calendar_mock_cli as cli
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py.trading_calendar_mock_publication import MockCalendarPublication
from bt_api_py.trading_calendar_store import TradingCalendarStore, _scope_key

FS = runs.TRUSTED_LOCAL_POSIX
LABEL = {"environment": "SIMULATION_ONLY", "source_authentication": "NOT_PROVEN"}
_FETCH_RETRY_LIMIT = 3
# A refused claim left no descriptor, intent or result; retrying the same
# observation ID is safe only after a read-back proves that nothing was written.
# CLAIM_UNCERTAIN is the real-AKShare descriptor shape; BUDGET_EXHAUSTED and
# DESCRIPTOR_UNAVAILABLE are the shapes the daily batch's mock module returns
# before any descriptor commit. All are retried only when a read-back proves
# that no descriptor, intent or result exists for the attempt id.
_TRANSIENT_FETCH_REASONS = frozenset(
    {"CLAIM_UNCERTAIN", "BUDGET_EXHAUSTED", "DESCRIPTOR_UNAVAILABLE"}
)
_TRANSIENT_FETCH_EXCEPTIONS = (TimeoutError, ConnectionError)
_UNKNOWN_FETCH_STATES = frozenset({"UNKNOWN", "ATTEMPT_UNKNOWN", "COMMIT_UNKNOWN"})
_ATTEMPT_LABEL = {
    "attempt": 0,
    "at": "",
    "outcome": "",
    "reason": "",
}


class ScopeIndexPendingError(ValueError):
    def __init__(self, batch_id):
        super().__init__("scope index reconciliation pending")
        self.batch_id = batch_id


def _now_utc():
    return datetime.now(UTC)


def _retry_policy(value):
    """Return the bounded attempt count; ``None`` selects the documented default."""
    if value is None:
        return 2
    if type(value) is not int or not 1 <= value <= _FETCH_RETRY_LIMIT:
        raise ValueError("invalid fetch retry policy")
    return value


def _unknown_fetch_result(reason):
    """Closed-shape result for an attempt that never produced a classifier input."""
    return {
        "observation_state": "UNKNOWN",
        "observation_status": "UNKNOWN",
        "projection_state": "UNKNOWN",
        "reason": reason,
    }


def _classify_fetch(value, unused):
    """Classify one AKShare observation attempt for retry safety.

    Only a documented transient that provably left no local record may be
    repeated; unknown or committed outcomes are never retried.
    """
    if type(value) is not dict:
        return "UNKNOWN_OUTCOME", "NON_RESULT"
    if (
        value.get("descriptor_state") != "COMMITTED"
        and value.get("observation_state") == "NOT_ATTEMPTED"
        and value.get("reason") in _TRANSIENT_FETCH_REASONS
    ):
        if unused():
            return "TRANSIENT_PRECOMMIT", value["reason"]
        return "NOT_RETRIABLE", "PRECOMMIT_NOT_PROVEN"
    if (
        value.get("observation_state") in _UNKNOWN_FETCH_STATES
        or value.get("descriptor_state") in _UNKNOWN_FETCH_STATES
    ):
        return "UNKNOWN_OUTCOME", "OUTCOME_UNKNOWN"
    return "NOT_RETRIABLE", "DEFINITE_OR_UNCLASSIFIED"


def _fetch_with_retry(*, fetch, max_attempts, unused):
    """Run the source fetch at most ``max_attempts`` times; fail closed.

    Returns ``(value, attempts, outcome)``. Every attempt records an attempt
    number, a local unverified-clock timestamp, an outcome and a reason code.
    Unknown outcomes and committed records are returned untouched.
    """
    if type(max_attempts) is not int or not 1 <= max_attempts <= _FETCH_RETRY_LIMIT:
        raise ValueError("invalid fetch retry policy")
    attempts = []
    value = None
    while len(attempts) < max_attempts:
        attempt = len(attempts) + 1
        stamp = _now_utc().isoformat(timespec="microseconds").replace("+00:00", "Z")
        try:
            value = fetch()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            proven = isinstance(exc, _TRANSIENT_FETCH_EXCEPTIONS) and bool(unused())
            attempts.append(
                {
                    **_ATTEMPT_LABEL,
                    "attempt": attempt,
                    "at": stamp,
                    "outcome": "TRANSIENT_PRECOMMIT" if proven else "UNKNOWN_OUTCOME",
                    "reason": (
                        type(exc).__name__ if proven else "UNEXPECTED_EXCEPTION"
                    ),
                }
            )
            if proven and attempt < max_attempts:
                continue
            return (
                _unknown_fetch_result("FETCH_OUTCOME_UNKNOWN"),
                attempts,
                "TRANSIENT_EXHAUSTED" if proven else "UNKNOWN_OUTCOME",
            )
        outcome, reason = _classify_fetch(value, unused)
        attempts.append(
            {
                **_ATTEMPT_LABEL,
                "attempt": attempt,
                "at": stamp,
                "outcome": outcome,
                "reason": reason,
            }
        )
        if outcome == "TRANSIENT_PRECOMMIT":
            if attempt < max_attempts:
                continue
            return value, attempts, "TRANSIENT_EXHAUSTED"
        return value, attempts, outcome
    return (_unknown_fetch_result("FETCH_OUTCOME_UNKNOWN"), attempts, "UNKNOWN_OUTCOME")


def _retry_summary(records, max_attempts):
    """Aggregate per-scope attempt metadata for the caller-facing result."""
    return {
        "max_attempts": max_attempts,
        "retried": sum(1 for item in records if len(item["attempts"]) > 1),
        "outcome_unknown": sum(1 for item in records if item["outcome"] == "UNKNOWN_OUTCOME"),
        "exhausted": sum(1 for item in records if item["outcome"] == "TRANSIENT_EXHAUSTED"),
        "attempts": records,
        "clock": "LOCAL_CLOCK_UNVERIFIED",
    }


def _record_fetch_retry(root, batch_id, records, max_attempts):
    """Persist attempt count, reason codes and timestamps outside the fixed evidence schema.

    The batch evidence row shape is a frozen contract; attempts live in their own
    unauthenticated local record. A failed write is reported, never assumed.
    """
    body = {
        "schema": "bt_api.calendar-daily-batch-fetch-retry/1",
        **LABEL,
        "batch_id": batch_id,
        "max_attempts": max_attempts,
        "scopes": records,
        "clock": "LOCAL_CLOCK_UNVERIFIED",
    }
    name = f"mock-daily-batch-fetch-retry-{batch_id}.json"
    try:
        state, _ = _write(root, name, body)
    except Exception:
        try:
            existing = _read(root, name)
        except Exception:
            return "UNKNOWN"
        if existing is None or existing[0] != body:
            return "UNKNOWN"
        return "VISIBLE_UNAUTHENTICATED"
    return "VISIBLE_UNAUTHENTICATED" if state == "COMMITTED" else "UNKNOWN"


def _observation_id_unused(root, observation_id):
    """Prove that no descriptor, intent or result exists for this attempt ID."""
    from bt_api_py import trading_calendar_akshare_observation as observation

    try:
        if _read(root, f"mock-akshare-{observation_id}.json") is not None:
            return False
        try:
            fds, chain = observation._open(root, False)
        except FileNotFoundError:
            # No observation directory means no intent or result can be present.
            return True
        except Exception:
            return False
        try:
            return all(
                storage._read(fds[-1], chain, name, observation._LIMIT) is None
                for name in (f"intent-{observation_id}.json", f"result-{observation_id}.json")
            )
        except Exception:
            return False
        finally:
            runs._close(fds)
    except Exception:
        return False


def scope_sha256(scope):
    """Return the deterministic hash of a caller-declared scope, not source proof."""
    return mixed_publication._batch_scope_sha256(scope)


def _identity_bundle(value, run_id, scopes, requested_date):
    keys = {
        "original_run_id",
        "run_id",
        "batch_id",
        "reservation_id",
        "capture_id",
        "parent_id",
        "derived_id",
        "candidate_id",
        "review_id",
        "publication_id",
        "version_id",
        "observations",
    }
    if (
        type(value) is not dict
        or set(value) != keys
        or type(requested_date) is not date
        or value["original_run_id"] != run_id
        or type(value["observations"]) is not list
        or len(value["observations"]) != len(scopes)
    ):
        raise ValueError("invalid bundle")
    standard = keys - {"publication_id", "version_id", "observations"}
    if any(not runs._id(value[key]) for key in standard):
        raise ValueError("invalid bundle identity")
    if any(
        type(value[key]) is not str
        or re.fullmatch(r"MOCK_[A-Za-z0-9_-]{1,123}", value[key]) is None
        for key in ("publication_id", "version_id")
    ):
        raise ValueError("invalid mock identity")
    scope_keys = [_scope_key(scope["target"]) for scope in scopes]
    if scope_keys != sorted(scope_keys):
        raise ValueError("scope order mismatch")
    observations = value["observations"]
    for row, scope in zip(observations, scopes, strict=True):
        if (
            type(row) is not dict
            or set(row) != {"observation_id", "scope_sha256"}
            or not runs._id(row["observation_id"])
            or row["scope_sha256"] != scope_sha256(scope)
        ):
            raise ValueError("observation scope mismatch")
    identities = [value[key] for key in standard | {"publication_id", "version_id"}]
    identities.extend(row["observation_id"] for row in observations)
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate bundle identity")
    return copy.deepcopy(value)


def _write(root, name, body):
    fds, chain = runs._open_runs(root, create=False)
    try:
        return runs._write_once(fds[-1], chain, name, storage._envelope(body))
    finally:
        runs._close(fds)


def _read(root, name):
    fds, chain = runs._open_runs(root, create=False)
    try:
        raw = runs._read_entry(fds[-1], chain, name)
    finally:
        runs._close(fds)
    return None if raw is None else storage._unwrap(raw)


def _baseline(operator):
    operator._guard()
    with operator._store._readonly_lock():
        if operator._store._pending_exists() or operator._store._policy_change_pending():
            raise ValueError("store operation pending")
        policy = operator._store._review_policy_locked()
        if policy is None:
            raise ValueError("policy unavailable")
        operator._check_policy_locked(policy)
        fds, chain = runs._open_runs(operator.root, create=False)
        try:
            marker = _read(operator.root, "scope-check-head.json")
            if (
                marker is not None
                and marker[0].get("schema") == "bt_api.calendar-scope-check-head/2"
            ):
                from bt_api_py import trading_calendar_scope_index as scope_index

                index_marker, _ = scope_index._ready(operator.root, fds[-1], chain)
                pending = scope_index._pending(operator.root, fds[-1], chain)
                if pending is not None:
                    raise ScopeIndexPendingError(pending[0]["batch_id"])
                with scope_index.connect(
                    root=operator.root,
                    fd=fds[-1],
                    chain=chain,
                    database_id=index_marker["database_id"],
                    migration_id=index_marker["migration_id"],
                    write=False,
                ):
                    pass
        finally:
            runs._close(fds)
        current = operator._store.read_current()
        operator._check_simulated_current(current)
        return (
            {
                "publication_id": current.publication_id,
                "publication_sha256": current.publication_sha256,
                "version_sha256": current.version_sha256,
            },
            asdict(policy),
        )


def run(
    *,
    root,
    run_id,
    scopes,
    supplement=None,
    budget_seconds=600,
    identity_bundle=None,
    requested_date=None,
    fetch_retry=None,
):
    """Capture scopes once; an explicit date is caller-declared Asia/Shanghai, not effective date.

    The optional bundle contains original_run_id, run_id, batch_id, reservation_id,
    capture_id, parent_id, derived_id, candidate_id, review_id, publication_id,
    version_id, and observations. Each observation has observation_id and the
    scope_sha256 of the corresponding scope. Scopes must be ordered by their
    ``(venue, kind, product, mock_contract, valid_from, valid_to)`` target key.
    Pass the caller's already captured local date as ``requested_date``. A
    committed identity reserves observation IDs only; it authenticates no source.

    ``fetch_retry`` bounds the per-scope AKShare attempt count (1-3; ``None``
    keeps the default of two). Only a documented transient that a read-back
    proves left no descriptor, intent or result is repeated; unknown or
    committed outcomes are never retried and stay UNKNOWN.
    """
    try:
        fetch_attempts = _retry_policy(fetch_retry)
    except ValueError:
        return {**LABEL, "status": "NOT_COMMITTED", "reason": "INVALID_FETCH_RETRY"}
    explicit = identity_bundle is not None or requested_date is not None
    if explicit:
        try:
            if (
                type(scopes) is not list
                or not 2 <= len(scopes) <= 8
                or any(type(item) is not dict for item in scopes)
            ):
                raise ValueError("invalid scopes")
            scopes = copy.deepcopy(scopes)
            identity_bundle = _identity_bundle(identity_bundle, run_id, scopes, requested_date)
        except Exception:
            return {**LABEL, "status": "NOT_COMMITTED", "reason": "INVALID_IDENTITY_BUNDLE"}
    recovery = (
        {
            "identity_bundle": copy.deepcopy(identity_bundle),
            "requested_date": requested_date.isoformat(),
            "request_date_basis": "CALLER_DECLARED_ASIA_SHANGHAI_DATE",
        }
        if explicit
        else {}
    )
    try:
        if (
            type(scopes) is not list
            or not 2 <= len(scopes) <= 8
            or type(budget_seconds) not in (int, float)
            or not math.isfinite(budget_seconds)
            or not 0 < budget_seconds <= 600
        ):
            raise ValueError
        for item in scopes:
            if (
                type(item) is not dict
                or set(item) != {"contract", "target", "assumptions", "alignment", "timeout"}
                or item["alignment"] != "REPEAT_OVER_MOCK_OWNER_DAY_MAPPING"
            ):
                raise ValueError
            mixed._template_input(item["target"], item["assumptions"])
        keys = [_scope_key(item["target"]) for item in scopes]
        if len(keys) != len(set(keys)):
            return {**LABEL, **recovery, "status": "NOT_COMMITTED", "reason": "BATCH_SCOPE_OVERLAP"}
        for i, left in enumerate(keys):
            for right in keys[i + 1 :]:
                if (
                    left[:2] == right[:2]
                    and left[3] == right[3]
                    and left[4] <= right[5]
                    and right[4] <= left[5]
                ):
                    return {
                        **LABEL,
                        **recovery,
                        "status": "NOT_COMMITTED",
                        "reason": "BATCH_SCOPE_OVERLAP",
                    }
        if not explicit:
            requested_date = cli._beijing_today()
        deadline = time.monotonic() + budget_seconds
        operator = MockCalendarPublication(root=root, run_id=run_id)
        if operator._declaration is None:
            raise ValueError
        from bt_api_py.trading_calendar_check import _absolute_path

        path = str(
            _absolute_path(
                supplement if supplement is not None else os.path.join(root, "supplement.yaml")
            )
        )
        os.lstat(path)
        for item in scopes:
            akshare.validate_request(
                {
                    "venue": item["target"]["venue"],
                    "contract": item["contract"],
                    "requested_date": requested_date,
                    "kind": "future",
                    "timeout": item["timeout"],
                },
                year=operator._audit["year"],
            )
        baseline, policy = _baseline(operator)
        from bt_api_py import trading_calendar_mock as mock

        parent_data, _ = mock.build(operator._audit["year"])
        for item in scopes:
            try:
                shard, row = mixed_publication._scope_rows(parent_data, item["target"])
                if (
                    not (
                        shard["coverage_start"]
                        <= item["target"]["valid_from"]
                        <= item["target"]["valid_to"]
                        <= shard["coverage_end"]
                    )
                    or not row["windows_complete"]
                    or not row["exceptions_complete"]
                    or any(
                        item["assumptions"][scope]["phase"] not in {"continuous", "auction"}
                        or item["assumptions"][scope]["timezone"] != "Asia/Shanghai"
                        for scope in ("DAY", "NIGHT")
                    )
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                return {
                    **LABEL,
                    **recovery,
                    "status": "NOT_COMMITTED",
                    "reason": "BATCH_TARGET_NOT_MATERIALIZABLE",
                }
    except ScopeIndexPendingError as exc:
        return {
            **LABEL,
            **recovery,
            "status": "UNKNOWN",
            "reason": "SCOPE_INDEX_RECONCILE_ORIGINAL_ID",
            "batch_id": exc.batch_id,
            "old_writers": "OLD_WRITERS_STOPPED_DECLARED_NOT_VERIFIED",
            "publication": "NOT_ATTEMPTED",
        }
    except FileNotFoundError:
        return {**LABEL, **recovery, "status": "NOT_COMMITTED", "reason": "SUPPLEMENT_MISSING"}
    except Exception as exc:
        if hasattr(exc, "scope_migration_id"):
            return {
                **LABEL,
                **recovery,
                "status": "UNKNOWN",
                "reason": "SCOPE_MIGRATION_RECONCILE_ORIGINAL_ID",
                "migration_id": exc.scope_migration_id,
                "old_writers": "OLD_WRITERS_STOPPED_DECLARED_NOT_VERIFIED",
                "publication": "NOT_ATTEMPTED",
            }
        return {
            **LABEL,
            **recovery,
            "status": "UNKNOWN",
            "reason": "BATCH_INPUT_OR_BASELINE_UNAVAILABLE",
        }
    if explicit:
        new_run, capture_id, parent_id = (
            identity_bundle[key] for key in ("run_id", "capture_id", "parent_id")
        )
        batch_id, candidate_id = identity_bundle["batch_id"], identity_bundle["candidate_id"]
        ids = {key: identity_bundle[key] for key in ("review_id", "publication_id", "version_id")}
        ids["candidate_id"] = identity_bundle["derived_id"]
        observations = [row["observation_id"] for row in identity_bundle["observations"]]
    else:
        new_run, capture_id, parent_id, batch_id, candidate_id = (
            uuid.uuid4().hex for _ in range(5)
        )
        ids = cli.allocate()
        observations = [uuid.uuid4().hex for _ in scopes]
    all_ids = {new_run, capture_id, parent_id, batch_id, candidate_id, *ids.values(), *observations}
    if len(all_ids) != 9 + len(observations) or run_id in all_ids:
        return {**LABEL, **recovery, "status": "NOT_COMMITTED", "reason": "INVALID_ALLOCATED_IDS"}
    cli._report(
        "MOCK_BATCH_IDS_ALLOCATED",
        simulation_root=root,
        run_id=new_run,
        original_run_id=run_id,
        batch_id=batch_id,
        capture_id=capture_id,
        parent_id=parent_id,
        derived_id=ids["candidate_id"],
        batch_candidate_id=candidate_id,
        observation_ids=observations,
        **ids,
    )
    intent = {
        "schema": "bt_api.calendar-daily-batch-intent/2"
        if explicit
        else "bt_api.calendar-daily-batch-intent/1",
        **LABEL,
        "batch_id": batch_id,
        "run_id": new_run,
        "original_run_id": run_id,
        "candidate_id": candidate_id,
        "derived_id": ids["candidate_id"],
        "capture_id": capture_id,
        "parent_id": parent_id,
        "review_id": ids["review_id"],
        "publication_id": ids["publication_id"],
        "version_id": ids["version_id"],
        "observations": [
            {"observation_id": oid, **scope}
            for oid, scope in zip(observations, scopes, strict=True)
        ],
        "requested_date": requested_date.isoformat(),
        "date_role": "REQUEST_DATE_NOT_EFFECTIVE_DATE",
        "budget_seconds": budget_seconds,
        "baseline": baseline,
        "policy": policy,
    }
    if explicit:
        intent["reservation_id"] = identity_bundle["reservation_id"]
        intent["request_date_basis"] = "CALLER_DECLARED_ASIA_SHANGHAI_DATE"
        for row, binding in zip(
            intent["observations"], identity_bundle["observations"], strict=True
        ):
            row["scope_sha256"] = binding["scope_sha256"]
    try:
        state, reason = _write(root, f"mock-daily-batch-intent-{batch_id}.json", intent)
    except Exception:
        if not explicit:
            raise
        return {
            **LABEL,
            **recovery,
            "status": "UNKNOWN",
            "reason": "BATCH_INTENT_RECONCILE_FIRST",
            "batch_id": batch_id,
            "intent_state": "UNKNOWN",
        }
    except BaseException as exc:
        if explicit:
            exc.identity_bundle = copy.deepcopy(identity_bundle)
        raise
    if state != "COMMITTED":
        return {
            **LABEL,
            **recovery,
            "status": "UNKNOWN",
            "reason": "BATCH_INTENT_RECONCILE_FIRST",
            "batch_id": batch_id,
            "intent_state": state,
            "detail": reason,
        }
    try:
        stored_intent, intent_sha = mixed_publication._batch_intent(root, batch_id)
        if stored_intent != intent:
            raise ValueError
    except Exception:
        return {
            **LABEL,
            **recovery,
            "status": "UNKNOWN",
            "reason": "BATCH_INTENT_RECONCILE_FIRST",
            "batch_id": batch_id,
        }
    except BaseException as exc:
        if explicit:
            exc.identity_bundle = copy.deepcopy(identity_bundle)
        raise

    if explicit:
        from bt_api_py import trading_calendar_akshare_observation as observation

        requests = [
            {
                "observation_id": row["observation_id"],
                "scope_sha256": row["scope_sha256"],
                "venue": scope["target"]["venue"],
                "contract": scope["contract"],
                "requested_date": requested_date,
                "timeout": scope["timeout"],
            }
            for row, scope in zip(identity_bundle["observations"], scopes, strict=True)
        ]
        try:
            reservation = observation.reserve_observations(
                root=root,
                reservation_id=identity_bundle["reservation_id"],
                batch_id=batch_id,
                run_id=new_run,
                requests=requests,
                filesystem=FS,
            )
        except Exception:
            return {
                **LABEL,
                **recovery,
                "status": "UNKNOWN",
                "reason": "RESERVATION_RECONCILE_ORIGINAL_ID",
                "batch_id": batch_id,
                "publication": "NOT_ATTEMPTED",
            }
        except BaseException as exc:
            exc.identity_bundle = copy.deepcopy(identity_bundle)
            raise
        if (
            type(reservation) is not dict
            or reservation.get("status") != "COMMITTED"
            or any(
                reservation.get(key) != value
                for key, value in (
                    ("reservation_id", identity_bundle["reservation_id"]),
                    ("batch_id", batch_id),
                    ("run_id", new_run),
                )
            )
        ):
            return {
                **LABEL,
                **recovery,
                "status": "UNKNOWN",
                "reason": "RESERVATION_RECONCILE_ORIGINAL_ID",
                "batch_id": batch_id,
                "reservation_result": reservation,
                "publication": "NOT_ATTEMPTED",
            }
        try:
            shown = observation.show_reservation(
                root=root, reservation_id=identity_bundle["reservation_id"], filesystem=FS
            )
            expected = observation._reservation_payload(
                identity_bundle["reservation_id"], batch_id, new_run, requests
            )
            reservation_digest = storage._unwrap(storage._envelope(expected))[1]
            if (
                shown.get("status") != "VISIBLE_UNAUTHENTICATED"
                or shown.get("reservation") != expected
                or shown.get("payload_sha256") != reservation_digest
            ):
                raise ValueError("reservation readback mismatch")
        except Exception:
            return {
                **LABEL,
                **recovery,
                "status": "UNKNOWN",
                "reason": "RESERVATION_RECONCILE_ORIGINAL_ID",
                "batch_id": batch_id,
                "publication": "NOT_ATTEMPTED",
            }
        except BaseException as exc:
            exc.identity_bundle = copy.deepcopy(identity_bundle)
            raise

    def work(*, operator, derived_id, binding, ids, result):
        records, templates, captures, retry_records = [], [], [], []

        def retry_report():
            summary = _retry_summary(retry_records, fetch_attempts)
            if any(
                len(item["attempts"]) > 1
                or item["outcome"] in {"UNKNOWN_OUTCOME", "TRANSIENT_EXHAUSTED"}
                for item in retry_records
            ):
                summary["record"] = _record_fetch_retry(
                    root, batch_id, retry_records, fetch_attempts
                )
            else:
                summary["record"] = "NOT_NECESSARY"
            return summary

        try:
            if _baseline(operator) != (baseline, policy):
                return {
                    **result,
                    "status": "NOT_COMMITTED",
                    "reason": "BATCH_BASELINE_CHANGED",
                    "batch_id": batch_id,
                }
            for index, (scope, oid) in enumerate(zip(scopes, observations, strict=True)):
                try:
                    if _baseline(operator) != (baseline, policy):
                        return {
                            **result,
                            "status": "NOT_COMMITTED",
                            "reason": "BATCH_BASELINE_CHANGED",
                            "batch_id": batch_id,
                            "observations": records,
                        }
                except ScopeIndexPendingError as exc:
                    return {
                        **result,
                        "status": "UNKNOWN",
                        "reason": "SCOPE_INDEX_RECONCILE_ORIGINAL_ID",
                        "batch_id": batch_id,
                        "pending_batch_id": exc.batch_id,
                        "old_writers": "OLD_WRITERS_STOPPED_DECLARED_NOT_VERIFIED",
                        "observations": records,
                        "publication": "NOT_ATTEMPTED",
                    }
                request = {
                    "venue": scope["target"]["venue"],
                    "contract": scope["contract"],
                    "requested_date": requested_date,
                    "kind": "future",
                    "timeout": scope["timeout"],
                }

                def fetch_once(oid=oid, request=request, index=index):
                    try:
                        mixed_publication._batch_intent(root, batch_id, intent_sha)
                    except Exception:
                        return {
                            "observation_state": "NOT_ATTEMPTED",
                            "observation_status": "NOT_ATTEMPTED",
                            "projection_state": "NOT_ATTEMPTED",
                            "reason": "BATCH_INTENT_UNAVAILABLE",
                        }
                    return akshare.observe_once(
                        root=root,
                        run_id=new_run,
                        observation_id=oid,
                        year=operator._audit["year"],
                        request=request,
                        filesystem=FS,
                        deadline_monotonic=deadline,
                        **(
                            {
                                "reservation": {
                                    "reservation_id": identity_bundle["reservation_id"],
                                    "batch_id": batch_id,
                                    "scope_sha256": identity_bundle["observations"][index][
                                        "scope_sha256"
                                    ],
                                }
                            }
                            if explicit
                            else {}
                        ),
                    )

                fetched, retry_attempts, retry_outcome = _fetch_with_retry(
                    fetch=fetch_once,
                    max_attempts=fetch_attempts,
                    unused=lambda oid=oid: _observation_id_unused(root, oid),
                )
                row = {
                    "observation_id": oid,
                    "source_type": "AKSHARE",
                    "source_authentication": "NOT_PROVEN",
                    "target": scope["target"],
                    "assumptions": scope["assumptions"],
                    "alignment": scope["alignment"],
                    "timeout": scope["timeout"],
                    "contract": scope["contract"],
                    "requested_date": requested_date.isoformat(),
                    "date_role": "REQUEST_DATE_NOT_EFFECTIVE_DATE",
                    "observation_state": fetched.get("observation_state", "UNKNOWN"),
                    "observation_status": fetched.get("observation_status", "UNKNOWN"),
                    "projection_state": fetched.get("projection_state", "UNKNOWN"),
                    "reason": fetched.get("reason", "NONE"),
                }
                retry_records.append(
                    {
                        "observation_id": oid,
                        "venue": scope["target"]["venue"],
                        "contract": scope["contract"],
                        "max_attempts": fetch_attempts,
                        "attempts": retry_attempts,
                        "outcome": retry_outcome,
                    }
                )
                records.append(row)
                captures.append(fetched.get("observation_result", {}))
                if explicit and (
                    fetched.get("descriptor_state") != "COMMITTED"
                    or row["observation_state"] != "COMMITTED"
                    or row["observation_status"]
                    not in ({"SUCCESS"} | mixed_publication._DEFINITE_FAILURES)
                    or row["projection_state"] == "UNKNOWN"
                ):
                    return {
                        **result,
                        "status": "UNKNOWN",
                        "reason": "OBSERVATION_RECONCILE_ORIGINAL_IDS",
                        "batch_id": batch_id,
                        "observations": records,
                        "publication": "NOT_ATTEMPTED",
                        "fetch_retry": retry_report(),
                    }
                if (
                    row["observation_state"] != "COMMITTED"
                    or row["observation_status"] != "SUCCESS"
                    or row["projection_state"] != "VISIBLE_UNAUTHENTICATED"
                ):
                    continue
                try:
                    template = mixed.build_template(
                        root=root,
                        candidate_root=root,
                        observation_id=oid,
                        run_id=new_run,
                        candidate_id=derived_id,
                        filesystem=FS,
                        target=scope["target"],
                        assumptions=scope["assumptions"],
                    )
                    digest = mixed.template_digest(
                        template, root=root, candidate_root=root, filesystem=FS, include_trace=True
                    )
                    templates.append({"template": template, "evidence_sha256": digest})
                except Exception:
                    row["reason"] = "TEMPLATE_UNAVAILABLE"
            partial = len(templates) != len(scopes)
            if partial:
                if _baseline(operator) != (baseline, policy):
                    return {
                        **result,
                        "status": "NOT_COMMITTED",
                        "reason": "BATCH_BASELINE_CHANGED",
                        "batch_id": batch_id,
                        "observations": records,
                    }
                current = TradingCalendarStore(root).read_current()
                prior = current.simulation_provenance
                prior_scopes = (
                    prior["scopes"] if prior and "scopes" in prior else [prior] if prior else []
                )
                successes = {entry["template"]["trace"]["observation_id"] for entry in templates}
                from bt_api_py import trading_calendar_akshare_observation as observation

                for row, captured in zip(records, captures, strict=True):
                    shown = observation.show(
                        root=root,
                        run_id=new_run,
                        observation_id=row["observation_id"],
                        filesystem=FS,
                    )
                    row["observation_diagnostics"] = {
                        key: shown.get(key, captured.get(key, "UNKNOWN"))
                        for key in (
                            "data_state",
                            "transport_status",
                            "direct_child_cleanup",
                            "timeout_retry",
                            "automatic_retry",
                            "capture_state",
                            "reported_observation_status",
                        )
                    }
                    row["failure_proof"] = None
                    if row["observation_id"] in successes:
                        row.update(check_status="SUCCESS", disposition="UPDATED_AKSHARE")
                    elif (
                        row["observation_state"] == "COMMITTED"
                        and row["observation_status"] in mixed_publication._DEFINITE_FAILURES
                    ):
                        try:
                            row["failure_proof"] = mixed_publication._failure_proof(
                                root, new_run, row
                            )
                        except Exception:
                            row.update(
                                check_status="UNKNOWN",
                                disposition="UNAVAILABLE",
                                reason="FAILURE_OBSERVATION_RECONCILE_FIRST",
                            )
                        else:
                            retained = any(
                                scope["target"] == row["target"] for scope in prior_scopes
                            )
                            row.update(
                                check_status="FAILED",
                                disposition="RETAINED_AKSHARE" if retained else "MOCK_FALLBACK",
                            )
                    else:
                        row.update(check_status="UNKNOWN", disposition="UNAVAILABLE")
                if not templates:
                    for row in records:
                        if row["check_status"] == "FAILED":
                            row["disposition"] = "CURRENT_UNCHANGED"
            evidence = {
                "schema": (
                    "bt_api.calendar-daily-batch-evidence/4"
                    if partial
                    else "bt_api.calendar-daily-batch-evidence/3"
                )
                if explicit
                else (
                    "bt_api.calendar-daily-batch-evidence/2"
                    if partial
                    else "bt_api.calendar-daily-batch-evidence/1"
                ),
                **LABEL,
                "batch_id": batch_id,
                "run_id": new_run,
                "candidate_id": candidate_id,
                "derived_id": derived_id,
                "baseline": baseline,
                "policy": policy,
                "intent_sha256": intent_sha,
                "supplement_sha256": binding["normalized_content_sha256"],
                "observations": records,
            }
            if explicit:
                evidence["reservation_payload_sha256"] = reservation_digest
            state, reason = _write(root, f"mock-daily-batch-{batch_id}.json", evidence)
            if state != "COMMITTED":
                return {
                    **result,
                    "status": "UNKNOWN",
                    "reason": "BATCH_EVIDENCE_RECONCILE_FIRST",
                    "batch_id": batch_id,
                    "evidence_state": state,
                    "observations": records,
                }
            reread = _read(root, f"mock-daily-batch-{batch_id}.json")
            if reread is None or reread[0] != evidence:
                return {
                    **result,
                    "status": "UNKNOWN",
                    "reason": "BATCH_EVIDENCE_RECONCILE_FIRST",
                    "batch_id": batch_id,
                    "observations": records,
                }
            try:
                mixed_publication._batch_record(root, batch_id, reread[1])
            except Exception:
                return {
                    **result,
                    "status": "UNKNOWN",
                    "reason": "BATCH_INTENT_RECONCILE_FIRST",
                    "batch_id": batch_id,
                    "observations": records,
                    "publication": "NOT_ATTEMPTED",
                }
            from bt_api_py import trading_calendar_scope_checks as scope_checks

            checks = scope_checks.record(
                operator=operator, batch_id=batch_id, evidence_sha256=reread[1]
            )
            if checks.get("status") != "VISIBLE_UNAUTHENTICATED":
                return {
                    **result,
                    "status": "UNKNOWN",
                    "reason": "SCOPE_CHECK_RECONCILE_FIRST",
                    "batch_id": batch_id,
                    "batch_evidence_sha256": reread[1],
                    "observations": records,
                    "scope_checks": checks,
                    "publication": "NOT_ATTEMPTED",
                }
            if partial and (
                not templates or any(row["check_status"] == "UNKNOWN" for row in records)
            ):
                return {
                    **result,
                    "status": "UNKNOWN"
                    if any(row["check_status"] == "UNKNOWN" for row in records)
                    else "NOT_COMMITTED",
                    "reason": "BATCH_ALL_FAILED"
                    if not templates and all(row["check_status"] == "FAILED" for row in records)
                    else "BATCH_INCOMPLETE",
                    "batch_id": batch_id,
                    "observations": records,
                    "publication": "NOT_ATTEMPTED",
                    "fallback": "EXISTING_CURRENT_NOT_REPLACED_BY_THIS_RUN",
                    "scope_checks": checks,
                    "fetch_retry": retry_report(),
                }
            if _baseline(operator) != (baseline, policy):
                return {
                    **result,
                    "status": "NOT_COMMITTED",
                    "reason": "BATCH_BASELINE_CHANGED",
                    "batch_id": batch_id,
                    "observations": records,
                    "scope_checks": checks,
                }
            built = mixed_publication.build_batch_once(
                root=root,
                filesystem=FS,
                candidate_id=candidate_id,
                templates=templates,
                batch_id=batch_id,
                batch_record_sha256=reread[1],
                baseline=baseline,
            )
            if built.get("status") != "COMMITTED":
                return {
                    **result,
                    "status": built.get("status", "UNKNOWN"),
                    "reason": built.get("reason", "BATCH_CANDIDATE_RECONCILE_FIRST"),
                    "batch_id": batch_id,
                    "observations": records,
                    "scope_checks": checks,
                }
            final_ids = {**ids, "candidate_id": candidate_id}
            proposal = cli._call(
                operator.prepare, operator=operator, ids=final_ids, candidate_id=candidate_id
            )
            action = cli._act(operator, final_ids, proposal, input_binding=binding)
            return {
                **result,
                **action,
                "data_origin": "AKSHARE_MOCK",
                "batch_id": batch_id,
                "batch_evidence_sha256": reread[1],
                "observations": records,
                "batch_status": "PARTIAL" if partial else "COMPLETE",
                "scope_checks": checks,
                "date_role": "REQUEST_DATE_NOT_EFFECTIVE_DATE",
                "fetch_retry": retry_report(),
            }
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return {
                **result,
                "status": "UNKNOWN",
                "reason": "BATCH_RECONCILE_ORIGINAL_IDS",
                "batch_id": batch_id,
                "observations": records,
            }

    try:
        result = cli._update_with_supplement(
            operator,
            path=path,
            variant="BASE",
            new_run_id=new_run,
            capture_id=capture_id,
            parent_id=parent_id,
            ids=ids,
            batch_work=work,
            parent_data=parent_data,
        )
        return {**result, **recovery}
    except Exception:
        if not explicit:
            raise
        return {
            **LABEL,
            **recovery,
            "status": "UNKNOWN",
            "reason": "BATCH_RECONCILE_ORIGINAL_IDS",
            "batch_id": batch_id,
        }
    except BaseException as exc:
        if explicit:
            exc.identity_bundle = copy.deepcopy(identity_bundle)
        raise


def show(*, root, run_id, batch_id, reconcile_scope_checks=False, legacy_scope_ids=None):
    if type(reconcile_scope_checks) is not bool:
        return {**LABEL, "status": "UNKNOWN", "reason": "INVALID_RECONCILE_OPTION"}
    if not runs._id(batch_id) or not runs._id(run_id):
        return {**LABEL, "status": "UNKNOWN", "reason": "INVALID_BATCH_ID"}
    try:
        operator = MockCalendarPublication(root=root, run_id=run_id)
        operator._guard()
        intent = _read(root, f"mock-daily-batch-intent-{batch_id}.json")
        evidence = _read(root, f"mock-daily-batch-{batch_id}.json")
        if intent is None:
            return (
                {**LABEL, "status": "UNKNOWN", "reason": "BATCH_ORPHAN_EVIDENCE"}
                if evidence is not None
                else {**LABEL, "status": "NOT_OBSERVED"}
            )
        if (
            intent[0].get("batch_id") != batch_id
            or run_id not in {intent[0].get("run_id"), intent[0].get("original_run_id")}
            or (evidence is not None and evidence[0].get("batch_id") != batch_id)
        ):
            raise ValueError
        checked_intent, _ = mixed_publication._batch_intent(root, batch_id, intent[1])
        if checked_intent["schema"] == "bt_api.calendar-daily-batch-intent/2":
            mixed_publication._check_batch_reservation(root, checked_intent)
            if evidence is None:
                return {
                    **LABEL,
                    "status": "UNKNOWN",
                    "reason": "BATCH_EVIDENCE_UNAVAILABLE",
                    "batch_id": batch_id,
                }
        if evidence is not None:
            mixed_publication._batch_record(root, batch_id, evidence[1])
        from bt_api_py import trading_calendar_scope_checks as scope_checks

        reconciliation = (
            scope_checks.reconcile(operator=operator, batch_id=batch_id)
            if reconcile_scope_checks
            else None
        )
        scope_report = scope_checks.show(
            operator=operator,
            targets=[
                {"target": item["target"], "requested_contract": item["contract"]}
                for item in intent[0]["observations"]
            ],
            batch_id=batch_id,
            legacy_ids=legacy_scope_ids,
        )
        return {
            **LABEL,
            "status": "VISIBLE_UNAUTHENTICATED"
            if scope_report.get("status") == "VISIBLE_UNAUTHENTICATED"
            and (
                reconciliation is None or reconciliation.get("status") == "VISIBLE_UNAUTHENTICATED"
            )
            else "UNKNOWN",
            "intent": intent[0],
            "intent_sha256": intent[1],
            "evidence": None if evidence is None else evidence[0],
            "evidence_sha256": None if evidence is None else evidence[1],
            "scope_checks": scope_report,
            "scope_check_reconciliation": reconciliation,
        }
    except Exception:
        return {**LABEL, "status": "UNKNOWN", "reason": "BATCH_EVIDENCE_UNAVAILABLE"}
