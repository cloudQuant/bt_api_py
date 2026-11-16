"""Offline v2 journal relay with a real SDK runtime and synthetic RPC leaf.

The peer proof is a controlled test substitute, not a CURVE security test.
"""

from copy import deepcopy
from dataclasses import replace
from threading import Thread

import pytest
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.market_recovery_observation import (
    RecoveryCoverageObservation,
    RecoveryObservationMember,
    RecoveryObservationPage,
)
from bt_api_gateway.recovery_observation_clock import RecoveryObservationQualification
from bt_api_gateway.recovery_observation_journal import RecoveryObservationJournal

from bt_api_py.gateway_recovery_monitor_relay import (
    GatewayRecoveryMonitorRelay,
    GatewayRecoveryRelayError,
)
from bt_api_py.service_monitor_authenticated import (
    AuthenticatedMonitorSinkError,
    AuthenticatedMonitorV2Sink,
)
from bt_api_py.service_runtime_thread import _copy_wire
from tests.unit.test_service_monitor_outbox import _OUTBOX_SCOPE, _PEER, _SCOPE_REF, _runtime

EPOCH = "a" * 32
KEY = MarketSubscriptionKey(
    "src", "grant", MarketInstrumentKey("ctp", "venue", "demo", "future", "id", "symbol"), "trade"
)
MEMBER = RecoveryObservationMember(KEY, "generation")


class Remote:
    def __init__(self):
        self.events, self.calls = {}, []
        self.expected_event_id = None

    def append(self, arguments):
        arguments = _copy_wire(arguments)
        self.calls.append(arguments)
        key = arguments["event_id"]
        if key in self.events:
            assert self.events[key][0] == arguments
        else:
            self.events[key] = (arguments, len(self.events) + 1)
        return {
            "event_id": self.expected_event_id(key),
            "source_event_id": key,
            "sequence": self.events[key][1],
        }


def journal(path):
    return RecoveryObservationJournal(
        path,
        scope_ref="source-scope",
        max_events=100,
        max_bytes=100000,
        timeout_seconds=1,
        qualification_mode="local_clock_v1",
    )


def populate(j, source_ref="source"):
    obs = RecoveryCoverageObservation(
        EPOCH, 321, 3, "d" * 32, "RECOVERING", "WATERMARK", 103, 100, 102, 1, 2, (MEMBER,)
    )
    q = RecoveryObservationQualification(
        obs, "QUALIFIED", "OK", 1000, 1002, 1010, 1012, "c" * 32, 1, 2
    )
    page = RecoveryObservationPage(EPOCH, 321, 0, 3, 3, (obs,), True)
    j.append_page(source_ref, page, qualifications=(q,))
    j.append_capture_stopped(source_ref, EPOCH, owner_pid=321, checkpoint=3)


def install_synthetic_proof(runtime):
    """Synthetic leaf has no CURVE receipt; this is only a unit fixture."""
    binding = runtime.bindings[0]
    runtime._registration_lineage[id(binding)] = (binding, None, *(object() for _ in range(7)))
    runtime._has_current_registration_observation = lambda b: (
        id(b) in runtime._registration_lineage and runtime._registration_lineage[id(b)][0] is b
    )
    runtime.authenticated_peer_for_binding = lambda binding: (_PEER, "f" * 64)


def setup_sink(remote=None, *, scope=None):
    remote = remote or Remote()
    kwargs = {"requested_operations": frozenset({"events.append"})}
    if scope is not None:
        kwargs["scope"] = scope
    runtime, transports, registrations = _runtime(remote, **kwargs)
    # Real ServiceClientRuntime with an explicitly synthetic transport/proof leaf.
    install_synthetic_proof(runtime)
    sink = AuthenticatedMonitorV2Sink(
        runtime,
        scope_ref=_SCOPE_REF,
        outbox_scope=_OUTBOX_SCOPE,
        binding_ref="binding",
        source_ref="source",
        expected_peer=_PEER,
    )
    remote.expected_event_id = sink._expected_event_id
    return sink, runtime, transports, registrations, remote


