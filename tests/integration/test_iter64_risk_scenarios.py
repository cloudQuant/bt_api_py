"""Iteration 64 (64.09) INTEGRATION_SIM scenario matrix of the independent Risk service.

Every test runs its own processes (``_iter64_risk_fixture.RiskScenarioEnv``): a spawned
loopback Gateway with synthetic native dispatch and synthetic venue returns, the Risk
host started through ``python -m bt_api_py.risk_service_host run``, and spawned strategy
processes: A and B place through Risk, C has no Risk and talks to the Gateway directly.
The test process is the Risk operator and the Gateway operator.

Evidence level: INTEGRATION_SIM (temporary SQLite files, CURVE over loopback, no provider
session, no account, no venue). It shows the Risk and Gateway processes' behaviour on
these synthetic inputs only.
"""

from __future__ import annotations

import contextlib

import pytest

from tests.integration._iter64_risk_fixture import (
    BASE_POLICY,
    STRATEGIES,
    WATCHED,
    RiskScenarioEnv,
    until,
)

A, B, C = (STRATEGIES[name].actor_ref for name in ("a", "b", "c"))


@pytest.fixture
def make_env(tmp_path):
    """Start one scenario environment (closed at teardown); options go to RiskScenarioEnv."""
    created = []

    def make(**options):
        item = RiskScenarioEnv(tmp_path, **options)
        created.append(item)
        item.start()
        item.wait_risk_verified(bypass_at_least=1)
        return item

    yield make
    for item in created:
        item.close()


@pytest.fixture
def env(make_env):
    return make_env()


def _placed(result):
    """Dispatch answer of a place through Risk (QUEUED: Risk answered DISPATCHED)."""
    return result.get("dispatch", {}).get("answer", {}) or {}


def test_r01_quantity_cap_refuses_locally_and_caps_are_per_strategy(env):
    commands_before = env.gateway_commands()
    refused = env.call("a", "place", key="a-big", cid="a-big", quantity="2")
    reserve = refused["reserve"]
    assert reserve["outcome"] == "REQUEST_DENIED", refused
    assert reserve["risk_denied"] == "risk_order_quantity_exceeds_cap", refused
    assert "dispatch" not in refused
    # Never in the Gateway journal: Risk refused before any Gateway call.
    assert "a-big" not in env.gateway_commands()
    accepted = env.call("a", "place", key="a-one", cid="a-one", quantity="1")
    assert _placed(accepted).get("outcome") == "DISPATCHED", accepted
    sent = env.wait_gateway_state("a-one", "SENT")
    assert sent[0] == A and sent[1] == "risk-principal"
    # B's own cap (2) is unaffected by A's cap (1).
    b_two = env.call("b", "place", key="b-two", cid="b-two", quantity="2", price="9")
    assert _placed(b_two).get("outcome") == "DISPATCHED", b_two
    assert env.wait_gateway_state("b-two", "SENT")[0] == B
    after = env.gateway_commands()
    assert set(after) - set(commands_before) == {"a-one", "b-two"}
    status = env.call("a", "risk_service_status")
    assert status["refusal_counts"] == {"risk_order_quantity_exceeds_cap": 1}, status
    assert env.call("b", "risk_service_status")["refusal_counts"] == {}


def _denied(result, phase):
    """Risk's own refusal code of a place at ``phase`` ("reserve" or "dispatch")."""
    part = result.get(phase) or {}
    assert part.get("outcome") == "REQUEST_DENIED", result
    return part.get("risk_denied")


def test_r05_default_refuses_after_replay_without_complete_active_order_proof(make_env):
    # Removing the explicit opt-out selects the product's default-enabled R-05.
    policy = BASE_POLICY.replace("self_trade: {enabled: false, on_unverifiable: deny}\n", "")
    env = make_env(policy=policy)
    assert env.risk_summary()["observation"]["continuity"] == "VERIFIED"
    before = env.gateway_commands()
    result = env.call("a", "place", key="no-coverage", cid="no-coverage")
    assert _denied(result, "reserve") == "risk_self_trade_unverifiable", result
    assert "dispatch" not in result
    assert env.gateway_commands() == before


def test_r04_refuses_without_complete_day_counter_coverage(make_env):
    env = make_env(policy=R04_POLICY)
    before = env.gateway_commands()
    result = env.call("a", "place", key="counter-gap", cid="counter-gap")
    assert _denied(result, "reserve") == "risk_facts_unverified", result
    assert "dispatch" not in result and env.gateway_commands() == before


