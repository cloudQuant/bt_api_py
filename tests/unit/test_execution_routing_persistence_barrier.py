"""Local SQLite and fake-port evidence for the optional PREPARED barrier."""

import json
import sqlite3
from dataclasses import FrozenInstanceError, asdict

import pytest

from bt_api_py.execution_routing import ExecutionPreparedRoute, ExecutionRoutingOutcome
from tests.unit.test_iter65_sdk_execution_routing import (
    RecordingPort,
    db,
    make_config,
    make_intent,
    make_router,
    parent_row,
)


def test_committed_descriptor_precedes_submit_and_preserves_wire(db):
    intent = make_intent()
    config = make_config()
    events = []
    port = RecordingPort(events=events)
    router = make_router(db, port, config=config)
    path = db.execute("PRAGMA database_list").fetchone()[2]
    seen = {}

    def persist(descriptor):
        events.append("persist")
        with sqlite3.connect(path) as reader:
            row = reader.execute(
                "SELECT account, strategy, request_key, intent_digest, request_id, "
                "request_sha256, configuration_id, payload, result "
                "FROM execution_parent_requests_v1"
            ).fetchone()
        payload = json.loads(row[7])
        assert row[8] is None
        assert type(descriptor) is ExecutionPreparedRoute
        assert asdict(descriptor) == dict(
            zip(
                ("account", "strategy", "request_key", "intent_digest", "request_id",
                 "request_sha256", "configuration_id", "execution_domain_ref", "parent_ref"),
                (*row[:7], payload["spec"]["execution_domain_ref"], payload["spec"]["parent_ref"]),
            )
        )
        assert descriptor.intent_digest == intent.digest
        assert descriptor.configuration_id == config.configuration_id
        with pytest.raises(FrozenInstanceError):
            descriptor.parent_ref = "replacement"
        seen["payload"] = payload
        return True

    router.route(intent, before_submit=persist)
    assert events == ["persist", "submit"]
    assert port.submit_calls == [seen["payload"]]


@pytest.mark.parametrize("ack", [False, None, 1, "true"])
def test_non_literal_ack_is_unknown_and_replay_never_resends(db, ack):
    port = RecordingPort()
    router = make_router(db, port)
    called = []
    result = router.route(make_intent(), before_submit=lambda d: called.append(d) or ack)
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert result.code == "EXECUTION_PERSISTENCE_BARRIER_FAILED"
    assert result.request_id == called[0].request_id
    assert result.request_sha256 == called[0].request_sha256
    assert result.parent_ref == called[0].parent_ref
    assert parent_row(db)[3] is None
    replay = router.route(make_intent(), before_submit=lambda _: pytest.fail("replayed callback"))
    assert replay.request_id == result.request_id
    assert not port.submit_calls


def test_exception_after_association_and_reopen_never_resends(db):
    path = db.execute("PRAGMA database_list").fetchone()[2]
    port = RecordingPort()
    router = make_router(db, port)
    db.execute("CREATE TABLE associations (request_id TEXT NOT NULL)")

    def persist_then_fail(descriptor):
        with sqlite3.connect(path) as association:
            association.execute("INSERT INTO associations VALUES (?)", (descriptor.request_id,))
        raise RuntimeError("simulated ambiguous association")

    result = router.route(make_intent(), before_submit=persist_then_fail)
    assert result.code == "EXECUTION_PERSISTENCE_BARRIER_FAILED"
    assert db.execute("SELECT request_id FROM associations").fetchone() == (result.request_id,)
    with sqlite3.connect(path, isolation_level=None) as reopened:
        replay = make_router(reopened, port).route(
            make_intent(), before_submit=lambda _: pytest.fail("replayed callback")
        )
    assert replay.request_id == result.request_id
    assert replay.request_sha256 == result.request_sha256
    assert parent_row(db)[3] is None
    assert not port.submit_calls


