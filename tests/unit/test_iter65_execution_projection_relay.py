"""Contract tests for the Execution projection -> Monitor relay (65.09).

Everything here is a fake projection, a fake sink and a temporary SQLite
checkpoint file.  No journal, runtime, account or network is touched.
"""

from __future__ import annotations

import hashlib
import socket
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from bt_api_py import service_execution_projection_relay as relay_module
from bt_api_py.service_execution_projection_relay import (
    ExecutionProjectionCheckpoint,
    ServiceExecutionProjectionRelay,
    ServiceExecutionProjectionRelayError,
)
from bt_api_py.service_monitor import ServiceMonitorOutboxSinkError

_SCOPE = "sdk-execution-projection-relay-test-v1"
_JOURNAL = hashlib.sha256(b"journal").hexdigest()
_CHECKPOINT_NAME = "projection-relay.sqlite"
_WHITELIST = {
    "schema",
    "event_id",
    "cursor",
    "occurred_at_ns",
    "subject_kind",
    "subject_ref",
    "event_type",
    "state",
    "reason_code",
    "detail",
}
_EVENT_ID_PREFIX = "service-event:"


def _token(number: int) -> str:
    key = hashlib.sha256(f"key-{number}".encode()).hexdigest()
    version = hashlib.sha256(f"version-{number}".encode()).hexdigest()
    return f"pev1:{number}:0:0:{key}:{_JOURNAL}:{version}"


class _Cursor:
    def __init__(self, token: str) -> None:
        self._token = token

    def token(self) -> str:
        return self._token


def _event(
    number: int,
    *,
    subject_kind: str = "PARENT",
    event_type: str = "PARENT_ACCEPTED",
    state: str = "ACCEPTED",
    reason_code: str | None = None,
    detail: object = (),
    schema: str = "execution.parent_event_projection.v1",
) -> SimpleNamespace:
    return SimpleNamespace(
        schema=schema,
        event_id="pev-id:" + hashlib.sha256(f"event-{number}".encode()).hexdigest(),
        cursor=_Cursor(_token(number)),
        occurred_at_ns=1_700_000_000_000_000_000 + number,
        subject_kind=subject_kind,
        subject_ref=f"parent-{number}",
        event_type=event_type,
        state=state,
        reason_code=reason_code,
        detail=detail,
    )


def _page(
    events: object,
    *,
    cursor: str,
    head: str,
    truncated: bool = False,
    gap: object = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        events=tuple(events),
        cursor=_Cursor(cursor),
        head=_Cursor(head),
        truncated=truncated,
        gap=gap,
    )


class _FakeProjectionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _event_token(event: object) -> str:
    cursor = event["cursor"] if isinstance(event, dict) else event.cursor
    return cursor if isinstance(cursor, str) else cursor.token()