def relay(root, source, sink, *, clock=lambda: 1800000000.0):
    return GatewayRecoveryMonitorRelay(
        source,
        journal_path=root / "source.sqlite",
        scope_ref="source-scope",
        source_ref="source",
        checkpoint_path=root / "checkpoint.sqlite",
        sink=sink,
        clock_utc=clock,
    )


def test_all_kinds_checkpoint_and_reopen(tmp_path):
    source = journal(tmp_path / "source.sqlite")
    populate(source)
    sink, runtime, _, _, remote = setup_sink()
    r = relay(tmp_path, source, sink)
    assert r.pump(max_records=20).cursor == 4
    assert [x["data"]["event"]["kind"] for x in remote.calls] == [
        "epoch",
        "gap",
        "observation",
        "capture_stopped",
    ]
    assert all(
        x["schema_version"] == 2 and x["data"]["envelope_time_basis"] == "FIRST_RELAY_ENQUEUE"
        for x in remote.calls
    )
    assert all(x["source_ref"] == "source" and x["binding_ref"] == "binding" for x in remote.calls)
    r.close()
    reopened = relay(tmp_path, source, sink)
    assert reopened.pump().state == "IDLE" and reopened.status().cursor == 4
    reopened.close()
    runtime.close()


def test_ack_loss_replays_identical_envelope_after_reopen(tmp_path):
    source = journal(tmp_path / "source.sqlite")
    populate(source)
    sink, runtime, transports, _, remote = setup_sink()
    r = relay(tmp_path, source, sink, clock=lambda: 1800000000.5)
    transports[0].fail_after_append = True
    result = r.pump(max_records=1)
    assert (result.state, result.cursor, result.pending_count) == ("SINK_UNAVAILABLE", 0, 1)
    original = deepcopy(remote.calls[0])
    r.close()
    reopened = relay(tmp_path, source, sink, clock=lambda: 1900000000.0)
    assert reopened.pump(max_records=1).cursor == 1
    assert remote.calls[1] == original and len(remote.events) == 1
    reopened.close()
    runtime.close()


def test_two_writers_and_owner_thread_guard(tmp_path):
    source = journal(tmp_path / "source.sqlite")
    populate(source)
    sink, runtime, _, _, _ = setup_sink()
    r = relay(tmp_path, source, sink)
    with pytest.raises(GatewayRecoveryRelayError, match="WRITER_BUSY"):
        relay(tmp_path, source, sink)
    errors = []

    def invoke():
        try:
            r.pump()
        except GatewayRecoveryRelayError as exc:
            errors.append(exc.code)

    thread = Thread(target=invoke)
    thread.start()
    thread.join()
    assert errors == ["WRONG_OWNER_THREAD"]
    r.close()
    with pytest.raises(GatewayRecoveryRelayError):
        r.pump()
    runtime.close()


def test_sink_rejects_binding_change_and_bad_receipt():
    sink, runtime, transports, _, _ = setup_sink()
    envelope = {
        "schema_version": 2,
        "event_id": "event",
        "event_type": "gateway.recovery.journal.v1",
        "occurred_at": 1800000000.0,
        "data": {},
        "binding_ref": "binding",
        "source_ref": "source",
    }
    transports[0].receipt_transform = lambda x: dict(x, event_id="service-event-v2:" + "a" * 64)
    with pytest.raises(AuthenticatedMonitorSinkError, match="INGRESS_OUTCOME_UNKNOWN"):
        sink.append(envelope)
    runtime._bindings[0] = replace(runtime._bindings[0], registration_id="different")
    with pytest.raises(AuthenticatedMonitorSinkError, match="INGRESS_BINDING_CHANGED"):
        sink.append(envelope)
    runtime.close()


