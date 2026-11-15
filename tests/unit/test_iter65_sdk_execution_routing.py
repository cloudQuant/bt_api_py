"""SDK-side Execution routing: durable identity first, then one submit.

The tests use fake Execution/Gateway/Risk ports and temporary SQLite only. A
local PREPARED row or a port acknowledgement is not venue state, and no test
starts a thread, opens a socket, or contacts an account.
"""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from decimal import Decimal

import pytest
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py.execution_routing import (
    ExecutionOrderRouter,
    ExecutionRoutingError,
    ExecutionRoutingOutcome,
    install_execution_routing_tables,
    parse_execution_routing,
    route_order,
)
from bt_api_py.gateway_direct_client import GatewayClientRoute, build_gateway_order_intent

KEY = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")


class SimulatedProcessDeath(BaseException):
    """A crash injection no ``except Exception`` may swallow."""


def routing_raw(**overrides):
    value = {
        "schema_version": 1,
        "component": "exec-instance",
        "account_ref": "acct",
        "strategy_ref": "strategy",
        "execution_domain_ref": "paper",
        "grant_revision": "grant-v1",
        "delegation": {"delegation_id": "delegation-1", "principal_ref": "principal-a"},
        "instruments": [KEY.to_dict()],
        "rules": [
            {
                "id": "rule-1",
                "match": {
                    "instrument": KEY.to_dict(),
                    "side": "buy",
                    "offset": "open",
                    "order_types": ["limit"],
                    "minimum_quantity": "1",
                    "quantity_unit": "lots",
                },
                "scheme": {"name": "pass-v1", "version": 1},
                "algorithm": {"class": "PASS", "version": 1, "params": {}},
                "deadline": {"kind": "absolute", "at": "2099-12-31T08:00:00+08:00"},
                "time_in_force": "DAY",
                "trading_day": "20261109",
            }
        ],
    }
    value.update(overrides)
    return value


def make_config(**overrides):
    return parse_execution_routing(routing_raw(**overrides))


def make_intent(
    *,
    price="10",
    request_key="request-1",
    side=Side.BUY,
    order_type=OrderType.LIMIT,
    quantity="5",
    quantity_unit="lots",
    offset="open",
    run_id="run-1",
):
    binding = GatewayCommandBinding("journal", "acct", "strategy", "principal-a")
    route = GatewayClientRoute(
        "broker.direct_write",
        "gw-service",
        "gw-instance",
        "gw-scope",
        WireScope("paper", account_ref="acct", strategy_ref="strategy", run_id=run_id),
        WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy"),
    )
    request = OrderRequest(
        symbol="rb",
        side=side,
        order_type=order_type,
        quantity=Decimal(quantity),
        account_id="acct",
        client_order_id="client",
        idempotency_key=request_key,
        price=None if price is None else Decimal(price),
        quantity_unit=quantity_unit,
        exchange_id="SHFE",
        offset=offset,
    )
    return build_gateway_order_intent(binding, route, request)


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / "routes.sqlite", isolation_level=None)
    connection.execute("BEGIN IMMEDIATE")
    install_execution_routing_tables(connection)
    connection.commit()
    yield connection
    connection.close()


def parent_row(db, request_key="request-1"):
    return db.execute(
        "SELECT request_id, request_sha256, payload, result, cancel_result "
        "FROM execution_parent_requests_v1 WHERE account=? AND strategy=? AND request_key=?",
        ("acct", "strategy", request_key),
    ).fetchone()


def candidate_outcome(db, request_key="request-1"):
    row = db.execute(
        "SELECT snapshot FROM execution_route_candidates_v1 WHERE request_key=?", (request_key,)
    ).fetchone()
    return None if row is None else json.loads(row[0])["outcome"]


class RecordingPort:
    def __init__(
        self,
        *,
        reply=None,
        status_reply=None,
        cancel_reply=None,
        events=None,
        on_submit=None,
    ):
        self.submit_reply = (
            {"status": "ACCEPTED", "parent_ref": "parent-1"} if reply is None else reply
        )
        self.status_reply = status_reply
        self.cancel_reply = {"status": "ACCEPTED"} if cancel_reply is None else cancel_reply
        self.events = events if events is not None else []
        self.submit_calls = []
        self.status_calls = []
        self.cancel_calls = []
        self.on_submit = on_submit

    def submit(self, request):
        self.events.append("submit")
        self.submit_calls.append(deepcopy(request))
        if self.on_submit is not None:
            self.on_submit(request)
        if isinstance(self.submit_reply, BaseException):
            raise self.submit_reply
        return self.submit_reply

    def status(self, request_id):
        self.events.append("status")
        self.status_calls.append(request_id)
        if isinstance(self.status_reply, BaseException):
            raise self.status_reply
        return self.status_reply

    def cancel(self, payload):
        self.events.append("cancel")
        self.cancel_calls.append(deepcopy(payload))
        if isinstance(self.cancel_reply, BaseException):
            raise self.cancel_reply
        return self.cancel_reply