class _ListProjection:
    """A bounded fake that pages a fixed event list like the real projection."""

    def __init__(
        self,
        events: list[object],
        *,
        journal_fingerprint: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = list(events)
        self.reads: list[tuple[str | None, int]] = []
        self.error = error
        if journal_fingerprint is not None:
            self.journal_fingerprint = journal_fingerprint

    def read(self, *, after: str | None = None, limit: int = 64) -> object:
        self.reads.append((after, limit))
        if self.error is not None:
            raise self.error
        start = 0
        if after is not None:
            tokens = [_event_token(event) for event in self.events]
            if after not in tokens:
                raise _FakeProjectionError("CURSOR_UNKNOWN")
            start = tokens.index(after) + 1
        selected = self.events[start : start + limit]
        truncated = start + limit < len(self.events)
        cursor = _event_token(selected[-1]) if selected else (after or _token(0))
        head = _event_token(self.events[-1]) if self.events else _token(0)
        return _page(selected, cursor=cursor, head=head, truncated=truncated)


class _ScriptedProjection:
    def __init__(self, outcomes: dict[str | None, object]) -> None:
        self.outcomes = dict(outcomes)
        self.reads: list[tuple[str | None, int]] = []

    def read(self, *, after: str | None = None, limit: int = 64) -> object:
        self.reads.append((after, limit))
        outcome = self.outcomes.get(after)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _Sink:
    def __init__(self) -> None:
        self.items: list[object] = []
        self.calls = 0
        self.fail: Exception | None = None
        self.result: object = None

    def __call__(self, item: object) -> object:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        self.items.append(item)
        return self.result


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _checkpoint_path(tmp_path: object) -> str:
    return str(tmp_path / _CHECKPOINT_NAME)


def _relay(
    projection: object,
    sink: object,
    tmp_path: object,
    *,
    clock: object = None,
    **kwargs: object,
) -> ServiceExecutionProjectionRelay:
    return ServiceExecutionProjectionRelay(
        projection,
        sink,
        checkpoint_path=_checkpoint_path(tmp_path),
        outbox_scope=_SCOPE,
        clock=clock,
        **kwargs,
    )


def _checkpoint_rows(tmp_path: object) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(_checkpoint_path(tmp_path))
    try:
        return connection.execute(
            "SELECT namespace_id, binding_json, cursor_token, delivered, last_event_id, "
            "updated_at_ns FROM projection_relay_checkpoints"
        ).fetchall()
    finally:
        connection.close()


def _set_checkpoint_cursor(tmp_path: object, cursor: str) -> None:
    connection = sqlite3.connect(_checkpoint_path(tmp_path))
    try:
        connection.execute("UPDATE projection_relay_checkpoints SET cursor_token = ?", (cursor,))
        connection.commit()
    finally:
        connection.close()


def _delete_checkpoint_rows(tmp_path: object) -> None:
    connection = sqlite3.connect(_checkpoint_path(tmp_path))
    try:
        connection.execute("DELETE FROM projection_relay_checkpoints")
        connection.commit()
    finally:
        connection.close()


def test_forwarded_data_is_whitelisted(tmp_path: object) -> None:
    first = _event(1, detail=(("missed_windows", 2),))
    first.account_ref = "secret-account"
    first.price = "123.45"
    first.raw_payload = {"raw": "payload"}
    second = {
        "schema": "execution.parent_event_projection.v1",
        "event_id": "pev-id:" + "a" * 64,
        "cursor": _token(2),
        "occurred_at_ns": 1_700_000_000_000_000_002,
        "subject_kind": "CHILD",
        "subject_ref": "child-2",
        "event_type": "CHILD_SENT",
        "state": "SENT",
        "reason_code": None,
        "detail": [["filled", "1.50"]],
        "account_ref": "secret-account",
        "price": "999",
        "raw_payload": {"raw": "payload"},
    }
    projection = _ListProjection([first, second])
    sink = _Sink()
    relay = _relay(projection, sink, tmp_path)

    result = relay.poll()

    assert result.delivered == 2
    assert result.checkpointed == 2
    first_data = dict(sink.items[0].event.data)
    assert set(first_data) == _WHITELIST
    assert first_data["schema"] == "execution.parent_event_projection.v1"
    assert first_data["subject_ref"] == "parent-1"
    assert dict(first_data["detail"]) == {"missed_windows": 2}
    assert "secret-account" not in repr(first_data)
    assert "payload" not in repr(first_data)
    second_data = dict(sink.items[1].event.data)
    assert set(second_data) == _WHITELIST
    assert second_data["cursor"] == _token(2)
    assert dict(second_data["detail"]) == {"filled": "1.50"}
    assert sink.items[0].event.scope == _SCOPE
    assert sink.items[0].event.event_type == "execution.parent_child_projection"
    assert sink.items[0].sequence == 1
    assert sink.items[1].sequence == 2
    event_id = sink.items[0].event.event_id
    assert event_id.startswith(_EVENT_ID_PREFIX)
    assert len(event_id) == len(_EVENT_ID_PREFIX) + 64


def test_checkpoint_restart_resumes_at_exact_token(tmp_path: object) -> None:
    events = [_event(1), _event(2), _event(3)]
    projection = _ListProjection(events)
    sink = _Sink()
    relay = _relay(projection, sink, tmp_path, max_events_per_poll=1)

    assert relay.poll().delivered == 1
    assert relay.checkpoint() == ExecutionProjectionCheckpoint(
        cursor=_token(1),
        delivered=1,
        last_event_id=events[0].event_id,
    )
    relay.close()

    resumed_projection = _ListProjection(events)
    resumed_sink = _Sink()
    resumed = _relay(resumed_projection, resumed_sink, tmp_path)

    result = resumed.poll()

    assert resumed_projection.reads[0] == (_token(1), 64)
    assert result.delivered == 2
    assert [item.event.data["event_id"] for item in resumed_sink.items] == [
        events[1].event_id,
        events[2].event_id,
    ]
    assert [item.sequence for item in resumed_sink.items] == [2, 3]
    assert resumed.checkpoint().cursor == _token(3)


def test_crash_after_sink_acceptance_reuses_event_identity(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [_event(1), _event(2)]
    projection = _ListProjection(events)
    sink = _Sink()
    clock = _Clock()
    relay = _relay(projection, sink, tmp_path, clock=clock, max_events_per_poll=1)
    original = relay_module._advance_checkpoint

    def fail_once(*args: object, **kwargs: object) -> str:
        raise relay_module._CheckpointFailureError("LOCAL_CHECKPOINT_FAILED")

    monkeypatch.setattr(relay_module, "_advance_checkpoint", fail_once)
    first = relay.poll()

    assert first.delivered == 1
    assert first.checkpointed == 0
    assert first.errors == 1
    assert first.last_error_code == "LOCAL_CHECKPOINT_FAILED"
    assert relay.checkpoint().cursor == ""

    monkeypatch.setattr(relay_module, "_advance_checkpoint", original)
    clock.advance(first.backoff_seconds + 0.001)
    second = relay.poll()

    assert second.delivered == 1
    identities = [item.event.event_id for item in sink.items]
    assert len(identities) == 2
    assert identities[0] == identities[1]
    assert relay.checkpoint().cursor == _token(1)


def test_event_identity_ignores_the_projection_cursor_version(tmp_path: object) -> None:
    first = _event(1)
    sink = _Sink()
    relay = _relay(_ListProjection([first]), sink, tmp_path)
    assert relay.poll().delivered == 1
    relay.close()
    # A lost checkpoint is replayed from an unchanged event identity even when
    # the projection token's data version changed.
    _delete_checkpoint_rows(tmp_path)
    replayed = _event(1)
    replayed.cursor = _Cursor(_token(99))
    replayed_sink = _Sink()
    resumed = _relay(_ListProjection([replayed]), replayed_sink, tmp_path)

    assert resumed.poll().delivered == 1
    assert sink.items[0].event.event_id == replayed_sink.items[0].event.event_id
    assert resumed.checkpoint().cursor == _token(99)


def test_tampered_cursor_is_refused_without_reading(tmp_path: object) -> None:
    projection = _ListProjection([_event(1), _event(2)])
    relay = _relay(projection, _Sink(), tmp_path, max_events_per_poll=1)
    assert relay.poll().delivered == 1
    reads = len(projection.reads)
    _set_checkpoint_cursor(tmp_path, "tampered cursor with spaces")

    result = relay.poll()

    assert result.delivered == 0
    assert result.refused == 1
    assert result.last_error_code == "CHECKPOINT_CORRUPT"
    assert len(projection.reads) == reads
    assert _checkpoint_rows(tmp_path)[0][2] == "tampered cursor with spaces"


@pytest.mark.parametrize(
    "code",
    ["CURSOR_JOURNAL_MISMATCH", "CURSOR_AHEAD", "CURSOR_UNKNOWN", "INVALID_CURSOR"],
)
def test_foreign_or_expired_cursor_is_refused_and_never_advanced(
    tmp_path: object, code: str
) -> None:
    projection = _ListProjection([_event(1), _event(2)])
    relay = _relay(projection, _Sink(), tmp_path, max_events_per_poll=1)
    assert relay.poll().delivered == 1
    projection.error = _FakeProjectionError(code)

    result = relay.poll()

    assert result.delivered == 0
    assert result.refused == 1
    assert result.last_error_code == code
    assert projection.reads[-1][0] == _token(1)
    assert relay.checkpoint().cursor == _token(1)


def test_a_valid_shaped_foreign_token_in_the_checkpoint_is_refused(
    tmp_path: object,
) -> None:
    projection = _ListProjection([_event(1)])
    relay = _relay(projection, _Sink(), tmp_path)
    assert relay.poll().delivered == 1
    _set_checkpoint_cursor(tmp_path, _token(77))

    result = relay.poll()

    assert result.delivered == 0
    assert result.refused == 1
    assert result.last_error_code == "CURSOR_UNKNOWN"
    assert relay.checkpoint().cursor == _token(77)


def test_source_fingerprint_change_is_refused_in_session(tmp_path: object) -> None:
    events = [_event(1)]
    projection = _ListProjection(events, journal_fingerprint="f" * 64)
    relay = _relay(projection, _Sink(), tmp_path)
    assert relay.poll().delivered == 1
    projection.journal_fingerprint = "e" * 64

    result = relay.poll()

    assert result.delivered == 0
    assert result.refused == 1
    assert result.last_error_code == "SOURCE_BINDING_CHANGED"
    assert len(projection.reads) == 1


def test_restart_with_a_changed_source_binding_is_refused(tmp_path: object) -> None:
    events = [_event(1)]
    first = _relay(_ListProjection(events, journal_fingerprint="f" * 64), _Sink(), tmp_path)
    assert first.poll().delivered == 1
    first.close()

    projection = _ListProjection(events, journal_fingerprint="e" * 64)
    resumed = _relay(projection, _Sink(), tmp_path)

    result = resumed.poll()

    assert result.delivered == 0
    assert result.refused == 1
    assert result.last_error_code == "CHECKPOINT_SOURCE_CHANGED"
    assert projection.reads == []


def test_gap_is_reported_and_head_is_not_advanced(tmp_path: object) -> None:
    first = _event(1)
    gap = SimpleNamespace(
        code="SOURCE_ROW_UNREADABLE",
        journal="parent",
        subject_kind="CHILD",
        subject_ref="",
    )
    projection = _ScriptedProjection(
        {
            None: _page([first], cursor=_token(1), head=_token(9), gap=gap),
            _token(1): _page([], cursor=_token(1), head=_token(9), gap=gap),
        }
    )
    sink = _Sink()
    relay = _relay(projection, sink, tmp_path)

    first_poll = relay.poll()

    assert first_poll.delivered == 1
    assert first_poll.gaps == 1
    assert first_poll.last_gap_code == "SOURCE_ROW_UNREADABLE"
    assert relay.checkpoint().cursor == _token(1)

    second_poll = relay.poll()

    assert second_poll.delivered == 0
    assert second_poll.gaps == 1
    assert relay.checkpoint().cursor == _token(1)
    assert len(sink.items) == 1


def test_gap_without_events_reports_without_advancing(tmp_path: object) -> None:
    gap = SimpleNamespace(code="CHILD_TERMINAL_UNBACKED")
    projection = _ScriptedProjection({None: _page([], cursor=_token(0), head=_token(0), gap=gap)})
    sink = _Sink()
    relay = _relay(projection, sink, tmp_path)

    result = relay.poll()

    assert result.delivered == 0
    assert result.gaps == 1
    assert result.last_gap_code == "CHILD_TERMINAL_UNBACKED"
    assert relay.checkpoint().cursor == ""
    assert sink.calls == 0


@pytest.mark.parametrize(
    ("failure", "field"),
    [
        (RuntimeError("boom"), "errors"),
        (ServiceMonitorOutboxSinkError("MONITOR_OUTBOX_UNAVAILABLE"), "unavailable"),
        (ServiceMonitorOutboxSinkError("OUTBOX_SCOPE_MISMATCH"), "rejected"),
        (ServiceMonitorOutboxSinkError("INGRESS_BACKPRESSURE"), "backpressure"),
        (BlockingIOError("busy"), "backpressure"),
    ],
)
def test_sink_failures_are_counted_and_backed_off(
    tmp_path: object, failure: Exception, field: str
) -> None:
    clock = _Clock()
    projection = _ListProjection([_event(1)])
    sink = _Sink()
    sink.fail = failure
    relay = _relay(projection, sink, tmp_path, clock=clock)

    result = relay.poll()

    assert getattr(result, field) >= 1
    assert result.delivered == 0
    assert result.backoff is True
    assert result.backoff_seconds > 0
    assert relay.checkpoint().cursor == ""

    calls = sink.calls
    skipped = relay.poll()

    assert skipped.backoff is True
    assert skipped.delivered == 0
    assert sink.calls == calls

    clock.advance(result.backoff_seconds + 0.001)
    sink.fail = None
    recovered = relay.poll()

    assert recovered.delivered == 1
    assert relay.checkpoint().cursor == _token(1)


def test_explicit_sink_rejection_does_not_advance(tmp_path: object) -> None:
    sink = _Sink()
    sink.result = False
    relay = _relay(_ListProjection([_event(1)]), sink, tmp_path)

    result = relay.poll()

    assert result.delivered == 0
    assert result.rejected == 1
    assert result.last_error_code == "SINK_REJECTED"
    assert relay.checkpoint().cursor == ""


@pytest.mark.parametrize(
    ("code", "field"),
    [
        ("PROJECTION_UNAVAILABLE", "unavailable"),
        ("PROJECTION_CLOSED", "unavailable"),
        ("SOURCE_CAPACITY", "errors"),
        ("SOURCE_READ_FAILED", "errors"),
    ],
)
def test_projection_failures_are_counted_without_raising(
    tmp_path: object, code: str, field: str
) -> None:
    projection = _ListProjection([_event(1)], error=_FakeProjectionError(code))
    relay = _relay(projection, _Sink(), tmp_path)

    result = relay.poll()

    assert getattr(result, field) >= 1
    assert result.delivered == 0
    assert result.last_error_code == code
    assert relay.checkpoint().cursor == ""


@pytest.mark.parametrize(
    "mutate",
    [
        lambda event: setattr(event, "subject_kind", "ACCOUNT"),
        lambda event: setattr(event, "event_type", "lowercase"),
        lambda event: setattr(event, "reason_code", ""),
        lambda event: setattr(event, "occurred_at_ns", 0),
        lambda event: setattr(event, "cursor", _Cursor("contains a space")),
        lambda event: setattr(event, "detail", (("price", "1.0"),)),
        lambda event: setattr(event, "detail", (("missed_windows", True),)),
        lambda event: setattr(event, "detail", (("missed_windows", -1),)),
    ],
)
def test_untrusted_event_is_refused_without_skipping(tmp_path: object, mutate: object) -> None:
    bad = _event(1)
    mutate(bad)
    projection = _ListProjection([bad, _event(2)])
    sink = _Sink()
    relay = _relay(projection, sink, tmp_path)

    result = relay.poll()

    assert result.invalid == 1
    assert result.delivered == 0
    assert result.last_error_code == "SOURCE_EVENT_INVALID"
    assert sink.calls == 0
    assert relay.checkpoint().cursor == ""


def test_poll_limit_and_page_limit_bound_the_work(tmp_path: object) -> None:
    events = [_event(number) for number in range(1, 6)]
    projection = _ListProjection(events)
    sink = _Sink()
    relay = _relay(projection, sink, tmp_path, page_limit=2)

    result = relay.poll(limit=2)

    assert result.delivered == 2
    assert all(limit <= 2 for _after, limit in projection.reads)
    assert relay.poll(limit=2).delivered == 2
    assert relay.poll(limit=2).delivered == 1
    assert sink.calls == 5


def test_byte_budget_stops_after_one_event(tmp_path: object) -> None:
    events = [_event(number) for number in range(1, 4)]
    projection = _ListProjection(events)
    sink = _Sink()
    relay = _relay(projection, sink, tmp_path, byte_budget=1)

    first = relay.poll()

    assert first.delivered == 1
    assert first.bytes_forwarded > 0
    assert len(projection.reads) == 1
    assert relay.poll().delivered == 1
    assert relay.poll().delivered == 1
    assert sink.calls == 3


def test_zero_limit_reads_nothing(tmp_path: object) -> None:
    projection = _ListProjection([_event(1)])
    relay = _relay(projection, _Sink(), tmp_path)

    result = relay.poll(limit=0)

    assert result.delivered == 0
    assert projection.reads == []


def test_disabled_relay_performs_no_work(tmp_path: object) -> None:
    projection = _ListProjection([_event(1)])
    relay = ServiceExecutionProjectionRelay(projection, None)

    result = relay.poll()

    assert relay.enabled is False
    assert result.disabled is True
    assert result.delivered == 0
    assert result.dropped == 0
    assert projection.reads == []
    assert relay.checkpoint() == ExecutionProjectionCheckpoint()
    assert list(tmp_path.iterdir()) == []


def test_poll_is_owner_thread_only(tmp_path: object) -> None:
    relay = _relay(_ListProjection([_event(1)]), _Sink(), tmp_path)
    captured: list[str] = []

    def wrong_thread() -> None:
        try:
            relay.poll()
        except ServiceExecutionProjectionRelayError as error:
            captured.append(error.code)

    thread = threading.Thread(target=wrong_thread)
    thread.start()
    thread.join()

    assert captured == ["WRONG_OWNER_THREAD"]
    assert relay.poll().delivered == 1


def test_poll_calls_only_the_injected_projection_and_sink(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("poll must not open a network connection")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    projection = _ListProjection([_event(1)])
    sink = _Sink()
    relay = _relay(projection, sink, tmp_path)

    result = relay.poll()

    assert result.delivered == 1
    assert projection.reads == [(None, 64)]
    assert sink.calls == 1
    assert relay_module.ServiceExecutionProjectionRelay is ServiceExecutionProjectionRelay


def test_closed_relay_refuses_poll(tmp_path: object) -> None:
    relay = _relay(_ListProjection([_event(1)]), _Sink(), tmp_path)
    relay.close()

    with pytest.raises(ServiceExecutionProjectionRelayError) as exc_info:
        relay.poll()

    assert exc_info.value.code == "RELAY_CLOSED"


def test_invalid_configuration_is_rejected(tmp_path: object) -> None:
    projection = _ListProjection([_event(1)])
    sink = _Sink()

    with pytest.raises(ServiceExecutionProjectionRelayError) as exc_info:
        ServiceExecutionProjectionRelay(projection, object())
    assert exc_info.value.code == "INVALID_SINK"

    with pytest.raises(ServiceExecutionProjectionRelayError) as exc_info:
        _relay(projection, sink, tmp_path, page_limit=0)
    assert exc_info.value.code == "INVALID_PAGE_LIMIT"

    with pytest.raises(ServiceExecutionProjectionRelayError) as exc_info:
        _relay(projection, sink, tmp_path, byte_budget=0)
    assert exc_info.value.code == "INVALID_BYTE_BUDGET"

    with pytest.raises(ServiceExecutionProjectionRelayError) as exc_info:
        ServiceExecutionProjectionRelay(
            projection,
            sink,
            checkpoint_path=_checkpoint_path(tmp_path),
            outbox_scope="",
        )
    assert exc_info.value.code == "INVALID_OUTBOX_SCOPE"

    with pytest.raises(ServiceExecutionProjectionRelayError) as exc_info:
        ServiceExecutionProjectionRelay(projection, sink, outbox_scope=_SCOPE)
    assert exc_info.value.code == "INVALID_CHECKPOINT_PATH"

    with pytest.raises(ServiceExecutionProjectionRelayError) as exc_info:
        ServiceExecutionProjectionRelay(object(), sink, outbox_scope=_SCOPE)
    assert exc_info.value.code == "INVALID_PROJECTION"


def test_checkpoint_must_be_separate_from_an_exposed_source(tmp_path: object) -> None:
    source = tmp_path / "parent-journal.sqlite"
    source.write_bytes(b"")
    projection = _ListProjection([_event(1)])
    projection.database_path = source

    with pytest.raises(ServiceExecutionProjectionRelayError) as exc_info:
        ServiceExecutionProjectionRelay(
            projection,
            _Sink(),
            checkpoint_path=source,
            outbox_scope=_SCOPE,
        )

    assert exc_info.value.code == "CHECKPOINT_MUST_BE_SEPARATE"


def test_checkpoint_never_carries_a_payload_or_credential(tmp_path: object) -> None:
    event = _event(1, reason_code="REMOTE_REJECTED", detail=(("filled", "0.00"),))
    event.account_ref = "secret-account"
    event.raw_payload = {"raw": "payload"}
    relay = _relay(_ListProjection([event]), _Sink(), tmp_path)

    assert relay.poll().delivered == 1
    rows = _checkpoint_rows(tmp_path)

    assert len(rows) == 1
    rendered = repr(rows)
    assert "secret-account" not in rendered
    assert "payload" not in rendered
    assert rows[0][2] == _token(1)
    assert rows[0][3] == 1
    assert rows[0][4] == event.event_id
