"""Gateway ports of the Risk service process (64.09). UNIT/FAKE evidence: no Gateway, no account."""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole

from bt_api_py.risk_service_config import RiskInstrumentBinding
from bt_api_py.risk_service_gateway import (
    DelegateOrderPort,
    GatewayControlAdapter,
    ObservationPump,
    RiskFactsCache,
    account_snapshot_from_observation,
    map_delegate_result,
    observed_event_from_account_event,
    price_fact_from_market_event,
    strategy_financials_from_reply,
)

ACCOUNT = "account-a"
RB = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb")
RISK = WireIdentity(WireRole.SERVICE, "risk-principal", "risk-actor")
EPOCH = "e" * 64


def obs(state="RESERVED", version=None, digest="d" * 64):
    return SimpleNamespace(state=state, reservation_id="res-1", digest=digest,
                           version=version or {"RESERVED": 1, "DISPATCHING": 2, "SENT": 3,
                                               "NOT_SENT": 3, "UNKNOWN": 3, "RELEASED": 2,
                                               "EXPIRED": 2}[state],
                           owner_generation="g1", reservation_generation="g1")


def result(outcome, observation=None, code=None):
    return SimpleNamespace(outcome=outcome, observation=observation, code=code)


# ---------------------------------------------------------------------------- outcome mapping


@pytest.mark.parametrize("outcome", ["REQUEST_DENIED", "TRANSPORT_NOT_STARTED", "UNKNOWN"])
def test_client_outcomes_pass_through_exactly(outcome):
    reply = map_delegate_result(result(outcome, code="SOME_CODE"))
    assert reply.outcome == outcome and reply.code == "SOME_CODE" and reply.state is None


def test_observed_carries_the_gateway_proof_fields():
    reply = map_delegate_result(result("OBSERVED", obs("RELEASED")))
    assert (reply.outcome, reply.state, reply.reservation_id, reply.digest, reply.version) == (
        "OBSERVED", "RELEASED", "res-1", "d" * 64, 2)
    assert reply.owner_generation == reply.reservation_generation == "g1"


@pytest.mark.parametrize("value", [
    result("OBSERVED", None), result("OBSERVED", SimpleNamespace(state="BOGUS")),
    result("SENT_MAYBE"), object(), result("QUEUED"),
])
def test_anything_unproven_is_unknown(value):
    assert map_delegate_result(value).outcome == "UNKNOWN"


# ---------------------------------------------------------------------------- delegate port


class FakeClient:
    def __init__(self, host):
        self.host, self.calls = host, []
        self.actor_state = "RESERVED"
        self.dispatch_result = result("QUEUED")
        self.release_result = SimpleNamespace(outcome="OBSERVED", state="RELEASED", code=None)

    def reserve(self, command, request, *, ttl_seconds, timeout_seconds):
        self.calls.append(("reserve", command, request, ttl_seconds))
        return result("OBSERVED", obs("RESERVED"))

    def dispatch(self, request_key, *, market_ref, timeout_seconds):
        self.calls.append(("dispatch", request_key, market_ref))
        return self.dispatch_result

    def actor_status(self, request_key, *, timeout_seconds):
        self.calls.append(("status", request_key))
        self.last = SimpleNamespace(command=obs(self.actor_state), native_identity_digest=None)
        return result("OBSERVED", self.last)

    def release_actor(self, observation, *, operation_id, timeout_seconds):
        assert observation is self.last
        self.calls.append(("release", operation_id))
        self.host.saved = obs("RELEASED")
        return self.release_result


class FakeHost:
    saved = None

    def load_delegate_observation(self, _key):
        return self.saved if self.saved is not None else obs("RESERVED")


class Handle:
    def __init__(self, route):
        self.route, self.host = route, FakeHost()
        self.client, self.closed = FakeClient(self.host), False

    def close(self):
        self.closed = True