@pytest.mark.skip(reason="GAP: Gateway active-orders full-coverage proof not available")
def test_r05_self_trade_with_risk_orders_of_all_strategies_and_a_bypass_order(env):
    # A and B rest non-crossing opposite passive orders through Risk; the synthetic
    # Gateway sends them and no venue return ends them (working at the Gateway).
    assert _placed(env.call("a", "place", key="a-buy", cid="a-buy", side="buy", price="10")).get(
        "outcome") == "DISPATCHED"
    assert _placed(env.call("b", "place", key="b-sell", cid="b-sell", side="sell", price="20")).get(
        "outcome") == "DISPATCHED"
    env.wait_gateway_state("a-buy", "SENT")
    env.wait_gateway_state("b-sell", "SENT")
    # B's sell at 9 crosses A's resting buy at 10: refused at claim, never dispatched.
    crossing = env.call("b", "place", key="b-cross", cid="b-cross", side="sell", price="9")
    assert _denied(crossing, "dispatch") == "risk_self_trade", crossing
    assert env.wait_gateway_state("b-cross", "RELEASED")[2] == "RELEASED"
    assert env.call("b", "risk_status", key="b-cross")["answer"]["state"] == "RELEASED"
    # C rests a sell at 15 directly at the Gateway (bypass); Risk sees it on the stream.
    bypass = env.risk_summary()["bypass_orders"]
    placed = env.call("c", "place", key="c-sell", cid="c-sell", side="sell", price="15")
    assert placed["dispatch"]["outcome"] in ("QUEUED", "OBSERVED"), placed
    env.wait_gateway_state("c-sell", "SENT")
    assert env.ingest("c-sell", "accepted", side="sell", price="15") == "ATTRIBUTED"
    env.wait_risk_verified(bypass_at_least=bypass + 1)
    # A's buy at 16 crosses only C's sell at 15 (not B's sell at 20).
    against_bypass = env.call("a", "place", key="a-cross", cid="a-cross", side="buy", price="16")
    assert _denied(against_bypass, "dispatch") == "risk_self_trade", against_bypass
    assert env.wait_gateway_state("a-cross", "RELEASED")[2] == "RELEASED"
    # A non-crossing order still passes (the refusals were price-specific).
    fine = env.call("a", "place", key="a-fine", cid="a-fine", side="buy", price="11")
    assert _placed(fine).get("outcome") == "DISPATCHED", fine
    statuses = {key: value[2] for key, value in env.gateway_commands().items()}
    assert statuses["b-cross"] == "RELEASED" and statuses["a-cross"] == "RELEASED"
    assert env.gateway_cancels() == []


R04_POLICY = BASE_POLICY + "counters:\n  orders: {warn: 2, block: 3}\n"


@pytest.mark.skip(reason="GAP: Gateway full-day order/cancel coverage proof not available")
def test_r04_block_latches_contract_stop_new_at_the_gateway_and_survives_restart(make_env):
    env = make_env(policy=R04_POLICY)
    # Three claims on rb reach the block (C's bootstrap order is on hc).
    first = env.call("a", "place", key="a-1", cid="a-1", price="10")
    assert _placed(first).get("outcome") == "DISPATCHED", first
    env.wait_gateway_state("a-1", "SENT")
    assert _placed(env.call("b", "place", key="b-1", cid="b-1", price="10")).get(
        "outcome") == "DISPATCHED"
    breach = env.call("a", "place", key="a-2", cid="a-2", price="10")
    # The Nth claim (it reaches the block) is dispatched normally and SENT: Risk asks the
    # Gateway for the contract STOP_NEW only after that claim's dispatch call returned.
    assert _placed(breach).get("outcome") == "DISPATCHED", breach
    assert env.wait_gateway_state("a-2", "SENT")[2] == "SENT"
    # The latch and the Gateway cause exist once the Nth order's dispatch returned.
    latches = env.admin("risk.latch.list", {"active_only": True})["latches"]
    assert [(row["control"], row["scope_kind"], row["scope_value"]) for row in latches] == [
        ("counters", "instrument", "rb")], latches
    causes = env.gateway_control("direct.control.list", {"after": "", "limit": 10})["causes"]
    risk_causes = [row for row in causes if row["origin"].startswith("risk:")]
    assert [(row["mode"], row["scope_kind"], row["scope_value"], bool(row["active"]))
            for row in risk_causes] == [("STOP_NEW", "instrument", "SHFE:rb", True)], causes
    cause_id = risk_causes[0]["cause_id"]
    status = env.gateway_control("direct.control.status", {"cause_id": cause_id})
    assert status["status"] == "OBSERVED" and status["cause"]["active"], status
    # The N+1th OPEN (A's and B's) on rb: refused by Risk locally, never at the Gateway.
    for which, key in (("a", "a-3"), ("b", "b-3")):
        refused = env.call(which, "place", key=key, cid=key, price="10")
        assert _denied(refused, "reserve") == "risk_order_count_limit", refused
        assert key not in env.gateway_commands()
    # C's direct OPEN on rb is refused by the Gateway once the cause is applied.
    direct = env.call("c", "place", key="c-1", cid="c-1", price="10")
    assert direct["reserve"]["outcome"] == "REQUEST_DENIED", direct
    assert direct["reserve"]["code"] == "CONTROL_STOP_NEW", direct
    # A CLOSE is not refused by R-04: A's first order fills, A closes it through Risk
    # and the Gateway lets a verified close pass its contract STOP_NEW.
    assert env.ingest("a-1", "accepted") == "ATTRIBUTED"
    assert env.ingest_trade("a-1", "trade-a-1") == "ATTRIBUTED"
    assert env.ingest("a-1", "filled", filled="1") == "ATTRIBUTED"
    close = env.call("a", "place", key="a-close", cid="a-close", side="sell", offset="close",
                     price="11")
    assert _placed(close).get("outcome") == "DISPATCHED", close
    env.wait_gateway_state("a-close", "SENT")
    # Another instrument is unaffected (hc count: C's bootstrap + this claim < block).
    other = env.call("a", "place", key="a-hc", cid="a-hc", symbol="hc", price="10")
    assert _placed(other).get("outcome") == "DISPATCHED", other
    env.wait_gateway_state("a-hc", "SENT")
    # The latch survives a Risk restart.
    env.restart_risk()
    env.wait_risk_verified(bypass_at_least=1)
    env.wait_risk_route("a")
    again = env.call("a", "place", key="a-4", cid="a-4", price="10")
    assert _denied(again, "reserve") == "risk_order_count_limit", again
    # The operator releases Risk's latch; the Gateway cause stays until a Gateway operator
    # resumes it (Risk never resumes a cause, D64-21).
    latch = env.admin("risk.latch.list", {"active_only": True})["latches"][0]
    released = env.admin("risk.latch.release", {"latch_id": latch["latch_id"],
                                                "expected_version": latch["version"],
                                                "reason": "scenario: operator reviewed R-04"})
    assert released["status"] == "OBSERVED" and released["latch"]["active"] is False, released
    assert env.admin("risk.latch.list", {"active_only": True})["latches"] == []
    status = env.gateway_control("direct.control.status", {"cause_id": cause_id})
    assert status["cause"]["active"], status
    blocked = env.call("c", "place", key="c-2", cid="c-2", price="10")
    assert blocked["reserve"]["code"] == "CONTROL_STOP_NEW", blocked
    resumed = env.gateway_control("direct.control.resume", {
        "cause_id": cause_id, "expected_version": status["cause"]["version"],
        "operation_id": "ops-resume-1"})
    assert resumed["status"] == "OBSERVED" and not resumed["active"], resumed
    after = env.call("c", "place", key="c-3", cid="c-3", price="10")
    assert after["dispatch"]["outcome"] in ("QUEUED", "OBSERVED"), after
    env.wait_gateway_state("c-3", "SENT")
    # The permitted Nth order was never refused or released by its own breach.
    assert env.call("a", "risk_status", key="a-2")["answer"]["state"] == "DISPATCHED"
    risk_records = [(r["kind"], r["request_key"]) for r in env.audit()]
    assert risk_records.index(("dispatched", "a-2")) < next(
        i for i, (kind, _) in enumerate(risk_records) if kind == "control_reply")
    counts = env.admin("risk.counts.export", {"trading_day": "20300101", "format": "json"})
    rows = {(row["instrument_id"], row["kind"]): row["value"]
            for row in __import__("json").loads(counts["content"])["counts"]}
    assert rows[("rb", "order")] == "4", rows  # a-1, b-1, a-2 (the Nth), a-close