@pytest.mark.parametrize(
    "mutation", ["payload", "result", "configuration", "router_config", "timestamp", "identity"]
)
def test_mutated_record_or_config_blocks_submit(db, mutation):
    port = RecordingPort()
    router = make_router(db, port)

    def mutate(_):
        if mutation == "router_config":
            router._config = make_config(component="different")
        elif mutation == "configuration":
            db.execute("UPDATE execution_parent_requests_v1 SET configuration_id=?", ("f" * 64,))
        elif mutation == "result":
            db.execute("UPDATE execution_parent_requests_v1 SET result=?", ('{}',))
        elif mutation == "timestamp":
            db.execute("UPDATE execution_parent_requests_v1 SET created_at_ns=2")
        elif mutation == "identity":
            db.execute("UPDATE execution_parent_requests_v1 SET request_key='modified'")
        else:
            db.execute("UPDATE execution_parent_requests_v1 SET payload=?", ('{}',))
        return True

    result = router.route(make_intent(), before_submit=mutate)
    assert result.code == "EXECUTION_PERSISTENCE_BARRIER_FAILED"
    assert not port.submit_calls


def test_closed_connection_blocks_submit_and_reopen_does_not_resend(tmp_path):
    from bt_api_py.execution_routing import install_execution_routing_tables

    path = tmp_path / "close.sqlite"
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("BEGIN IMMEDIATE")
    install_execution_routing_tables(connection)
    connection.commit()
    port = RecordingPort()
    router = make_router(connection, port)

    def close(_):
        connection.close()
        return True

    result = router.route(make_intent(), before_submit=close)
    assert result.code == "EXECUTION_PERSISTENCE_BARRIER_FAILED"
    with sqlite3.connect(path, isolation_level=None) as reopened:
        replay = make_router(reopened, port).route(make_intent())
    assert replay.request_id == result.request_id
    assert not port.submit_calls


def test_reentrant_replay_does_not_call_callback_or_submit(db):
    port = RecordingPort()
    router = make_router(db, port)
    replayed = []

    def persist(descriptor):
        replayed.append(router.route(make_intent(), before_submit=lambda _: pytest.fail("reentered")))
        assert replayed[0].request_id == descriptor.request_id
        assert not port.submit_calls
        return True

    router.route(make_intent(), before_submit=persist)
    assert len(port.submit_calls) == 1


@pytest.mark.parametrize("changed", ["deadline", "invalid", "rewound", "raises", "replacement"])
def test_callback_clock_change_blocks_submit_and_replay(db, changed):
    config = make_config()
    clock_value = [10]

    def clock():
        if clock_value[0] == "raises":
            raise RuntimeError("clock unavailable")
        return clock_value[0]

    port = RecordingPort()
    router = make_router(db, port, config=config, clock=clock)

    def persist(_):
        if changed == "deadline":
            clock_value[0] = config.policies[0].deadline_ns
        elif changed == "invalid":
            clock_value[0] = True
        elif changed == "rewound":
            clock_value[0] = 9
        elif changed == "raises":
            clock_value[0] = "raises"
        else:
            router._clock = lambda: 10
        return True

    result = router.route(make_intent(), before_submit=persist)
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert result.code == "EXECUTION_PERSISTENCE_BARRIER_FAILED"
    assert parent_row(db)[3] is None
    assert router.route(make_intent()).request_id == result.request_id
    assert not port.submit_calls


@pytest.mark.parametrize("replacement", [True, 0, None])
def test_callback_risk_binding_change_blocks_submit(db, replacement):
    port = RecordingPort()
    router = make_router(db, port, risk_selected=False)

    def persist(_):
        router._risk_selected = replacement
        return True

    result = router.route(make_intent(), before_submit=persist)
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert result.code == "EXECUTION_PERSISTENCE_BARRIER_FAILED"
    assert parent_row(db)[3] is None
    assert router.route(make_intent()).request_id == result.request_id
    assert not port.submit_calls


@pytest.mark.parametrize("mutation", ["port", "record"])
def test_final_clock_side_effect_is_checked_before_submit(db, mutation):
    calls = []
    port = RecordingPort()
    replacement = RecordingPort()
    router = None

    def clock():
        calls.append(None)
        if len(calls) == 3:
            if mutation == "port":
                router._port = replacement
            else:
                db.execute("UPDATE execution_parent_requests_v1 SET created_at_ns=2")
        return 10

    router = make_router(db, port, clock=clock)
    result = router.route(make_intent(), before_submit=lambda _: True)
    assert len(calls) == 3
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert result.code == "EXECUTION_PERSISTENCE_BARRIER_FAILED"
    assert not port.submit_calls
    assert not replacement.submit_calls


def test_legacy_call_without_barrier_still_submits(db):
    port = RecordingPort()
    make_router(db, port).route(make_intent())
    assert len(port.submit_calls) == 1
