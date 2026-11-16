"""Process-local NO_MATCH decisions, using a real router and temporary SQLite."""
import pickle
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from bt_api_py._contracts.models import OrderType, Side
from bt_api_py.execution_routing import (
    ExecutionRoutingOutcome, consume_execution_direct_decision,
)
from tests.unit.test_iter65_sdk_execution_routing import (
    db, make_config, make_intent, make_router, RecordingPort, routing_raw,
)


def fresh(db):
    router = make_router(db, RecordingPort())
    intent = make_intent(side=Side.SELL)
    result = router.route(intent)
    assert result.outcome is ExecutionRoutingOutcome.DIRECT_PATH
    assert result.direct_decision is not None
    return router, intent, result.direct_decision


def consume(token, router, intent, config_id=None):
    return consume_execution_direct_decision(
        token, intent=intent, configuration_id=config_id or router._config.configuration_id)


def test_fresh_only_and_consume_once(db):
    router, intent, token = fresh(db)
    assert router.route(intent).direct_decision is None
    assert make_router(db, RecordingPort()).route(intent).direct_decision is None
    with pytest.raises(TypeError):
        pickle.dumps(token)
    assert consume(token, router, intent)
    assert not consume(token, router, intent)
    assert not consume(object(), router, intent)


@pytest.mark.parametrize("change", ["intent", "config", "candidate", "router_db", "router_config", "thread"])
def test_failed_check_consumes_decision(db, change):
    router, intent, token = fresh(db)
    if change == "intent":
        assert not consume(token, router, make_intent(side=Side.SELL, run_id="other"))
    elif change == "config":
        assert not consume(token, router, intent, "other-config")
    elif change == "candidate":
        db.execute("DELETE FROM execution_route_candidates_v1")
        assert not consume(token, router, intent)
    elif change == "router_db":
        router._db = object()
        assert not consume(token, router, intent)
    elif change == "router_config":
        router._config = make_config()
        assert not consume(token, router, intent)
    else:
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert not pool.submit(consume, token, router, intent).result()
    assert not consume(token, router, intent)


def test_matched_rejected_and_unknown_never_issue(db):
    router = make_router(db, RecordingPort())
    unknown = router.route(make_intent())
    assert unknown.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert unknown.direct_decision is None
    rule = routing_raw()["rules"][0]
    rule["match"] = {}
    reject_router = make_router(db, RecordingPort(), config=make_config(rules=[rule]))
    rejected = reject_router.route(make_intent(request_key="rejected", order_type=OrderType.MARKET, price=None))
    assert rejected.outcome is ExecutionRoutingOutcome.EXECUTION_REJECTED
    assert rejected.direct_decision is None
    unavailable = make_router(db, None).route(make_intent(request_key="unavailable"))
    assert unavailable.outcome is ExecutionRoutingOutcome.EXECUTION_UNAVAILABLE
    assert unavailable.direct_decision is None


def test_reopening_database_never_reissues(db):
    router, intent, token = fresh(db)
    path = db.execute("PRAGMA database_list").fetchone()[2]
    db.close()
    assert not consume(token, router, intent)
    with sqlite3.connect(path, isolation_level=None) as reopened:
        result = make_router(reopened, RecordingPort()).route(intent)
        assert result.outcome is ExecutionRoutingOutcome.DIRECT_PATH
        assert result.direct_decision is None
