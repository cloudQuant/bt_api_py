"""Iteration 72.04: Keep-same-Risk-PID automatic recovery across Gateway restart.

Verifies that when Gateway stops and restarts on the same durable journal while
the original RiskServiceHost process stays running:
1. During the Gateway outage, Risk does NOT prematurely attempt
   ``service.unregister`` on its strict ``account_facts`` runtime into a dead
   endpoint, so it never falsely latches ``_cleanup_uncertain``.
2. Once the successor Gateway starts on the same journal with a new
   ``service_epoch`` and ``gateway_generation``:
   - Risk's observer reconnects and reaches ``VERIFIED``;
   - Risk cleanly closes the retired ``account_facts`` runtime via exact
     retired-registration unregister ACK on the successor Gateway;
   - Risk opens a fresh ``account_facts`` runtime on the new epoch, updates
     persistent consumer ownership and R-05 v2 order-cut to the new Gateway
     generation;
   - Zero prior commands are replayed;
   - A new Execution v2 parent/child dispatches once through the same Risk PID
     to the successor Gateway and advances the R-05 cut;
   - Cooperative Risk shutdown exits cleanly with code 0.
"""
from __future__ import annotations

import json
import multiprocessing
import sqlite3
import time
from pathlib import Path

import pytest

from tests.integration import _iter64_recovery_support as support
from tests.integration.test_iter64_gateway_restart_risk_recovery import (
    _ExecutionParentLoopback,
    _Loopback,
    _cursor,
    _gateway_places,
    _gateway_rows,
    _r05_cut_snapshot,
    _rows_by_key,
)


def _gateway_fact_watermark(gateway_root: Path) -> int:
    db = Path(gateway_root) / "journal.sqlite"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) FROM account_fact_stream_events WHERE account=?",
            (support.ACCOUNT,),
        ).fetchone()
    return int(row[0]) if row is not None else 0


def _cut_matches_generation(value, generation: str, *, minimum_watermark: int = 0) -> bool:
    if value is None or value[0] is not True:
        return False
    cut = value[1]
    progress = value[2]
    return (
        cut is not None
        and progress is not None
        and cut[1] == generation
        and cut[2] >= minimum_watermark
        and cut[5] == 0
        and cut[6] == 1
        and cut[7] == 1
        and cut[8] == 0
        and progress[0] >= cut[2]
        and progress[1] >= cut[2]
    )