def test_d64_02_risk_stopped_refuses_place_and_cancel_still_reaches_the_gateway(env, request):
    working = env.call("a", "place", key="a-work", cid="a-work", price="10")
    assert _placed(working).get("outcome") == "DISPATCHED", working
    env.wait_gateway_state("a-work", "SENT")
    before = env.gateway_commands()
    assert env.stop_risk() == 0, env.risk_stderr
    # Lease failure proves only that this invocation was not sent; an earlier
    # host might have used the same key, so the key remains UNKNOWN.
    env.wait_risk_route("a", available=False, seconds=90)
    refused = env.call("a", "place", key="a-out", cid="a-out", price="10")
    assert refused["reserve"]["outcome"] == "UNKNOWN", refused
    assert "dispatch" not in refused
    assert env.gateway_commands() == before  # nothing reached the Gateway
    # The strategy cancels its earlier Risk-placed working order as its delegated command.
    cancel = env.call("a", "cancel", key="a-work", operation_id="cancel-a-work")
    assert cancel["outcome"] in ("QUEUED", "OBSERVED"), cancel
    status = until(lambda: env.call("a", "cancel_status", operation_id="cancel-a-work"),
                   lambda value: value.get("state") == "SENT", 20)
    assert status["state"] == "SENT", status
    assert env.gateway_cancels() == ["a-work"]
    snapshot = until(lambda: env.call("a", "risk_runtime_status"),
                     lambda value: not value["thread_alive"], 60)
    # Risk comes back; the client re-registers with bounded backoff and nothing that was
    # refused or in flight is sent again.
    env.start_risk()
    env.wait_risk_verified(bypass_at_least=1)
    if snapshot["unregister_uncertainties"]:
        # The separate safety regression checks this branch; recovery is still open.
        request.node.add_marker(pytest.mark.xfail(
            reason="GAP: Risk restart cannot recover while old unregister is unconfirmed",
            strict=True,
        ))
        assert snapshot["unregister_uncertainties"] == 0, snapshot
    attempts = []

    def place():
        key = "a-back-" + str(len(attempts))
        attempts.append(key)
        return env.call("a", "place", key=key, cid=key, price="9")

    result = until(place, lambda value: _placed(value).get("outcome") == "DISPATCHED", 90,
                   step=1.0)
    assert _placed(result).get("outcome") == "DISPATCHED", result
    commands = env.gateway_commands()
    assert set(commands) - set(before) == {attempts[-1]}, commands
    assert env.call("a", "risk_status", key="a-out")["answer"]["outcome"] == "NOT_FOUND"
    dispatched = [record["request_key"] for record in env.audit() if record["kind"] == "dispatched"]
    assert sorted(dispatched) == sorted(["a-work", attempts[-1]]), dispatched