def make_router(db, port, *, config=None, risk_selected=False, clock=None):
    return ExecutionOrderRouter(
        db=db,
        config=make_config() if config is None else config,
        execution_port=port,
        risk_selected=risk_selected,
        clock=(lambda: 1) if clock is None else clock,
    )


def test_matched_request_persists_identity_before_submit(db):
    observed = {}

    def on_submit(request):
        row = parent_row(db)
        observed["row_at_submit"] = row
        observed["request"] = deepcopy(request)

    port = RecordingPort(on_submit=on_submit)
    result = make_router(db, port).route(make_intent())

    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_ACCEPTED
    assert result.parent_ref == "parent-1"
    assert result.request_id and len(result.request_sha256) == 64
    assert port.submit_calls and port.events == ["submit"]

    row = observed["row_at_submit"]
    assert row is not None, "the local record must exist before the port is called"
    assert row[3] is None, "the record is PREPARED before the remote call"
    assert row[0] == result.request_id
    stored = parent_row(db)
    assert stored is not None and stored[3] is not None
    payload = json.loads(stored[2])
    assert set(payload) == {"request_id", "order_type", "spec", "strategy", "delegation"}
    assert payload["order_type"] == "limit"
    assert payload["spec"]["risk_selected"] is False
    assert payload["spec"]["time_in_force"] == "DAY"
    assert payload["spec"]["allowed_trading_days"] == ["20261109"]
    assert payload["delegation"] == {
        "delegation_id": "delegation-1",
        "principal_ref": "principal-a",
    }


def test_replay_same_identity_and_digest_is_idempotent(db):
    port = RecordingPort()
    router = make_router(db, port)
    first = router.route(make_intent())
    second = router.route(make_intent())

    assert first == second
    assert len(port.submit_calls) == 1
    assert port.events == ["submit"]


def test_same_request_key_with_different_content_conflicts(db):
    port = RecordingPort()
    router = make_router(db, port)
    first = router.route(make_intent())
    stored = parent_row(db)

    with pytest.raises(ExecutionRoutingError) as info:
        router.route(make_intent(price="11"))
    assert info.value.code == "EXECUTION_REQUEST_CONFLICT"
    assert len(port.submit_calls) == 1
    assert parent_row(db) == stored
    assert first.request_id


def test_unavailable_execution_fails_closed_and_never_takes_the_direct_path(db):
    direct_calls = []
    result = route_order(
        make_router(db, None),
        make_intent(),
        lambda: direct_calls.append("direct") or "DIRECT",
    )

    assert isinstance(result, object)
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNAVAILABLE
    assert direct_calls == []
    assert parent_row(db) is None
    assert candidate_outcome(db) is None


def test_unmatched_request_keeps_the_existing_path(db):
    port = RecordingPort()
    config = make_config(
        rules=[
            {
                **routing_raw()["rules"][0],
                "match": {**routing_raw()["rules"][0]["match"], "side": "sell"},
            }
        ]
    )
    direct_calls = []
    result = route_order(
        make_router(db, port, config=config),
        make_intent(),
        lambda: direct_calls.append("direct") or "DIRECT",
    )

    assert result == "DIRECT"
    assert direct_calls == ["direct"]
    assert port.submit_calls == []
    assert parent_row(db) is None
    assert candidate_outcome(db) == "NO_MATCH"


def test_no_router_keeps_the_existing_path():
    direct_calls = []
    result = route_order(None, make_intent(), lambda: direct_calls.append("direct") or "DIRECT")
    assert result == "DIRECT"
    assert direct_calls == ["direct"]


def test_indeterminate_match_is_refused_without_sending(db):
    rule = routing_raw()["rules"][0]
    rule["match"]["minimum_quantity"] = "1"
    rule["match"]["quantity_unit"] = "contracts"
    port = RecordingPort()
    direct_calls = []
    result = route_order(
        make_router(db, port, config=make_config(rules=[rule])),
        make_intent(quantity_unit="lots"),
        lambda: direct_calls.append("direct") or "DIRECT",
    )

    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_REJECTED
    assert result.code == "EXECUTION_ROUTE_INDETERMINATE"
    assert direct_calls == []
    assert port.submit_calls == []
    assert parent_row(db) is None


