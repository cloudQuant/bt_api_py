"""Risk crash matrix of plan 64 section 4.3 at four process-exit points (64.09).

INTEGRATION_SIM evidence: a spawned loopback Gateway (synthetic native
dispatcher, synthetic venue returns), the Risk host started through its CLI in
a second process with the test-only ``BT_RISK_FAULT_POINT`` (it calls
``os._exit`` at that point: no cleanup, no reply), restarted without it on the
same SQLite files, and the strategy in this process. No provider session, no
account.

For every point: the strategy's call is answered UNKNOWN (never a refusal), the
Gateway sends each request key at most once natively, and Risk releases
occupancy only on the freeze-section-5 proof.
"""

from __future__ import annotations

import multiprocessing
import time

import pytest

from bt_api_py.risk_service_host import FAULT_EXIT_CODE
from tests.integration import _iter64_recovery_support as support

# The counters-less v2 policy now publishes and activates (decision B-1), and
# the synthetic Gateway journal is now schema 19: the account-observer ACK
# handoff succeeds and Risk reports observation continuity VERIFIED.  What
# still blocks these scenarios is the unwired v1 write path: the Risk service
# refuses every authenticated order write with UNKNOWN risk_permit_mismatch
# (and risk.status reports available=False while
# bt_api_risk.pretrade.engine keeps the v1 source quarantined), so the runs
# never reach their crash points.  Keep them collected and strict-xfailed so a
# wired proof surfaces as XPASS.
OBSERVATION_ACK_BLOCKED = pytest.mark.xfail(
    strict=True,
    reason="risk_v1_writes_quarantined: the synthetic Gateway journal is now "
           "schema 19 and the account-observer ACK handoff succeeds "
           "(continuity VERIFIED), but the Risk service refuses every "
           "authenticated order write with UNKNOWN risk_permit_mismatch "
           "because the v1 first-creation proof is not wired",
)
pytestmark = OBSERVATION_ACK_BLOCKED

STRATEGY = support.strategy_identity(0)
TTL_SECONDS = 4


def _answer(result):
    assert result.outcome == "ANSWERED", result
    return result.answer


def _places(controls, messages):
    controls.put("stats")
    return support.next_message(messages, "stats")[1]