def test_d64_02_unconfirmed_unregister_retains_original_runtime_and_refuses(env):
    working = env.call("a", "place", key="a-work", cid="a-work", price="10")
    assert _placed(working).get("outcome") == "DISPATCHED", working
    env.wait_gateway_state("a-work", "SENT")
    before = env.gateway_commands()
    assert env.stop_risk() == 0, env.risk_stderr
    env.wait_risk_route("a", available=False, seconds=90)
    snapshot = until(lambda: env.call("a", "risk_runtime_status"),
                     lambda value: not value["thread_alive"], 60)
    assert snapshot["state"] == "failed" and snapshot["unregister_uncertainties"] == 1, snapshot
    env.start_risk()
    env.wait_risk_verified(bypass_at_least=1)
    status = env.call("a", "risk_status", key="a-work")
    assert status["outcome"] == "NOT_SENT" and status["code"] == "ROUTE_UNAVAILABLE", status
    blocked = env.call("a", "place", key="a-back-blocked", cid="a-back-blocked", price="9")
    assert blocked["reserve"]["outcome"] == "UNKNOWN", blocked
    assert "dispatch" not in blocked and env.gateway_commands() == before
    # Neither the read nor the new request replaced the unproven old runtime.
    assert env.call("a", "risk_runtime_status") == snapshot


def test_inv64_10_bypass_order_is_accepted_by_the_gateway_and_observed_by_risk(env):
    import json
    import sqlite3

    bypass = env.risk_summary()["bypass_orders"]
    placed = env.call("c", "place", key="c-bypass", cid="c-bypass", price="10")
    assert placed["reserve"]["outcome"] == "OBSERVED", placed
    assert placed["dispatch"]["outcome"] in ("QUEUED", "OBSERVED"), placed
    assert env.wait_gateway_state("c-bypass", "SENT")[1] == "principal-c"
    assert env.ingest("c-bypass", "accepted") == "ATTRIBUTED"
    assert env.ingest_trade("c-bypass", "trade-c-bypass") == "ATTRIBUTED"
    assert env.ingest("c-bypass", "filled", filled="1") == "ATTRIBUTED"
    summary = env.wait_risk_verified(bypass_at_least=bypass + 1)
    assert summary["bypass_orders"] == bypass + 1, summary
    # The bypass counter may be applied before the trade and terminal order.
    # Wait for the latter instead of racing the observation pump.
    def order_status():
        with sqlite3.connect("file:" + str(env.risk_database()) + "?mode=ro", uri=True) as db:
            return db.execute("SELECT status FROM risk_pretrade_orders WHERE request_key=?",
                              ("c-bypass",)).fetchone()

    assert until(order_status, lambda value: value == ("filled",), seconds=15) == ("filled",)
    counts = env.admin("risk.counts.export", {"trading_day": "20300101", "format": "json"})
    rows = {(row["instrument_id"], row["kind"]): row["value"]
            for row in json.loads(counts["content"])["counts"]}
    assert rows[("rb", "order")] == "1" and rows[("rb", "open_volume")] == "1", rows
    with sqlite3.connect(env.copy_risk_database("bypass")) as connection:
        order = connection.execute(
            "SELECT origin, strategy_ref, bypass, status FROM risk_pretrade_orders "
            "WHERE request_key='c-bypass'").fetchall()
        position = connection.execute(
            "SELECT quantity FROM risk_pretrade_positions WHERE strategy_ref=? "
            "AND instrument_id='rb' AND position_side='LONG'", (C,)).fetchall()
        permits = connection.execute(
            "SELECT COUNT(*) FROM risk_pretrade_permits WHERE request_key='c-bypass'").fetchone()
    assert order == [("self", C, 1, "filled")], order
    assert position == [("1",)], position
    assert permits == (0,)  # Risk never authorized it: the bypass is only observed


def test_inv64_02_gateway_and_strategy_processes_never_load_risk(make_env):
    import multiprocessing

    from tests.integration._iter64_risk_fixture import import_control_process

    env = make_env()
    assert _placed(env.call("a", "place", key="a-1", cid="a-1")).get("outcome") == "DISPATCHED"
    assert _placed(env.call("b", "place", key="b-1", cid="b-1", side="sell", price="20")).get(
        "outcome") == "DISPATCHED"
    env.wait_gateway_state("a-1", "SENT")
    assert env.call("a", "cancel", key="a-1", operation_id="cancel-a-1")["outcome"] in (
        "QUEUED", "OBSERVED")
    assert env.call("c", "place", key="c-1", cid="c-1", price="5")["dispatch"]["outcome"] in (
        "QUEUED", "OBSERVED")
    env.close()  # every process reports what its interpreter holds when it stops
    assert env.modules == {"gateway": [], "strategy_a": [], "strategy_b": [],
                           "strategy_c": []}, env.modules
    assert env.risk_exits == [0]
    # Control: the same observation sees the packages in a process that imports them.
    context = multiprocessing.get_context("spawn")
    reports = context.Queue()
    control = context.Process(target=import_control_process, args=(reports,))
    control.start()
    report = reports.get(timeout=60)
    control.join(30)
    assert "bt_api_risk" in report[3], report
    assert set(report[3]) <= set(WATCHED)


