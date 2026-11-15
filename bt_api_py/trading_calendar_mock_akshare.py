"""Explicit AKShare observation plus fictional missing-scope windows.

The output is an unauthenticated, read-only candidate and is never published.
The request date and venue are declarations; neither binds the response to an
instrument or establishes trading-day coverage. Only future contract details
are supported by the current AKShare adapter. Mock windows are applied only if
an entire DAY or NIGHT scope has no eligible AKShare syntax candidate.
"""

import math
import time
from datetime import date

from bt_api_py import trading_calendar_akshare_mock_projection as projection
from bt_api_py import trading_calendar_akshare_observation as observation
from bt_api_py import trading_calendar_detail_collection as storage
from bt_api_py import trading_calendar_mock as mock
from bt_api_py import trading_calendar_observation_run as runs
from bt_api_py.trading_calendar_mock_run import read_audit

_SCHEMA = "bt_api.calendar-mock-akshare-request/1"
_LABEL = {
    "environment": "SIMULATION_ONLY",
    "source_authentication": "NOT_PROVEN",
    "publication": "NOT_AUTHORIZED",
    "account_use": "NOT_AUTHORIZED",
}


def validate_request(request, *, year):
    if (
        type(request) is not dict
        or set(request) != {"venue", "contract", "requested_date", "kind", "timeout"}
        or type(year) is not int
        or not 1901 <= year <= 9998
        or request["kind"] != "future"
        or type(request["requested_date"]) is not date
        or request["requested_date"].year not in {year, year + 1}
        or not observation._settings(
            request["venue"], request["contract"], request["requested_date"], request["timeout"]
        )
    ):
        raise ValueError("invalid explicit AKShare request")
    return {**request, "requested_date": request["requested_date"].isoformat()}


def _guard(root, run_id, year):
    fds, chain = runs._open_runs(root, create=False)
    try:
        audit = read_audit(fds[-1], chain, run_id, root)
        if (
            audit is None
            or audit.get("state") != "VISIBLE_UNAUTHENTICATED"
            or audit.get("year") != year
        ):
            raise ValueError("mock audit unavailable")
    finally:
        runs._close(fds)


def _fallback(request, year):
    data, _ = mock.build(year)
    shard = next(item for item in data["shards"] if item["venue"] == request["venue"])
    template = next(item for item in shard["layers"]["trading_hours"] if item["kind"] == "future")
    return {
        "data_origin": "MOCK",
        "environment": "SIMULATION_ONLY",
        "mode": "MISSING_SCOPE",
        "requested_venue": request["venue"],
        "requested_contract": request["contract"],
        "mock_contract": template["contracts"][0],
        "windows": [
            {**window, "scope": scope}
            for name, scope in (("day_windows", "DAY"), ("night_windows", "NIGHT"))
            for window in template[name]
        ],
    }


def _body(*, run_id, observation_id, year, request):
    normalized = validate_request(request, year=year)
    return {
        "schema": _SCHEMA,
        **_LABEL,
        "run_id": run_id,
        "observation_id": observation_id,
        "mock_year": year,
        "request": normalized,
        "fallback": _fallback(normalized, year),
        "association": "DECLARED_ONLY",
        "mapping_identity": "UNKNOWN",
    }