def test_same_risk_pid_recovers_account_facts_and_r05_cut_after_gateway_restart(
    tmp_path_factory,
) -> None:
    root = support.retained_loopback_root(tmp_path_factory.getbasetemp())
    state = _Loopback(
        root,
        parent_contract_enabled=True,
        execution_parent_enabled=True,
        order_cut_enabled=True,
    )
    execution = None
    risk_process = None
    risk_pid = None
    gateway_processes = [state.gateway]
    try:
        state.start_risk(owner_stop_file=True)
        risk_process = state.risk
        risk_pid = risk_process.pid
        state.prime_trading_day()

        # 1. Confirm healthy R-05 v2 cut + observer before Gateway restart.
        fresh_before = support.until(
            lambda: _r05_cut_snapshot(state.risk_root),
            lambda v: v is not None
            and v[0] is True
            and v[1][5] == 0
            and v[1][6] == 1
            and v[1][7] == 1
            and v[1][8] == 0
            and v[2][0] >= v[1][2]
            and v[2][1] >= v[1][2],
            seconds=25,
        )
        assert fresh_before is not None and fresh_before[0] is True, fresh_before
        original_generation = fresh_before[1][1]

        execution = _ExecutionParentLoopback(state, root)
        execution.start()

        baseline_places = _gateway_places(state)
        baseline_rows = _gateway_rows(state)
        before = execution.submit("parent-before-same-pid-restart")
        assert before["status"] == "RECEIVED", before

        sent_rows = support.until(
            lambda: _gateway_rows(state),
            lambda rows: len(rows) == len(baseline_rows) + 1
            and any(
                row[2] == "SENT"
                for row in rows
                if (row[0], row[1]) not in _rows_by_key(baseline_rows)
            ),
            seconds=25,
        )
        assert sent_rows is not None, _gateway_rows(state)
        rows_before_restart = _rows_by_key(sent_rows)
        first_keys = set(rows_before_restart) - set(_rows_by_key(baseline_rows))
        assert len(first_keys) == 1, sent_rows
        (first_key,) = first_keys
        first_row = rows_before_restart[first_key]
        first_client_id = json.loads(first_row[5]).get("client_order_id")
        assert type(first_client_id) is str and first_client_id

        places_before = support.until(
            lambda: _gateway_places(state),
            lambda p: len(set(p) - set(baseline_places)) == 1,
            seconds=15,
        )
        assert places_before is not None
        original_native_ids = set(places_before)

        state.ingest(first_client_id, "accepted", "1", "0")
        state.ingest(first_client_id, "filled", "1", "1")
        fresh_first = support.until(
            lambda: _r05_cut_snapshot(state.risk_root),
            lambda v: _cut_matches_generation(v, original_generation),
            seconds=25,
        )
        assert fresh_first is not None, _r05_cut_snapshot(state.risk_root)

        journal_id = state.journal_id
        obs_before = support.gateway_observer_snapshot_facts(
            support.gateway_observer_snapshot(state.gateway_root)
        )
        assert obs_before["observer_binding_active"] is True
        assert obs_before["consumer_owner_epoch_current"] is True

        # 2. Stop Gateway #1 while keeping the SAME RiskServiceHost PID alive.
        state.strategy.close(timeout_seconds=5)
        state.strategy = None
        state.gateway_stop.close()
        assert state.gateway.is_alive() is False and state.gateway.exitcode == 0

        # Wait until Risk definitely observes the Gateway outage (UNVERIFIED).
        gap = support.until(
            lambda: _cursor(state.risk_root),
            lambda row: row is not None and row[0] == "UNVERIFIED",
            seconds=20,
        )
        assert gap is not None and gap[0] == "UNVERIFIED", gap
        # Keep Gateway down for 2.5s so strict account-facts maintenance ticks
        # run during the outage; Risk must defer unregister instead of poisoning cleanup.
        time.sleep(2.5)
        assert state.risk.poll() is None and state.risk.pid == risk_pid

        # 3. Restart Gateway #2 on the SAME durable journal.
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
        ready = support.next_message(state.messages, "ready", 40)
        assert ready[2] == journal_id, ready
        new_generation = ready[3]
        assert new_generation != original_generation
        state.gateway_stop.bind_ready(ready)

        state.strategy = support.strategy_session(
            state.gateway_endpoint, state.keys, state.journal_id
        )
        assert state.strategy.start(timeout_seconds=20).healthy is True

        # 4. Verify SAME Risk PID recovers observer, consumer ownership, AND R-05 v2 cut
        #    on the successor Gateway's NEW generation without replaying prior writes!
        recovered = support.until(
            lambda: _cursor(state.risk_root),
            lambda row: row is not None and row[0] == "VERIFIED",
            seconds=40,
        )
        assert recovered is not None and recovered[0] == "VERIFIED", recovered
        assert state.risk.poll() is None and state.risk.pid == risk_pid

        fresh_after = support.until(
            lambda: _r05_cut_snapshot(state.risk_root),
            lambda v: _cut_matches_generation(v, new_generation),
            seconds=25,
        )
        if not _cut_matches_generation(fresh_after, new_generation):
            snap_diag = _r05_cut_snapshot(state.risk_root)
            obs_diag = support.gateway_observer_snapshot_facts(
                support.gateway_observer_snapshot(state.gateway_root)
            )
            risk_stderr = ""
            if state.risk is not None and state.risk.poll() is None:
                try:
                    state.risk.terminate()
                    _, err = state.risk.communicate(timeout=5)
                    risk_stderr = err.decode("utf-8", errors="replace")[-2000:]
                except Exception:
                    pass
                state.risk = None
            raise AssertionError(
                f"R05 cut did not advance to {new_generation}: snap={snap_diag} obs={obs_diag}\n--- risk stderr ---\n{risk_stderr}"
            )

        obs_after = support.until(
            lambda: support.gateway_observer_snapshot_facts(
                support.gateway_observer_snapshot(state.gateway_root)
            ),
            lambda f: f["observer_binding_active"] is True
            and f["consumer_owner_present"] is True
            and f["consumer_owner_epoch_current"] is True
            and f["consumer_owner_matches_active_binding"] is True,
            seconds=20,
        )
        assert obs_after is not None, support.gateway_observer_snapshot_facts(
            support.gateway_observer_snapshot(state.gateway_root)
        )

        # Confirm zero replay of prior commands on Gateway #2.
        assert _rows_by_key(_gateway_rows(state)) == rows_before_restart
        for _ in range(10):
            time.sleep(0.05)
            assert _gateway_places(state) == {}

        restart_watermark = _gateway_fact_watermark(state.gateway_root)

        # 5. Dispatch a brand-new Execution v2 parent/child through the SAME Risk PID!
        after = execution.submit("parent-after-same-pid-restart")
        assert after["status"] == "RECEIVED", after
        final_rows = support.until(
            lambda: _gateway_rows(state),
            lambda rows: len(rows) == len(rows_before_restart) + 1
            and all(row[2] == "SENT" for row in rows),
            seconds=25,
        )
        assert final_rows is not None, _gateway_rows(state)
        final_by_key = _rows_by_key(final_rows)
        new_keys = set(final_by_key) - set(rows_before_restart)
        assert len(new_keys) == 1
        assert final_by_key[first_key] == first_row
        new_client_id = json.loads(final_by_key[next(iter(new_keys))][5]).get("client_order_id")
        assert type(new_client_id) is str and new_client_id

        final_places = support.until(
            lambda: _gateway_places(state),
            lambda p: len(p) == 1 and all(c == 1 for c in p.values()),
            seconds=15,
        )
        assert final_places is not None and not (original_native_ids & set(final_places))

        state.ingest(new_client_id, "accepted", "1", "0")
        state.ingest(new_client_id, "filled", "1", "1")
        ingested_watermark = _gateway_fact_watermark(state.gateway_root)
        assert ingested_watermark == restart_watermark + 2
        fresh_post_new = support.until(
            lambda: _r05_cut_snapshot(state.risk_root),
            lambda v: _cut_matches_generation(
                v, new_generation, minimum_watermark=ingested_watermark
            ),
            seconds=25,
        )
        assert _cut_matches_generation(
            fresh_post_new, new_generation, minimum_watermark=ingested_watermark
        ), _r05_cut_snapshot(state.risk_root)

        # 6. Cooperative Risk shutdown must succeed cleanly (proving ZERO cleanup_uncertain!).
        execution.close()
        execution = None
        for attr in ("client", "operator", "strategy"):
            client = getattr(state, attr, None)
            if client is not None:
                client.close(timeout_seconds=10)
                setattr(state, attr, None)
        risk_stop = support.stop_risk_cooperatively(risk_process, timeout=45.0)
        state.risk = None
        assert risk_stop["risk_cli_exit_zero"] is True, risk_stop
        assert risk_stop["post_cleanup_stopped_observed"] is True, risk_stop
    finally:
        if execution is not None:
            try:
                execution.close()
            except Exception:
                pass
        try:
            state.close()
        except Exception:
            pass
        for proc in gateway_processes:
            try:
                assert proc.is_alive() is False and proc.exitcode == 0
            except Exception:
                pass