class _Loopback:
    def __init__(self, tmp_path):
        from tests.integration.test_service_account_instrument_metadata import _free_endpoint

        self.keys = support.keypairs(1)
        self.gateway_root = tmp_path / "gateway"
        self.gateway_root.mkdir()
        self.risk_root = tmp_path / "risk"
        self.risk_root.mkdir()
        self.gateway_endpoint, self.risk_endpoint = _free_endpoint(), _free_endpoint()
        context = multiprocessing.get_context("spawn")
        self.messages, self.controls = context.Queue(), context.Queue()
        self.gateway = context.Process(
            target=support.gateway_main,
            args=(str(self.gateway_root), self.gateway_endpoint, self.keys["gateway_server"],
                  support.gateway_publics(self.keys, 1), 1, self.messages, self.controls),
        )
        self.gateway_stop = support.SyntheticGatewayStop(
            self.gateway, self.controls, self.messages)
        self.gateway.start()
        ready = support.next_message(self.messages, "ready", 40)
        self.journal_id = ready[2]
        self.gateway_stop.bind_ready(ready)
        self.config = support.risk_files(
            self.risk_root, self.keys, 1, self.gateway_endpoint, self.risk_endpoint,
            ttl_seconds=TTL_SECONDS, dispatch_recheck_seconds=1,
        )
        self.ready_file = self.risk_root / "ready.json"
        self.risk = self.client = self.operator = None
        self.strategy = support.strategy_session(self.gateway_endpoint, self.keys, self.journal_id)
        assert self.strategy.start(timeout_seconds=15).healthy

    def start_risk(self, fault_point=None):
        self.risk = support.start_risk(self.config, self.ready_file, fault_point=fault_point)
        self.client = support.risk_client(self.risk_endpoint, self.keys)
        assert self.client.start(timeout_seconds=15).healthy
        self.operator = support.operator_runtime(self.risk_endpoint, self.keys)
        assert self.operator.start(timeout_seconds=15).healthy

    def drop_clients(self):
        for client in (self.client, self.operator):
            if client is not None:
                try:
                    client.close(timeout_seconds=5)
                except Exception:
                    pass
        self.client = self.operator = None

    def admin(self, operation, arguments=None):
        return support.admin(self.operator, operation, arguments)

    def delegated(self, key):
        from bt_api_py.gateway_api import GatewayBtApi

        return GatewayBtApi(self.strategy, exchange_name="CTP").delegated_command_status("CTP", key)

    def ingest(self, client_order_id, status, quantity, filled):
        self.controls.put((client_order_id, status, quantity, filled))
        item = support.next_message(self.messages, "ingested")
        assert item[1:3] == (client_order_id, "ATTRIBUTED"), item

    def prime_trading_day(self):
        """A strategy's own direct order: its return gives Risk the trading day (as in 64.09)."""
        command = self.strategy.command_client
        assert command.reserve(support.order_request("direct-1", "client-direct-1"),
                               timeout_seconds=10).record is not None
        assert command.dispatch("direct-1", timeout_seconds=10).outcome in {"QUEUED", "OBSERVED"}
        sent = support.until(lambda: command.status("direct-1", timeout_seconds=5),
                             lambda r: r.record is not None and r.record.state == "SENT")
        assert sent.record.state == "SENT", sent
        self.ingest("client-direct-1", "accepted", "1", "0")
        summary = support.until(lambda: self.admin("risk.admin.status"),
                                lambda r: r["summary"]["bypass_orders"] >= 1)
        assert summary["summary"]["observation"]["continuity"] == "VERIFIED", summary

    def crash_wait(self, point):
        exit_code = self.risk.wait(timeout=30)
        stderr = self.risk.stderr.read().decode("ascii", "replace")
        assert exit_code == FAULT_EXIT_CODE, (exit_code, stderr)
        assert "RISK_SERVICE_FAULT_INJECTED " + point in stderr, stderr
        self.risk = None
        self.drop_clients()

    def close(self):
        self.drop_clients()
        support.stop_risk(self.risk)
        if self.strategy is not None:
            self.strategy.close(timeout_seconds=5)
        self.gateway_stop.close()


@pytest.fixture
def loopback(tmp_path_factory):
    root = support.retained_loopback_root(tmp_path_factory.getbasetemp())
    print(f"Retained synthetic Risk loopback root: {root}")
    state = _Loopback(root)
    try:
        yield state
    finally:
        state.close()
    assert state.gateway.exitcode == 0


def _strategy_view(loopback, key):
    """What the strategy learns from Risk about one of its keys."""
    return _answer(loopback.client.status(key, timeout_seconds=10))


def _no_send(loopback, client_order_id):
    places = _places(loopback.controls, loopback.messages)
    assert places.get(client_order_id, 0) == 0, places