V1_POLICY = BASE_POLICY + "counters:\n  orders: {warn: 5, block: 5}\n"


def _policy_document(text):
    import yaml

    return yaml.safe_load(text)


def test_policy_tightening_refuses_preexisting_reservation_at_dispatch(make_env):
    # Other-rule-only slice: R-05 is explicitly disabled, no R-04 policy.
    env = make_env(policy=BASE_POLICY)
    reserved = env.call("b", "reserve", key="b-2", cid="b-2", quantity="2", price="10")
    assert reserved["state"] == "RESERVED"
    policy = _policy_document(BASE_POLICY.replace('strategy-b: {rb: "2"',
                                                   'strategy-b: {rb: "1"'))
    result = env.admin("risk.policy.publish", {
        "expected_version": 1, "document": policy, "reason": "tighten B rb cap",
        "exchange_rules_text": None})
    assert result["status"] == "OBSERVED" and result["version"] == 2
    refused = env.call("b", "dispatch", key="b-2")
    assert refused["outcome"] == "REQUEST_DENIED"
    assert refused["risk_denied"] == "risk_order_quantity_exceeds_cap"
    assert env.wait_gateway_state("b-2", "RELEASED")[2] == "RELEASED"


@pytest.mark.skip(reason="GAP: latch half requires missing full-day counter coverage")
def test_d64_12_published_tightening_refuses_a_reserved_order_at_dispatch(make_env):
    env = make_env(policy=V1_POLICY)
    reserved = env.call("b", "reserve", key="b-2", cid="b-2", quantity="2", price="10")
    assert reserved["state"] == "RESERVED" and reserved["answer"]["policy_version"] == "1"
    assert env.gateway_commands()["b-2"][2] == "RESERVED"
    read = env.admin("risk.policy.read")
    assert read["status"] == "OBSERVED" and read["version"] == 1, read
    tightened = _policy_document(V1_POLICY.replace('strategy-b: {rb: "2"', 'strategy-b: {rb: "1"'))
    published = env.admin("risk.policy.publish", {
        "expected_version": 1, "document": tightened, "reason": "tighten B rb cap",
        "exchange_rules_text": None})
    assert published["status"] == "OBSERVED" and published["version"] == 2, published
    assert published["classification"]["loosened"] == []
    # The reserved order is re-checked under the version in force at claim (D64-12).
    refused = env.call("b", "dispatch", key="b-2")
    assert refused["outcome"] == "REQUEST_DENIED", refused
    assert refused["risk_denied"] == "risk_order_quantity_exceeds_cap", refused
    assert env.wait_gateway_state("b-2", "RELEASED")[2] == "RELEASED"
    status = env.call("b", "risk_status", key="b-2")["answer"]
    assert status["state"] == "RELEASED" and status["policy_version"] == "1", status
    # A latch in a family makes a loosening of that family refused.
    latch_policy = _policy_document(
        V1_POLICY.replace('strategy-b: {rb: "2"', 'strategy-b: {rb: "1"')
        .replace("{warn: 5, block: 5}", "{warn: 1, block: 1}"))
    assert env.admin("risk.policy.publish", {
        "expected_version": 2, "document": latch_policy, "reason": "tighten R-04",
        "exchange_rules_text": None})["version"] == 3
    breach = env.call("a", "place", key="a-1", cid="a-1", price="10")
    assert _placed(breach).get("outcome") == "DISPATCHED", breach
    latches = env.admin("risk.latch.list", {"active_only": True})["latches"]
    assert [row["control"] for row in latches] == ["counters"], latches
    loosened = _policy_document(V1_POLICY.replace('strategy-b: {rb: "2"', 'strategy-b: {rb: "1"'))
    with_reason = env.admin("risk.policy.publish", {
        "expected_version": 3, "document": loosened, "reason": "loosen R-04 back",
        "exchange_rules_text": None})
    assert with_reason == {"status": "REQUEST_REJECTED", "code": "policy_loosening_latched"}
    without_reason = env.admin("risk.policy.publish", {
        "expected_version": 3, "document": loosened, "reason": "",
        "exchange_rules_text": None})
    assert without_reason == {"status": "REQUEST_REJECTED",
                              "code": "policy_loosening_reason_required"}
    assert env.admin("risk.policy.read")["version"] == 3
    stale = env.admin("risk.policy.publish", {
        "expected_version": 1, "document": loosened, "reason": "stale",
        "exchange_rules_text": None})
    assert stale == {"status": "REQUEST_REJECTED", "code": "policy_version_conflict"}


