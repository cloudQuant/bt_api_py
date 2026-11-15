"""Contract tests for the iteration 68.07 single-source Monitor composition.

Everything here uses fake projections/sinks, temporary SQLite files and the
existing real ``ServiceClientRuntime``/journal fixtures from the relay tests.
No account, provider, real config or network is touched.
"""

from __future__ import annotations

import socket
import sys
import threading
from types import SimpleNamespace

import pytest

from bt_api_py.service_execution_monitor_composition import (
    SOURCE_JOURNAL,
    SOURCE_NONE,
    SOURCE_PROJECTION,
    ServiceExecutionMonitorCompositionError,
    compose_execution_monitor_relay,
    derive_monitor_outbox_scope,
)

_DOMAIN = "paper:execution"
_ACCOUNT = "acct-a"
_STRATEGY = "strategy-a"
_SCOPE_REF = "monitor-ingress-v1"
_JOURNAL_MODULE = "bt_api_py.service_execution_monitor"
_PROJECTION_MODULE = "bt_api_py.service_execution_projection_relay"
_UNSET = object()


def _refs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "execution_domain_ref": _DOMAIN,
        "account_ref": _ACCOUNT,
        "strategy_ref": _STRATEGY,
    }
    values.update(overrides)
    return values


def _projection_helpers() -> tuple[object, object, object]:
    from tests.unit.test_iter65_execution_projection_relay import (
        _event,
        _ListProjection,
        _Sink,
    )

    return _ListProjection, _Sink, _event


def _compose_projection(
    tmp_path: object,
    *,
    events: object = None,
    sink: object = _UNSET,
    **overrides: object,
) -> object:
    list_projection, sink_type, event = _projection_helpers()
    values: dict[str, object] = {
        "source": SOURCE_PROJECTION,
        "checkpoint_path": tmp_path / "monitor-relay.sqlite",
        "projection": list_projection([event(1)] if events is None else events),
        "sink": sink_type() if sink is _UNSET else sink,
    }
    values.update(_refs())
    values.update(overrides)
    return compose_execution_monitor_relay(**values)


def _journal_fixture(tmp_path: object, *, orders: int = 2, cancellations: int = 1):
    from bt_api_execution.journal_reader import ExecutionJournalReader

    from tests.unit.test_service_execution_monitor import (
        _build_journal,
        _RemoteOutbox,
        _runtime,
    )

    database = tmp_path / "source.sqlite"
    scope = _build_journal(database, orders=orders, cancellations=cancellations)
    reader = ExecutionJournalReader(database, scope=scope)
    remote = _RemoteOutbox()
    runtime, transports = _runtime(remote)
    return {
        "database": database,
        "scope": scope,
        "reader": reader,
        "remote": remote,
        "runtime": runtime,
        "transports": transports,
    }


def _compose_journal(tmp_path: object, fixture: dict, **overrides: object) -> object:
    values: dict[str, object] = {
        "source": SOURCE_JOURNAL,
        "checkpoint_path": tmp_path / "journal-relay.sqlite",
        "journal_reader": fixture["reader"],
        "runtime": fixture["runtime"],
        "scope_ref": _SCOPE_REF,
    }
    values.update(_refs())
    values.update(overrides)
    return compose_execution_monitor_relay(**values)


@pytest.mark.unit
def test_projection_selection_builds_only_the_projection_relay(tmp_path: object) -> None:
    from bt_api_py.service_execution_projection_relay import (
        ServiceExecutionProjectionRelay,
    )

    composed = _compose_projection(tmp_path)

    assert composed.source == SOURCE_PROJECTION
    assert composed.enabled is True
    assert type(composed.relay) is ServiceExecutionProjectionRelay
    assert composed.outbox_scope == composed.scope_key
    composed.close()


@pytest.mark.unit
def test_journal_selection_builds_only_the_journal_relay(tmp_path: object) -> None:
    from bt_api_py.service_execution_monitor import ServiceExecutionMonitorRelay

    fixture = _journal_fixture(tmp_path)
    composed = _compose_journal(tmp_path, fixture)
    try:
        assert composed.source == SOURCE_JOURNAL
        assert composed.enabled is True
        assert type(composed.relay) is ServiceExecutionMonitorRelay
        assert composed.outbox_scope is None
        composed.start()
        result = composed.poll(limit=8)
        assert result.remote_delivered > 0
        assert fixture["remote"].append_attempts
    finally:
        composed.stop()
        fixture["reader"].close()
        fixture["runtime"].close()


