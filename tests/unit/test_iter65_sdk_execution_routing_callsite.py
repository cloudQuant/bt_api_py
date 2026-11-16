"""The real ``BtApi.make_order``/``cancel_order`` entry points route to Execution.

These use a synthetic in-memory feed and provider, a fake Execution port and a
temporary SQLite file. No provider service, account, socket or thread is used.
A routed local PREPARED row or a port acknowledgement is a local fact only; it
does not prove Gateway acceptance, Risk approval or venue state.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from bt_api_base.direct_write_protocol import (
    DirectOrderReservation,
    DirectSubmitResult,
    DirectWriteErrorPolicy,
    NativeOrderIdentity,
    PreDispatchRecord,
    WriteOperation,
)
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

import bt_api_py.bt_api as facade_module
from bt_api_py._contracts.models import CancelOrderRequest, OrderRequest, OrderType, Side
from bt_api_py.bt_api import BtApi, ExecutionRoutingSelection
from bt_api_py.execution_routing import (
    ExecutionCancelTarget,
    ExecutionOrderRouter,
    ExecutionRoutingError,
    ExecutionRoutingOutcome,
    ExecutionRoutingResult,
    install_execution_routing_tables,
    parse_execution_routing,
)
from bt_api_py.gateway_direct_client import GatewayClientRoute, build_gateway_order_intent

ROUTE = "FAKE___FUTURE"
KEY = MarketInstrumentKey("ctp", "FAKE", "simnow", "future", "fake-symbol", "fake-symbol")
IDENTITY = NativeOrderIdentity(
    exchange_name=ROUTE,
    client_order_id="fake-101",
    session_generation=3,
    fields={
        "order_ref": "fake-101",
        "front_id": 1,
        "session_id": 7,
        "trading_day": "20261106",
        "exchange_id": "FAKE",
    },
)


def routing_raw(**overrides):
    value = {
        "schema_version": 2,
        "algorithm_build_id": "build-1",
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
                "schedule_binding": {"quantity_step": "1", "price_step": "1", "retry_limit": 2},
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


def make_binding():
    return GatewayCommandBinding("journal", "acct", "strategy", "principal-a")


def make_route():
    return GatewayClientRoute(
        "broker.direct_write",
        "gw-service",
        "gw-instance",
        "gw-scope",
        WireScope("paper", account_ref="acct", strategy_ref="strategy", run_id="run-1"),
        WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy"),
    )


def make_order_request(**overrides):
    value = {
        "symbol": "fake-symbol",
        "side": Side.BUY,
        "order_type": OrderType.LIMIT,
        "quantity": Decimal("1"),
        "price": Decimal("10"),
        "account_id": "acct",
        "client_order_id": "fake-101",
        "idempotency_key": "request-1",
        "quantity_unit": "lots",
        "exchange_id": "FAKE",
        "offset": "open",
    }
    value.update(overrides)
    return OrderRequest(**value)


def make_cancel_request(**overrides):
    value = {
        "symbol": "fake-symbol",
        "account_id": "acct",
        "client_order_id": "fake-101",
        "idempotency_key": "request-1",
        "exchange_id": "FAKE",
    }
    value.update(overrides)
    return CancelOrderRequest(**value)


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


class RecordingExecutionPort:
    """Record the exact port calls; every event is a fake, never a service."""

    def __init__(self, *, db=None, reply=None, cancel_reply=None, events=None, on_submit=None):
        self.db = db
        self.submit_reply = (
            {"status": "ACCEPTED", "parent_ref": "parent-1"} if reply is None else reply
        )
        self.cancel_reply = {"status": "ACCEPTED"} if cancel_reply is None else cancel_reply
        self.events = events if events is not None else []
        self.submit_calls = []
        self.cancel_calls = []
        self.on_submit = on_submit

    def submit(self, request):
        self.events.append("submit")
        self.submit_calls.append(request)
        if self.on_submit is not None:
            self.on_submit(request)
        if isinstance(self.submit_reply, BaseException):
            raise self.submit_reply
        return self.submit_reply

    def cancel(self, payload):
        self.events.append("cancel")
        self.cancel_calls.append(payload)
        if isinstance(self.cancel_reply, BaseException):
            raise self.cancel_reply
        return self.cancel_reply


def make_router(db, port, *, config=None, clock=lambda: 1):
    return ExecutionOrderRouter(
        db=db,
        config=make_config() if config is None else config,
        execution_port=port,
        risk_selected=False,
        clock=clock,
    )


def make_selection(router, *, ttl_seconds=10.0):
    return ExecutionRoutingSelection(
        router=router,
        binding=make_binding(),
        route=make_route(),
        ttl_seconds=ttl_seconds,
    )


class FakeFeed:
    def __init__(self):
        self.disconnected = False

    def disconnect(self):
        self.disconnected = True


class FakeProvider:
    """The existing direct-write provider double: nothing leaves the process."""

    direct_write_error_policy = DirectWriteErrorPolicy(frozenset(), frozenset())

    def __init__(self, events):
        self.events = events
        self.reserved = []
        self.submitted = []
        self.cancelled = []

    def session_generation(self):
        return 3

    def reserve_order(self, request):
        self.events.append("reserve")
        self.reserved.append(request)
        return DirectOrderReservation(identity=IDENTITY, symbol="fake-symbol", reservation_id="r1")

    def release_reservation(self, reservation):
        self.events.append("release")

    def submit_order(self, reservation, request, grant, verifier):
        self.events.append("submit")
        self.submitted.append(request)
        verifier.consume(
            grant,
            exchange_name=ROUTE,
            operation=WriteOperation.PLACE,
            session_generation=3,
        )
        return DirectSubmitResult(sent=True, identity=reservation.identity)

    def cancel_order(self, target, symbol, grant, verifier):
        self.events.append("cancel")
        self.cancelled.append((target, symbol))
        verifier.consume(
            grant,
            exchange_name=ROUTE,
            operation=WriteOperation.CANCEL,
            session_generation=3,
        )
        return DirectSubmitResult(sent=True, identity=target)

    def close(self):
        self.events.append("provider_close")


class CommitPort:
    def __init__(self, events):
        self.events = events

    def commit(self, record):
        assert isinstance(record, PreDispatchRecord)
        self.events.append("commit")
        return "marker"


@pytest.fixture
def fake_facade(monkeypatch):
    """Build the real BtApi over a synthetic feed and provider double."""

    class Harness:
        def __init__(self):
            self.feed = FakeFeed()
            self.events = []
            self.provider = FakeProvider(self.events)
            self.apis = []

        def set_fakes(self):
            harness = self

            def create_feed(name, queue, **settings):
                assert name == ROUTE
                harness.events.append("feed_create")
                return harness.feed

            def load_entrypoint(group, name, *, optional=False):
                assert group == "bt_api.direct_write_providers" and name == ROUTE and optional
                harness.events.append("discovery")

                def factory(selected_feed):
                    assert selected_feed is harness.feed
                    harness.events.append("provider_create")
                    return harness.provider

                return factory

            monkeypatch.setattr(facade_module, "_ensure_plugins_loaded", lambda: None)
            monkeypatch.setattr(facade_module.ExchangeRegistry, "create_feed", create_feed)
            monkeypatch.setattr(
                "bt_api_py.provider_entrypoints.load_route_entrypoint", load_entrypoint
            )

        def api(self, selection=None):
            self.set_fakes()
            kwargs = {"write_admission": {ROUTE: True}}
            if selection is not None:
                kwargs["execution_order_routing"] = selection
            api = BtApi({ROUTE: {"synthetic": True}}, debug=False, **kwargs)
            self.apis.append(api)
            # Construction discovery is not a write; each test observes writes.
            self.events.clear()
            return api

        def close(self):
            for api in self.apis:
                api.close()

    harness = Harness()
    try:
        yield harness
    finally:
        harness.close()


# ---------------------------------------------------------------------------
# make_order
# ---------------------------------------------------------------------------


def test_matched_make_order_persists_then_submits_and_never_reaches_direct(db, fake_facade):
    observed = {}
    port = RecordingExecutionPort(
        db=db,
        on_submit=lambda request: observed.update(row=parent_row(db), request=dict(request)),
    )
    api = fake_facade.api(make_selection(make_router(db, port)))

    result = api.make_order(
        ROUTE,
        make_order_request(),
        pre_dispatch_commit=CommitPort(fake_facade.events),
        host_scope="scope-1",
    )

    assert isinstance(result, ExecutionRoutingResult)
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert result.code == "EXECUTION_UNVERIFIED_REPLY"
    assert result.parent_ref is None
    assert result.request_id and len(result.request_sha256) == 64
    # The durable PREPARED row existed before the one remote call.
    assert observed["row"] is not None and observed["row"][3] is None
    assert observed["request"]["request_id"] == result.request_id
    assert len(port.submit_calls) == 1 and port.events == ["submit"]
    # The existing direct implementation was never entered.
    assert fake_facade.events == []
    assert fake_facade.provider.reserved == []
    assert parent_row(db)[3] is not None


def test_unmatched_make_order_keeps_the_existing_direct_implementation_once(db, fake_facade):
    rule = routing_raw()["rules"][0]
    rule["match"] = {**rule["match"], "side": "sell"}
    port = RecordingExecutionPort(db=db)
    api = fake_facade.api(make_selection(make_router(db, port, config=make_config(rules=[rule]))))

    outcome = api.make_order(
        ROUTE,
        make_order_request(),
        pre_dispatch_commit=CommitPort(fake_facade.events),
        host_scope="scope-1",
    )

    assert outcome.sent is True and outcome.marker_id == "marker"
    assert fake_facade.events.count("reserve") == 1
    assert fake_facade.events.count("commit") == 1
    assert fake_facade.events.count("submit") == 1
    assert port.submit_calls == []
    assert parent_row(db) is None


def test_unselected_make_order_is_unchanged(fake_facade):
    api = fake_facade.api()

    outcome = api.make_order(
        ROUTE,
        make_order_request(),
        pre_dispatch_commit=CommitPort(fake_facade.events),
        host_scope="scope-1",
    )

    assert outcome.sent is True and outcome.marker_id == "marker"
    assert fake_facade.events.count("submit") == 1


def test_matched_but_unavailable_execution_never_falls_back_to_direct(db, fake_facade):
    api = fake_facade.api(make_selection(make_router(db, None)))

    result = api.make_order(
        ROUTE,
        make_order_request(),
        pre_dispatch_commit=CommitPort(fake_facade.events),
        host_scope="scope-1",
    )

    assert isinstance(result, ExecutionRoutingResult)
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNAVAILABLE
    assert result.code == "EXECUTION_UNAVAILABLE"
    assert fake_facade.events == []
    assert parent_row(db) is None


def test_submit_exception_is_unknown_and_is_never_retried_or_sent_directly(db, fake_facade):
    port = RecordingExecutionPort(db=db, reply=TimeoutError("reply lost"))
    api = fake_facade.api(make_selection(make_router(db, port)))

    first = api.make_order(
        ROUTE,
        make_order_request(),
        pre_dispatch_commit=CommitPort(fake_facade.events),
        host_scope="scope-1",
    )
    second = api.make_order(
        ROUTE,
        make_order_request(),
        pre_dispatch_commit=CommitPort(fake_facade.events),
        host_scope="scope-1",
    )

    assert first.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert second == first
    assert len(port.submit_calls) == 1
    assert fake_facade.events == []


def test_unknown_reply_is_projected_without_a_direct_send(db, fake_facade):
    port = RecordingExecutionPort(db=db, reply={"status": "REGISTERING"})
    api = fake_facade.api(make_selection(make_router(db, port)))

    result = api.make_order(
        ROUTE,
        make_order_request(),
        pre_dispatch_commit=CommitPort(fake_facade.events),
        host_scope="scope-1",
    )

    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert result.code == "EXECUTION_UNVERIFIED_REPLY"
    assert result.execution_state is None
    assert fake_facade.events == []


def test_matched_request_without_a_stable_intent_fails_closed(db, fake_facade):
    port = RecordingExecutionPort(db=db)
    api = fake_facade.api(make_selection(make_router(db, port)))

    with pytest.raises(ExecutionRoutingError) as info:
        api.make_order(
            ROUTE,
            make_order_request(idempotency_key=""),
            pre_dispatch_commit=CommitPort(fake_facade.events),
            host_scope="scope-1",
        )

    assert info.value.code == "INVALID_ORDER_INTENT"
    assert port.submit_calls == []
    assert fake_facade.events == []


# ---------------------------------------------------------------------------
# cancel_order
# ---------------------------------------------------------------------------


def _place(db, api, fake_facade):
    return api.make_order(
        ROUTE,
        make_order_request(),
        pre_dispatch_commit=CommitPort(fake_facade.events),
        host_scope="scope-1",
    )


def test_matched_cancel_routes_to_the_execution_cancel_port(db, fake_facade):
    port = RecordingExecutionPort(db=db)
    api = fake_facade.api(make_selection(make_router(db, port)))
    placed = _place(db, api, fake_facade)

    result = api.cancel_order(
        ROUTE,
        make_cancel_request(),
        pre_dispatch_commit=CommitPort(fake_facade.events),
        target_identity=IDENTITY,
        host_scope="scope-1",
    )

    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert result.code == "EXECUTION_UNVERIFIED_REPLY"
    assert len(port.cancel_calls) == 1
    payload = port.cancel_calls[0]
    assert payload["request_id"] == placed.request_id
    assert payload["request_sha256"] == placed.request_sha256
    assert payload["operation_id"].startswith("sdk-cancel-v1:")
    assert fake_facade.events == []
    assert fake_facade.provider.cancelled == []


def test_routed_cancel_accepts_an_explicit_stable_request_key(db, fake_facade):
    port = RecordingExecutionPort(db=db)
    api = fake_facade.api(make_selection(make_router(db, port)))
    placed = _place(db, api, fake_facade)

    result = api.cancel_order(
        ROUTE,
        "fake-symbol",
        pre_dispatch_commit=CommitPort(fake_facade.events),
        target_identity=IDENTITY,
        host_scope="scope-1",
        request_key="request-1",
    )

    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert result.code == "EXECUTION_UNVERIFIED_REPLY"
    assert port.cancel_calls[0]["request_id"] == placed.request_id
    assert fake_facade.events == []


def test_matched_cancel_without_an_execution_port_is_finite_and_never_direct(db, fake_facade):
    api = fake_facade.api(make_selection(make_router(db, RecordingExecutionPort(db=db))))
    placed = _place(db, api, fake_facade)
    fake_facade.events.clear()

    unavailable = ExecutionOrderRouter(
        db=db,
        config=make_config(),
        execution_port=None,
        risk_selected=False,
        clock=lambda: 1,
    )
    cancel_api = fake_facade.api(make_selection(unavailable))
    result = cancel_api.cancel_order(
        ROUTE,
        make_cancel_request(),
        pre_dispatch_commit=CommitPort(fake_facade.events),
        target_identity=IDENTITY,
        host_scope="scope-1",
    )

    assert placed.request_id
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_UNAVAILABLE
    assert result.code == "EXECUTION_CANCEL_UNAVAILABLE"
    assert fake_facade.events == []
    assert fake_facade.provider.cancelled == []
    assert parent_row(db)[4] is None


def test_unmatched_cancel_keeps_the_existing_direct_path(db, fake_facade):
    port = RecordingExecutionPort(db=db)
    api = fake_facade.api(make_selection(make_router(db, port)))

    outcome = api.cancel_order(
        ROUTE,
        make_cancel_request(idempotency_key="never-routed"),
        pre_dispatch_commit=CommitPort(fake_facade.events),
        target_identity=IDENTITY,
        host_scope="scope-1",
    )

    assert outcome.sent is True and outcome.marker_id == "marker"
    assert fake_facade.events.count("cancel") == 1
    assert fake_facade.provider.cancelled[0][1] == "fake-symbol"
    assert port.cancel_calls == []


def test_unselected_cancel_is_unchanged(fake_facade):
    api = fake_facade.api()

    outcome = api.cancel_order(
        ROUTE,
        "fake-symbol",
        pre_dispatch_commit=CommitPort(fake_facade.events),
        target_identity=IDENTITY,
        host_scope="scope-1",
    )

    assert outcome.sent is True and outcome.marker_id == "marker"
    assert fake_facade.events.count("cancel") == 1


def test_selected_cancel_without_a_stable_key_is_finite_and_never_direct(db, fake_facade):
    port = RecordingExecutionPort(db=db)
    api = fake_facade.api(make_selection(make_router(db, port)))

    result = api.cancel_order(
        ROUTE,
        "fake-symbol",
        pre_dispatch_commit=CommitPort(fake_facade.events),
        target_identity=IDENTITY,
        host_scope="scope-1",
    )

    assert isinstance(result, ExecutionRoutingResult)
    assert result.outcome is ExecutionRoutingOutcome.EXECUTION_REJECTED
    assert result.code == "EXECUTION_CANCEL_IDENTITY_UNAVAILABLE"
    assert fake_facade.events == []
    assert port.cancel_calls == []


def test_cancel_target_looks_up_the_key_and_never_resends(db):
    """The additive key-based hook replays one operation id and never resends."""
    port = RecordingExecutionPort(db=db)
    router = make_router(db, port)
    intent = build_gateway_order_intent(make_binding(), make_route(), make_order_request())
    routed = router.route(intent)

    target = ExecutionCancelTarget(request_key="request-1", run_id="run-1")
    first = router.cancel_target(target)
    second = router.cancel_target(target)

    assert first.outcome is ExecutionRoutingOutcome.EXECUTION_UNKNOWN
    assert first.code == "EXECUTION_UNVERIFIED_REPLY"
    assert second == first
    assert len(port.cancel_calls) == 1
    assert first.request_id == routed.request_id
    assert first.request_sha256 == routed.request_sha256
    assert port.submit_calls and first.request_id

    unknown = router.cancel_target(ExecutionCancelTarget(request_key="other", run_id="run-1"))
    assert unknown.outcome is ExecutionRoutingOutcome.DIRECT_PATH
    with pytest.raises(ExecutionRoutingError) as info:
        router.cancel_target(ExecutionCancelTarget(request_key="", run_id="run-1"))
    assert info.value.code == "INVALID_CANCEL_TARGET"


# ---------------------------------------------------------------------------
# import isolation
# ---------------------------------------------------------------------------


def test_unselected_import_does_not_load_routing_or_execution_packages():
    repo = Path(__file__).resolve().parents[2]
    code = (
        "import sys\n"
        "import bt_api_py.bt_api\n"
        "bad = [n for n in sorted(sys.modules) if n == 'bt_api_py.execution_routing'"
        " or n == 'bt_api_py.gateway_direct_client'"
        " or n == 'bt_api_execution' or n.startswith('bt_api_execution.')"
        " or n == 'bt_api_risk' or n.startswith('bt_api_risk.')]\n"
        "assert bad == [], bad\n"
    )
    env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