def observe_once(
    *, root, run_id, observation_id, year, request, filesystem, deadline_monotonic=None,
    reservation=None,
):
    """Check a caller deadline before the sidecar and cap its child timeout.

    Sidecar claim and other local I/O are not preemptible and may run beyond
    that deadline before the provider child is started.
    """
    result = {
        **_LABEL,
        "run_id": run_id,
        "observation_id": observation_id,
        "descriptor_state": "NOT_ATTEMPTED",
        "observation_state": "NOT_ATTEMPTED",
        "projection_state": "NOT_ATTEMPTED",
        "association": "DECLARED_ONLY",
        "budget_assurance": "DECISION_AND_CHILD_TIMEOUT_ONLY_LOCAL_IO_UNBOUNDED",
    }
    try:
        if (
            not runs._id(run_id)
            or not runs._id(observation_id)
            or run_id == observation_id
            or filesystem != runs.TRUSTED_LOCAL_POSIX
            or (
                deadline_monotonic is not None
                and (
                    type(deadline_monotonic) not in (int, float)
                    or not math.isfinite(deadline_monotonic)
                )
            )
        ):
            raise ValueError("invalid IDs")
        if deadline_monotonic is not None and deadline_monotonic <= time.monotonic():
            return {**result, "observation_status": "NOT_ATTEMPTED", "reason": "BUDGET_EXHAUSTED"}
        body = _body(run_id=run_id, observation_id=observation_id, year=year, request=request)
        _guard(root, run_id, year)
        if deadline_monotonic is not None and deadline_monotonic <= time.monotonic():
            return {**result, "observation_status": "NOT_ATTEMPTED", "reason": "BUDGET_EXHAUSTED"}
    except Exception:
        return {**result, "reason": "INVALID_OR_UNAVAILABLE_INPUT"}
    remaining = None if deadline_monotonic is None else deadline_monotonic - time.monotonic()
    if remaining is not None and remaining <= 0:
        return {**result, "observation_status": "NOT_ATTEMPTED", "reason": "BUDGET_EXHAUSTED"}
    effective_timeout = (
        request["timeout"] if remaining is None else min(request["timeout"], remaining)
    )
    result.update(
        configured_timeout_seconds=request["timeout"], effective_timeout_seconds=effective_timeout
    )
    descriptor = observation._MockDescriptor(
        root, observation_id, storage._envelope(body), deadline_monotonic
    )
    try:
        captured = observation.observe(
            root=root,
            observation_id=observation_id,
            run_id=run_id,
            venue=request["venue"],
            contract=request["contract"],
            requested_date=request["requested_date"],
            timeout=effective_timeout,
            filesystem=filesystem,
            _mock_descriptor=descriptor,
            **({"reservation": reservation} if reservation is not None else {}),
        )
    except (KeyboardInterrupt, SystemExit) as exc:
        exc.mock_akshare_outcome = {
            **result,
            "descriptor_state": descriptor.state,
            "observation_state": getattr(exc, "record_state", "ATTEMPT_UNKNOWN"),
        }
        raise
    result["descriptor_state"] = descriptor.state
    if descriptor.effective_timeout is not None:
        result["effective_timeout_seconds"] = descriptor.effective_timeout
    if descriptor.state != "COMMITTED" or captured.get("reason") == "BUDGET_EXHAUSTED":
        result["observation_status"] = "NOT_ATTEMPTED"
        return {**result, "reason": captured.get("reason", "DESCRIPTOR_UNAVAILABLE")}
    result["observation_state"] = captured.get("record_state", "UNKNOWN")
    result["observation_status"] = captured.get("observation_status", "UNKNOWN")
    result["observation_result"] = captured
    if captured.get("record_state") != "COMMITTED":
        return {**result, "reason": "OBSERVATION_OUTCOME_UNKNOWN_RECONCILE_ORIGINAL_ID"}
    try:
        _guard(root, run_id, year)
        candidate = projection.project(
            root=root,
            observation_id=observation_id,
            run_id=run_id,
            filesystem=filesystem,
            fallback=body["fallback"],
        )
        result.update(projection_state="VISIBLE_UNAUTHENTICATED", candidate=candidate)
        return result
    except Exception:
        return {**result, "projection_state": "UNKNOWN", "reason": "PROJECTION_UNAVAILABLE"}


def show(*, root, run_id, observation_id, filesystem):
    """Reread only the original request and observation; this proves no write outcome."""
    result = {
        **_LABEL,
        "run_id": run_id,
        "observation_id": observation_id,
        "status": "UNKNOWN",
        "original_write_outcome": "NOT_PROVEN",
    }
    if (
        not runs._id(run_id)
        or not runs._id(observation_id)
        or filesystem != runs.TRUSTED_LOCAL_POSIX
    ):
        return result
    try:
        fds, chain = runs._open_runs(root, create=False)
        try:
            raw = runs._read_entry(fds[-1], chain, f"mock-akshare-{observation_id}.json")
            if raw is None:
                return {**result, "status": "NOT_OBSERVED"}
            body, digest = storage._unwrap(raw)
        finally:
            runs._close(fds)
        request = dict(body["request"])
        request["requested_date"] = date.fromisoformat(request["requested_date"])
        expected = _body(
            run_id=run_id, observation_id=observation_id, year=body["mock_year"], request=request
        )
        if body != expected:
            raise ValueError("descriptor changed")
        _guard(root, run_id, body["mock_year"])
        observed = observation.show(
            root=root,
            observation_id=observation_id,
            run_id=run_id,
            filesystem=filesystem,
            include_content=False,
        )
        result.update(descriptor_sha256=digest, observation=observed)
        if observed.get("status") == "VISIBLE_UNAUTHENTICATED":
            result["status"] = "VISIBLE_UNAUTHENTICATED"
        if observed.get("status") in {"VISIBLE_UNAUTHENTICATED", "ATTEMPT_UNKNOWN"}:
            result["candidate"] = projection.project(
                root=root,
                observation_id=observation_id,
                run_id=run_id,
                filesystem=filesystem,
                fallback=body["fallback"],
            )
        return result
    except Exception:
        return result