def port(connect=None, clock=None):
    handles = []

    def default(route):
        handle = Handle(route)
        handles.append(handle)
        return handle

    value = DelegateOrderPort(
        account_scope=ACCOUNT, execution_domain_ref="paper:demo", identity=RISK,
        service_id="gateway", instance_id="gateway-a", scope_ref="scope-a",
        grant_revisions={"strategy-a": "risk-v1"},
        instrument_for=lambda venue, native: RB if (venue, native) == ("SHFE", "rb") else None,
        connect=connect or default, timeout_seconds=5, retry_seconds=10,
        monotonic=clock or (lambda: 0.0),
    )
    return value, handles


def canonical(key="k1"):
    from bt_api_base.gateway_direct_protocol import canonical_gateway_payload

    from bt_api_py._contracts.models import OrderRequest, OrderType, Side

    request = OrderRequest(symbol="rb", side=Side.BUY, order_type=OrderType.LIMIT,
                           quantity=Decimal("1"), price=Decimal("10"), account_id=ACCOUNT,
                           client_order_id="c", idempotency_key=key, offset="open",
                           quantity_unit="lots", exchange_id="SHFE")
    return canonical_gateway_payload(request.to_dict())


def test_one_delegate_client_per_strategy_run_and_a_new_run_retires_the_old(tmp_path):
    value, handles = port()
    value.note_run("strategy-a", "run-1")
    reply = value.reserve(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                          canonical_payload=canonical(), parent_ref=None, child_id=None,
                          ttl_seconds=10)
    assert reply.outcome == "OBSERVED" and reply.state == "RESERVED"
    (handle,) = handles
    assert handle.route.scope.run_id == "run-1" and handle.route.scope.strategy_ref == "strategy-a"
    _, command, _, ttl = handle.client.calls[0]
    assert command.run_id == "run-1" and command.actor_kind == "risk" and ttl == 10
    assert command.actor_principal == RISK.principal_ref and command.grant_revision == "risk-v1"
    assert command.instrument == RB
    # Same run: same client.
    value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    assert len(handles) == 1
    # The strategy registered a new run: the old client is closed, the old run gets none.
    value.note_run("strategy-a", "run-2")
    assert handle.closed
    old = value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    assert old.outcome == "TRANSPORT_NOT_STARTED" and len(handles) == 1
    value.status(strategy_ref="strategy-a", run_id="run-2", request_key="k1")
    assert len(handles) == 2 and handles[1].route.scope.run_id == "run-2"
    # A strategy without a grant revision never gets a client.
    assert value.status(strategy_ref="strategy-z", run_id="run-1",
                        request_key="k1").outcome == "TRANSPORT_NOT_STARTED"
    value.close()
    assert handles[1].closed


def test_failed_registration_is_not_retried_before_the_interval():
    now = [0.0]
    attempts = []

    def connect(route):
        attempts.append(route)
        raise RuntimeError("refused")

    value, _ = port(connect=connect, clock=lambda: now[0])
    first = value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    assert first.outcome == "TRANSPORT_NOT_STARTED" and first.code == "ROUTE_UNAVAILABLE"
    now[0] = 5.0
    value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    assert len(attempts) == 1
    now[0] = 10.5
    value.status(strategy_ref="strategy-a", run_id="run-1", request_key="k1")
    assert len(attempts) == 2


def test_dispatch_queued_is_dispatching_and_other_outcomes_are_exact():
    value, handles = port()
    reply = value.dispatch(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                           market_ref={"key": {}, "epoch": "e", "sequence": 1, "message_id": "m"})
    assert (reply.outcome, reply.state, reply.reservation_id) == ("QUEUED", "DISPATCHING", "res-1")
    assert handles[0].client.calls[0][2]["sequence"] == 1
    handles[0].client.dispatch_result = result("UNKNOWN", code="DISPATCH_ALREADY_ATTEMPTED")
    reply = value.dispatch(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                           market_ref=None)
    assert reply.outcome == "UNKNOWN" and reply.code == "DISPATCH_ALREADY_ATTEMPTED"


