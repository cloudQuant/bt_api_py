"""Synthetic Gateway restart while the same Risk service and journal stay alive.

The Gateway has synthetic venue calls and loopback CURVE.  This does not use an
account or prove provider recovery. R-05 is explicitly disabled by the fixture.
"""
from __future__ import annotations

import multiprocessing
import sqlite3

import pytest

from tests.integration import _iter64_recovery_support as support
from tests.integration.test_iter64_risk_crash_matrix import _answer, _Loopback

# The synthetic Gateway journal is now schema 19, so the account-observer ACK
# handoff succeeds and Risk reports observation continuity VERIFIED.  This
# scenario is still blocked by the unwired v1 write path: the first Risk
# reservation is refused with UNKNOWN risk_permit_mismatch, so the Gateway
# restart is never reached.
RISK_V1_WRITES_BLOCKED = pytest.mark.xfail(
    strict=True,
    reason="risk_v1_writes_quarantined: the first Risk reservation returns "
           "UNKNOWN risk_permit_mismatch because the v1 first-creation proof is "
           "not wired; the synthetic Gateway journal is schema 19 and the "
           "account-observer ACK handoff succeeds",
)


def _cursor(root):
    with sqlite3.connect(f"file:{root / 'state' / 'risk.sqlite'}?mode=ro", uri=True) as db:
        return db.execute("SELECT continuity, gap_reason FROM risk_pretrade_cursor").fetchone()


@RISK_V1_WRITES_BLOCKED
def test_gateway_restart_recovers_original_risk_process_without_replaying_writes(tmp_path_factory):
    root = support.retained_loopback_root(tmp_path_factory.getbasetemp())
    print(f"Retained synthetic Risk loopback root: {root}")
    state = _Loopback(root)
    try:
        state.start_risk()
        state.prime_trading_day()
        risk_pid = state.risk.pid
        request = support.order_request("before-restart", "client-before-restart")
        reserved = _answer(state.client.reserve(request, request_key="before-restart",
                                                timeout_seconds=10))
        assert reserved.outcome == "RESERVED", reserved
        dispatch = _answer(state.client.dispatch(
            "before-restart", permit_id=reserved.permit_id,
            reservation_id=reserved.reservation_id, view_digest=reserved.view_digest,
            timeout_seconds=10))
        assert dispatch.outcome == "DISPATCHED", dispatch
        sent = support.until(lambda: support.gateway_command(state.gateway_root, support.strategy_identity(0).actor_ref,
                                                              "before-restart"),
                             lambda rows: rows and rows[0][0] == "SENT", seconds=15)
        assert sent and sent[0][0] == "SENT", sent
        state.strategy.close(timeout_seconds=5)
        state.strategy = None
        state.gateway_stop.close()
        assert state.gateway.exitcode == 0
        gap = support.until(lambda: _cursor(state.risk_root),
                            lambda row: row is not None and row[0] == "UNVERIFIED", seconds=20)
        assert gap is not None and gap[0] == "UNVERIFIED", gap

        state.gateway = multiprocessing.get_context("spawn").Process(
            target=support.gateway_main,
            args=(str(state.gateway_root), state.gateway_endpoint, state.keys["gateway_server"],
                  support.gateway_publics(state.keys, 1), 1, state.messages, state.controls),
        )
        state.gateway_stop = support.SyntheticGatewayStop(
            state.gateway, state.controls, state.messages)
        state.gateway.start()
        ready = support.next_message(state.messages, "ready", 40)
        assert ready[2] == state.journal_id
        state.gateway_stop.bind_ready(ready)
        state.strategy = support.strategy_session(state.gateway_endpoint, state.keys, state.journal_id)
        assert state.strategy.start(timeout_seconds=20).healthy
        recovered = support.until(lambda: _cursor(state.risk_root),
                                  lambda row: row is not None and row[0] == "VERIFIED", seconds=40)
        assert recovered is not None and recovered[0] == "VERIFIED", recovered
        assert state.risk.poll() is None and state.risk.pid == risk_pid
        # The previous identity remains sent exactly once, without a new native call.
        (old,) = support.gateway_command(state.gateway_root, support.strategy_identity(0).actor_ref,
                                          "before-restart")
        assert old[0] == "SENT" and old[2] == reserved.reservation_id, old
        old_status = _answer(state.client.status("before-restart", timeout_seconds=10))
        assert old_status.reservation_id == reserved.reservation_id
        new = _answer(state.client.reserve(support.order_request("after-restart", "client-after-restart"),
                                           request_key="after-restart", timeout_seconds=10))
        assert new.outcome == "RESERVED", (new.outcome, new.code, new.gateway_code)
        placed = _answer(state.client.dispatch("after-restart", permit_id=new.permit_id,
                                              reservation_id=new.reservation_id,
                                              view_digest=new.view_digest, timeout_seconds=10))
        assert placed.outcome == "DISPATCHED", placed
        after = support.until(lambda: support.gateway_command(state.gateway_root, support.strategy_identity(0).actor_ref,
                                                               "after-restart"),
                              lambda rows: rows and rows[0][0] == "SENT", seconds=15)
        assert after and after[0][0] == "SENT", after
        state.controls.put("stats")
        places = support.next_message(state.messages, "stats")[1]
        assert places.get("client-before-restart", 0) == 0
        assert places.get("client-after-restart", 0) == 1
    finally:
        state.close()