@pytest.mark.unit
def test_none_selection_builds_no_relay_and_does_no_work(tmp_path: object) -> None:
    composed = compose_execution_monitor_relay(
        source=SOURCE_NONE,
        **_refs(),
    )

    assert composed.source == SOURCE_NONE
    assert composed.enabled is False
    assert composed.relay is None
    assert composed.scope_key is None
    assert composed.outbox_scope is None
    assert composed.checkpoint_path is None

    composed.start()
    assert composed.poll() is None
    assert composed.checkpoints() == ()
    composed.stop()
    composed.stop()
    assert composed.state == "closed"


@pytest.mark.unit
@pytest.mark.parametrize("source", [SOURCE_PROJECTION, SOURCE_JOURNAL])
def test_both_sources_are_refused_as_ambiguous(tmp_path: object, source: str) -> None:
    with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
        compose_execution_monitor_relay(
            source=source,
            journal_reader=object(),
            projection=object(),
            checkpoint_path=tmp_path / "relay.sqlite",
            scope_ref=_SCOPE_REF,
            **_refs(),
        )

    assert error.value.code == "AMBIGUOUS_SOURCE_SELECTION"


@pytest.mark.unit
@pytest.mark.parametrize("source", ["", "both", "Journal", "gateway", None])
def test_unknown_selection_is_refused(source: object) -> None:
    with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
        compose_execution_monitor_relay(source=source, **_refs())

    assert error.value.code == "UNKNOWN_SOURCE_SELECTION"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("source", "handles", "code"),
    [
        (SOURCE_PROJECTION, {"journal_reader": object()}, "UNEXPECTED_SOURCE_HANDLE"),
        (SOURCE_JOURNAL, {"projection": object()}, "UNEXPECTED_SOURCE_HANDLE"),
        (SOURCE_NONE, {"projection": object()}, "UNEXPECTED_SOURCE_HANDLE"),
        (SOURCE_PROJECTION, {}, "SOURCE_HANDLE_REQUIRED"),
        (SOURCE_JOURNAL, {}, "SOURCE_HANDLE_REQUIRED"),
    ],
)
def test_mismatched_or_missing_handles_are_refused(
    tmp_path: object, source: str, handles: dict, code: str
) -> None:
    with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
        compose_execution_monitor_relay(
            source=source,
            checkpoint_path=tmp_path / "relay.sqlite",
            scope_ref=_SCOPE_REF,
            **handles,
            **_refs(),
        )

    assert error.value.code == code


@pytest.mark.unit
def test_scope_is_derived_from_trusted_references_only() -> None:
    base = derive_monitor_outbox_scope(source=SOURCE_PROJECTION, **_refs())

    assert base == derive_monitor_outbox_scope(source=SOURCE_PROJECTION, **_refs())
    assert base.startswith("sdk-execution-monitor-composition-v1:")
    for overrides in (
        {"account_ref": "acct-b"},
        {"strategy_ref": "strategy-b"},
        {"execution_domain_ref": "live:execution"},
    ):
        assert derive_monitor_outbox_scope(source=SOURCE_PROJECTION, **_refs(**overrides)) != base
    assert derive_monitor_outbox_scope(source=SOURCE_JOURNAL, **_refs()) != base

    with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
        derive_monitor_outbox_scope(source=SOURCE_NONE, **_refs())
    assert error.value.code == "UNKNOWN_SOURCE_SELECTION"
    with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
        derive_monitor_outbox_scope(source=SOURCE_PROJECTION, **_refs(account_ref=""))
    assert error.value.code == "INVALID_TRUSTED_SCOPE"


@pytest.mark.unit
def test_projection_outbox_scope_is_applied_and_payload_cannot_change_it(
    tmp_path: object,
) -> None:
    _list_projection, sink_type, event = _projection_helpers()
    poisoned = event(1)
    poisoned.account_ref = "payload-account"
    poisoned.price = "123.45"
    poisoned.raw_payload = {"account_ref": "payload-account", "secret": "payload-secret"}
    sink = sink_type()
    composed = _compose_projection(tmp_path, events=[poisoned], sink=sink)
    expected = derive_monitor_outbox_scope(source=SOURCE_PROJECTION, **_refs())
    try:
        assert composed.outbox_scope == expected
        composed.start()
        result = composed.poll()
        assert result.delivered == 1
        delivered = sink.items[0]
        assert delivered.event.scope == expected
        data = dict(delivered.event.data)
        assert "account_ref" not in data
        assert "price" not in data
        assert "raw_payload" not in data
        assert "payload-secret" not in repr(data)
    finally:
        composed.stop()