def test_release_returns_the_verified_terminal_observation_only():
    value, handles = port()
    reply = value.release(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                          operation_id="risk-release-1")
    assert (reply.outcome, reply.state, reply.digest) == ("OBSERVED", "RELEASED", "d" * 64)
    client = handles[0].client
    assert [c[0] for c in client.calls] == ["status", "release"]
    client.release_result = SimpleNamespace(outcome="UNKNOWN", state=None, code="X")
    assert value.release(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                         operation_id="r2").outcome == "UNKNOWN"
    # Not RESERVED any more: no release call, the status itself is the answer.
    client.actor_state = "SENT"
    calls = len(client.calls)
    reply = value.release(strategy_ref="strategy-a", run_id="run-1", request_key="k1",
                          operation_id="r3")
    assert reply.state == "SENT" and len(client.calls) == calls + 1


# ---------------------------------------------------------------------------- control port


class Runtime:
    def __init__(self, replies, registered=True):
        self.replies, self.calls, self.registered = list(replies), [], registered

    def status(self):
        binding = SimpleNamespace(active=True, mount="application.direct_write",
                                  service_id="gateway", instance_id="gateway-a",
                                  scope_ref="scope-a", operations=frozenset({
                                      "direct.control.apply", "direct.control.status",
                                      "direct.control.cancel_scope",
                                      "direct.control.cancel_scope_status"}))
        return SimpleNamespace(state="running", healthy=True,
                               bindings=[binding] if self.registered else [])

    def call(self, mount, operation, arguments, **kw):
        self.calls.append((operation, arguments))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def control(replies, registered=True):
    runtime = Runtime(replies, registered)
    return GatewayControlAdapter(runtime, mount="application.direct_write", service_id="gateway",
                                 instance_id="gateway-a", scope_ref="scope-a",
                                 account_ref=ACCOUNT, timeout_seconds=5), runtime


def test_control_apply_instrument_scope_and_reply_mapping():
    adapter, runtime = control([{"status": "OBSERVED", "cause_id": "c1", "version": 1,
                                 "active": True}])
    reply = adapter.apply(cause_id="c1", mode="STOP_NEW", scope_kind="instrument",
                          scope_value="SHFE:rb")
    assert (reply.outcome, reply.active, reply.version) == ("OBSERVED", True, 1)
    assert runtime.calls == [("direct.control.apply",
                              {"cause_id": "c1", "mode": "STOP_NEW", "instrument": "SHFE:rb"})]


def test_control_strategy_scope_is_refused_locally_and_never_sent():
    adapter, runtime = control([])
    reply = adapter.apply(cause_id="c1", mode="FREEZE", scope_kind="strategy",
                          scope_value="strategy-a")
    assert reply.outcome == "REQUEST_DENIED" and reply.code == "RISK_CONTROL_SCOPE_UNSUPPORTED"
    assert adapter.cancel_scope(operation_id="o", scope_kind="strategy",
                                scope_value="strategy-a").outcome == "REQUEST_DENIED"
    assert runtime.calls == []


def test_control_refusals_unknowns_and_not_registered():
    adapter, _ = control([{"status": "REQUEST_REJECTED", "code": "SCOPE_DENIED"},
                          {"status": "UNKNOWN"}, RuntimeError("lost"),
                          {"status": "NOT_FOUND", "cause_id": "c1"}])
    assert adapter.apply(cause_id="c1", mode="STOP_NEW", scope_kind="account",
                         scope_value="").code == "SCOPE_DENIED"
    assert adapter.apply(cause_id="c1", mode="STOP_NEW", scope_kind="account",
                         scope_value="").outcome == "UNKNOWN"
    assert adapter.status(cause_id="c1").outcome == "UNKNOWN"
    absent = adapter.status(cause_id="c1")
    assert absent.outcome == "OBSERVED" and absent.active is False
    unregistered, runtime = control([], registered=False)
    assert unregistered.apply(cause_id="c1", mode="STOP_NEW", scope_kind="account",
                              scope_value="").outcome == "TRANSPORT_NOT_STARTED"
    assert runtime.calls == []


# ---------------------------------------------------------------------------- facts


def binding():
    return RiskInstrumentBinding("rb", RB, Decimal(10), Decimal(1))


