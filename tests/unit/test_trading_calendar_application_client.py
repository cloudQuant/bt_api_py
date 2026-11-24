from __future__ import annotations

from datetime import date

from bt_api_py.trading_calendar_application_client import TradingCalendarApplicationClient
from bt_api_py.trading_calendar_application_service import (
    CALENDAR_APPLICATION_SCHEMA,
    CALENDAR_CANDIDATE_READ_OPERATION,
    CALENDAR_CURRENT_READ_OPERATION,
    SIMULATION_ONLY,
)


def _current(directory_ref: str = "calendar-main") -> dict:
    return {
        "schema": CALENDAR_APPLICATION_SCHEMA,
        "status": "CURRENT_VISIBLE_UNAUTHENTICATED",
        "reason": "OK",
        "directory_ref": directory_ref,
        "mode": SIMULATION_ONLY,
        "sequence": 7,
        "content_sha256": "a" * 64,
        "publication_sha256": "b" * 64,
        "coverage_status": "DECLARED_UNVERIFIED",
        "integrity_status": "STORE_INTEGRITY_ONLY",
        "semantics_status": "NOT_VERIFIED",
        "receipt_consistency": "LOCAL_RECEIPTS_CONSISTENT_UNAUTHENTICATED",
        "receipt_authentication": "NOT_PROVEN",
        "principal_binding": "TRUSTED_REGISTRY_CONTEXT",
        "actor_authentication": "NOT_PROVEN",
        "identity_authentication": "NOT_PROVEN",
        "runtime_binding": "NOT_PROVEN",
        "source_authentication": "NOT_PROVEN",
        "coverage_authentication": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
    }


def _candidate(candidate_id: str = "a" * 32) -> dict:
    return {
        "schema": CALENDAR_APPLICATION_SCHEMA,
        "status": "CANDIDATE_VISIBLE_UNAUTHENTICATED",
        "reason": "OK",
        "directory_ref": "calendar-main",
        "candidate_id": candidate_id,
        "payload_sha256": "a" * 64,
        "mode": SIMULATION_ONLY,
        "review_status": "STRUCTURALLY_READY_UNVERIFIED",
        "review_reason": "DECLARED_SCOPES_CHECKED_SIMULATED",
        "checker_status": "READY_SIMULATED",
        "checker_reason": "",
        "declared_ranges": [
            {
                "venue": venue,
                "start": "2026-11-02",
                "end": "2026-11-06",
                "declared_scope_count": 2,
            }
            for venue in ("SHFE", "INE", "DCE", "CZCE", "CFFEX", "GFEX")
        ],
        "declared_scope_count": 12,
        "source_text_status": "DECLARED_UNAUTHENTICATED",
        "source_binding_counts": {
            "MATCH_UNAUTHENTICATED": 1,
            "MISMATCH_OR_LOSSY_ENCODING": 0,
            "UNBOUND": 0,
            "TEXT_MISSING_OR_INVALID": 0,
        },
        "unresolved_status": "NONE_DECLARED",
        "snapshot_sha256": "b" * 64,
        "manifest_sha256": "c" * 64,
        "binding_sha256": "d" * 64,
        "principal_binding": "TRUSTED_REGISTRY_CONTEXT",
        "actor_authentication": "NOT_PROVEN",
        "source_authentication": "NOT_PROVEN",
        "coverage_authentication": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
    }


def test_client_binds_current_directory_and_projects_exact_service_response():
    calls = []

    def dispatch(operation, arguments, *, timeout_seconds):
        calls.append((operation, dict(arguments), timeout_seconds))
        return _current("calendar-main")

    client = TradingCalendarApplicationClient(
        dispatch,
        directory_ref="calendar-main",
        timeout_seconds=0.75,
    )

    result = client.read_current()

    assert calls == [
        (CALENDAR_CURRENT_READ_OPERATION, {"directory_ref": "calendar-main"}, 0.75)
    ]
    assert result.status == "CURRENT_VISIBLE_UNAUTHENTICATED"
    assert result.directory_ref == "calendar-main"
    assert result.sequence == 7
    assert result.content_sha256 == "a" * 64
    assert result.mode == "SIMULATION_ONLY"
    assert result.actor_authentication == "NOT_PROVEN"
    assert result.source_authentication == "NOT_PROVEN"
    assert result.publication == "NOT_AUTHORIZED"


def test_client_allows_only_shortened_finite_per_call_budgets():
    calls = []

    def dispatch(operation, arguments, *, timeout_seconds):
        calls.append((operation, timeout_seconds))
        if operation == CALENDAR_CANDIDATE_READ_OPERATION:
            return _candidate(arguments["candidate_id"])
        return _current()

    client = TradingCalendarApplicationClient(
        dispatch,
        directory_ref="calendar-main",
        timeout_seconds=0.75,
    )
    assert client.max_timeout_seconds == 0.75
    assert client.read_current(timeout_seconds=0.2).status == "CURRENT_VISIBLE_UNAUTHENTICATED"
    assert calls == [(CALENDAR_CURRENT_READ_OPERATION, 0.2)]

    for invalid in (0.009, 0.76, True, float("nan"), float("inf"), 10**1000):
        result = client.read_current(timeout_seconds=invalid)
        assert result.status == "UNKNOWN"
        assert result.reason == "INVALID_REQUEST"
    assert calls == [(CALENDAR_CURRENT_READ_OPERATION, 0.2)]

    candidate_id = "a" * 32
    result = client.read_candidate(candidate_id, timeout_seconds=0.3)
    assert result.status == "CANDIDATE_VISIBLE_UNAUTHENTICATED"
    assert calls[-1] == (CALENDAR_CANDIDATE_READ_OPERATION, 0.3)
    result = client.read_candidate(candidate_id, timeout_seconds=0.8)
    assert result.status == "UNKNOWN"
    assert result.reason == "INVALID_REQUEST"
    assert len(calls) == 2