@pytest.mark.parametrize(
    "stage", ["before_pending", "after_pending", "before_ack_commit", "after_ack_commit"]
)
def test_commit_uncertainty_stops_and_reopen_recovers(tmp_path, stage):
    source = journal(tmp_path / "source.sqlite")
    populate(source)
    sink, runtime, _, _, remote = setup_sink()
    r = relay(tmp_path, source, sink)
    commit = r._commit
    calls = [0]

    def fail():
        calls[0] += 1
        target = 1 if stage in {"before_pending", "after_pending"} else 2
        if calls[0] == target:
            if stage.startswith("after_"):
                commit()
            raise OSError("synthetic commit uncertainty")
        commit()

    r._commit = fail
    with pytest.raises(GatewayRecoveryRelayError, match="CHECKPOINT_UNCERTAIN"):
        r.pump(max_records=1)
    with pytest.raises(GatewayRecoveryRelayError, match="RELAY_UNAVAILABLE"):
        r.pump(max_records=1)
    r.close()
    recovered = relay(tmp_path, source, sink)
    state = recovered.status()
    if stage == "before_pending":
        assert (state.cursor, state.pending_count, len(remote.events)) == (0, 0, 0)
    elif stage == "after_pending":
        assert (state.cursor, state.pending_count, len(remote.events)) == (0, 1, 0)
    elif stage == "before_ack_commit":
        assert (state.cursor, state.pending_count, len(remote.events)) == (0, 1, 1)
    else:
        # COMMIT succeeded before the injected failure; its outcome is unknown
        # until reopen and must not be falsely described as uncommitted.
        assert (state.cursor, state.pending_count, len(remote.events)) == (1, 0, 1)
    if state.pending_count or not state.cursor:
        assert recovered.pump(max_records=1).cursor == 1
        assert len(remote.events) == 1
    recovered.close()
    runtime.close()


def test_budget_keeps_cursor_and_pending_without_truncation(tmp_path):
    source = journal(tmp_path / "source.sqlite")
    populate(source)
    sink, runtime, transports, _, remote = setup_sink()
    r = relay(tmp_path, source, sink)
    assert r.pump(max_bytes=1).state == "BUDGET_EXCEEDED"
    assert r.status().cursor == 0 and r.status().pending_count == 0 and not remote.calls
    transports[0].fail_after_append = True
    assert r.pump(max_records=1).pending_count == 1
    assert r.pump(max_bytes=1).pending_count == 1
    assert len(remote.calls) == 1
    assert r.pump(max_records=1).cursor == 1 and len(remote.events) == 1
    r.close()
    runtime.close()


@pytest.mark.parametrize(
    "mode", ["inode", "first_row", "consumed_row", "pending_row", "journal_object", "target"]
)
def test_source_or_destination_change_stops_without_skipping(tmp_path, mode):
    import os
    import sqlite3

    source = journal(tmp_path / "source.sqlite")
    populate(source)
    sink, runtime, transports, _, remote = setup_sink()
    r = relay(tmp_path, source, sink)
    if mode == "pending_row":
        transports[0].fail_after_append = True
        assert r.pump(max_records=1).pending_count == 1
    elif mode == "consumed_row":
        assert r.pump(max_records=2).cursor == 2
    else:
        assert r.pump(max_records=1).cursor == 1
    old_calls = len(remote.calls)
    if mode == "inode":
        import shutil

        shutil.copyfile(tmp_path / "source.sqlite", tmp_path / "replacement.sqlite")
        os.replace(tmp_path / "replacement.sqlite", tmp_path / "source.sqlite")
    elif mode in {"first_row", "consumed_row", "pending_row"}:
        row_id = 2 if mode == "consumed_row" else 1
        with sqlite3.connect(tmp_path / "source.sqlite") as db:
            db.execute("DELETE FROM records WHERE id=?", (row_id,))
    elif mode == "journal_object":
        r._journal = journal(tmp_path / "source.sqlite")
    else:
        sink._source_ref = "changed"
    with pytest.raises(GatewayRecoveryRelayError):
        r.pump(max_records=1)
    assert len(remote.calls) == old_calls
    r.close()
    runtime.close()