def test_market_order_in_a_wildcard_rule_is_refused_before_any_send(db):
    rule = routing_raw()["rules"][0]
    rule["match"] = {}
    port = RecordingPort()
    direct_calls = []
    result = route_order(
        make_router(db, port, config=make_config(rules=[rule])),
        make_intent(order_type=OrderType.MARKET, price=None),
        lambda: direct_calls.append("direct") or "DIRECT",
    )

    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_REJECTED
    assert result.code == "EXECUTION_ORDER_TYPE_UNSUPPORTED"
    assert direct_calls == []
    assert port.submit_calls == []
    assert parent_row(db) is None


def test_expired_deadline_is_refused_before_any_send(db):
    rule = routing_raw()["rules"][0]
    rule["deadline"] = {"kind": "absolute", "at": "2000-01-01T00:00:00+00:00"}
    port = RecordingPort()
    result = make_router(db, port, config=make_config(rules=[rule]), clock=lambda: 10**18).route(
        make_intent()
    )

    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_REJECTED
    assert result.code == "EXECUTION_DEADLINE_EXPIRED"
    assert port.submit_calls == []
    assert parent_row(db) is None


def test_crash_between_local_record_and_submit_stays_unknown(db):
    port = RecordingPort(reply=SimulatedProcessDeath())
    with pytest.raises(SimulatedProcessDeath):
        make_router(db, port).route(make_intent())

    row = parent_row(db)
    assert row is not None and row[3] is None

    replay_port = RecordingPort()
    result = make_router(db, replay_port).route(make_intent())
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert result.request_id == row[0]
    assert result.request_sha256 == row[1]
    assert replay_port.submit_calls == []
    assert parent_row(db) == row


def test_lost_reply_is_unknown_and_is_never_resent(db):
    port = RecordingPort(reply=TimeoutError("reply lost"))
    router = make_router(db, port)
    first = router.route(make_intent())
    second = router.route(make_intent())

    assert first.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert second.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert len(port.submit_calls) == 1
    row = parent_row(db)
    assert row is not None and json.loads(row[3])["status"] == "EXECUTION_UNKNOWN"


def test_local_record_failure_does_not_reach_the_port(tmp_path):
    path = tmp_path / "routes.sqlite"
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    install_execution_routing_tables(writer)
    writer.commit()
    writer.close()

    reader = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
    port = RecordingPort()
    try:
        with pytest.raises(ExecutionRoutingError) as info:
            make_router(reader, port).route(make_intent())
    finally:
        reader.close()
    assert info.value.code == "EXECUTION_LOCAL_RECORD_FAILED"
    assert port.submit_calls == []


def test_cancel_for_a_matched_parent_routes_to_execution(db):
    port = RecordingPort()
    router = make_router(db, port)
    routed = router.route(make_intent())

    first = router.cancel(make_intent())
    assert first.outcome is ExecutionRoutingOutcome.EXECUTION_CANCEL_ACCEPTED
    assert len(port.cancel_calls) == 1
    payload = port.cancel_calls[0]
    assert payload["request_id"] == routed.request_id
    assert payload["parent_ref"] == json.loads(parent_row(db)[2])["spec"]["parent_ref"]
    assert payload["remote_parent_ref"] == "parent-1"
    assert payload["request_sha256"] == routed.request_sha256
    assert payload["operation_id"].startswith("sdk-cancel-v1:")

    second = router.cancel(make_intent())
    assert second == first
    assert len(port.cancel_calls) == 1
    assert len(port.submit_calls) == 1


def test_cancel_without_an_execution_port_is_not_a_direct_cancel(db):
    port = RecordingPort()
    routed = make_router(db, port).route(make_intent())

    direct_calls = []
    result = make_router(db, None).cancel(make_intent())
    assert routed.request_id
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNAVAILABLE
    assert result.code == "EXECUTION_CANCEL_UNAVAILABLE"
    assert direct_calls == []
    stored = parent_row(db)
    assert stored[4] is None


def test_cancel_of_an_unmatched_request_keeps_the_existing_path(db):
    result = make_router(db, RecordingPort()).cancel(make_intent())
    assert result.outcome is ExecutionRoutingOutcome.DIRECT_PATH