def _foreign_runtime(env, *, identity, scope, mount, operations, process_role, registration,
                     keys=None):
    """A raw client runtime of the test process (strategy A's CURVE key unless given)."""
    from bt_api_py.service_config import parse_client_config
    from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
    from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
    from tests.integration._iter64_risk_fixture import ACCOUNT, RISK_HOST

    config = parse_client_config({
        "client_config_schema_version": 1, "process_role": process_role,
        "registration": registration,
        "clients": {"risk": {"config_schema_version": 1, "settings": {"bindings": [{
            "mount": mount, "service_id": RISK_HOST.actor_ref, "instance_id": "risk-a",
            "scope_ref": "risk-scope", "account_ref": ACCOUNT, "transport_ref": "t",
            "required": True}]}}},
        "communication": {"config_schema_version": 1, "profiles": {"t": {
            "backend": "zmq", "config_schema_version": 1,
            "endpoints": {"rpc_connect": env.risk_endpoint}, "security_profile_ref": "c"}}},
    })
    factory = ZmqTransportFactory({"c": ClientCurveKeys(env.keys["risk_server"][0],
                                                        *(keys or env.keys["strategy_a"]))})
    return ThreadedServiceClientRuntime(lambda: ServiceClientRuntime(
        config, identity=identity, scope_resolver=lambda _: scope,
        peer_resolver=lambda _: RISK_HOST, transport_factory=factory,
        run_id=scope.run_id, requested_operations={mount: frozenset(operations)}))


def _registered(runtime):
    try:
        snapshot = runtime.start(timeout_seconds=10)
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):  # test-process probe cleanup only
            runtime.close(timeout_seconds=5)
    return bool(snapshot.healthy) and any(binding.active for binding in snapshot.bindings)


def test_inv64_12_identity_binding_idempotency_and_operator_operations(env):
    from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

    from tests.integration._iter64_risk_fixture import ACCOUNT, DOMAIN, RISK_OPERATOR

    a_identity = STRATEGIES["a"]
    # Positive controls through the same helper: the own scope and the real operator register.
    probe = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=A, run_id="run-probe")
    assert _registered(_foreign_runtime(
        env, identity=a_identity, scope=probe, mount="broker.risk_order",
        operations={"risk.order.status"}, process_role="strategy_client",
        registration={"strategy_ref": A}))
    assert _registered(_foreign_runtime(
        env, identity=RISK_OPERATOR, scope=WireScope(DOMAIN, account_ref=ACCOUNT),
        mount="application.risk_admin", operations={"risk.admin.status"},
        process_role="application_client", registration={"application_ref": RISK_OPERATOR.actor_ref},
        keys=env.keys["risk_operator"]))
    # A strategy principal cannot register for another strategy's scope.
    other = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=B, run_id="run-x")
    assert not _registered(_foreign_runtime(
        env, identity=a_identity, scope=other, mount="broker.risk_order",
        operations={"risk.order.reserve", "risk.order.status"}, process_role="strategy_client",
        registration={"strategy_ref": B}))
    # ... nor claim another strategy's actor with its own key.
    assert not _registered(_foreign_runtime(
        env, identity=WireIdentity(WireRole.STRATEGY_CLIENT, a_identity.principal_ref, B),
        scope=other, mount="broker.risk_order", operations={"risk.order.status"},
        process_role="strategy_client", registration={"strategy_ref": B}))
    # A strategy principal is refused the operator operations (registration refused).
    own = WireScope(DOMAIN, account_ref=ACCOUNT)
    assert not _registered(_foreign_runtime(
        env, identity=WireIdentity(WireRole.APPLICATION_CLIENT, a_identity.principal_ref,
                                   a_identity.actor_ref),
        scope=own, mount="application.risk_admin", operations={"risk.admin.status"},
        process_role="application_client", registration={"application_ref": A}))
    # B's permit is not A's: A cannot dispatch it, and A's status does not see it.
    b_reserved = env.call("b", "reserve", key="b-x", cid="b-x", price="10")
    assert b_reserved["state"] == "RESERVED", b_reserved
    stolen = env.call("a", "risk_dispatch_raw", key="b-x", permit_id=b_reserved["permit_id"],
                      reservation_id=b_reserved["reservation_id"],
                      view_digest=b_reserved["view_digest"])
    assert stolen["outcome"] == "ANSWERED" and stolen["answer"]["code"] == "risk_unauthorized"
    assert env.call("a", "risk_status", key="b-x")["answer"]["outcome"] == "NOT_FOUND"
    assert env.gateway_commands()["b-x"][2] == "RESERVED"  # untouched
    # Same key, same payload: the same answer; one Gateway command, one dispatch.
    first = env.call("a", "risk_reserve_raw", key="a-r", cid="a-r", request_key="a-r")
    again = env.call("a", "risk_reserve_raw", key="a-r", cid="a-r", request_key="a-r")
    assert first["answer"] == again["answer"] and first["answer"]["outcome"] == "RESERVED"
    permit = first["answer"]
    for _ in range(2):
        dispatched = env.call("a", "risk_dispatch_raw", key="a-r", permit_id=permit["permit_id"],
                              reservation_id=permit["reservation_id"],
                              view_digest=permit["view_digest"])
        assert dispatched["answer"]["outcome"] == "DISPATCHED", dispatched
    env.wait_gateway_state("a-r", "SENT")
    records = [r["kind"] for r in env.audit() if r["request_key"] == "a-r"]
    assert records.count("reserve") == 1 and records.count("dispatched") == 1, records
    assert records.count("claim") == 1, records
    # Same key, other payload: a conflict, nothing new at the Gateway.
    conflict = env.call("a", "risk_reserve_raw", key="a-r", cid="a-r", request_key="a-r",
                        price="11")
    assert conflict["answer"]["code"] == "risk_request_conflict", conflict
    # Wrong permit, reservation or digest: a mismatch, never a dispatch.
    b_y = env.call("b", "reserve", key="b-y", cid="b-y", price="10")
    for wrong in ({"permit_id": "0" * 32}, {"reservation_id": "res-wrong"},
                  {"view_digest": "0" * 64}):
        arguments = {"permit_id": b_y["permit_id"], "reservation_id": b_y["reservation_id"],
                     "view_digest": b_y["view_digest"], **wrong}
        refused = env.call("b", "risk_dispatch_raw", key="b-y", **arguments)
        assert refused["answer"]["code"] == "risk_permit_mismatch", (wrong, refused)
    assert env.gateway_commands()["b-y"][2] == "RESERVED"
    counts = env.call("b", "risk_service_status")["refusal_counts"]
    assert counts == {"risk_permit_mismatch": 3}, counts