def test_existing_empty_or_aliased_checkpoint_is_not_reinitialized(tmp_path):
    import os

    source_path = tmp_path / "source.sqlite"
    source = journal(source_path)
    populate(source)
    sink, runtime, _, _, _ = setup_sink()
    checkpoint = tmp_path / "checkpoint.sqlite"
    checkpoint.touch(mode=0o600)
    with pytest.raises(GatewayRecoveryRelayError, match="INITIALIZATION_FAILED"):
        relay(tmp_path, source, sink)
    assert checkpoint.stat().st_size == 0
    checkpoint.unlink()
    os.link(source_path, checkpoint)
    with pytest.raises(GatewayRecoveryRelayError, match="INITIALIZATION_FAILED"):
        relay(tmp_path, source, sink)
    assert os.path.samefile(source_path, checkpoint)
    runtime.close()


def test_wrong_source_row_never_skipped(tmp_path):
    source = journal(tmp_path / "source.sqlite")
    obs = RecoveryCoverageObservation(
        EPOCH, 321, 1, None, "UNKNOWN", "BEGIN_FAILED", 102, None, None, None, None, ()
    )
    q = RecoveryObservationQualification(obs, "UNKNOWN", "SOURCE_UNKNOWN")
    page = RecoveryObservationPage(EPOCH, 321, 0, 1, 1, (obs,), False)
    source.append_page("other-source", page, qualifications=(q,))
    sink, runtime, _, _, remote = setup_sink()
    with pytest.raises(GatewayRecoveryRelayError, match="SOURCE_SCOPE_MISMATCH"):
        relay(tmp_path, source, sink)
    assert not remote.calls
    runtime.close()


def test_sink_pid_thread_and_destination_signature(tmp_path):
    sink, runtime, _, registrations, _ = setup_sink()
    stable = sink.destination_manifest
    errors = []

    def inspect():
        try:
            sink.assert_current()
        except AuthenticatedMonitorSinkError as exc:
            errors.append(exc.code)

    thread = Thread(target=inspect)
    thread.start()
    thread.join()
    assert errors == ["WRONG_OWNER_THREAD"]
    sink._pid = -1
    with pytest.raises(AuthenticatedMonitorSinkError, match="WRONG_PROCESS"):
        sink.assert_current()
    sink._pid = __import__("os").getpid()
    runtime.close()
    remote = Remote()
    runtime2, _, _ = _runtime(
        remote, requested_operations=frozenset({"events.append"}), registration_ids=registrations
    )
    install_synthetic_proof(runtime2)
    second = AuthenticatedMonitorV2Sink(
        runtime2,
        scope_ref=_SCOPE_REF,
        outbox_scope=_OUTBOX_SCOPE,
        binding_ref="binding",
        source_ref="source",
        expected_peer=_PEER,
    )
    assert second.destination_manifest == stable
    assert runtime2.bindings[0].registration_id != sink._binding.registration_id
    runtime2.close()


def test_consumed_row_rewrite_with_valid_journal_digest_is_detected(tmp_path):
    import json
    import sqlite3

    from bt_api_gateway.recovery_observation_journal import _digest

    source = journal(tmp_path / "source.sqlite")
    populate(source)
    sink, runtime, _, _, remote = setup_sink()
    r = relay(tmp_path, source, sink)
    assert r.pump(max_records=2).cursor == 2
    with sqlite3.connect(tmp_path / "source.sqlite") as db:
        source_ref, epoch, kind, sequence, payload = db.execute(
            "SELECT source_ref,source_epoch,kind,sequence,payload FROM records WHERE id=2"
        ).fetchone()
        data = json.loads(payload)
        data["start"] = 2
        replacement = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        assert len(replacement) == len(payload)
        db.execute(
            "UPDATE records SET payload=?,digest=? WHERE id=2",
            (replacement, _digest(source_ref, epoch, kind, sequence, replacement)),
        )
    with pytest.raises(GatewayRecoveryRelayError, match="SOURCE_HISTORY_CHANGED"):
        r.pump(max_records=1)
    assert len(remote.calls) == 2
    r.close()
    runtime.close()


