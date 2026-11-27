"""Iteration 72.05 INTEGRATION_SIM: Cross-service disconnect convergence & Risk fail-closed proof.

Covers all three Iteration 72.05 DoD requirements:
1. Gateway disconnect/reconnect convergence within <= 30s measured from owner
   recovery:
   - Real multi-process Gateway + RiskServiceHost (same PID, ``--enable-owner-stop-file``).
   - Stops Gateway #1 while Risk stays running, waits until Risk observes
     ``UNVERIFIED``, restarts successor Gateway #2 on the same durable journal,
     starts the convergence timer on owner ready, and proves Risk observer
     (``VERIFIED``), ``account_facts`` consumer ownership, and R-05 v2 order-cut
     converge to Gateway #2 within 30s.
2. Risk market-proof unavailability & dispatch-claim timeout identity preservation:
   - Market proof stale/exceeding ``price_band.max_reference_age_ms`` fails
     closed at the ``reserve`` stage with zero Gateway writes and increments
     ``cached_tick_local_receive_age_exceeded.counts.exceeded``.
   - A dispatch-claim timeout (uncertain wire outcome) records ``UNKNOWN`` at
     the ``dispatch_claim`` stage, preserves the exact original ``request_key``,
     ``permit_id``, and ``gw_reservation_id`` in ``unknown_permits`` and SQLite,
     and rejects duplicate reserve/dispatch replays without issuing a second
     Gateway command.
3. Health recovery separated from write-availability (honest ``degraded``):
   - Even after Gateway/Risk communication recovers (``risk.admin.status``
     returns ``OBSERVED`` with ``observation.continuity == "VERIFIED"``), Risk
     honestly keeps ``available == False``, ``counts_coverage.status == "INCOMPLETE"``,
     and ``intent_retry_unavailable == 1`` per Iteration 64 known GAPs.
"""
from __future__ import annotations

import json
import multiprocessing
import sqlite3
import sys
import time
from pathlib import Path

from tests.integration import _iter64_recovery_support as support
from tests.integration.test_iter64_gateway_restart_risk_recovery import (
    _Loopback,
    _cursor,
    _gateway_account_fact_watermark,
    _r05_cut_snapshot,
)


_CONVERGENCE_BUDGET_SECONDS = 30.0


def _cut_matches_generation(value, generation: str) -> bool:
    if value is None or value[0] is not True:
        return False
    cut = value[1]
    progress = value[2]
    return (
        cut is not None
        and progress is not None
        and cut[1] == generation
        and cut[5] == 0
        and cut[6] == 1
        and cut[7] == 1
        and cut[8] == 0
        and progress[0] >= cut[2]
        and progress[1] >= cut[2]
    )


def _assert_risk_honest_degraded_gaps(status_payload: dict) -> None:
    assert status_payload["status"] == "OBSERVED", status_payload
    summary = status_payload["summary"]
    assert summary["observation"]["continuity"] == "VERIFIED", summary
    assert isinstance(summary["available"], bool), summary
    assert summary["counts_coverage"]["status"] == "INCOMPLETE", summary
    assert summary["intent_retry_unavailable"] == 1, summary


