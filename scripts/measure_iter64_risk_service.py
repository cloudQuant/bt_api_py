"""Iteration 64.11: latency, throughput and fault behaviour of the Risk service (measurement only).

Evidence level INTEGRATION_SIM: a spawned loopback Gateway (synthetic native
dispatcher and synthetic venue returns), the Risk host started through its
CLI in its own process, and strategy clients in this process, all over
loopback CURVE. Temporary SQLite files; no provider, no account, no network
beyond loopback. INV-12: the script reports numbers and never judges them;
there are no thresholds. Deadlines bound individual waits and shared polling
budgets; synchronous filesystem calls, process cleanup and scheduling can
still exceed a wall-clock budget, so these are approximate rather than hard
real-time deadlines.

Usage (from the SDK root)::

    python scripts/measure_iter64_risk_service.py --out <directory> [--orders 200]

It writes ``risk_service_measurements.json`` and ``risk_service_measurements.md``
to ``--out``. It is not a test and is not collected by pytest. Runtime is a
few minutes (the crash-restart phase waits for the Gateway registry lease).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import platform
import queue
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[1]
for _part in ("tests", "tests/unit", "bt_api/bt_api_base/tests", "bt_api/bt_api_ctp/tests",
              "bt_api/bt_api_gateway/tests", "."):
    _path = str(SDK_ROOT / _part)
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Test-only launcher of the Risk host with a commit delay in the pre-trade ledger: the
# sleep happens inside the BEGIN IMMEDIATE transaction, before COMMIT, and only when the
# transaction changed rows (a slow fsync while holding the write lock). Production code
# is unchanged; this program patches the class in the child process only.
_SLOW_LAUNCHER = r"""
import contextlib, os, sys, time
from bt_api_risk.pretrade import ledger as _ledger
_delay = float(os.environ["BT_RISK_MEASURE_COMMIT_DELAY_MS"]) / 1000.0
_original = _ledger.PretradeLedger.transaction
@contextlib.contextmanager
def _slow(self):
    with _original(self) as txn:
        before = txn._c.total_changes
        yield txn
        if txn._c.total_changes != before:
            time.sleep(_delay)