@pytest.mark.unit
def test_journal_scope_mismatch_is_refused_before_relay_construction(
    tmp_path: object,
) -> None:
    fixture = _journal_fixture(tmp_path, orders=0, cancellations=0)
    try:
        with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
            _compose_journal(tmp_path, fixture, account_ref="other-account")
        assert error.value.code == "SCOPE_MISMATCH"

        with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
            _compose_journal(
                tmp_path,
                fixture,
                journal_reader=SimpleNamespace(),
            )
        assert error.value.code == "INVALID_SOURCE_SCOPE"
    finally:
        fixture["reader"].close()
        fixture["runtime"].close()


@pytest.mark.unit
def test_checkpoint_dir_derives_a_stable_source_scoped_path(tmp_path: object) -> None:
    cursor_dir = tmp_path / "cursors"
    first = _compose_projection(tmp_path, checkpoint_path=None, checkpoint_dir=cursor_dir)
    second = _compose_projection(tmp_path, checkpoint_path=None, checkpoint_dir=cursor_dir)
    other = _compose_projection(
        tmp_path,
        checkpoint_path=None,
        checkpoint_dir=cursor_dir,
        account_ref="acct-b",
    )
    try:
        assert first.checkpoint_path == second.checkpoint_path
        assert first.checkpoint_path.parent == cursor_dir.resolve()
        assert first.checkpoint_path.name.startswith("execution-monitor-projection-")
        assert first.checkpoint_path.suffix == ".sqlite3"
        assert other.checkpoint_path != first.checkpoint_path
    finally:
        first.stop()
        second.stop()
        other.stop()


@pytest.mark.unit
def test_checkpoint_path_rules_are_finite(tmp_path: object) -> None:
    with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
        _compose_projection(
            tmp_path,
            checkpoint_path=tmp_path / "a.sqlite",
            checkpoint_dir=tmp_path,
        )
    assert error.value.code == "CHECKPOINT_PATH_AMBIGUOUS"

    with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
        _compose_projection(tmp_path, checkpoint_path=None)
    assert error.value.code == "CHECKPOINT_PATH_REQUIRED"


@pytest.mark.unit
def test_construction_starts_no_thread_and_opens_no_network(
    tmp_path: object, monkeypatch: object
) -> None:
    _projection_helpers()
    before = set(threading.enumerate())

    def forbid_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("network is not allowed in monitor composition")

    monkeypatch.setattr(socket, "socket", forbid_network)
    monkeypatch.setattr(socket, "create_connection", forbid_network)

    composed = _compose_projection(tmp_path)
    try:
        assert set(threading.enumerate()) == before
        composed.start()
        assert set(threading.enumerate()) == before
        composed.poll()
        assert set(threading.enumerate()) == before
    finally:
        composed.stop()
    assert set(threading.enumerate()) == before


@pytest.mark.unit
def test_journal_construction_starts_no_thread_and_opens_no_network(
    tmp_path: object, monkeypatch: object
) -> None:
    fixture = _journal_fixture(tmp_path)
    before = set(threading.enumerate())

    def forbid_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("network is not allowed in monitor composition")

    monkeypatch.setattr(socket, "socket", forbid_network)
    monkeypatch.setattr(socket, "create_connection", forbid_network)
    try:
        composed = _compose_journal(tmp_path, fixture)
        assert composed.enabled is True
        assert set(threading.enumerate()) == before
        composed.start()
        assert set(threading.enumerate()) == before
        composed.stop()
    finally:
        fixture["reader"].close()
        fixture["runtime"].close()


@pytest.mark.unit
def test_lifecycle_is_owner_thread_only(tmp_path: object) -> None:
    composed = _compose_projection(tmp_path)
    outcomes: list[str] = []

    def worker() -> None:
        for action in (
            composed.start,
            composed.maintain,
            composed.poll,
            composed.checkpoints,
            composed.stop,
        ):
            try:
                action()
            except ServiceExecutionMonitorCompositionError as error:
                outcomes.append(error.code)
            else:
                outcomes.append("OK")

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert outcomes == ["WRONG_OWNER_THREAD"] * 5
    assert composed.state == "new"
    composed.start()
    composed.stop()