def test_client_unwraps_standard_registry_reply_and_returns_typed_candidate():
    calls = []
    candidate_id = "e" * 32

    def dispatch(operation, arguments, *, timeout_seconds):
        calls.append((operation, dict(arguments), timeout_seconds))
        return {"ok": True, "code": "OK", "result": _candidate(candidate_id)}

    client = TradingCalendarApplicationClient(dispatch, directory_ref="calendar-main")
    result = client.read_candidate(candidate_id)

    assert calls == [
        (
            CALENDAR_CANDIDATE_READ_OPERATION,
            {"directory_ref": "calendar-main", "candidate_id": candidate_id},
            1.5,
        )
    ]
    assert result.status == "CANDIDATE_VISIBLE_UNAUTHENTICATED"
    assert result.candidate_id == candidate_id
    assert result.mode == SIMULATION_ONLY
    assert len(result.declared_ranges) == 6
    assert result.declared_ranges[0].venue == "CFFEX"
    assert result.declared_ranges[0].start == date(2026, 11, 2)
    assert result.declared_scope_count == 12
    assert result.source_binding_counts == (
        ("MATCH_UNAUTHENTICATED", 1),
        ("MISMATCH_OR_LOSSY_ENCODING", 0),
        ("TEXT_MISSING_OR_INVALID", 0),
        ("UNBOUND", 0),
    )
    assert result.actor_authentication == "NOT_PROVEN"
    assert result.coverage_authentication == "NOT_PROVEN"
    assert result.publication == "NOT_AUTHORIZED"


def test_client_preserves_finite_busy_status_and_does_not_accept_authority_labels():
    busy = {
        "schema": CALENDAR_APPLICATION_SCHEMA,
        "status": "UNKNOWN",
        "reason": "BUSY",
        "mode": SIMULATION_ONLY,
        "principal_binding": "TRUSTED_REGISTRY_CONTEXT",
        "actor_authentication": "NOT_PROVEN",
        "source_authentication": "NOT_PROVEN",
        "coverage_authentication": "NOT_PROVEN",
        "publication": "NOT_AUTHORIZED",
    }
    calls = []

    def dispatch(operation, arguments, *, timeout_seconds):
        calls.append(operation)
        return busy

    client = TradingCalendarApplicationClient(dispatch, directory_ref="calendar-main")
    result = client.read_current()
    assert result.status == "UNKNOWN"
    assert result.reason == "BUSY"

    busy["actor_authentication"] = "AUTHENTICATED"
    invalid = client.read_current()
    assert invalid.status == "UNKNOWN"
    assert invalid.reason == "INVALID_RESPONSE"
    assert calls == [CALENDAR_CURRENT_READ_OPERATION, CALENDAR_CURRENT_READ_OPERATION]


def test_client_fails_closed_for_exceptions_extra_fields_and_invalid_candidate_ids():
    calls = []

    def exploding(*_args, **_kwargs):
        raise RuntimeError("private transport detail")

    client = TradingCalendarApplicationClient(exploding, directory_ref="calendar-main")
    current = client.read_current()
    assert current.status == "UNKNOWN"
    assert current.reason == "CLIENT_UNAVAILABLE"
    assert "private transport detail" not in repr(current)

    def expanded(operation, arguments, *, timeout_seconds):
        calls.append((operation, dict(arguments)))
        value = _candidate()
        value["source_text"] = "must not escape"
        return value

    expanded_client = TradingCalendarApplicationClient(expanded, directory_ref="calendar-main")
    invalid_id = expanded_client.read_candidate("not-an-id")
    assert invalid_id.status == "UNKNOWN" and invalid_id.reason == "INVALID_REQUEST"
    assert calls == []

    invalid_shape = expanded_client.read_candidate("a" * 32)
    assert invalid_shape.status == "UNKNOWN" and invalid_shape.reason == "INVALID_RESPONSE"
    assert "must not escape" not in repr(invalid_shape)
    assert calls == [
        (CALENDAR_CANDIDATE_READ_OPERATION, {"directory_ref": "calendar-main", "candidate_id": "a" * 32})
    ]


def test_client_rejects_unbounded_configuration():
    def dispatch(_operation, _arguments, *, timeout_seconds):
        return _current()

    for directory_ref, timeout in (
        ("..", 1.0),
        ("calendar-main", 2.0),
        ("calendar-main", True),
        ("calendar-main", 10**1000),
    ):
        try:
            TradingCalendarApplicationClient(
                dispatch,
                directory_ref=directory_ref,
                timeout_seconds=timeout,
            )
        except ValueError as exc:
            assert str(exc) == "INVALID_CALENDAR_APPLICATION_CLIENT"
        else:
            raise AssertionError("invalid client configuration was accepted")