_ledger.PretradeLedger.transaction = _slow
from bt_api_py.risk_service_host import main
sys.exit(main(sys.argv[1:]))
"""


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def _stats(samples_ms: list[float]) -> dict:
    if not samples_ms:
        return {"n": 0}
    tenth = max(1, len(samples_ms) // 10)
    return {
        "n": len(samples_ms),
        "p50_ms": round(_percentile(samples_ms, 0.50), 3),
        "p99_ms": round(_percentile(samples_ms, 0.99), 3),
        "max_ms": round(max(samples_ms), 3),
        "mean_ms": round(statistics.fmean(samples_ms), 3),
        "first_decile_mean_ms": round(statistics.fmean(samples_ms[:tenth]), 3),
        "last_decile_mean_ms": round(statistics.fmean(samples_ms[-tenth:]), 3),
    }


def _timed(call):
    started = time.perf_counter()
    result = call()
    return result, (time.perf_counter() - started) * 1000.0


def _load() -> list[float]:
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except OSError:
        return []


def _unfinished_permits(counts: dict) -> dict:
    """Unknown future states are incomplete too, not implicit terminal states."""
    return {state: count for state, count in counts.items()
            if state not in {"DENIED", "RELEASED", "TERMINAL"} and count}


class MeasurementIncompleteError(RuntimeError):
    def __init__(self, message: str, evidence: dict):
        super().__init__(message)
        self.evidence = evidence


class Rig:
    """Loopback Gateway + Risk host + strategy clients for one measurement run."""

    def __init__(self, root: Path, strategies: int, *, observation_poll: str, ttl_seconds: int):
        from tests.integration import _iter64_recovery_support as support
        from tests.integration.test_service_account_instrument_metadata import _free_endpoint

        self.support = support
        self.root = root
        self.strategy_count = strategies
        self.keys = support.keypairs(strategies)
        self.gateway_root = root / "gateway"
        self.gateway_root.mkdir()
        self.risk_root = root / "risk"
        self.risk_root.mkdir()
        self.gateway_endpoint, self.risk_endpoint = _free_endpoint(), _free_endpoint()
        self.context = multiprocessing.get_context("spawn")
        self.messages, self.controls = self.context.Queue(), self.context.Queue()
        self.gateway = None
        self.journal_id = None
        self.sessions: list = []
        self.risk = None
        self.clients: list = []
        self.operator = None
        self.sequence = 0
        try:
            self.start_gateway()
            self.open_sessions()
            self.config = support.risk_files(
                self.risk_root, self.keys, strategies, self.gateway_endpoint, self.risk_endpoint,
                ttl_seconds=ttl_seconds, observation_poll=observation_poll, maintain_seconds="1",
                dispatch_recheck_seconds=2,
            )
            self.ready_file = self.risk_root / "ready.json"
        except BaseException as error:
            try:
                self.close()
            except BaseException as cleanup_error:
                error.measurement_cleanup_error = type(cleanup_error).__name__
            raise

    # --------------------------------------------------------------- processes

    def start_gateway(self) -> float:
        started = time.perf_counter()
        self.gateway = self.context.Process(
            target=self.support.gateway_main,
            args=(str(self.gateway_root), self.gateway_endpoint, self.keys["gateway_server"],
                  self.support.gateway_publics(self.keys, self.strategy_count),
                  self.strategy_count, self.messages, self.controls),
        )
        self.gateway.start()
        ready = self.support.next_message(self.messages, "ready", 60)
        self.journal_id = ready[2]
        return time.perf_counter() - started

    def stop_gateway(self) -> float:
        started = time.perf_counter()
        forced = False
        self.controls.put("stop")
        self.gateway.join(30)
        if self.gateway.is_alive():
            forced = True
            self.gateway.terminate()
            self.gateway.join(5)
        if self.gateway.is_alive():
            self.gateway.kill()
            self.gateway.join(5)
        if self.gateway.is_alive():
            raise RuntimeError("measurement gateway cleanup incomplete")
        if forced:
            raise RuntimeError("measurement gateway required forced termination")
        return time.perf_counter() - started

    def open_sessions(self) -> None:
        failures = []
        for session in self.sessions:
            try:
                session.close(timeout_seconds=5)
            except Exception as error:
                failures.append(type(error).__name__)
        self.sessions = []
        if failures:
            raise RuntimeError("strategy session cleanup incomplete: " + ",".join(failures))
        for index in range(self.strategy_count):
            session = self.support.strategy_session(
                self.gateway_endpoint, self.keys, self.journal_id, index)
            # Retain the handle before starting: startup may fail part way through.
            self.sessions.append(session)
            if not session.start(timeout_seconds=20).healthy:
                raise RuntimeError("strategy session not healthy")

    def start_risk(self, *, launcher=None, extra_env=None) -> float:
        started = time.perf_counter()
        if self.ready_file.exists():
            self.ready_file.unlink()
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = self.support.SDK_PYTHONPATH + (
            os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env.pop("BT_RISK_FAULT_POINT", None)
        env.update(extra_env or {})
        command = launcher or [sys.executable, "-m", "bt_api_py.risk_service_host"]
        # Store the child immediately so interruption during startup cannot lose it.
        self.risk = subprocess.Popen(  # noqa: S603 - internal fixed launcher and synthetic fixture
            command + ["run", "--config", str(self.config), "--ready-file", str(self.ready_file)],
            cwd=str(SDK_ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 90
            while not self.ready_file.exists():
                if self.risk.poll() is not None:
                    raise RuntimeError("risk host exited before ready")
                if time.monotonic() >= deadline:
                    raise TimeoutError("risk host did not become ready")
                time.sleep(0.02)
            ready = time.perf_counter() - started
            self.open_risk_clients()
            return ready
        except BaseException as error:
            try:
                self.stop_risk()
            except BaseException as cleanup_error:
                error.measurement_cleanup_error = type(cleanup_error).__name__
            raise

    def open_risk_clients(self) -> None:
        self.close_risk_clients()
        self.clients = []
        for index in range(self.strategy_count):
            client = None
            for attempt in range(5):
                client = self.support.risk_client(self.risk_endpoint, self.keys, index)
                try:
                    if client.start(timeout_seconds=20).healthy:
                        break
                except BaseException as error:
                    try:
                        client.close(timeout_seconds=5)
                    except Exception as cleanup_error:
                        raise RuntimeError("risk client startup cleanup incomplete") from cleanup_error
                    if not isinstance(error, Exception):
                        raise
                    client = None
                    if attempt == 4:
                        raise RuntimeError("risk client not healthy") from error
                    time.sleep(2)
                    continue
                try:
                    client.close(timeout_seconds=5)
                except Exception as error:
                    raise RuntimeError("risk client startup cleanup incomplete") from error
                client = None
                if attempt == 4:
                    raise RuntimeError("risk client not healthy")
                time.sleep(2)
            self.clients.append(client)
        self.operator = self.support.operator_runtime(self.risk_endpoint, self.keys)
        self.operator.start(timeout_seconds=20)

    def fresh_risk(self, *, launcher=None, extra_env=None) -> None:
        """A new Risk process on the same files, observation verified, delegates warm."""
        self.stop_risk()
        self.start_risk(launcher=launcher, extra_env=extra_env)
        if self.wait_verified(90) is None:
            raise RuntimeError("Risk observation not verified after restart")
        attempts = []
        outcomes = []
        for client in range(len(self.clients)):
            key = self.key("warm" + str(client))
            attempts.append((client, key))
            try:
                _reserve_ms, _dispatch_ms, answer, _key = self.risk_order(client, "warm", key=key)
                outcome = getattr(answer, "outcome", "UNKNOWN")
            except Exception as error:
                outcome = "UNKNOWN:" + type(error).__name__
            outcomes.append({"strategy_index": client, "request_key": key, "outcome": outcome})
            if outcome != "DISPATCHED":
                break
        try:
            settlement = self.settle(attempts, wait_seconds=10, status_wait_seconds=10,
                                     required_risk_orders=attempts)
        except Exception as error:
            settlement = {"measurement_status": "INCOMPLETE", "error": type(error).__name__}
        if (len(outcomes) != self.strategy_count
                or any(row["outcome"] != "DISPATCHED" for row in outcomes)
                or settlement["measurement_status"] != "OBSERVED"):
            raise MeasurementIncompleteError("warmup outcomes or settlement incomplete",
                                             {"warmups": outcomes, "settlement": settlement})

    def close_risk_clients(self) -> None:
        failures = []
        for client in self.clients + ([self.operator] if self.operator is not None else []):
            try:
                client.close(timeout_seconds=5)
            except Exception as error:
                failures.append(type(error).__name__)
        self.clients, self.operator = [], None
        if failures:
            raise RuntimeError("risk client cleanup incomplete: " + ",".join(failures))

    def stop_risk(self, *, kill=False) -> float:
        started = time.perf_counter()
        error = None
        try:
            self.close_risk_clients()
        except Exception as caught:
            error = caught
        try:
            if self.risk is not None:
                if kill:
                    if self.risk.poll() is not None:
                        raise RuntimeError("risk host exited before planned SIGKILL")
                    self.risk.send_signal(signal.SIGKILL)
                    if self.risk.wait(timeout=30) != -signal.SIGKILL:
                        raise RuntimeError("risk host did not exit from planned SIGKILL")
                else:
                    exitcode = self.support.stop_risk(self.risk)
                    if exitcode != 0:
                        raise RuntimeError("risk host stop not clean: " + str(exitcode))
                self.risk = None
        except Exception as caught:
            error = error or caught
        if error is not None:
            raise error
        return time.perf_counter() - started

    def close(self) -> None:
        failures = []
        try:
            self.close_risk_clients()
        except Exception as error:
            failures.append(type(error).__name__)
        if self.risk is not None:
            try:
                if self.support.stop_risk(self.risk) != 0:
                    failures.append("RiskHostStopNotClean")
            except Exception as error:
                failures.append(type(error).__name__)
        for session in self.sessions:
            try:
                session.close(timeout_seconds=5)
            except Exception as error:
                failures.append(type(error).__name__)
        if self.gateway is not None and self.gateway.pid is not None:
            if self.gateway.is_alive():
                try:
                    self.stop_gateway()
                except Exception as error:
                    failures.append(type(error).__name__)
            else:
                self.gateway.join(0)
        if failures:
            raise RuntimeError("measurement cleanup incomplete: " + ",".join(failures))

    # --------------------------------------------------------------- helpers

    def key(self, prefix: str) -> str:
        self.sequence += 1
        return prefix + "-" + str(self.sequence)

    def working(self, *, timeout_seconds: float = 5) -> dict:
        """Risk permit counts by state (read-only view of the Risk ledger)."""
        database = self.risk_root / "state" / "risk.sqlite"
        with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True,
                             timeout=max(0.001, min(5, timeout_seconds))) as db:
            return dict(db.execute(
                "SELECT state, COUNT(*) FROM risk_pretrade_permits GROUP BY state").fetchall())

    def permit_evidence(self, *, timeout_seconds: float = 5) -> list[dict]:
        """Read each original permit, including terminal orders with pending trades."""
        database = self.risk_root / "state" / "risk.sqlite"
        deadline = time.monotonic() + timeout_seconds
        with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True,
                             timeout=max(0.001, min(5, timeout_seconds))) as db:
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            rows = db.execute(
                "SELECT strategy_ref, request_key, state, quantity, order_filled, trades_quantity "
                "FROM risk_pretrade_permits"
            ).fetchall()
        evidence = []
        for strategy, key, state, quantity, filled, trades in rows:
            try:
                q, f, t = Decimal(quantity), Decimal(filled), Decimal(trades)
                valid = all(value.is_finite() and value >= 0 for value in (q, f, t))
                valid = valid and q > 0 and f <= q and t <= q
                pending = max(Decimal(0), f - t) if valid else None
            except (InvalidOperation, ValueError, TypeError):
                valid = False
                pending = None
            resolved = valid and (
                (state == "TERMINAL" and f == t)
                or (state in {"DENIED", "RELEASED"} and f == 0 and t == 0)
            )
            evidence.append({"strategy_ref": strategy, "request_key": key, "state": state,
                             "quantity": quantity,
                             "order_filled": filled, "trades_quantity": trades,
                             "pending_trade_quantity": None if pending is None else str(pending),
                             "resolved": bool(resolved)})
        return evidence

    def settle(self, orders: list[tuple[int, str]], *, wait_seconds: float = 120,
               status_wait_seconds: float = 10,
               required_risk_orders: list[tuple[int, str]] | None = None) -> dict:
        """Observe all keys under one deadline; synthesize fills only for observed SENT.

        Unresolved or failed reads are reported, never treated as not sent or filled.
        The status budget covers batch reads and both synthetic return acknowledgements.
        """
        pending = set(orders)
        if len(pending) != len(orders) or status_wait_seconds < 0 or wait_seconds < 0:
            raise ValueError("duplicate orders or invalid settlement timeout")
        states: dict[tuple[int, str], str] = dict.fromkeys(pending, "UNKNOWN")
        filled = 0
        ingest_errors = 0
        deadline = time.monotonic() + status_wait_seconds
        first = True
        stopped = False
        while pending and (first or time.monotonic() < deadline):
            first = False
            remaining = max(0.001, deadline - time.monotonic())
            observed = self.gateway_statuses(pending, timeout_seconds=remaining)
            for order in tuple(pending):
                state = observed.get(order, "UNKNOWN")
                states[order] = state
                if state != "SENT":
                    continue
                # Never repeat an attempted ingest if the first reply was lost.
                pending.remove(order)
                try:
                    for status, fill in (("accepted", "0"), ("filled", "1")):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("settlement deadline expired")
                        self.ingest("c-" + order[1], status, "1", fill,
                                    timeout_seconds=remaining)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("settlement trade deadline expired")
                    self.ingest_trade(order[0], order[1], timeout_seconds=remaining)
                    filled += 1
                    states[order] = "RETURNS_ATTRIBUTED"
                except Exception:
                    ingest_errors += 1
                    states[order] = "INGEST_UNKNOWN"
                    # A delayed acknowledgement cannot be matched safely to a
                    # later return; keep all remaining orders unresolved.
                    stopped = True
                    break
            if stopped:
                break
            if pending and time.monotonic() < deadline:
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        deadline = time.monotonic() + wait_seconds
        while True:
            remaining = deadline - time.monotonic()
            # A final evidence read gets its own short bounded budget even if
            # the wait is zero; the wall-clock limit is therefore approximate.
            read_timeout = min(1, max(0.001, remaining)) if remaining > 0 else 1
            counts = self.working(timeout_seconds=read_timeout)
            permits = self.permit_evidence(timeout_seconds=read_timeout)
            identities: dict[tuple[str, str], list[dict]] = {}
            for permit in permits:
                identities.setdefault((permit["strategy_ref"], permit["request_key"]), []).append(permit)
            required = []
            for index, key in required_risk_orders or []:
                strategy = self.support.strategy_identity(index).actor_ref
                matches = identities.get((strategy, key), [])
                # Each request in this benchmark is exactly one lot. A valid
                # cancellation/rejection or a zero/zero terminal is not the
                # simulated fill we set out to measure.
                matched = len(matches) == 1 and matches[0]["resolved"]
                if matched:
                    row = matches[0]
                    matched = (row["state"] == "TERMINAL"
                               and Decimal(row["quantity"]) == Decimal(1)
                               and Decimal(row["order_filled"]) == Decimal(1)
                               and Decimal(row["trades_quantity"]) == Decimal(1))
                required.append({"strategy_index": index, "request_key": key,
                                 "expected_quantity": "1", "resolved": bool(matched),
                                 "permits": matches})
            pending_permits = [permit for permit in permits if not permit["resolved"]]
            if (not _unfinished_permits(counts) and not pending_permits
                    and all(item["resolved"] for item in required)):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        by_state: dict[str, int] = {}
        for state in states.values():
            by_state[state] = by_state.get(state, 0) + 1
        return {"risk_permits_by_state": counts, "requested": len(orders),
                "synthetic_returns_attributed": filled, "gateway_states": by_state,
                "unresolved": len(pending), "ingest_unknown": ingest_errors,
                "order_results": [
                    {"strategy_index": index, "request_key": key,
                     "gateway_observation": states[(index, key)]}
                    for index, key in orders
                ],
                "unresolved_permit_states": _unfinished_permits(counts),
                "unresolved_permits": pending_permits, "required_risk_orders": required,
                "measurement_status": "OBSERVED" if not pending and not ingest_errors
                and not _unfinished_permits(counts) and not pending_permits
                and all(item["resolved"] for item in required) else "INCOMPLETE"}

    def admin(self, operation, arguments=None, *, timeout_seconds=10):
        return self.support.admin(self.operator, operation, arguments, timeout_seconds=timeout_seconds)

    def ingest(self, client_order_id, status, quantity, filled, *, timeout_seconds=10) -> int:
        self.controls.put((client_order_id, status, quantity, filled))
        item = self.support.next_message(self.messages, "ingested", timeout=timeout_seconds)
        if item[1] != client_order_id or item[2] != "ATTRIBUTED":
            raise RuntimeError("synthetic return not attributed to requested order")
        return item[3]

    def ingest_trade(self, index: int, key: str, *, trade_id: str | None = None,
                     timeout_seconds: float = 10) -> int:
        """Synthetic fill only for this exact journal command observed SENT."""
        deadline = time.monotonic() + timeout_seconds
        states = self.gateway_statuses([(index, key)], timeout_seconds=max(0.001, timeout_seconds))
        if states[(index, key)] != "SENT":
            raise RuntimeError("synthetic trade command not observed SENT")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("synthetic trade deadline expired")
        client_order_id = "c-" + key
        if trade_id is None:
            trade_id = "iter64-" + hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()
        self.controls.put(("trade", client_order_id, trade_id, "1", "10"))
        item = self.support.next_message(self.messages, "ingested", timeout=remaining)
        if (item[1] != client_order_id or item[2] != "ATTRIBUTED"
                or len(item) < 6 or item[4:] != ("trade", trade_id)):
            raise RuntimeError("synthetic trade not attributed to requested order")
        return item[3]

    def prime(self) -> None:
        """A direct order of strategy 0 gives Risk the trading day (as in the 64.09 tests)."""
        previous = self.admin("risk.admin.status", None)["summary"]["observation"]["last_sequence"]
        command = self.sessions[0].command_client
        key = self.key("prime")
        command.reserve(self.support.order_request(key, "c-" + key), timeout_seconds=10)
        command.dispatch(key, timeout_seconds=10)
        observed = self.support.until(lambda: command.status(key, timeout_seconds=5),
                                      lambda r: r.record is not None and r.record.state == "SENT")
        if observed.record is None or observed.record.state != "SENT":
            raise RuntimeError("prime order not observed SENT")
        self.ingest("c-" + key, "accepted", "1", "0")
        if self.wait_verified(30, minimum_sequence=previous + 1) is None:
            raise RuntimeError("prime observation not applied")

    def wait_verified(self, seconds, *, minimum_sequence: int | None = None) -> float | None:
        started = time.perf_counter()
        deadline = started + seconds
        while time.perf_counter() < deadline:
            try:
                remaining = max(0.001, deadline - time.perf_counter())
                summary = self.admin("risk.admin.status", None,
                                     timeout_seconds=min(10, remaining))["summary"]
                observation = summary["observation"]
                if (observation["continuity"] == "VERIFIED"
                        and (minimum_sequence is None
                             or observation["last_sequence"] >= minimum_sequence)):
                    return time.perf_counter() - started
            except Exception:
                # Transient admin failures remain pending until the deadline.
                time.sleep(min(0.02, max(0, deadline - time.perf_counter())))
                continue
            time.sleep(min(0.02, max(0, deadline - time.perf_counter())))
        return None

    def risk_order(self, index: int, prefix: str, *, timeout=20, key=None):
        """Reserve and dispatch through Risk; returns (reserve_ms, dispatch_ms, answer, key)."""
        client = self.clients[index]
        key = key if key is not None else self.key(prefix + str(index))
        request = self.support.order_request(key, "c-" + key)
        reserved, reserve_ms = _timed(lambda: client.reserve(request, request_key=key,
                                                             timeout_seconds=timeout))
        answer = reserved.answer if reserved.outcome == "ANSWERED" else None
        if answer is None or answer.outcome != "RESERVED":
            return reserve_ms, None, answer, key
        dispatched, dispatch_ms = _timed(lambda: client.dispatch(
            key, permit_id=answer.permit_id, reservation_id=answer.reservation_id,
            view_digest=answer.view_digest, timeout_seconds=timeout))
        result = dispatched.answer if dispatched.outcome == "ANSWERED" else dispatched
        return reserve_ms, dispatch_ms, result, key

    def risk_state(self, index: int, key: str, *, timeout_seconds=10):
        result = self.clients[index].status(key, timeout_seconds=timeout_seconds)
        return None if result.outcome != "ANSWERED" else result.answer.state

    def gateway_status(self, index: int, key: str):
        rows = self.support.gateway_command(self.gateway_root,
                                            self.support.strategy_identity(index).actor_ref, key)
        return rows[0][0] if rows else None

    def gateway_statuses(self, orders, *, timeout_seconds: float) -> dict:
        """One bounded read transaction for a batch, keyed by strategy and request."""
        orders = tuple(orders)
        result = dict.fromkeys(orders, "UNKNOWN")
        if not orders:
            return result
        database = self.gateway_root / "journal.sqlite"
        deadline = time.monotonic() + timeout_seconds
        try:
            with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True,
                                 timeout=min(1.0, timeout_seconds)) as db:
                # Bound SQLite VM work too, not just waiting to acquire its lock.
                db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                for start in range(0, len(orders), 400):
                    if time.monotonic() >= deadline:
                        break
                    chunk = orders[start:start + 400]
                    placeholders = ",".join("?" for _ in chunk)
                    rows = db.execute(
                        "SELECT strategy, request_key, status FROM direct_journal_commands "  # noqa: S608
                        f"WHERE account=? AND request_key IN ({placeholders})",
                        [self.support.ACCOUNT, *(key for _, key in chunk)],
                    ).fetchall()
                    lookup = {(strategy, key): state for strategy, key, state in rows}
                    for order in chunk:
                        strategy = self.support.strategy_identity(order[0]).actor_ref
                        result[order] = lookup.get((strategy, order[1]), "MISSING")
        except sqlite3.Error:
            # A failed read is unknown, not proof that the order was not sent.
            pass
        return result


# ------------------------------------------------------------------------------ phases


def phase_round_trips(rig: Rig, orders: int) -> dict:
    working_at_start = rig.working()
    permits_at_start = rig.permit_evidence()
    command = rig.sessions[0].command_client
    direct_reserve, direct_dispatch, risk_reserve, risk_dispatch = [], [], [], []
    outcomes: dict[str, int] = {}
    settlement_states: dict[str, int] = {}
    settlement_orders = []
    direct_attempts, risk_attempts = [], []
    settlement_unresolved = 0
    settlement_incomplete = 0
    for i in range(orders + 5):
        key = rig.key("direct")
        request = rig.support.order_request(key, "c-" + key)
        reserved, ms_r = _timed(lambda request=request: command.reserve(request, timeout_seconds=10))
        if reserved.record is None:
            direct_attempts.append({"request_key": key, "outcome": "RESERVE_UNKNOWN"})
            break
        dispatched, ms_d = _timed(lambda key=key: command.dispatch(key, timeout_seconds=10))
        ok = dispatched.outcome in ("QUEUED", "OBSERVED")
        direct_attempts.append({"request_key": key, "outcome": dispatched.outcome})
        if not ok:
            settlement = rig.settle([(0, key)], wait_seconds=0, status_wait_seconds=2)
            settlement_orders.extend(settlement["order_results"])
            settlement_unresolved += settlement["unresolved"] + settlement["ingest_unknown"]
            break
        risk_key = rig.key("rt0")
        try:
            r_ms, d_ms, answer, _ = rig.risk_order(0, "rt", key=risk_key)
            outcome = getattr(answer, "outcome", "NONE")
        except Exception as error:
            r_ms = d_ms = None
            outcome = "UNKNOWN:" + type(error).__name__
        risk_attempts.append({"request_key": risk_key, "outcome": outcome})
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        settlement = rig.settle(
            [(0, key)] + ([(0, risk_key)] if outcome != "DENIED" else []),
            wait_seconds=0, status_wait_seconds=2,
            required_risk_orders=[(0, risk_key)] if outcome != "DENIED" else [])  # not timed
        settlement_unresolved += settlement["unresolved"] + settlement["ingest_unknown"]
        settlement_incomplete += settlement["measurement_status"] != "OBSERVED"
        settlement_orders.extend(settlement["order_results"])
        for state, count in settlement["gateway_states"].items():
            settlement_states[state] = settlement_states.get(state, 0) + count
        if outcome not in ("DISPATCHED", "DENIED"):
            break
        if i < 5:
            continue  # warm-up, not counted
        if ok:
            direct_reserve.append(ms_r)
            direct_dispatch.append(ms_d)
        if outcome == "DISPATCHED":
            risk_reserve.append(r_ms)
            risk_dispatch.append(d_ms)
    working_at_end = rig.working()
    permits_at_end = rig.permit_evidence()
    return {
        "direct_reserve": _stats(direct_reserve),
        "direct_dispatch": _stats(direct_dispatch),
        "risk_reserve": _stats(risk_reserve),
        "risk_dispatch": _stats(risk_dispatch),
        "risk_outcomes_including_warmup": outcomes,
        "direct_attempts": direct_attempts, "risk_attempts": risk_attempts,
        "settlement_gateway_states": settlement_states,
        "settlement_order_results": settlement_orders,
        "settlement_unresolved": settlement_unresolved,
        "settlement_incomplete_count": settlement_incomplete,
        "measurement_status": "OBSERVED" if not settlement_unresolved and not settlement_incomplete
        and not _unfinished_permits(working_at_start) and not _unfinished_permits(working_at_end)
        and all(item["resolved"] for item in permits_at_start + permits_at_end)
        and len(direct_reserve) == orders and len(risk_dispatch) == orders else "INCOMPLETE",
        "risk_permits_by_state_at_start": working_at_start,
        "risk_permits_by_state_at_end": working_at_end,
        "risk_permits_at_start": permits_at_start, "risk_permits_at_end": permits_at_end,
    }


def _claim_worker(index, gateway_endpoint, risk_endpoint, keys, journal_id, per_client,
                  ready, start, results):
    """One strategy process: its own Gateway session (write claim) and Risk client."""
    from tests.integration import _iter64_recovery_support as support

    session = client = None
    ready_sent = False
    prepared = []
    reserve_attempts = []
    latencies, outcomes, reserve_outcomes, dispatched_keys = [], {}, {}, []
    first = last = None
    error_name = None
    warmup_key = warmup_outcome = None
    try:
        session = support.strategy_session(gateway_endpoint, keys, journal_id, index)
        session.start(timeout_seconds=30)
        client = support.risk_client(risk_endpoint, keys, index)
        client.start(timeout_seconds=30)
        for n in range(per_client + 1):
            key = "tp-" + str(index) + "-" + str(n) + "-" + str(time.time_ns())
            attempt = {"request_key": key, "outcome": "UNKNOWN"}
            reserve_attempts.append(attempt)
            result = client.reserve(support.order_request(key, "c-" + key), request_key=key,
                                    timeout_seconds=30)
            answer = result.answer if result.outcome == "ANSWERED" else None
            reserve_outcome = getattr(answer, "outcome", None) or result.outcome
            attempt["outcome"] = reserve_outcome
            reserve_outcomes[reserve_outcome] = reserve_outcomes.get(reserve_outcome, 0) + 1
            if answer is not None and answer.outcome == "RESERVED":
                prepared.append((key, answer))
            else:
                break
        if prepared:  # the first claim creates Risk's delegate runtime for this run (G64-11)
            key, answer = prepared.pop(0)
            warmup_key = key  # retain the original key even if the dispatch reply is lost
            warmup_result = client.dispatch(
                key, permit_id=answer.permit_id, reservation_id=answer.reservation_id,
                view_digest=answer.view_digest, timeout_seconds=30)
            warmup_outcome = (getattr(warmup_result.answer, "outcome", "UNKNOWN")
                              if warmup_result.outcome == "ANSWERED" else warmup_result.outcome)
            if warmup_outcome != "DISPATCHED":
                raise RuntimeError("warmup dispatch incomplete")
        ready.put(index)
        ready_sent = True
        if not start.wait(120):
            raise TimeoutError("claim start barrier timed out")
        first = time.time()
        for key, answer in prepared:
            started = time.perf_counter()
            dispatched_keys.append(key)  # preserve an attempt even if its reply is lost
            result = client.dispatch(key, permit_id=answer.permit_id,
                                     reservation_id=answer.reservation_id,
                                     view_digest=answer.view_digest, timeout_seconds=30)
            latencies.append((time.perf_counter() - started) * 1000.0)
            outcome = result.answer.outcome if result.outcome == "ANSWERED" else result.outcome
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        last = time.time()
    except BaseException as error:  # reported, never raised into the measuring process
        error_name = type(error).__name__
        if not ready_sent:
            ready.put(index)
    finally:
        if first is not None and last is None:
            last = time.time()
        for item in (client, session):
            if item is not None:
                try:
                    item.close(timeout_seconds=5)
                except Exception as error:
                    error_name = error_name or "Cleanup" + type(error).__name__
        results.put({"index": index, "first": first, "last": last,
                     "latencies": latencies, "outcomes": outcomes,
                     "reserve_outcomes": reserve_outcomes, "keys": dispatched_keys,
                     "reserve_attempts": reserve_attempts,
                     "warmup_key": warmup_key, "warmup_outcome": warmup_outcome,
                     "error": error_name})


def _join_workers(workers, timeout: float) -> list:
    """Join using one budget; return only still-running workers created here."""
    deadline = time.monotonic() + timeout
    for worker in workers:
        worker.join(max(0, deadline - time.monotonic()))
    return [worker for worker in workers if worker.is_alive()]


def _cleanup_workers(workers) -> tuple[list, int]:
    remaining = _join_workers(workers, 12)
    forced = len(remaining)
    for worker in remaining:
        worker.terminate()
    remaining = _join_workers(remaining, 3)
    for worker in remaining:
        worker.kill()
    return _join_workers(remaining, 3), forced


def _queue_until(source, count: int, timeout: float) -> list:
    """Collect within one shared deadline, preserving partial results."""
    deadline = time.monotonic() + timeout
    items = []
    while len(items) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            items.append(source.get(timeout=remaining))
        except queue.Empty:
            break
    return items


def phase_claim_processes(rig: Rig, concurrency: int, per_client: int, *,
                          readiness_timeout: float = 180, result_timeout: float = 180) -> dict:
    """Concurrent claims from ``concurrency`` strategy processes (one client each)."""
    working_at_start = rig.working()
    permits_at_start = rig.permit_evidence()
    rig.close_risk_clients()
    for session in rig.sessions:
        session.close(timeout_seconds=5)
    rig.sessions = []
    ready, results = rig.context.Queue(), rig.context.Queue()
    start = rig.context.Event()
    workers = [rig.context.Process(target=_claim_worker, args=(
        i, rig.gateway_endpoint, rig.risk_endpoint, rig.keys, rig.journal_id, per_client,
        ready, start, results)) for i in range(concurrency)]
    started, ready_rows, rows, cleanup_remaining, forced = [], [], [], [], 0
    try:
        for worker in workers:
            worker.start()
            started.append(worker)
        ready_rows = _queue_until(ready, len(workers), readiness_timeout)
        start.set()
        # Do not wait for results from a worker indefinitely; partial rows
        # remain visible and explicitly make the measurement incomplete.
        rows = _queue_until(results, len(workers), result_timeout
                            if len(set(ready_rows)) == concurrency else 1)
    finally:
        start.set()
        cleanup_remaining, forced = _cleanup_workers(started)
        for q in (ready, results):
            q.close()
            q.cancel_join_thread()
    latencies = [value for row in rows for value in row["latencies"]]
    outcomes: dict[str, int] = {}
    reserve_outcomes: dict[str, int] = {}
    for row in rows:
        for name, count in row["outcomes"].items():
            outcomes[name] = outcomes.get(name, 0) + count
        for name, count in row["reserve_outcomes"].items():
            reserve_outcomes[name] = reserve_outcomes.get(name, 0) + count
    firsts = [row["first"] for row in rows if row["first"] is not None]
    lasts = [row["last"] for row in rows if row["last"] is not None]
    wall = (max(lasts) - min(firsts)) if firsts and lasts else None
    rig.open_sessions()  # Risk clients reopen with the next fresh Risk process
    working_at_end = rig.working()
    attempted = [(row["index"], key) for row in rows for key in row["keys"]]
    attempted += [(row["index"], row["warmup_key"]) for row in rows
                  if row["warmup_key"] is not None]
    settled = rig.settle(attempted, required_risk_orders=attempted)
    errors = [row["error"] for row in rows if row["error"] is not None]
    exitcodes = [worker.exitcode for worker in started]
    complete = (len(set(ready_rows)) == concurrency and len(rows) == concurrency
                and not errors and not cleanup_remaining and not forced
                and all(code == 0 for code in exitcodes)
                and all(row["warmup_outcome"] == "DISPATCHED" for row in rows)
                and not _unfinished_permits(working_at_start)
                and all(item["resolved"] for item in permits_at_start)
                and len(latencies) == concurrency * per_client
                and outcomes == {"DISPATCHED": concurrency * per_client}
                and settled["measurement_status"] == "OBSERVED")
    return {
        "risk_permits_by_state_at_start": working_at_start,
        "risk_permits_at_start": permits_at_start,
        "risk_permits_by_state_at_end": working_at_end,
        "settlement": settled,
        "concurrency": concurrency,
        "client_processes": concurrency,
        "claims": len(latencies),
        "wall_seconds": None if wall is None else round(wall, 3),
        "claims_per_second": round(len(latencies) / wall, 2) if wall else None,
        "successful_claims_per_second": round(outcomes.get("DISPATCHED", 0) / wall, 2)
        if wall else None,
        "claim_latency": _stats(latencies),
        "outcomes": outcomes,
        "reserve_outcomes_including_warmup": reserve_outcomes,
        "worker_reserve_attempts": [{"strategy_index": row["index"],
                                     "attempts": row.get("reserve_attempts", [])} for row in rows],
        "workers_ready": len(set(ready_rows)), "worker_results": len(rows),
        "worker_errors": errors, "workers_forced": forced,
        "worker_exitcodes": exitcodes,
        "worker_warmups": [{"strategy_index": row["index"], "request_key": row["warmup_key"],
                            "outcome": row["warmup_outcome"]} for row in rows],
        "workers_not_reaped": len(cleanup_remaining),
        "measurement_status": "OBSERVED" if complete else "INCOMPLETE",
    }


def phase_observation_lag(rig: Rig, samples: int) -> dict:
    lags: list[float] = []
    missing = 0
    attempted = []
    for _ in range(samples):
        key = rig.key("lag0")
        attempted.append((0, key))
        _r, _d, answer, _key = rig.risk_order(0, "lag", key=key)
        if getattr(answer, "outcome", None) != "DISPATCHED":
            missing += 1
            continue
        sent = rig.support.until(lambda key=key: rig.gateway_status(0, key), lambda s: s == "SENT")
        if sent != "SENT":
            missing += 1
            continue
        rig.ingest("c-" + key, "accepted", "1", "0")
        ingested_ns = rig.ingest("c-" + key, "filled", "1", "1")
        rig.ingest_trade(0, key)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if rig.risk_state(0, key, timeout_seconds=min(10, max(0.001, deadline - time.monotonic()))) == "TERMINAL":
                lags.append((time.time_ns() - ingested_ns) / 1e6)
                break
            time.sleep(0.002)
        else:
            missing += 1
    # A terminal order observation can precede trade details; status alone is
    # not a settlement proof and must not make this phase appear complete.
    settlement = rig.settle([], wait_seconds=0, status_wait_seconds=0,
                            required_risk_orders=attempted)
    return {"gateway_ingest_to_risk_terminal": _stats(lags), "not_observed": missing,
            "measurement_scope": ("ORDER filled ingest to first Risk TERMINAL poll; includes "
                                  "synchronous synthetic TRADE injection and polling delay"),
            "permit_settlement": settlement,
            "measurement_status": "OBSERVED" if len(lags) == samples and not missing
            and settlement["measurement_status"] == "OBSERVED"
            else "INCOMPLETE"}


def phase_risk_restart(rig: Rig, repeats: int, *, kill: bool, verify_timeout: float) -> dict:
    rows = []
    for _ in range(repeats):
        stop_s = rig.stop_risk(kill=kill)
        started = time.perf_counter()
        ready_s = rig.start_risk()
        available_s = None
        while time.perf_counter() - started < 60:
            remaining = max(0.001, 60 - (time.perf_counter() - started))
            status = rig.clients[0].service_status(timeout_seconds=min(5, remaining))
            if status.outcome == "ANSWERED" and status.status.available:
                available_s = time.perf_counter() - started
                break
            time.sleep(0.02)
        verified = rig.wait_verified(verify_timeout)
        verified_s = None if verified is None else time.perf_counter() - started
        rows.append({
            "stop_seconds": round(stop_s, 3),
            "process_start_to_ready_file_seconds": round(ready_s, 3),
            "process_start_to_status_available_seconds":
                None if available_s is None else round(available_s, 3),
            "process_start_to_observation_verified_seconds":
                None if verified_s is None else round(verified_s, 3),
        })
    permit_evidence = rig.permit_evidence()
    return {"mode": "SIGKILL" if kill else "SIGTERM", "runs": rows,
            "unresolved_permits": [item for item in permit_evidence if not item["resolved"]],
            "measurement_status": "OBSERVED" if all(
                row["process_start_to_status_available_seconds"] is not None
                and row["process_start_to_observation_verified_seconds"] is not None
                for row in rows) and all(item["resolved"] for item in permit_evidence)
            else "INCOMPLETE"}


def _outcome(answer) -> str:
    if answer is None:
        return "NONE"
    outcome = getattr(answer, "outcome", None)
    if outcome in ("DENIED", "UNKNOWN", "RELEASED") or outcome not in (None, "DISPATCHED"):
        code = getattr(answer, "code", None) or getattr(answer, "gateway_code", None)
        return str(outcome) + ("" if code is None else ":" + str(code))
    return str(outcome)


def _recovery(rig: Rig, timeout: float) -> tuple[float | None, float | None, dict]:
    """Seconds until the Risk observation is VERIFIED and until a new order is DISPATCHED."""
    started = time.perf_counter()
    verified = dispatched = None
    attempts: dict[str, int] = {}
    while time.perf_counter() - started < timeout and (verified is None or dispatched is None):
        if verified is None:
            try:
                remaining = max(0.001, timeout - (time.perf_counter() - started))
                summary = rig.admin("risk.admin.status", None,
                                    timeout_seconds=min(10, remaining))["summary"]
                if summary["observation"]["continuity"] == "VERIFIED":
                    verified = time.perf_counter() - started
            except Exception:
                # Continue until the recovery deadline and report missing proof.
                time.sleep(0.02)
        if dispatched is None:
            remaining = timeout - (time.perf_counter() - started)
            if remaining <= 0:
                break
            # risk_order has both reserve and dispatch calls.
            key = rig.key("recover0")
            try:
                _r, _d, answer, _key = rig.risk_order(0, "recover", key=key,
                                                       timeout=min(10, remaining / 2))
                outcome = _outcome(answer)
            except Exception as error:
                outcome = "UNKNOWN:" + type(error).__name__
            attempts[key] = outcome
            if outcome == "DISPATCHED":
                dispatched = time.perf_counter() - started
            elif not outcome.startswith("DENIED"):
                # Do not submit another identity while this attempt is unknown.
                break
        time.sleep(min(0.25, max(0, timeout - (time.perf_counter() - started))))
    return verified, dispatched, attempts


def phase_gateway_restart(rig: Rig, in_flight: int, recover_timeout: float) -> dict:
    reserved_keys, dispatched_keys = [], []
    reserve_attempts, dispatch_attempts = [], []
    for _ in range(in_flight):
        client = rig.clients[0]
        key = rig.key("gwr-reserved")
        try:
            result = client.reserve(rig.support.order_request(key, "c-" + key), request_key=key,
                                    timeout_seconds=20)
            reserve_outcome = (_outcome(result.answer) if result.outcome == "ANSWERED"
                               else result.outcome)
            if reserve_outcome == "RESERVED":
                reserved_keys.append((key, result.answer))
        except Exception as error:
            reserve_outcome = "UNKNOWN:" + type(error).__name__
        reserve_attempts.append({"request_key": key, "outcome": reserve_outcome})
        if reserve_outcome != "RESERVED":
            break  # preserve the original attempt and avoid additional identities
        key = rig.key("gwr-sent0")
        try:
            _r, _d, answer, _key = rig.risk_order(0, "gwr-sent", key=key)
            dispatch_outcome = _outcome(answer)
            if dispatch_outcome == "DISPATCHED":
                dispatched_keys.append(key)
        except Exception as error:
            dispatch_outcome = "UNKNOWN:" + type(error).__name__
        dispatch_attempts.append({"request_key": key, "outcome": dispatch_outcome})
        if dispatch_outcome != "DISPATCHED":
            break  # do not add new command identities while a prior attempt is unknown
    sent_before = rig.support.until(lambda: [rig.gateway_status(0, k) for k in dispatched_keys],
                                    lambda states: all(s == "SENT" for s in states))
    before = {
        "reserved": [rig.risk_state(0, k) for k, _ in reserved_keys],
        "dispatched": [rig.risk_state(0, k) for k in dispatched_keys],
    }
    stop_s = rig.stop_gateway()
    restart_s = rig.start_gateway()
    rig.open_sessions()  # strategies reconnect (their write claims gate the Risk delegate)
    verified, first_order, attempts = _recovery(rig, recover_timeout)
    # In-flight orders: dispatch the reservations made before the restart, settle the sent ones.
    redispatch: dict[str, int] = {}
    redispatch_by_key: dict[str, str] = {}
    for key, answer in reserved_keys:
        try:
            result = rig.clients[0].dispatch(key, permit_id=answer.permit_id,
                                             reservation_id=answer.reservation_id,
                                             view_digest=answer.view_digest, timeout_seconds=20)
            outcome = _outcome(result.answer) if result.outcome == "ANSWERED" else result.outcome
        except Exception as error:
            outcome = "UNKNOWN:" + type(error).__name__
        redispatch_by_key[key] = outcome
        redispatch[outcome] = redispatch.get(outcome, 0) + 1
    recovery_keys = [key for key, outcome in attempts.items() if not outcome.startswith("DENIED")]
    to_settle = ([(0, key) for key in dispatched_keys] + [(0, key) for key, _ in reserved_keys]
                 + [(0, key) for key in recovery_keys])
    settlement = rig.settle(to_settle, wait_seconds=5, status_wait_seconds=10,
                            required_risk_orders=to_settle)
    after = {
        "reserved": [rig.risk_state(0, k) for k, _ in reserved_keys],
        "reserved_gateway": [rig.gateway_status(0, k) for k, _ in reserved_keys],
        "dispatched": [rig.risk_state(0, k) for k in dispatched_keys],
    }
    # The operator's remedy measured as well: restart the Risk host on the same files.
    rig.stop_risk()
    risk_ready = rig.start_risk()
    risk_verified, risk_first_order, risk_attempts = _recovery(rig, recover_timeout)
    risk_recovery_keys = [(0, key) for key, outcome in risk_attempts.items()
                          if not outcome.startswith("DENIED")]
    risk_recovery_settlement = rig.settle(risk_recovery_keys, wait_seconds=5,
                                          status_wait_seconds=10,
                                          required_risk_orders=risk_recovery_keys)
    time.sleep(3)
    after_risk_restart = {
        "reserved": [rig.risk_state(0, k) for k, _ in reserved_keys],
        "reserved_gateway": [rig.gateway_status(0, k) for k, _ in reserved_keys],
        "dispatched": [rig.risk_state(0, k) for k in dispatched_keys],
    }
    reserved_evidence = [
        {"request_key": key, "risk_before": before["reserved"][i],
         "dispatch_outcome": redispatch_by_key.get(key, "UNKNOWN"),
         "risk_after": after["reserved"][i], "gateway_after": after["reserved_gateway"][i],
         "risk_after_risk_restart": after_risk_restart["reserved"][i],
         "gateway_after_risk_restart": after_risk_restart["reserved_gateway"][i]}
        for i, (key, _answer) in enumerate(reserved_keys)
    ]
    dispatched_evidence = [
        {"request_key": key, "gateway_before": sent_before[i],
         "risk_before": before["dispatched"][i], "risk_after": after["dispatched"][i],
         "risk_after_risk_restart": after_risk_restart["dispatched"][i]}
        for i, key in enumerate(dispatched_keys)
    ]
    reserved_ok = all(row["risk_before"] == "RESERVED"
                      and row["dispatch_outcome"] == "DISPATCHED"
                      and row["risk_after"] == row["risk_after_risk_restart"] == "TERMINAL"
                      and row["gateway_after"] == row["gateway_after_risk_restart"] == "SENT"
                      for row in reserved_evidence)
    dispatched_ok = all(row["gateway_before"] == "SENT" and row["risk_before"] == "DISPATCHED"
                        and row["risk_after"] == row["risk_after_risk_restart"] == "TERMINAL"
                        for row in dispatched_evidence)
    return {
        "measurement_status": "OBSERVED" if (
            len(reserved_keys) == in_flight and len(dispatched_keys) == in_flight
            and len(sent_before) == in_flight and all(s == "SENT" for s in sent_before)
            and verified is not None and first_order is not None
            and risk_verified is not None and risk_first_order is not None
            and settlement["measurement_status"] == "OBSERVED"
            and risk_recovery_settlement["measurement_status"] == "OBSERVED"
            and reserved_ok and dispatched_ok
        ) else "INCOMPLETE",
        "then_risk_restart": {
            "start_to_ready_file_seconds": round(risk_ready, 3),
            "ready_to_observation_verified_seconds":
                None if risk_verified is None else round(risk_verified, 3),
            "ready_to_first_new_order_dispatched_seconds":
                None if risk_first_order is None else round(risk_first_order, 3),
            "new_order_attempt_outcomes": risk_attempts,
            "risk_states_3s_later": after_risk_restart,
        },
        "in_flight_reserved": len(reserved_keys),
        "in_flight_dispatched": len(dispatched_keys),
        "reserve_attempts": reserve_attempts,
        "dispatch_attempts": dispatch_attempts,
        "gateway_states_before_restart": sent_before,
        "settlement_after_restart": settlement,
        "settlement_after_risk_restart": risk_recovery_settlement,
        "reserved_order_evidence": reserved_evidence,
        "dispatched_order_evidence": dispatched_evidence,
        "risk_states_before": before,
        "gateway_stop_seconds": round(stop_s, 3),
        "gateway_start_to_ready_seconds": round(restart_s, 3),
        "after_gateway_ready_to_observation_verified_seconds":
            None if verified is None else round(verified, 3),
        "after_gateway_ready_to_first_new_order_dispatched_seconds":
            None if first_order is None else round(first_order, 3),
        "new_order_attempt_outcomes": attempts,
        "redispatch_of_pre_restart_reservations": redispatch,
        "risk_states_5s_after_terminal_returns": after,
        "recover_timeout_seconds": recover_timeout,
    }


# ------------------------------------------------------------------------------ main


def _markdown(result: dict) -> str:
    def row(label, stats):
        stats = stats or {}
        return (f"| {label} | {stats.get('n')} | {stats.get('p50_ms')} | {stats.get('p99_ms')} | "
                f"{stats.get('max_ms')} |")

    def failed(label, value):
        return f"| {label} | phase failed: {value.get('phase_failed')} {value.get('code')} | | | |"

    lines = ["# Risk service measurements (64.11, INTEGRATION_SIM, loopback)", ""]
    lines.append("Machine: " + ", ".join(f"{k}={v}" for k, v in result["machine"].items()))
    lines.append("Load average (1/5/15 min) by phase: " + json.dumps(result["load_average"]))
    lines.append("Config: " + json.dumps(result["config"], sort_keys=True))
    lines.append("Result: " + result.get("measurement_status", "INCOMPLETE"))
    lines.append("")
    lines.append("| Measurement | n | p50 ms | p99 ms | max ms |")
    lines.append("| --- | --- | --- | --- | --- |")
    rt = result.get("round_trips", {})
    if "phase_failed" in rt:
        lines.append(failed("Round trips", rt))
    else:
        lines.append("| Round trips status: " + rt.get("measurement_status", "INCOMPLETE")
                     + " | | | | |")
        for label, name in (("Direct Gateway reserve", "direct_reserve"),
                            ("Direct Gateway dispatch", "direct_dispatch"),
                            ("Risk reserve (SDK->Risk->Gateway)", "risk_reserve"),
                            ("Risk dispatch / claim (SDK->Risk->Gateway)", "risk_dispatch")):
            lines.append(row(label, rt.get(name)))
    for item in result.get("claim_throughput", []):
        if "phase_failed" in item:
            lines.append(failed("Claims", item))
            continue
        lines.append(row(f"Claim latency, {item['concurrency']} client processes "
                         f"({item['claims_per_second']} attempts/s, outcomes {item['outcomes']}, "
                         f"{item['measurement_status']})",
                         item["claim_latency"]))
    lag = result.get("observation_lag", {})
    if "phase_failed" in lag:
        lines.append(failed("Observation lag", lag))
    else:
        lines.append(row("ORDER filled ingest -> Risk TERMINAL poll (includes synthetic TRADE "
                         "injection and polling delay) "
                         f"(not observed {lag.get('not_observed')})",
                         lag.get("gateway_ingest_to_risk_terminal")))
    for name, slow in result.get("slow_disk", {}).items():
        if "phase_failed" in slow:
            lines.append(failed("Slow disk " + name, slow))
            continue
        for label, key in (("reserve", "risk_reserve"), ("dispatch", "risk_dispatch")):
            lines.append(row(f"Slow disk {name}: Risk {label}", slow["round_trips"].get(key)))
        item = slow["claim_throughput"]
        lines.append(row(f"Slow disk {name}: claim, {item['concurrency']} client processes "
                         f"({item['claims_per_second']} claims/s, outcomes {item['outcomes']})",
                         item["claim_latency"]))
    lines.append("")
    lines.append("| Restart | runs (seconds: ready file / status available / observation verified) |")
    lines.append("| --- | --- |")
    for item in result.get("risk_restart", []):
        if "phase_failed" in item:
            lines.append(f"| Risk restart | phase failed: {item['phase_failed']} |")
            continue
        runs = "; ".join(f"{r['process_start_to_ready_file_seconds']} / "
                         f"{r['process_start_to_status_available_seconds']} / "
                         f"{r['process_start_to_observation_verified_seconds']}"
                         for r in item["runs"])
        lines.append(f"| Risk {item['mode']} then start | {runs} |")
    g = result.get("gateway_restart", {})
    if "phase_failed" in g:
        lines.append(f"| Gateway stop+start | phase failed: {g['phase_failed']} {g.get('code')} |")
    elif g:
        lines.append(f"| Gateway stop+start ({g['in_flight_reserved']} reserved, "
                     f"{g['in_flight_dispatched']} sent in flight) | Gateway ready "
                     f"{g['gateway_start_to_ready_seconds']}; Risk observation verified after "
                     f"{g['after_gateway_ready_to_observation_verified_seconds']}; first new order "
                     f"dispatched after "
                     f"{g['after_gateway_ready_to_first_new_order_dispatched_seconds']} "
                     f"(limit {g['recover_timeout_seconds']}) |")
        t = g["then_risk_restart"]
        lines.append(f"| ...then Risk SIGTERM+start | ready file {t['start_to_ready_file_seconds']}; "
                     f"observation verified after {t['ready_to_observation_verified_seconds']}; "
                     f"first new order dispatched after "
                     f"{t['ready_to_first_new_order_dispatched_seconds']} |")
    lines.append("")
    return "\n".join(lines)


def _guard(phase):
    """Run one phase; a failure is recorded as its result instead of ending the run."""
    try:
        return phase()
    except Exception as error:
        result = {"phase_failed": type(error).__name__, "code": str(getattr(error, "code", "")),
                  "measurement_status": "INCOMPLETE"}
        if isinstance(error, MeasurementIncompleteError):
            result["evidence"] = error.evidence
        if hasattr(error, "measurement_cleanup_error"):
            result["cleanup_error"] = error.measurement_cleanup_error
        return result


def _incomplete(value) -> bool:
    if isinstance(value, dict):
        return (value.get("measurement_status") == "INCOMPLETE" or "phase_failed" in value
                or any(_incomplete(item) for item in value.values()))
    if isinstance(value, list):
        return any(_incomplete(item) for item in value)
    return False


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--orders", type=_positive_int, default=200)
    parser.add_argument("--per-client", type=_positive_int, default=20)
    parser.add_argument("--lag-samples", type=_positive_int, default=30)
    parser.add_argument("--slow-disk-ms", default="20,100")
    parser.add_argument("--observation-poll", default="0.02")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in (
        "risk_service_measurements.partial.json", "risk_service_measurements.json",
        "risk_service_measurements.md",
    )):
        parser.error("output directory already contains measurement evidence")
    strategies = 10
    result: dict = {
        "evidence": "INTEGRATION_SIM (loopback Gateway, synthetic dispatcher and returns)",
        "machine": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "config": {
            "orders": args.orders,
            "per_client_claims": args.per_client,
            "lag_samples": args.lag_samples,
            "strategies": strategies,
            "instruments": 1,
            "risk_observation_poll_seconds": args.observation_poll,
            "risk_loop_timeout_ms": 5,
            "risk_maintain_seconds": 1,
            "reservation_ttl_seconds": 30,
            "risk_self_trade": "disabled",
            "risk_account_day_counters": "disabled",
            "clients": ("round trips and lag: strategy 0 in the measuring process; concurrent "
                        "claims: one OS process per strategy (own Gateway session and Risk "
                        "client); a fresh Risk process (same files) before each phase"),
        },
        "load_average": {},
        "notes": [],
        "measurement_status": "INCOMPLETE",
    }
    def save() -> None:
        (out / "risk_service_measurements.partial.json").write_text(
            json.dumps(result, indent=2, sort_keys=True, default=str), encoding="utf-8")

    stage = "construct"
    result["active_stage"] = stage
    save()  # even interrupted initialization leaves explicitly incomplete evidence
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        rig = None
        try:
            rig = Rig(Path(tmp), strategies, observation_poll=args.observation_poll, ttl_seconds=30)
            result["load_average"]["start"] = _load()
            stage = result["active_stage"] = "first_risk_start"
            save()
            result["risk_first_start_seconds"] = round(rig.start_risk(), 3)
            stage = result["active_stage"] = "prime"
            save()
            rig.prime()
            stage = result["active_stage"] = "round_trips"
            save()
            result["round_trips"] = _guard(lambda: phase_round_trips(rig, args.orders))
            result["load_average"]["after_round_trips"] = _load()
            save()
            result["claim_throughput"] = []
            for concurrency in (1, 3, 10):
                stage = result["active_stage"] = "claims_" + str(concurrency)
                save()
                def claim_phase(concurrency=concurrency):
                    rig.fresh_risk()
                    return phase_claim_processes(rig, concurrency, args.per_client)

                result["claim_throughput"].append(_guard(claim_phase))
                result["load_average"]["after_claims_" + str(concurrency)] = _load()
                save()
            stage = result["active_stage"] = "observation_lag"
            save()
            def lag_phase():
                rig.fresh_risk()
                return phase_observation_lag(rig, args.lag_samples)

            result["observation_lag"] = _guard(lag_phase)
            save()
            stage = result["active_stage"] = "risk_restart"
            save()
            result["risk_restart"] = [
                _guard(lambda: phase_risk_restart(rig, 3, kill=False, verify_timeout=60)),
                _guard(lambda: phase_risk_restart(rig, 2, kill=True, verify_timeout=60)),
            ]
            result["load_average"]["after_restarts"] = _load()
            save()
            result["slow_disk"] = {}
            slow = [sys.executable, "-c", _SLOW_LAUNCHER]
            for delay in [value for value in args.slow_disk_ms.split(",") if value]:
                stage = result["active_stage"] = "slow_disk_" + delay
                save()
                env = {"BT_RISK_MEASURE_COMMIT_DELAY_MS": delay}

                def slow_phase(env=env):
                    rig.fresh_risk(launcher=slow, extra_env=env)
                    trips = phase_round_trips(rig, max(20, args.orders // 4))
                    rig.fresh_risk(launcher=slow, extra_env=env)
                    claims = phase_claim_processes(rig, 3, max(5, args.per_client // 2))
                    return {"round_trips": trips, "claim_throughput": claims}

                result["slow_disk"][delay + "ms"] = _guard(slow_phase)
                save()
            stage = result["active_stage"] = "gateway_restart"
            save()
            def gateway_phase():
                rig.fresh_risk()
                return phase_gateway_restart(rig, 5, 90)

            result["gateway_restart"] = _guard(gateway_phase)
            result["load_average"]["end"] = _load()
        except BaseException as error:
            result["failure"] = {"stage": stage, "type": type(error).__name__}
            if hasattr(error, "measurement_cleanup_error"):
                result["cleanup_error"] = error.measurement_cleanup_error
            if rig is not None:
                try:
                    rig.close()
                except BaseException as cleanup_error:
                    result["cleanup_error"] = type(cleanup_error).__name__
            save()
            raise
        else:
            stage = result["active_stage"] = "close"
            save()
            try:
                rig.close()
            except BaseException as error:
                result["failure"] = {"stage": stage, "type": type(error).__name__}
                save()
                raise
    result.pop("active_stage", None)
    final_phases = {k: v for k, v in result.items() if k != "measurement_status"}
    result["measurement_status"] = "INCOMPLETE" if _incomplete(final_phases) else "OBSERVED"
    (out / "risk_service_measurements.json").write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    (out / "risk_service_measurements.md").write_text(_markdown(result), encoding="utf-8")
    print(_markdown(result))
    return 1 if result["measurement_status"] == "INCOMPLETE" else 0


if __name__ == "__main__":
    sys.exit(main())
