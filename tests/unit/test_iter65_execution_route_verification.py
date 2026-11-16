"""Only local journal evidence; no provider or account authority."""
from dataclasses import replace
import pickle
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from bt_api_py._contracts.models import Side
from bt_api_py.execution_routing import (
    ExecutionRoutingError, ExecutionRoutingOutcome,
    consume_execution_rejection_decision, verify_execution_direct_route,
    verify_execution_parent_route,
)
from tests.unit.test_iter65_sdk_execution_routing import (
    db, make_config, make_intent, make_router, RecordingPort, routing_raw,
)


def verify_direct(router, intent):
    return verify_execution_direct_route(router, intent=intent,
                                         configuration_id=router._config.configuration_id)


def test_direct_verification_requires_candidate_and_no_parent(db):
    router = make_router(db, RecordingPort())
    intent = make_intent(side=Side.SELL)
    assert not verify_direct(router, intent)
    assert router.route(intent).outcome is ExecutionRoutingOutcome.DIRECT_PATH
    assert verify_direct(router, intent)
    assert not verify_direct(router, make_intent(side=Side.SELL, run_id="different"))
    assert not verify_execution_direct_route(router, intent=intent, configuration_id="wrong")
    db.execute("DELETE FROM execution_route_candidates_v1")
    assert not verify_direct(router, intent)


def test_parent_verification_survives_result_and_fails_on_rollback(db):
    router = make_router(db, RecordingPort())
    intent = make_intent()
    descriptors = []
    result = router.route(intent, before_submit=lambda d: descriptors.append(d) or True)
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    descriptor = descriptors[0]
    assert verify_execution_parent_route(router, intent=intent, prepared=descriptor)
    for field in descriptor.__dataclass_fields__:
        assert not verify_execution_parent_route(router, intent=intent,
                                                 prepared=replace(descriptor, **{field: "wrong"}))
    assert not verify_execution_parent_route(router, intent=make_intent(run_id="different"),
                                             prepared=descriptor)
    db.execute("DELETE FROM execution_parent_requests_v1")
    assert not verify_execution_parent_route(router, intent=intent, prepared=descriptor)


def expired_router(db):
    rule = routing_raw()["rules"][0]
    rule["deadline"] = {"kind": "absolute", "at": "2000-01-01T00:00:00+00:00"}
    return make_router(db, RecordingPort(), config=make_config(rules=[rule]), clock=lambda: 10**18)


def rejected(db):
    router = expired_router(db)
    intent = make_intent()
    result = router.route(intent)
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_REJECTED
    assert result.code == "EXECUTION_DEADLINE_EXPIRED"
    assert result.rejection_decision is not None
    return router, intent, result


def consume(router, intent, result, *, token=None, code=None, config_id=None):
    return consume_execution_rejection_decision(
        result.rejection_decision if token is None else token, intent=intent,
        configuration_id=config_id or router._config.configuration_id, code=code or result.code)


def test_rejection_first_only_and_one_use(db):
    router, intent, result = rejected(db)
    assert router.route(intent).rejection_decision is None
    assert expired_router(db).route(intent).rejection_decision is None
    with pytest.raises(TypeError):
        pickle.dumps(result.rejection_decision)
    assert not consume(router, intent, result, token=object())
    assert consume(router, intent, result)
    assert not consume(router, intent, result)


@pytest.mark.parametrize("change", ["intent", "code", "config", "candidate", "source", "thread"])
def test_rejection_failed_check_consumes_token(db, change):
    router, intent, result = rejected(db)
    if change == "intent":
        assert not consume(router, make_intent(run_id="different"), result)
    elif change == "code":
        assert not consume(router, intent, result, code="other")
    elif change == "config":
        assert not consume(router, intent, result, config_id="other")
    elif change == "candidate":
        db.execute("DELETE FROM execution_route_candidates_v1")
        assert not consume(router, intent, result)
    elif change == "source":
        router._config.policies[0].algorithm_params["changed"] = 1
        assert not consume(router, intent, result)
    else:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert not pool.submit(consume, router, intent, result).result()
    assert not consume(router, intent, result)


def test_rejection_no_reissue_after_database_reopen(db):
    router, intent, result = rejected(db)
    path = db.execute("PRAGMA database_list").fetchone()[2]
    db.close()
    assert not consume(router, intent, result)
    with sqlite3.connect(path, isolation_level=None) as reopened:
        assert expired_router(reopened).route(intent).rejection_decision is None


@pytest.mark.parametrize("consume_first", [False, True])
def test_expired_rejection_cannot_send_after_clock_moves_back(db, consume_first):
    router, intent, result = rejected(db)
    if consume_first:
        assert consume(router, intent, result)
    port = RecordingPort()
    earlier = make_router(db, port, config=router._config, clock=lambda: 1)
    replay = earlier.route(intent)
    assert replay.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert replay.rejection_decision is None
    assert not port.submit_calls
    if not consume_first:
        assert consume(router, intent, result)


@pytest.mark.parametrize("kind", ["direct", "rejection"])
def test_same_process_empty_journal_reopen_does_not_reissue(db, kind):
    if kind == "direct":
        router = make_router(db, RecordingPort())
        intent = make_intent(side=Side.SELL)
        assert router.route(intent).direct_decision is not None
    else:
        router, intent, _ = rejected(db)
    path = db.execute("PRAGMA database_list").fetchone()[2]
    db.execute("DELETE FROM execution_route_candidates_v1")
    db.close()
    with sqlite3.connect(path, isolation_level=None) as reopened:
        replacement = make_router(reopened, RecordingPort()) if kind == "direct" else expired_router(reopened)
        result = replacement.route(intent)
        assert result.direct_decision is None
        assert result.rejection_decision is None


def test_rejection_never_from_unknown_or_no_match(db):
    router = make_router(db, RecordingPort())
    assert router.route(make_intent()).rejection_decision is None
    assert router.route(make_intent(side=Side.SELL, request_key="direct")).rejection_decision is None


def test_config_is_deeply_detached_and_tampering_fails_closed(db):
    config = make_config()
    router = make_router(db, RecordingPort(), config=config)
    config.policies[0].algorithm_params["external"] = 1
    assert "external" not in router._config.policies[0].algorithm_params
    exported = router._config.to_plain()
    exported["rules"][0]["algorithm"]["params"]["external"] = 2
    assert "external" not in router._config.policies[0].algorithm_params
    router._config.policies[0].algorithm_params["internal"] = 3
    with pytest.raises(ExecutionRoutingError, match="INVALID_EXECUTION_CONFIG"):
        router.route(make_intent())
    assert not verify_direct(router, make_intent(side=Side.SELL))
    with pytest.raises(ExecutionRoutingError, match="INVALID_EXECUTION_CONFIG"):
        make_router(db, RecordingPort(), config=config)