def test_oversize_event_is_not_truncated_or_acknowledged(tmp_path):
    source = RecoveryObservationJournal(
        tmp_path / "source.sqlite",
        scope_ref="source-scope",
        max_events=100,
        max_bytes=1000000,
        timeout_seconds=1,
        qualification_mode="local_clock_v1",
    )
    members = []
    for i in range(400):
        key = MarketSubscriptionKey(
            "src",
            "grant",
            MarketInstrumentKey("ctp", "venue", "demo", "future", f"id{i:03d}", f"symbol{i:03d}"),
            "trade",
        )
        members.append(RecoveryObservationMember(key, "generation"))
    members.sort(key=lambda m: (m.key.stream_id, m.generation))
    obs = RecoveryCoverageObservation(
        EPOCH, 321, 1, "d" * 32, "RECOVERING", "WATERMARK", 103, 100, 102, 1, 2, tuple(members)
    )
    q = RecoveryObservationQualification(
        obs, "QUALIFIED", "OK", 1000, 1002, 1010, 1012, "c" * 32, 1, 2
    )
    source.append_page(
        "source", RecoveryObservationPage(EPOCH, 321, 0, 1, 1, (obs,), False), qualifications=(q,)
    )
    sink, runtime, _, _, remote = setup_sink()
    r = relay(tmp_path, source, sink)
    assert r.pump(max_records=1).cursor == 1  # epoch marker
    with pytest.raises(GatewayRecoveryRelayError, match="EVENT_TOO_LARGE"):
        r.pump(max_records=1)
    assert r.status().cursor == 1 and r.status().pending_count == 0 and len(remote.events) == 1
    r.close()
    runtime.close()


def test_first_row_anchor_is_persisted_before_delivery_budget(tmp_path):
    import sqlite3

    source = journal(tmp_path / "source.sqlite")
    sink, runtime, _, _, remote = setup_sink()
    r = relay(tmp_path, source, sink)
    assert r.pump().state == "IDLE"
    populate(source)
    assert r.pump(max_bytes=1).state == "BUDGET_EXCEEDED"
    with sqlite3.connect(tmp_path / "checkpoint.sqlite") as db:
        assert db.execute("SELECT cursor,anchor_cursor,pending_cursor FROM state").fetchone() == (
            0,
            1,
            None,
        )
    with sqlite3.connect(tmp_path / "source.sqlite") as db:
        db.execute("DELETE FROM records WHERE id=1")
    with pytest.raises(GatewayRecoveryRelayError, match="SOURCE_HISTORY_CHANGED"):
        r.pump()
    assert not remote.calls
    r.close()
    runtime.close()


def test_live_checkpoint_cursor_regression_fails_closed(tmp_path):
    import sqlite3

    source = journal(tmp_path / "source.sqlite")
    populate(source)
    sink, runtime, _, _, remote = setup_sink()
    r = relay(tmp_path, source, sink)
    assert r.pump(max_records=2).cursor == 2
    _, digest = r._at(1)
    with sqlite3.connect(tmp_path / "checkpoint.sqlite") as db:
        db.execute("UPDATE state SET cursor=1,cursor_digest=?", (digest,))
    with pytest.raises(GatewayRecoveryRelayError, match="CHECKPOINT_INVALID"):
        r.pump()
    assert len(remote.calls) == 2
    r.close()
    runtime.close()