def test_cancel_reply_loss_is_unknown_and_not_retried(db):
    port = RecordingPort(cancel_reply=TimeoutError("cancel reply lost"))
    router = make_router(db, port)
    router.route(make_intent())

    first = router.cancel(make_intent())
    second = router.cancel(make_intent())
    assert first.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert second == first
    assert len(port.cancel_calls) == 1


def test_status_projects_parent_children_and_local_identity(db):
    port = RecordingPort()
    router = make_router(db, port)
    routed = router.route(make_intent())
    port.status_reply = {
        "status": "ACCEPTED",
        "state": "ACCEPTED",
        "request_id": routed.request_id,
        "request_sha256": routed.request_sha256,
        "parent_ref": "parent-1",
        "children": [
            {
                "child_id": "child-1",
                "state": "SENT",
                "quantity": "2",
                "reservation_id": "reservation-1",
                "attempts": 1,
                "last_error": None,
            }
        ],
        "unknown_children": [],
    }

    status = router.status(make_intent())
    assert status.routed is True
    assert status.outcome is ExecutionRoutingOutcome.EXECUTION_ACCEPTED
    assert status.request_id == routed.request_id
    assert status.request_sha256 == routed.request_sha256
    assert status.parent_ref == "parent-1"
    assert status.execution_state == "ACCEPTED"
    assert status.children[0]["child_id"] == "child-1"
    assert status.unknown_children == ()


def test_status_without_a_port_preserves_local_identity(db):
    port = RecordingPort()
    routed = make_router(db, port).route(make_intent())
    status = make_router(db, None).status(make_intent())

    assert status.routed is True
    assert status.request_id == routed.request_id
    assert status.code == "EXECUTION_STATUS_UNAVAILABLE"
    assert status.execution_state is None


def test_status_rejects_a_foreign_reply(db):
    port = RecordingPort()
    routed = make_router(db, port).route(make_intent())
    port.status_reply = {
        "state": "ACCEPTED",
        "request_id": routed.request_id,
        "request_sha256": "0" * 64,
    }
    status = make_router(db, port).status(make_intent())

    assert status.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert status.code == "EXECUTION_INVALID_REPLY"
    assert status.execution_state is None


def test_status_of_an_unmatched_request_is_not_routed(db):
    status = make_router(db, RecordingPort()).status(make_intent())
    assert status.routed is False
    assert status.outcome is ExecutionRoutingOutcome.DIRECT_PATH


def test_scope_mismatch_is_refused_before_any_send(db):
    port = RecordingPort()
    result = make_router(db, port, config=make_config(execution_domain_ref="other")).route(
        make_intent()
    )

    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_REJECTED
    assert result.code == "EXECUTION_SCOPE_DENIED"
    assert port.submit_calls == []
    assert parent_row(db) is None


def test_unresolved_instrument_is_refused_before_any_send(db):
    other = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "ag", "ag")
    rule = routing_raw()["rules"][0]
    rule["match"] = {**rule["match"], "instrument": other.to_dict()}
    config = make_config(instruments=[other.to_dict()], rules=[rule])
    port = RecordingPort()
    result = make_router(db, port, config=config).route(make_intent())

    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_REJECTED
    assert result.code == "EXECUTION_INSTRUMENT_UNRESOLVED"
    assert port.submit_calls == []
    assert parent_row(db) is None


def test_local_receipt_is_not_projected_as_acceptance(db):
    port = RecordingPort(reply={"status": "LOCAL_RECEIVED"})
    result = make_router(db, port).route(make_intent())

    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_LOCAL_RECEIPT
    assert result.parent_ref is None
    assert result.request_id
    assert len(port.submit_calls) == 1


class ObservationReply:
    def __init__(self, outcome, code=None):
        self.outcome = outcome
        self.code = code
        self.parent_ref = None


def test_observation_object_replies_are_mapped_fail_closed(db):
    not_sent = make_router(
        db, RecordingPort(reply=ObservationReply("NOT_SENT", "ROUTE_UNAVAILABLE"))
    )
    first = not_sent.route(make_intent())
    assert first.outcome is ExecutionRoutingOutcome.EXECUTION_UNAVAILABLE
    assert first.code == "ROUTE_UNAVAILABLE"

    unknown = make_router(
        db, RecordingPort(reply=ObservationReply("UNKNOWN", "CALL_OUTCOME_UNKNOWN"))
    )
    second = unknown.route(make_intent(request_key="request-2"))
    assert second.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert second.code == "CALL_OUTCOME_UNKNOWN"
    assert second.request_id != first.request_id