def test_after_local_reserve_keeps_unknown_and_never_reaches_the_gateway(loopback):
    point = "after_local_reserve"
    loopback.start_risk(point)
    loopback.prime_trading_day()
    request = support.order_request("risk-1", "risk-client-1")
    crashed = loopback.client.reserve(request, request_key="risk-1", timeout_seconds=4)
    assert crashed.outcome == "UNKNOWN", crashed  # never a refusal
    loopback.crash_wait(point)
    assert support.risk_permit(loopback.risk_root, "risk-1") == [
        {"state": "RESERVING", "gateway_state": None, "reservation_id": None, "code": None}]
    assert support.gateway_command(loopback.gateway_root, STRATEGY.actor_ref, "risk-1") == []

    loopback.start_risk()
    # Recovery asked the Gateway; "not found" is no NOT_SENT proof: UNKNOWN, occupancy kept.
    view = _strategy_view(loopback, "risk-1")
    assert (view.outcome, view.state) == ("UNKNOWN", "UNKNOWN"), view
    time.sleep(3)  # several maintain cycles
    view = _strategy_view(loopback, "risk-1")
    assert (view.outcome, view.state) == ("UNKNOWN", "UNKNOWN"), view
    unknown = loopback.admin("risk.unknown.list")["permits"]
    assert [row["request_key"] for row in unknown] == ["risk-1"], unknown
    # The same identity asked again is not re-reserved at the Gateway and not refused.
    retried = _answer(loopback.client.reserve(request, request_key="risk-1", timeout_seconds=10))
    assert (retried.outcome, retried.state) == ("UNKNOWN", "UNKNOWN"), retried
    assert support.gateway_command(loopback.gateway_root, STRATEGY.actor_ref, "risk-1") == []
    assert loopback.delegated("risk-1").observation is None
    _no_send(loopback, "risk-client-1")


def test_after_gateway_reserve_recovers_by_status_and_releases_only_with_proof(loopback):
    point = "after_gateway_reserve"
    loopback.start_risk(point)
    loopback.prime_trading_day()
    request = support.order_request("risk-1", "risk-client-1")
    crashed = loopback.client.reserve(request, request_key="risk-1", timeout_seconds=4)
    assert crashed.outcome == "UNKNOWN", crashed
    loopback.crash_wait(point)
    # Risk recorded nothing of the Gateway's answer; the Gateway holds the reservation.
    assert [row["state"] for row in support.risk_permit(loopback.risk_root, "risk-1")] == ["RESERVING"]
    (row,) = support.gateway_command(loopback.gateway_root, STRATEGY.actor_ref, "risk-1")
    assert row[0] == "RESERVED", row
    gateway_reservation = row[2]

    loopback.start_risk()
    # Not dispatched: once its TTL has passed (here already at restart) Risk releases the
    # Gateway reservation and settles on that proof; the strategy never sees a refusal.
    released = support.until(lambda: _strategy_view(loopback, "risk-1"),
                             lambda v: v.outcome not in ("RESERVED", "UNKNOWN"),
                             seconds=TTL_SECONDS + 15)
    assert (released.outcome, released.state) == ("RELEASED", "RELEASED"), released
    assert released.gateway_state in ("RELEASED", "EXPIRED"), released
    # Recovered by status onto the Gateway's own reservation (not a new one), then released.
    assert released.reservation_id == gateway_reservation
    records = loopback.admin("risk.audit.read", {"after": 0, "limit": 500,
                                                 "through": None, "snapshot": None})["records"]
    path = [(r["kind"], r["detail"].get("from"), r["detail"].get("to"))
            for r in records if r.get("request_key") == "risk-1"]
    assert ("resolve", "RESERVING", "RESERVED") in path, path
    assert path[-1][0] == "released", path
    (row,) = support.gateway_command(loopback.gateway_root, STRATEGY.actor_ref, "risk-1")
    assert row[0] in ("RELEASED", "EXPIRED") and row[2] == gateway_reservation, row
    assert loopback.admin("risk.unknown.list")["permits"] == []
    _no_send(loopback, "risk-client-1")