@pytest.mark.unit
def test_stop_is_idempotent_and_never_closes_borrowed_objects(tmp_path: object) -> None:
    fixture = _journal_fixture(tmp_path)
    composed = _compose_journal(tmp_path, fixture)
    composed.start()
    composed.poll(limit=1)
    try:
        composed.stop()
        composed.stop()
        composed.close()

        assert composed.state == "closed"
        assert fixture["runtime"].state == "registered"
        assert fixture["reader"].scope.account_ref == _ACCOUNT
        with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
            composed.poll()
        assert error.value.code == "COMPOSITION_CLOSED"
    finally:
        fixture["reader"].close()
        fixture["runtime"].close()


@pytest.mark.unit
def test_new_factory_resumes_projection_from_the_same_checkpoint(tmp_path: object) -> None:
    _list_projection, sink_type, event = _projection_helpers()
    checkpoint = tmp_path / "projection-relay.sqlite"
    first_sink = sink_type()
    first = _compose_projection(
        tmp_path,
        events=[event(1), event(2), event(3)],
        sink=first_sink,
        checkpoint_path=checkpoint,
    )
    first.start()
    result = first.poll(limit=2)
    assert result.delivered == 2
    position = first.checkpoints()[0]
    assert position.source == SOURCE_PROJECTION
    assert position.stream is None
    assert position.position == 2
    assert position.last_event_id
    assert position.cursor
    first.stop()
    first.stop()

    second_sink = sink_type()
    second = _compose_projection(
        tmp_path,
        events=[event(1), event(2), event(3)],
        sink=second_sink,
        checkpoint_path=checkpoint,
    )
    assert second.checkpoints()[0].position == 2
    second.start()
    resumed = second.poll()
    assert resumed.delivered == 1
    assert len(second_sink.items) == 1
    assert second_sink.items[0].event.data["event_id"] == event(3).event_id
    second.stop()


@pytest.mark.unit
def test_new_factory_resumes_journal_from_the_same_checkpoint(tmp_path: object) -> None:
    from bt_api_execution.journal_reader import ExecutionJournalReader

    fixture = _journal_fixture(tmp_path)
    checkpoint = tmp_path / "journal-relay.sqlite"
    first = _compose_journal(tmp_path, fixture, checkpoint_path=checkpoint)
    first.start()
    result = first.poll(limit=128)
    assert result.remote_delivered > 0
    prior = {item.stream: item.position for item in first.checkpoints()}
    assert prior["execution"] > 0
    first.stop()
    fixture["reader"].close()

    reader = ExecutionJournalReader(fixture["database"], scope=fixture["scope"])
    second = compose_execution_monitor_relay(
        source=SOURCE_JOURNAL,
        checkpoint_path=checkpoint,
        journal_reader=reader,
        runtime=fixture["runtime"],
        scope_ref=_SCOPE_REF,
        **_refs(),
    )
    try:
        assert {item.stream: item.position for item in second.checkpoints()} == prior
        second.start()
        resumed = second.poll(limit=8)
        assert resumed.remote_delivered == 0
        assert resumed.checkpointed == 0
    finally:
        second.stop()
        reader.close()
        fixture["runtime"].close()


@pytest.mark.unit
def test_projection_sink_failures_are_counted_and_never_raise(tmp_path: object) -> None:
    from bt_api_py.service_monitor import ServiceMonitorOutboxSinkError

    _list_projection, sink_type, _event = _projection_helpers()
    sink = sink_type()
    sink.fail = ServiceMonitorOutboxSinkError("INGRESS_BINDING_UNAVAILABLE")
    composed = _compose_projection(tmp_path, sink=sink)
    composed.start()
    try:
        result = composed.poll()
        assert result.delivered == 0
        assert result.unavailable == 1
        assert result.last_error_code == "INGRESS_BINDING_UNAVAILABLE"
        assert composed.checkpoints()[0].position == 0

        backoff = composed.poll()
        assert backoff.backoff is True
        assert backoff.delivered == 0
    finally:
        composed.stop()