def test_in_flight_parent_state_is_unknown_not_accepted(db):
    port = RecordingPort(reply={"status": "REGISTERING"})
    result = make_router(db, port).route(make_intent())

    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert result.code == "EXECUTION_INVALID_REPLY"
    assert result.execution_state == "REGISTERING"


class FakeGateway:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def register_parent(self, payload):
        self.events.append("gateway.register")
        self.calls.append(deepcopy(payload))
        return {"status": "ACCEPTED"}

    def reserve_child(self, payload):
        return {"status": "RESERVED", "reservation_id": "reservation-1"}

    def dispatch_child(self, payload):
        return {"status": "SENT"}


class FakeRisk:
    def __init__(self, events):
        self.events = events
        self.calls = []

    def available(self):
        return True

    def reserve_parent(self, payload):
        self.events.append("risk.reserve")
        self.calls.append(deepcopy(payload))
        return {"status": "ACCEPTED"}


class ServicePort:
    """Call the committed Execution parent.submit port and record the ordering."""

    def __init__(self, service, db, events):
        self.service = service
        self.db = db
        self.events = events

    def submit(self, request):
        self.events.append("submit")
        row = parent_row(self.db)
        assert row is not None, "SDK PREPARED row must precede parent.submit"
        assert row[3] is None, "the SDK row has no outcome before the remote call"
        return self.service.submit(request)


def _service(tmp_path, events, *, risk=None):
    from bt_api_execution.parent_journal import ParentJournal
    from bt_api_execution.parent_submit_service import ParentSubmitService

    journal = ParentJournal(tmp_path / "parent.sqlite")
    gateway = FakeGateway(events)
    service = ParentSubmitService(journal, gateway_port=gateway, risk_port=risk)
    return journal, gateway, service


def test_composed_execution_service_orders_submit_then_gateway(db, tmp_path):
    events = []
    journal, gateway, service = _service(tmp_path, events)
    try:
        result = make_router(db, ServicePort(service, db, events)).route(make_intent())
        assert result.outcome is ExecutionRoutingOutcome.EXECUTION_ACCEPTED
        assert result.parent_ref is not None
        assert events == ["submit", "gateway.register"]
        assert gateway.calls[0]["request_sha256"] == result.request_sha256
        row = journal.get_parent(result.request_id)
        assert row is not None and row["state"] == "ACCEPTED"
    finally:
        journal.close()


def test_composed_execution_service_orders_submit_then_risk_then_gateway(db, tmp_path):
    events = []
    risk = FakeRisk(events)
    journal, gateway, service = _service(tmp_path, events, risk=risk)
    try:
        result = make_router(db, ServicePort(service, db, events), risk_selected=True).route(
            make_intent()
        )
        assert result.outcome is ExecutionRoutingOutcome.EXECUTION_ACCEPTED
        assert events == ["submit", "risk.reserve", "gateway.register"]
        assert gateway.calls[0]["spec"]["risk_selected"] is True
        assert risk.calls[0]["request_id"] == result.request_id
    finally:
        journal.close()


class ServiceStatePort:
    """Expose the committed Execution ``status`` method as the routing port."""

    def __init__(self, service, db, events):
        self.service = service
        self.db = db
        self.events = events

    def submit(self, request):
        self.events.append("submit")
        return self.service.submit(request)

    def status(self, request_id):
        self.events.append("status")
        return self.service.status(request_id)


def test_composed_execution_status_projects_parent_state(db, tmp_path):
    events = []
    journal, _, service = _service(tmp_path, events)
    port = ServiceStatePort(service, db, events)
    router = make_router(db, port)
    try:
        routed = router.route(make_intent())
        status = router.status(make_intent())
        assert routed.outcome is ExecutionRoutingOutcome.EXECUTION_ACCEPTED
        assert status.routed is True
        assert status.request_id == routed.request_id
        assert status.request_sha256 == routed.request_sha256
        assert status.execution_state == "ACCEPTED"
        assert status.children == ()
        assert status.unknown_children == ()
        assert status.code is None
    finally:
        journal.close()


def test_composed_execution_without_cancel_fails_closed(db, tmp_path):
    events = []
    journal, _, service = _service(tmp_path, events)
    router = make_router(db, ServicePort(service, db, events))
    try:
        assert router.route(make_intent()).outcome is ExecutionRoutingOutcome.EXECUTION_ACCEPTED
        result = router.cancel(make_intent())
        assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNAVAILABLE
        assert result.code == "EXECUTION_CANCEL_UNAVAILABLE"
    finally:
        journal.close()