def test_after_claim_commit_does_not_resend_and_stays_unknown_without_proof(loopback):
    point = "after_claim_commit"
    loopback.start_risk(point)
    loopback.prime_trading_day()
    reserved = _answer(loopback.client.reserve(support.order_request("risk-1", "risk-client-1"),
                                               request_key="risk-1", timeout_seconds=10))
    assert reserved.outcome == "RESERVED", reserved
    crashed = loopback.client.dispatch("risk-1", permit_id=reserved.permit_id,
                                       reservation_id=reserved.reservation_id,
                                       view_digest=reserved.view_digest, timeout_seconds=4)
    assert crashed.outcome == "UNKNOWN", crashed
    loopback.crash_wait(point)
    assert [row["state"] for row in support.risk_permit(loopback.risk_root, "risk-1")] == ["CLAIMED"]
    (row,) = support.gateway_command(loopback.gateway_root, STRATEGY.actor_ref, "risk-1")
    assert row[0] == "RESERVED", row

    loopback.start_risk()
    # Past the reservation TTL and several re-check intervals Risk never sends the dispatch
    # itself. While the Gateway holds the command RESERVED the strategy sees UNKNOWN; Risk
    # settles RELEASED only on a terminal Gateway answer (RELEASED/EXPIRED) for the same
    # reservation, e.g. after releasing its own expired reservation at the Gateway.
    deadline = time.monotonic() + TTL_SECONDS + 8
    seen = set()
    view = None
    while time.monotonic() < deadline:
        view = _strategy_view(loopback, "risk-1")
        seen.add((view.outcome, view.state))
        (row,) = support.gateway_command(loopback.gateway_root, STRATEGY.actor_ref, "risk-1")
        assert row[0] in ("RESERVED", "RELEASED", "EXPIRED"), row  # never DISPATCHING/SENT
        if view.outcome == "RELEASED":
            assert view.gateway_state in ("RELEASED", "EXPIRED"), view
            assert row[0] in ("RELEASED", "EXPIRED") and row[2] == view.reservation_id, row
            break
        assert row[0] == "RESERVED", row  # unsettled only while the Gateway still holds it
        time.sleep(0.5)
    assert seen <= {("UNKNOWN", "CLAIMED"), ("UNKNOWN", "UNKNOWN"), ("RELEASED", "RELEASED")}, seen
    unknown = [r["request_key"] for r in loopback.admin("risk.unknown.list")["permits"]]
    # The operator list names UNKNOWN permits only (a CLAIMED one is not listed: reported).
    assert unknown == (["risk-1"] if view.state == "UNKNOWN" else []), (unknown, view)
    _no_send(loopback, "risk-client-1")


def test_after_dispatch_sent_recovers_dispatched_then_terminal_from_the_stream(loopback):
    point = "after_dispatch_sent"
    loopback.start_risk(point)
    loopback.prime_trading_day()
    reserved = _answer(loopback.client.reserve(support.order_request("risk-1", "risk-client-1"),
                                               request_key="risk-1", timeout_seconds=10))
    assert reserved.outcome == "RESERVED", reserved
    crashed = loopback.client.dispatch("risk-1", permit_id=reserved.permit_id,
                                       reservation_id=reserved.reservation_id,
                                       view_digest=reserved.view_digest, timeout_seconds=4)
    assert crashed.outcome == "UNKNOWN", crashed
    loopback.crash_wait(point)
    assert [row["state"] for row in support.risk_permit(loopback.risk_root, "risk-1")] == ["CLAIMED"]
    sent = support.until(
        lambda: support.gateway_command(loopback.gateway_root, STRATEGY.actor_ref, "risk-1"),
        lambda rows: rows and rows[0][0] == "SENT")
    assert sent[0][0] == "SENT", sent

    loopback.start_risk()
    view = support.until(lambda: _strategy_view(loopback, "risk-1"),
                         lambda v: v.outcome == "DISPATCHED")
    assert (view.outcome, view.state) == ("DISPATCHED", "DISPATCHED"), view
    loopback.ingest("risk-client-1", "accepted", "1", "0")
    loopback.ingest("risk-client-1", "filled", "1", "1")
    # The crashed process's account-observer registration stays live at the Gateway until
    # its lease (30 s registry default) lapses; only then is the stream verified again.
    terminal = support.until(lambda: _strategy_view(loopback, "risk-1"),
                             lambda v: v.outcome == "TERMINAL", seconds=60, interval=0.25)
    assert (terminal.outcome, terminal.state) == ("TERMINAL", "TERMINAL"), terminal
    places = _places(loopback.controls, loopback.messages)
    assert places.get("risk-client-1") == 1, places  # exactly one native send