@pytest.mark.skip(reason="GAP: R-05 remains unavailable even when event replay is continuous")
def test_observation_replay_after_restart_and_unverified_gap_during_a_gateway_outage(env):
    import time

    # Orders exist: A's working buy through Risk and C's working sell at the Gateway.
    assert _placed(env.call("a", "place", key="a-buy", cid="a-buy", price="10")).get(
        "outcome") == "DISPATCHED"
    env.wait_gateway_state("a-buy", "SENT")
    assert env.ingest("a-buy", "accepted") == "ATTRIBUTED"
    bypass = env.risk_summary()["bypass_orders"]
    assert env.call("c", "place", key="c-sell", cid="c-sell", side="sell", price="30")[
        "dispatch"]["outcome"] in ("QUEUED", "OBSERVED")
    env.wait_gateway_state("c-sell", "SENT")
    assert env.ingest("c-sell", "accepted", side="sell", price="30") == "ATTRIBUTED"
    env.wait_risk_verified(bypass_at_least=bypass + 1)
    audit_before = len(env.audit())
    # Restart: the host marks the projection unverified, replays the stream from 0 and
    # only then binds its RPC port, so no strategy call meets the replay window.
    env.restart_risk()
    summary = env.wait_risk_verified(bypass_at_least=bypass + 1)
    assert summary["observation"]["verified_from_zero"] is True, summary
    restart_records = [(r["kind"], r["detail"]) for r in env.audit()[audit_before:]]
    gap = restart_records.index(("observation_gap", {"reason": "observation_restart"}))
    reset = next(i for i, (kind, _) in enumerate(restart_records) if kind == "observation_reset")
    assert gap < reset, restart_records
    assert restart_records[reset][1]["head_sequence"] >= 2, restart_records
    assert not any(kind in ("reserve", "claim", "reserve_refused")
                   for kind, _ in restart_records[: reset + 1]), restart_records
    env.wait_risk_route("b")
    # After the replay R-05 sees the replayed working orders again.
    crossing = env.call("b", "place", key="b-cross", cid="b-cross", side="buy", price="31")
    assert _denied(crossing, "dispatch") == "risk_self_trade", crossing
    # An in-service gap: the Gateway serves nothing for a while; Risk's stream read fails
    # and R-05 refuses every claim as unverifiable until the stream is replayed.
    reserved = env.call("b", "reserve", key="b-gap", cid="b-gap", side="buy", price="5")
    assert reserved["state"] == "RESERVED", reserved
    env.pause_gateway(8)
    time.sleep(4)  # longer than Risk's 3 s Gateway call deadline: its stream read failed
    refused = env.call("b", "dispatch", key="b-gap", timeout=60)
    assert refused["outcome"] == "REQUEST_DENIED", refused
    assert refused["risk_denied"] == "risk_self_trade_unverifiable", refused
    env.wait_gateway_resumed()
    env.wait_risk_verified(bypass_at_least=bypass + 1, seconds=30)
    after = env.call("b", "place", key="b-after", cid="b-after", side="buy", price="6")
    assert _placed(after).get("outcome") == "DISPATCHED", after
    # The refused order never left the Gateway; its permit (UNKNOWN when the release
    # answer was lost in the outage) is released once its reservation ends.
    assert env.gateway_commands()["b-gap"][2] in ("RESERVED", "RELEASED", "EXPIRED")
    state = until(lambda: env.call("b", "risk_status", key="b-gap")["answer"]["state"],
                  lambda value: value == "RELEASED", 60, step=0.5)
    assert state == "RELEASED"
    assert env.gateway_commands()["b-gap"][2] in ("RELEASED", "EXPIRED")


RULES = {"tests.integration._iter64_risk_rules:PriceTripRule": 1}
CANCEL_POLICY = (
    BASE_POLICY
    + "rules:\n"
    + "  - {id: trip-rb, implementation: 'tests.integration._iter64_risk_rules:PriceTripRule',"
    + " version: 1, params: {instrument: rb, trip_price: '50'}}\n"
    + "breach_actions:\n  - {rule: trip-rb, request: stop_new, scope: instrument}\n"
    + "cancel_rules:\n  - {rule: trip-rb, action: cancel_only, scope: instrument}\n"
)