@pytest.mark.unit
def test_failed_start_closes_the_composition_and_refuses_reuse(
    tmp_path: object, monkeypatch: object
) -> None:
    composed = _compose_projection(tmp_path)
    close_calls: list[str] = []
    original_close = composed.relay.close

    def recording_close() -> None:
        close_calls.append("close")
        original_close()

    monkeypatch.setattr(composed.relay, "close", recording_close)
    composed.checkpoint_path.write_bytes(b"this is not a sqlite checkpoint")

    with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
        composed.start()
    assert error.value.code in {
        "CHECKPOINT_UNAVAILABLE",
        "CHECKPOINT_CORRUPT",
        "CHECKPOINT_READ_FAILED",
    }
    assert close_calls == ["close"]
    assert composed.state == "closed"

    with pytest.raises(ServiceExecutionMonitorCompositionError) as closed:
        composed.poll()
    assert closed.value.code == "COMPOSITION_CLOSED"
    with pytest.raises(ServiceExecutionMonitorCompositionError) as started:
        composed.start()
    assert started.value.code == "COMPOSITION_CLOSED"

    composed.stop()
    composed.stop()


@pytest.mark.unit
def test_unselected_paths_do_not_import_the_other_relay(
    tmp_path: object, monkeypatch: object
) -> None:
    monkeypatch.delitem(sys.modules, _JOURNAL_MODULE, raising=False)
    monkeypatch.delitem(sys.modules, _PROJECTION_MODULE, raising=False)

    disabled = compose_execution_monitor_relay(source=SOURCE_NONE, **_refs())
    assert disabled.relay is None
    assert _JOURNAL_MODULE not in sys.modules
    assert _PROJECTION_MODULE not in sys.modules
    disabled.start()
    disabled.stop()

    projection_comp = _compose_projection(tmp_path)
    assert _PROJECTION_MODULE in sys.modules
    assert _JOURNAL_MODULE not in sys.modules
    projection_comp.stop()

    monkeypatch.delitem(sys.modules, _PROJECTION_MODULE, raising=False)
    fixture = _journal_fixture(tmp_path)
    journal_comp = _compose_journal(tmp_path, fixture)
    assert _JOURNAL_MODULE in sys.modules
    assert _PROJECTION_MODULE not in sys.modules
    journal_comp.stop()
    fixture["reader"].close()
    fixture["runtime"].close()


@pytest.mark.unit
def test_assembly_entry_point_delegates_without_importing_a_relay(
    tmp_path: object, monkeypatch: object
) -> None:
    monkeypatch.delitem(sys.modules, _JOURNAL_MODULE, raising=False)
    monkeypatch.delitem(sys.modules, _PROJECTION_MODULE, raising=False)

    from bt_api_py.service_execution_assembly import assemble_execution_monitor_relay

    assert _JOURNAL_MODULE not in sys.modules
    assert _PROJECTION_MODULE not in sys.modules

    disabled = assemble_execution_monitor_relay(source=SOURCE_NONE, **_refs())
    assert disabled.relay is None
    disabled.start()
    disabled.stop()

    list_projection, sink_type, event = _projection_helpers()
    composed = assemble_execution_monitor_relay(
        source=SOURCE_PROJECTION,
        checkpoint_path=tmp_path / "assembly-relay.sqlite",
        projection=list_projection([event(1)]),
        sink=sink_type(),
        **_refs(),
    )
    assert composed.enabled is True
    assert _JOURNAL_MODULE not in sys.modules
    composed.stop()


@pytest.mark.unit
def test_unselected_options_and_invalid_handles_are_refused(tmp_path: object) -> None:
    fixture = _journal_fixture(tmp_path, orders=0, cancellations=0)
    try:
        with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
            _compose_journal(tmp_path, fixture, consumer_id="caller-chosen")
        assert error.value.code == "UNSUPPORTED_OPTION"

        with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
            compose_execution_monitor_relay(
                source=SOURCE_NONE,
                sink=lambda item: None,
                **_refs(),
            )
        assert error.value.code == "UNSUPPORTED_OPTION"

        with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
            _compose_projection(tmp_path, sink=object())
        assert error.value.code == "INVALID_SINK"

        with pytest.raises(ServiceExecutionMonitorCompositionError) as error:
            _compose_projection(tmp_path, sink=None, runtime=object())
        assert error.value.code == "INVALID_RUNTIME"
    finally:
        fixture["reader"].close()
        fixture["runtime"].close()