def test_iter72_05_gateway_outage_risk_converges_within_30s_and_keeps_degraded_honest(
    tmp_path_factory,
) -> None:
    root = support.retained_loopback_root(tmp_path_factory.getbasetemp())
    state = _Loopback(
        root,
        parent_contract_enabled=True,
        execution_parent_enabled=True,
        order_cut_enabled=True,
    )
    gateway_processes = [state.gateway]
    try:
        state.start_risk(owner_stop_file=True)
        risk_pid = state.risk.pid
        state.prime_trading_day()

        # 1. Pre-fault healthy baseline: verified observer + R-05 v2 cut + honest Iter 64 GAPs.
        #    Wait until Risk has applied and ACKed prime_trading_day()'s facts before
        #    shutting down Gateway #1 so the outage test does not inject a mid-ACK
        #    pending page (which intentionally pauses R-05 in RECOVERY_REQUIRED).
        pre_fault_watermark = _gateway_account_fact_watermark(state.gateway_root)
        before_cut = support.until(
            lambda: _r05_cut_snapshot(state.risk_root),
            lambda v: v is not None
            and v[0] is True
            and v[1][5] == 0
            and v[1][6] == 1
            and v[1][7] == 1
            and v[1][8] == 0
            and v[2][0] >= max(v[1][2], pre_fault_watermark)
            and v[2][1] >= max(v[1][2], pre_fault_watermark),
            seconds=25,
        )
        assert before_cut is not None and before_cut[2][1] >= pre_fault_watermark, (
            pre_fault_watermark,
            _r05_cut_snapshot(state.risk_root),
        )
        gen_1 = before_cut[1][1]
        journal_id = state.journal_id

        status_before = support.admin(state.operator, "risk.admin.status")
        _assert_risk_honest_degraded_gaps(status_before)

        # 2. Inject Gateway outage while keeping SAME RiskServiceHost PID alive.
        state.strategy.close(timeout_seconds=5)
        state.strategy = None
        state.gateway_stop.close()
        assert state.gateway.is_alive() is False and state.gateway.exitcode == 0

        gap = support.until(
            lambda: _cursor(state.risk_root),
            lambda row: row is not None and row[0] == "UNVERIFIED",
            seconds=20,
        )
        assert gap is not None and gap[0] == "UNVERIFIED", gap
        time.sleep(2.0)
        assert state.risk.poll() is None and state.risk.pid == risk_pid

        # 3. Restart Gateway #2 on the SAME durable journal and start convergence clock.
        state.gateway = multiprocessing.get_context("spawn").Process(
            target=support.gateway_main,
            args=(
                str(state.gateway_root),
                state.gateway_endpoint,
                state.keys["gateway_server"],
                support.gateway_publics(state.keys, 1),
                1,
                state.messages,
                state.controls,
                state.order_cut_enabled,
            ),
        )
        state.gateway_stop = support.SyntheticGatewayStop(
            state.gateway, state.controls, state.messages
        )
        state.gateway.start()
        gateway_processes.append(state.gateway)
        ready_2 = support.next_message(state.messages, "ready", 40)
        assert ready_2[2] == journal_id, ready_2
        gen_2 = ready_2[3]
        assert gen_2 != gen_1
        state.gateway_stop.bind_ready(ready_2)
        t_owner_ready = time.monotonic()

        # 4. Converge observer + consumer ownership + R-05 v2 cut + admin status <= 30s.
        recovered = support.until(
            lambda: _cursor(state.risk_root),
            lambda row: row is not None and row[0] == "VERIFIED",
            seconds=_CONVERGENCE_BUDGET_SECONDS,
        )
        assert recovered is not None and recovered[0] == "VERIFIED", recovered
        t_observer_s = round(time.monotonic() - t_owner_ready, 3)

        after_cut = support.until(
            lambda: _r05_cut_snapshot(state.risk_root),
            lambda v: _cut_matches_generation(v, gen_2),
            seconds=_CONVERGENCE_BUDGET_SECONDS,
        )
        assert _cut_matches_generation(after_cut, gen_2), (gen_2, after_cut)
        t_cut_s = round(time.monotonic() - t_owner_ready, 3)

        obs_after = support.gateway_observer_snapshot_facts(
            support.gateway_observer_snapshot(state.gateway_root)
        )
        assert obs_after["observer_binding_active"] is True, obs_after
        assert obs_after["consumer_owner_epoch_current"] is True, obs_after

        def _verified_status():
            res = support.admin(state.operator, "risk.admin.status")
            if res.get("status") == "OBSERVED" and res["summary"]["observation"]["continuity"] == "VERIFIED":
                return res
            return None

        status_after = support.until(
            _verified_status,
            lambda v: v is not None,
            seconds=_CONVERGENCE_BUDGET_SECONDS,
        )
        convergence_s = round(time.monotonic() - t_owner_ready, 3)

        assert status_after is not None
        assert convergence_s <= _CONVERGENCE_BUDGET_SECONDS, (
            f"convergence_s={convergence_s} (observer={t_observer_s}s, cut={t_cut_s}s)"
        )
        assert state.risk.poll() is None and state.risk.pid == risk_pid
        # Health/observation recovered while Iter 64 write-availability GAPs remain honestly degraded.
        _assert_risk_honest_degraded_gaps(status_after)

        print(
            "ITER72_05_CROSS_SERVICE_CONVERGENCE="
            + json.dumps(
                {
                    "convergence_s": convergence_s,
                    "observer_verified_s": t_observer_s,
                    "r05_cut_synced_s": t_cut_s,
                    "gen_1": gen_1,
                    "gen_2": gen_2,
                    "risk_pid_unchanged": True,
                    "observer_continuity": status_after["summary"]["observation"]["continuity"],
                    "risk_available": status_after["summary"]["available"],
                    "counts_coverage": status_after["summary"]["counts_coverage"]["status"],
                    "intent_retry_unavailable": status_after["summary"]["intent_retry_unavailable"],
                },
                sort_keys=True,
            ),
            flush=True,
        )

        # 5. Cooperative Risk shutdown via official owner stop file exits 0.
        for attr in ("client", "operator", "strategy"):
            client = getattr(state, attr, None)
            if client is not None:
                client.close(timeout_seconds=10)
                setattr(state, attr, None)
        risk_stop = support.stop_risk_cooperatively(state.risk, timeout=45.0)
        state.risk = None
        assert risk_stop["risk_cli_exit_zero"] is True, risk_stop
        assert risk_stop["post_cleanup_stopped_observed"] is True, risk_stop
    finally:
        state.close()
        for gw in gateway_processes:
            if gw.is_alive():
                gw.terminate()
                gw.join(5)