def test_facts_cache_instrument_facts_staleness_and_trading_day():
    now = [1_000]
    facts = RiskFactsCache({"rb": binding()}, now_ns=lambda: now[0], account_max_age_ns=100,
                           financial_max_age_ns=100)
    assert facts.instrument_facts(RB).multiplier == Decimal(10)
    other = MarketInstrumentKey("ctp", "SHFE", "simnow", "future", "rb", "rb2")
    assert facts.instrument_facts(other) is None
    assert facts.trading_day() is None
    facts.note_event_day("20300101")
    facts.note_tick("rb", None, None, "20291231")
    assert facts.trading_day() == "20300101"
    facts.note_tick("rb", None, None, "20300102")
    assert facts.trading_day() == "20300102"
    from bt_api_risk.pretrade import AccountSnapshot

    facts.set_account(AccountSnapshot(complete=True, positions={}))
    assert facts.account_snapshot().complete is True
    now[0] += 101
    assert facts.account_snapshot().complete is False


def test_strategy_financial_mapping_fails_closed():
    reply = {"status": "OBSERVED", "schema_version": 1, "account_scope": ACCOUNT,
             "owner_generation": "g", "complete": True, "strategies": [
                 {"strategy": "s1", "currency": "CNY", "status": "OBSERVED", "value": "100.5",
                  "cash": "1"},
                 {"strategy": "s2", "currency": "CNY", "status": "UNAVAILABLE"},
                 {"strategy": "s3", "currency": "CNY", "status": "OBSERVED", "value": "1"},
                 {"strategy": "s3", "currency": "USD", "status": "OBSERVED", "value": "2"}]}
    values = strategy_financials_from_reply(reply, account_scope=ACCOUNT,
                                            trading_day="20300101", now_ns=5)
    assert set(values) == {"s1"}
    fact = values["s1"]
    assert (fact.value, fact.trading_day, fact.complete, fact.day_start_value) == (
        Decimal("100.5"), "20300101", True, None)
    assert not strategy_financials_from_reply(dict(reply, complete=False), account_scope=ACCOUNT,
                                              trading_day="20300101", now_ns=5)["s1"].complete
    assert not strategy_financials_from_reply(reply, account_scope=ACCOUNT, trading_day=None,
                                              now_ns=5)["s1"].complete
    with pytest.raises(ValueError):
        strategy_financials_from_reply(dict(reply, account_scope="other"), account_scope=ACCOUNT,
                                       trading_day="20300101", now_ns=5)


def test_account_snapshot_mapping_marks_unprovable_rows_incomplete():
    def position(side, quantity, today=None, yesterday=None, venue="SHFE"):
        instrument = MarketInstrumentKey("ctp", venue, "simnow", "future", "rb", "rb")
        return SimpleNamespace(side=side, quantity=quantity, today_quantity=today,
                               yesterday_quantity=yesterday, instrument=instrument)

    def observation(*positions):
        return SimpleNamespace(
            positions_complete=True, balances_complete=True, positions=positions,
            balances=(SimpleNamespace(total="100", available="90", currency="CNY"),),
            valuation=None, completed_at_utc="2030-01-01T01:00:00Z")

    ids = {("SHFE", "rb"): "rb"}
    full = account_snapshot_from_observation(observation(position("long", "3", "1", "2")),
                                             instrument_ids=ids, dated_venues=frozenset({"SHFE"}))
    assert full.complete and full.position("rb", "LONG") == (Decimal(1), Decimal(2))
    assert full.balance == Decimal(100) and full.available == Decimal(90)
    assert full.margin is None and full.close_profit is None and full.trading_day is None
    no_split = account_snapshot_from_observation(observation(position("long", "3")),
                                                 instrument_ids=ids,
                                                 dated_venues=frozenset({"SHFE"}))
    assert not no_split.complete
    net = account_snapshot_from_observation(observation(position("net", "3", "1", "2")),
                                            instrument_ids=ids, dated_venues=frozenset())
    assert not net.complete