def test_receiver_stored_shape_over_limit_fails_before_pending(tmp_path):
    from bt_api_base.service_protocol import WireScope
    from bt_api_gateway.recovery_observation_event import recovery_journal_event
    from bt_api_monitor.durable import DurableOutbox
    from bt_api_monitor.service_ingress import (
        MonitorIngressProducerBinding,
        MonitorIngressService,
        MonitorIngressServiceError,
        MonitorIngressTarget,
    )

    from bt_api_py.service_monitor_authenticated import _bounded
    from tests.unit.test_service_monitor_outbox import _PRODUCER

    source = RecoveryObservationJournal(
        tmp_path / "source.sqlite",
        scope_ref="source-scope",
        max_events=100,
        max_bytes=1000000,
        timeout_seconds=1,
        qualification_mode="local_clock_v1",
    )
    members = []
    for i in range(32):
        suffix = str(i)
        instrument = MarketInstrumentKey(
            "p" * 198,
            "v" * 198,
            "a" * 198,
            "f" * 198,
            suffix + "i" * (198 - len(suffix)),
            "n" * 198,
        )
        key = MarketSubscriptionKey("s" * 198, "e" * 198, instrument, "tick")
        members.append(RecoveryObservationMember(key, "g" * 198))
    members.sort(key=lambda m: (m.key.stream_id, m.generation))
    obs = RecoveryCoverageObservation(
        EPOCH, 321, 1, "d" * 32, "RECOVERING", "WATERMARK", 103, 100, 102, 1, 2, tuple(members)
    )
    q = RecoveryObservationQualification(
        obs, "QUALIFIED", "OK", 1000, 1002, 1010, 1012, "c" * 32, 1, 2
    )
    source.append_page(
        "source", RecoveryObservationPage(EPOCH, 321, 0, 1, 1, (obs,), False), qualifications=(q,)
    )
    scope = WireScope(
        "paper:monitor", account_ref="account-a", strategy_ref="strategy-a", run_id="run-a"
    )
    sink, runtime, _, _, remote = setup_sink(scope=scope)
    r = relay(tmp_path, source, sink)
    assert r.pump(max_records=1).cursor == 1
    record = source.read_after(1, limit=1).records[0]
    event = recovery_journal_event(record, scope_ref="source-scope", journal_version=2).to_dict()
    envelope = {
        "schema_version": 2,
        "event_id": r._event_id(record.cursor),
        "event_type": "gateway.recovery.journal.v1",
        "occurred_at": 1800000000.0,
        "data": {
            "schema": "sdk.gateway_recovery_relay/v1",
            "envelope_time_basis": "FIRST_RELAY_ENQUEUE",
            "event": event,
        },
        "binding_ref": "binding",
        "source_ref": "source",
    }
    assert len(_bounded(envelope)) <= 65536
    binding = MonitorIngressProducerBinding(
        "binding", "source", _PRODUCER, scope, _OUTBOX_SCOPE, ("gateway.recovery.journal.v1",)
    )
    receiver = MonitorIngressService(
        DurableOutbox(tmp_path / "remote.sqlite"),
        {
            replace(scope, run_id=None): MonitorIngressTarget(
                _OUTBOX_SCOPE, ("gateway.recovery.journal.v1",)
            )
        },
        producer_bindings=(binding,),
    )
    with pytest.raises(MonitorIngressServiceError, match="EVENT_TOO_LARGE"):
        receiver.handle(
            "events.append",
            principal_ref=_PRODUCER.principal_ref,
            identity=_PRODUCER,
            scope=scope,
            arguments=envelope,
        )
    with pytest.raises(GatewayRecoveryRelayError, match="EVENT_TOO_LARGE"):
        r.pump(max_records=1)
    assert r.status().cursor == 1 and r.status().pending_count == 0 and len(remote.calls) == 1
    # Simulate a pending envelope written by the older request-only budget check.
    _, digest = r._project(record)
    r._transaction(
        "UPDATE state SET pending_cursor=?,pending_digest=?,pending_envelope=?",
        (record.cursor, digest, _bounded(envelope).decode("ascii")),
    )
    with pytest.raises(GatewayRecoveryRelayError, match="EVENT_TOO_LARGE"):
        r.pump(max_records=1)
    assert r.status().cursor == 1 and r.status().pending_count == 1 and len(remote.calls) == 1
    r.close()
    runtime.close()