@pytest.mark.skip(reason=(
    "NOT_RUN: historical cancel/control retries are disabled; this rule-owned intent "
    "is not an operator status row. Gateway origin and per-order results await a "
    "separate verified source binding and a revised integration scenario."
))
def test_d64_11_cancel_rule_cancels_working_targets_once_and_retries_after_an_outage(make_env):
    import json
    import sqlite3

    env = make_env(policy=CANCEL_POLICY, trusted_rules=RULES)
    # Working orders on rb (A and B through Risk, C directly) and one on hc.
    for which, key, side, price, symbol in (("a", "a-rb", "buy", "10", "rb"),
                                            ("b", "b-rb", "sell", "20", "rb"),
                                            ("a", "a-hc", "buy", "10", "hc")):
        placed = env.call(which, "place", key=key, cid=key, side=side, price=price, symbol=symbol)
        assert _placed(placed).get("outcome") == "DISPATCHED", placed
        env.wait_gateway_state(key, "SENT")
    assert env.call("c", "place", key="c-rb", cid="c-rb", price="8")["dispatch"]["outcome"] in (
        "QUEUED", "OBSERVED")
    env.wait_gateway_state("c-rb", "SENT")
    # The order that trips the rule is reserved before the Gateway becomes unreachable.
    trip = env.call("b", "reserve", key="b-trip", cid="b-trip", side="sell", price="60")
    assert trip["state"] == "RESERVED", trip
    env.pause_gateway(120, hold=True)
    refused = env.call("b", "dispatch", key="b-trip", timeout=60)
    assert refused["risk_denied"] == "risk_price_band_exceeded", refused
    # Wait for the first attempted cancel while the Gateway is still held down.
    def cancel_replies():
        # Read-only SQLite avoids waiting on the Risk host's Gateway call loop.
        with sqlite3.connect("file:" + str(env.risk_database()) + "?mode=ro", uri=True) as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT detail_json FROM risk_pretrade_audit WHERE kind=? ORDER BY seq",
                ("cancel_reply",))]

    failed_reply = until(cancel_replies, lambda rows: bool(rows), seconds=60, step=0.2)
    assert failed_reply and failed_reply[0]["outcome"] != "OBSERVED", failed_reply
    env.wait_gateway_resumed()
    # The intent was kept through the outage; after the Gateway returns Risk re-checks that
    # the rule's latch still holds and issues the same cancel operation again.
    def cancel_state():
        rows = env.admin("risk.audit.read", {"after": 0, "limit": 500,
                                            "through": None, "snapshot": None})["records"]
        operations = [r["detail"]["operation_id"] for r in rows if r["kind"] == "cancel_intent"]
        assert len(operations) == 1, rows
        return operations[0], env.admin("risk.cancel.status", {"operation_id": operations[0]})

    operation, status = until(cancel_state, lambda value: value[1]["state"] == "ACCEPTED", 60,
                              step=0.5)
    assert status["state"] == "ACCEPTED", status
    replies = [r["detail"]["outcome"] for r in env.audit()
               if r["kind"] == "cancel_reply" and r["detail"]["operation_id"] == operation]
    assert replies[0] != "OBSERVED" and replies[-1] == "OBSERVED", replies  # retried
    cancels = until(env.gateway_cancels, lambda value: len(value) >= 3, 20)
    assert sorted(cancels) == ["a-rb", "b-rb", "c-rb"], cancels  # each working target once
    gateway_view = status["gateway"]
    assert gateway_view["scope_kind"] == "instrument" and gateway_view["scope_value"] == "SHFE:rb"
    assert gateway_view["origin"].startswith("risk:"), gateway_view
    # The STOP_NEW of the same breach is applied as well, and hc is untouched.
    causes = env.gateway_control("direct.control.list", {"after": "", "limit": 10})["causes"]
    assert [(c["mode"], c["scope_value"], bool(c["active"])) for c in causes
            if c["origin"].startswith("risk:") and c["mode"] == "STOP_NEW"] == [
        ("STOP_NEW", "SHFE:rb", True)], causes
    hc = env.call("a", "place", key="a-hc-2", cid="a-hc-2", symbol="hc", price="9")
    assert _placed(hc).get("outcome") == "DISPATCHED", hc
    assert "a-hc" not in env.gateway_cancels()


def test_place_right_after_a_risk_restart_keeps_original_key_safe(env):
    # STALE_EPOCH proves this invocation was not sent, not that no earlier host
    # used the same key. Keep the original key UNKNOWN unless Risk answers.
    env.restart_risk()  # returns once the new host serves (stream replayed)
    first = env.call("a", "place", key="a-after-restart", cid="a-after-restart")
    reserve = first["reserve"]
    assert reserve["outcome"] in ("OBSERVED", "UNKNOWN"), first
    if reserve["outcome"] == "UNKNOWN":
        assert "dispatch" not in first
        assert "a-after-restart" not in env.gateway_commands()
    # The client re-registers (lease renewal, then reconnect); the next PLACE is placed.
    env.wait_risk_route("a")
    later = env.call("a", "place", key="a-later", cid="a-later")
    assert _placed(later).get("outcome") == "DISPATCHED", later
    if reserve["outcome"] == "UNKNOWN":
        # This synthetic scenario has no earlier host; check its local result.
        status = env.call("a", "risk_status", key="a-after-restart")
        assert status["answer"]["outcome"] == "NOT_FOUND", status