def test_market_event_maps_to_price_and_own_market_reference(monkeypatch):
    import bt_api_base.market_tick_protocol as ticks

    key = MarketSubscriptionKey("src", "ent", RB, "tick")
    tick = SimpleNamespace(instrument=RB, last_price=3601.0, provider_event_time_utc=
                           "2030-01-01T01:00:00Z", quality_contract_ref="q",
                           continuity_status="continuous", trading_date="20300101")
    monkeypatch.setattr(ticks.MarketTickPayload, "from_dict", classmethod(lambda cls, raw: tick))
    event = SimpleNamespace(payload={"key": key.to_dict(), "data": {}},
                            header=SimpleNamespace(stream_epoch="ep", sequence=7, message_id="m7"))
    received = SimpleNamespace(event=event, receipt=SimpleNamespace(received_wall_ns=99))
    fact, ref, day = price_fact_from_market_event(received, key)
    assert fact.last_price == Decimal("3601.0") and fact.quality_ok is True
    assert fact.received_time_ns == 99 and day == "20300101"
    assert ref == {"key": key.to_dict(), "epoch": "ep", "sequence": 7, "message_id": "m7"}
    foreign = SimpleNamespace(event=SimpleNamespace(payload={"key": {}, "data": {}},
                                                    header=event.header), receipt=None)
    with pytest.raises(ValueError):
        price_fact_from_market_event(foreign, key)


# ---------------------------------------------------------------------------- observation pump


def account_event(sequence, *, principal="other", key="k", status="accepted", day="20300101",
                  side="buy", quantity="1"):
    observation = SimpleNamespace(
        kind=DirectExecutionEventKind.ORDER, side=side, offset="open", order_quantity=quantity,
        filled_quantity="0", limit_price="10", status=DirectOrderStatus(status), trade_quantity=None,
        trade_price=None, trade_key_digest=None, trading_day=day, venue_key_digest=None,
        instrument=RB)
    event = SimpleNamespace(sequence=sequence, request_key=key, reservation_id="r" + str(sequence),
                            command_principal=principal, observation=observation)
    return SimpleNamespace(strategy_scope="strategy-a", event=event)


def page(events, *, epoch=EPOCH, has_more=False):
    progress = SimpleNamespace(epoch=epoch, offered_sequence=events[-1].event.sequence if events else 0)
    return SimpleNamespace(outcome="OBSERVED", code=None,
                           page=SimpleNamespace(progress=progress, events=tuple(events),
                                                has_more=has_more))


class Observer:
    def __init__(self, pages, offered=0, epoch=EPOCH):
        self.pages, self.reads, self.acks = list(pages), [], []
        self.offered, self.epoch = offered, epoch

    def progress(self, *, timeout_seconds):
        return SimpleNamespace(outcome="OBSERVED", code=None, progress=SimpleNamespace(
            epoch=self.epoch, offered_sequence=self.offered))

    def read_after(self, **kw):
        self.reads.append(kw)
        return self.pages.pop(0)

    def acknowledge(self, sequence, *, expected_epoch, timeout_seconds):
        self.acks.append(sequence)
        return SimpleNamespace(outcome="OBSERVED")


class Engine:
    def __init__(self, fail=False):
        self.applied, self.resets, self.gaps, self.fail = [], [], [], fail
        self.last = -1

    def reset_observations(self, epoch, head):
        self.resets.append((epoch, head))
        self.last = -1

    def mark_observation_gap(self, reason):
        self.gaps.append(reason)

    def apply_observations(self, events):
        if self.fail:
            raise RuntimeError("storage lost")
        self.applied.extend(events)
        self.last = max([self.last] + [e.sequence for e in events])
        return self.last


def pump(engine, observer, facts=None):
    return ObservationPump(engine, observer, facts or RiskFactsCache({}),
                           risk_principal=RISK.principal_ref,
                           intent_payload=lambda key: None, timeout_seconds=5)