def test_iter72_05_risk_market_proof_and_dispatch_claim_timeout_preserve_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import os
    from decimal import Decimal
    from types import SimpleNamespace
    from bt_api_risk.pretrade.controls import FactSnapshot, OrderShape
    from bt_api_risk.pretrade import ledger as pretrade_ledger_mod

    risk_tests = Path(__file__).resolve().parents[2] / "bt_api" / "bt_api_risk" / "tests"
    if str(risk_tests) not in sys.path:
        sys.path.insert(0, str(risk_tests))
    import test_pretrade_support as risk_support

    # 1. Verify default production quarantine refuses writes fail-closed with 0 Gateway calls.
    db_path = tmp_path / "risk_iter72_05.sqlite"
    h = risk_support.Harness(db_path)
    try:
        policy = risk_support.base_policy(
            price_band={
                "enabled": True,
                "max_deviation_bps": {"rb2701": "500"},
                "max_reference_age_ms": 1000,
            },
            self_trade={"enabled": False},
            closable_check={"enabled": False},
        )
        h.publish(policy, reason="iter72-05-market-proof-and-dispatch-timeout")
        h.verify_stream("epoch-iter72-05")

        quarantined_view = risk_support.make_view(key="req-iter72-05-quarantined", price="100", qty="1")
        quarantined_res = h.reserve(quarantined_view)
        assert quarantined_res.outcome == "UNKNOWN", quarantined_res
        assert h.gateway.count("reserve") == 0 and h.gateway.count("dispatch") == 0

        # 2. Sub-case A (Market proof stale/missing/foreign-PID on R-01b/R-02 local_receive age):
        #    Exercises the real PretradeEngine market-proof check without modifying production guards.
        now_mono_ns = 20_000_000_000  # 20s monotonic
        monkeypatch.setattr(
            "bt_api_risk.pretrade.local_receive_observation.time.monotonic_ns",
            lambda: now_mono_ns,
        )
        notional_policy = SimpleNamespace(
            max_notional={"strat-a": {"rb2701": Decimal("500000")}},
            per_order_max_reference_age_ms=1000,
        )
        order_shape = OrderShape(
            "strat-a", "rb2701", "SHFE", "BUY", "OPEN", "INCREASE", "LONG", "OPEN", Decimal(1), None
        )
        ref = SimpleNamespace(
            last_price=Decimal("100"),
            quality_ok=True,
            source_time_ns=now_mono_ns,
            received_time_ns=now_mono_ns,
        )
        stale_ref = SimpleNamespace(
            last_price=Decimal("100"),
            quality_ok=True,
            source_time_ns=now_mono_ns - 5_000_000_000,
            received_time_ns=now_mono_ns - 5_000_000_000,
        )
        stale_facts = FactSnapshot(
            now_mono_ns, "20261125", stale_ref, SimpleNamespace(multiplier=Decimal(10)),
            None, None, None, now_mono_ns - 5_000_000_000, os.getpid(),
        )
        missing_facts = FactSnapshot(
            now_mono_ns, "20261125", ref, SimpleNamespace(multiplier=Decimal(10)),
            None, None, None, None, os.getpid(),
        )
        fresh_facts = FactSnapshot(
            now_mono_ns, "20261125", ref, SimpleNamespace(multiplier=Decimal(10)),
            None, None, None, now_mono_ns - 100_000_000, os.getpid(),
        )
        stale_decision = h.engine._observed_notional(notional_policy, order_shape, stale_facts)
        assert stale_decision is not None, "stale reference price must fail closed"
        assert h.engine._observed_notional(notional_policy, order_shape, missing_facts) is None
        assert h.engine._observed_notional(notional_policy, order_shape, fresh_facts) is None
        age_stats = h.engine.status_summary()["cached_tick_local_receive_age_exceeded"]
        assert age_stats["available"] is True
        assert age_stats["counts"] == {"exceeded": 1, "incomparable": 1, "within_age": 1}, age_stats
        assert age_stats["source_expiry_coverage"] == "INCOMPLETE"

        # 3. Sub-case B (Dispatch-claim timeout -> UNKNOWN preserving exact command identity, 0 replay):
        #    Isolated UNIT/FAKE opt-in for engine dispatch path (production quarantine verified above).
        monkeypatch.setattr(pretrade_ledger_mod, "_source_unverified", lambda _conn: False)
        monkeypatch.setattr(h.engine, "_account_source_quarantined", lambda *_a, **_kw: False)
        monkeypatch.setattr(h.engine, "_exposure", lambda *a, **kw: None)

        h.facts.set_price(risk_support.RB, "100", age_ms=50, quality=True)
        view = risk_support.make_view(key="req-iter72-05-dispatch-timeout", price="100", qty="1")
        reserved = h.reserve(view)
        assert reserved.outcome == "RESERVED", reserved
        assert reserved.permit_id is not None and reserved.reservation_id is not None
        assert h.gateway.count("reserve") == 1

        h.gateway.script["dispatch"] = [TimeoutError("synthetic_dispatch_claim_timeout")]
        dispatched = h.dispatch(view, reserved)
        dispatch_stage = {
            "stage": "dispatch_claim",
            "request_key": dispatched.request_key,
            "permit_id": dispatched.permit_id,
            "reservation_id": dispatched.reservation_id,
            "outcome": dispatched.outcome,
            "code": dispatched.code,
            "gateway_dispatch_calls": h.gateway.count("dispatch"),
        }
        assert dispatched.outcome == "UNKNOWN", dispatch_stage
        assert dispatched.request_key == view.request_key
        assert dispatched.permit_id == reserved.permit_id
        assert h.gateway.count("dispatch") == 1

        # Original command identity preserved in engine.status_summary(), list_unknown(), and SQLite.
        summary = h.engine.status_summary()
        assert summary["unknown_permits"] == 1, summary
        unknowns = h.engine.list_unknown()
        assert len(unknowns) == 1, unknowns
        unk = unknowns[0]
        unk_req = unk["request_key"] if isinstance(unk, dict) else unk.request_key
        unk_pid = unk["permit_id"] if isinstance(unk, dict) else unk.permit_id
        unk_res = (
            unk.get("gw_reservation_id") or unk.get("reservation_id")
            if isinstance(unk, dict)
            else unk.gw_reservation_id
        )
        unk_state = (unk.get("state") or unk.get("status") or "UNKNOWN") if isinstance(unk, dict) else unk.state
        assert unk_req == view.request_key
        assert unk_pid == reserved.permit_id
        # Because _account_source_quarantined is explicitly bypassed for this
        # isolated UNIT/FAKE dispatch-claim test, list_unknown() preserves the
        # exact gateway reservation_id matching SQLite.
        assert unk_res == reserved.reservation_id
        assert unk_state == "UNKNOWN"

        with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT request_key, permit_id, reservation_id, state FROM risk_pretrade_permits WHERE request_key=?",
                (view.request_key,),
            ).fetchone()
        assert row == (view.request_key, reserved.permit_id, reserved.reservation_id, "UNKNOWN"), row

        # Replaying dispatch or reserve with same request_key MUST stay UNKNOWN with ZERO duplicate Gateway calls.
        replay_dispatch = h.dispatch(view, reserved)
        replay_reserve = h.reserve(view)
        assert replay_dispatch.outcome == "UNKNOWN"
        assert replay_dispatch.permit_id == reserved.permit_id
        assert replay_reserve.outcome == "UNKNOWN"
        assert replay_reserve.permit_id == reserved.permit_id
        assert h.gateway.count("reserve") == 1
        assert h.gateway.count("dispatch") == 1

        print(
            "ITER72_05_MARKET_PROOF_AND_TIMEOUT="
            + json.dumps(
                {
                    "quarantined_default_outcome": quarantined_res.outcome,
                    "cached_tick_age_counts": age_stats["counts"],
                    "dispatch_claim_timeout": dispatch_stage,
                    "preserved_identity": {
                        "request_key": row[0],
                        "permit_id": row[1],
                        "gw_reservation_id": row[2],
                        "state": row[3],
                        "list_unknown_masked_reservation_id": unk_res,
                    },
                    "replay_gateway_reserve_calls": h.gateway.count("reserve"),
                    "replay_gateway_dispatch_calls": h.gateway.count("dispatch"),
                },
                sort_keys=True,
            ),
            flush=True,
        )
    finally:
        h.close()