def test_pump_resets_maps_sequences_and_acks_only_what_was_applied():
    engine, facts = Engine(), RiskFactsCache({})
    observer = Observer([page([account_event(1, principal=RISK.principal_ref, key="mine"),
                               account_event(2)], has_more=True), page([account_event(3)])],
                        offered=2)
    value = pump(engine, observer, facts)
    assert value.poll() == 3
    assert engine.resets == [(EPOCH, 1)]  # head = offered - 1 (engine counts from 0)
    assert [e.sequence for e in engine.applied] == [0, 1, 2]
    assert engine.applied[0].origin == "risk:" + RISK.principal_ref
    assert engine.applied[1].origin == "self" and engine.applied[1].side == "BUY"
    assert observer.acks == [2, 3] and observer.reads[1]["after_sequence"] == 2
    assert facts.trading_day() == "20300101" and value.verified_epoch == EPOCH


def test_a_restarted_pump_marks_the_persisted_projection_unverified_before_replay():
    engine = Engine()
    observer = Observer([], offered=0)
    observer.progress = lambda **kw: SimpleNamespace(outcome="UNKNOWN", code="LOST", progress=None)
    value = pump(engine, observer)
    assert value.poll() == 0
    assert engine.gaps == ["observation_restart"] and engine.resets == []
    value.poll()
    assert engine.gaps == ["observation_restart"]  # once per process


def test_pump_never_acks_when_the_durable_apply_fails():
    engine, observer = Engine(fail=True), Observer([page([account_event(1)])])
    value = pump(engine, observer)
    with pytest.raises(RuntimeError):
        value.poll()
    assert observer.acks == []


def test_pump_epoch_change_marks_a_gap_and_replays_from_zero():
    engine = Engine()
    observer = Observer([page([account_event(1)]), page([account_event(2)], epoch="f" * 64)])
    value = pump(engine, observer)
    value.poll()
    observer.epoch = "f" * 64
    value.poll()
    assert engine.gaps == ["observation_restart", "epoch_changed"]
    assert value.verified_epoch is None
    observer.pages.append(page([account_event(1)], epoch="f" * 64))
    value.poll()
    assert engine.resets[-1] == ("f" * 64, -1) and observer.reads[-1]["after_sequence"] == 0


@pytest.mark.parametrize("reply, reason", [
    (SimpleNamespace(outcome="REQUEST_DENIED", code="CONSUMER_REPLAY_REQUIRED", page=None),
     "observation_replay_required"),
    (SimpleNamespace(outcome="UNKNOWN", code=None, page=None), "observation_read_failed"),
])
def test_pump_read_failures_mark_the_projection_unverified(reply, reason):
    engine = Engine()
    value = pump(engine, Observer([page([account_event(1)]), reply]))
    value.poll()
    value.poll()
    assert engine.gaps == ["observation_restart", reason] and value.verified_epoch is None


def test_pump_unmappable_event_applies_the_prefix_and_marks_a_gap():
    engine = Engine()
    observer = Observer([page([account_event(1), account_event(2, side=None), account_event(3)])])
    value = pump(engine, observer)
    assert value.poll() == 1
    assert [e.sequence for e in engine.applied] == [0]
    assert observer.acks == [1]
    assert engine.gaps == ["observation_restart", "observation_unmappable"]


def test_own_event_without_economics_uses_risks_own_durable_intent_only():
    payload = {"side": "buy", "offset": "open", "quantity": "2", "price": "10"}
    item = account_event(1, principal=RISK.principal_ref, side=None, quantity=None)
    event = observed_event_from_account_event(item, epoch=EPOCH, risk_principal=RISK.principal_ref,
                                              intent_payload=lambda key: payload)
    assert (event.side, event.quantity, event.offset) == ("BUY", Decimal(2), "OPEN")
    from bt_api_risk.pretrade import RiskFactError

    with pytest.raises(RiskFactError):
        observed_event_from_account_event(account_event(1, side=None), epoch=EPOCH,
                                          risk_principal=RISK.principal_ref,
                                          intent_payload=lambda key: payload)


def test_cancel_error_keeps_the_order_working():
    item = account_event(1)
    item.event.observation.kind = DirectExecutionEventKind.CANCEL_ERROR
    item.event.observation.status = None
    event = observed_event_from_account_event(item, epoch=EPOCH, risk_principal="x",
                                              intent_payload=lambda key: None)
    assert event.kind == "cancel_error" and event.status == "accepted"
    assert json.dumps(event.order_identity)
