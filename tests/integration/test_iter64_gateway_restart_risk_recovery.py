"""Synthetic Gateway restart scenarios with persistent journals.

The Gateway has synthetic venue calls and loopback CURVE. This does not use an
account or prove provider recovery. The legacy v1 case keeps R-05 disabled; the
v2 Execution-parent case opts into a synthetic authenticated R-05 cut.
"""
from __future__ import annotations

import multiprocessing
import json
import sqlite3
import sys
import threading
import time
import uuid

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


def _r05_cut_snapshot(risk_root):
    from bt_api_risk.pretrade.account_order_observations import verify_order_cut_coverage

    path = risk_root / "state" / "risk.sqlite"
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        meta = db.execute("SELECT scope_key FROM risk_r05_meta").fetchone()
        if meta is None:
            return None
        scope = meta[0]
        cut = db.execute(
            "SELECT seq, owner_generation, fact_watermark, observed_at_ns, max_age_ns, "
            "query_row_count, query_complete, receipt_complete, source_history_has_gap "
            "FROM risk_r05_order_cuts WHERE scope_key=? ORDER BY seq DESC LIMIT 1",
            (scope,),
        ).fetchone()
        progress = db.execute(
            "SELECT cursor, ack_observed FROM risk_r05_state WHERE scope_key=?", (scope,)
        ).fetchone()
        if cut is None or progress is None:
            return None
        coverage = verify_order_cut_coverage(db, scope, now_ns=time.time_ns())
        return coverage, cut, progress


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


class _ExecutionParentLoopback:
    """Real Execution journal/dispatcher and CURVE service around the 72.04 loopback."""

    def __init__(self, state, root, *, reuse_storage_id=None):
        from tests.integration.test_service_account_instrument_metadata import _free_endpoint

        from bt_api_base.service_protocol import WireRole, WireScope
        from bt_api_execution.account_journal_host import AccountJournalHost
        from bt_api_execution.contracts import ExecutionScope
        from bt_api_py.execution_parent_service_host import (
            ParentServiceHost,
            load_config as load_parent_config,
        )

        self.state = state
        self.root = root / "execution"
        self.journal_root = self.root / "journal"
        self.registry_root = self.root / "registry"
        if reuse_storage_id is None:
            self.root.mkdir()
            support._restrict_fixture_path(self.root, mode=0o700)
            self.journal_root.mkdir()
            self.registry_root.mkdir()
            support._restrict_fixture_path(self.journal_root, mode=0o700)
            support._restrict_fixture_path(self.registry_root, mode=0o700)
        elif (type(reuse_storage_id) is not str or not reuse_storage_id
              or not self.root.is_dir() or not self.journal_root.is_dir()
              or not self.registry_root.is_dir()):
            raise ValueError("SYNTHETIC_EXECUTION_JOURNAL_REUSE_INVALID")

        self.endpoint = _free_endpoint()
        self.identity = support.execution_identity()
        self.strategy = support.strategy_identity(0)
        self.scope_ref = "execution-scope"
        self.instance_id = "execution-a"
        self.run_id = support.RUN
        self.instrument = support.instrument()
        self.storage_id = str(uuid.uuid4()) if reuse_storage_id is None else reuse_storage_id
        self.registration_key = "72.04-strategy-registration-" + uuid.uuid4().hex
        self.client_session_id = "72.04-strategy-session-" + uuid.uuid4().hex
        execution_scope = ExecutionScope(
            "ctp", "simnow", support.ACCOUNT, self.strategy.actor_ref
        )
        journal_options = ({} if reuse_storage_id is None else {
            "writer_mode": "single_host_cooperative", "require_existing": True,
        })
        with AccountJournalHost(
            root=str(self.journal_root), scope=execution_scope, storage_id=self.storage_id,
            **journal_options,
        ) as journal:
            journal.acquire_intake_lease(
                execution_scope, "72.04-execution-bootstrap", ttl_ns=30_000_000_000
            )

        keys = state.risk_root / "keys"
        public = lambda name: str(keys / (name + ".public"))
        secret = lambda name: str(keys / (name + ".secret"))
        server_identity = support.execution_identity()
        config = {
            "schema": "bt_api_execution.parent_service.v1",
            "service": {
                "service_config_schema_version": 1,
                "process_role": "service",
                "service_id": server_identity.actor_ref,
                "scope_ref": self.scope_ref,
                "transport_ref": "execution-server",
                "registration_policy_ref": "execution-policy-v1",
                "service_requirements_ref": "execution-requirements-v1",
                "components": {"execution": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": self.instance_id,
                        "bind_ports": ["service.execution_parent"],
                        "options": {"role": "atomic_intake"},
                    },
                }},
                "communication": {"config_schema_version": 1, "profiles": {
                    "execution-server": {
                        "backend": "zmq", "config_schema_version": 1,
                        "endpoints": {"rpc_bind": self.endpoint},
                        "security_profile_ref": "execution-curve",
                    },
                }},
            },
            "service_identity": {
                "role": "service", "principal_ref": server_identity.principal_ref,
                "actor_ref": server_identity.actor_ref,
            },
            "authority_versions": {
                "scope": "v1", "security": "v1",
                "registration_policy": "v1", "requirements": "v1",
            },
            "execution_scope": {
                "provider": "ctp", "environment": "simnow",
                "account_ref": support.ACCOUNT,
                "strategy_id": self.strategy.actor_ref,
            },
            "wire_scope": {
                "execution_domain_ref": support.DOMAIN,
                "account_ref": support.ACCOUNT,
                "strategy_ref": self.strategy.actor_ref,
                "run_id": self.run_id,
            },
            "storage": {
                "journal_root": str(self.journal_root),
                "registry_path": str(self.registry_root / "registry.sqlite"),
                "storage_id": self.storage_id,
                "writer_mode": "single_host_cooperative",
                "require_existing": True,
            },
            "server_keys": {
                "public_key_file": public("execution_server"),
                "secret_key_file": secret("execution_server"),
            },
            "strategy_grants": [{
                "identity": {
                    "role": "strategy_client",
                    "principal_ref": self.strategy.principal_ref,
                    "actor_ref": self.strategy.actor_ref,
                },
                "scope_ref": self.scope_ref,
                "revision": "execution-grant-v1",
                "instruments": [self.instrument.to_dict()],
                "operations": ["execution.parent.submit", "execution.parent.query"],
                "public_key_file": public("strategy-execution-0"),
            }],
            "action_grant": {
                "revision": "execution-action-v1",
                "instruments": [self.instrument.to_dict()],
                "plan_versions": ["1"],
                "actions": ["accept", "query"],
            },
            "schedule_build_id": "72.04-synthetic-build",
            "algorithm_policies": [{
                "plan_version": "1",
                "schedules": [{"name": "PASS", "version": 1, "params": {}, "max_retry": 0}],
            }],
            "lease": {"owner": "72.04-execution-owner", "ttl_ns": 30_000_000_000},
            "dispatch": {
                "route": "CTP___FUTURE",
                "risk_endpoint": state.risk_endpoint,
                "risk_service_id": support.RISK_HOST.actor_ref,
                "risk_instance_id": "risk-a",
                "risk_scope_ref": "risk-scope",
                "risk_peer": {
                    "role": "service",
                    "principal_ref": support.RISK_HOST.principal_ref,
                    "actor_ref": support.RISK_HOST.actor_ref,
                },
                "risk_server_public": public("risk_server"),
                "client_public": public("execution_risk"),
                "client_secret": secret("execution_risk"),
                "poll_interval_seconds": 0.05,
            },
        }
        config_path = self.root / (
            "parent_service.json" if reuse_storage_id is None else "parent_service-restart.json"
        )
        config_path.write_text(json.dumps(config), encoding="utf-8")
        support._restrict_fixture_path(config_path, mode=0o600)
        self.host = ParentServiceHost(load_parent_config(str(config_path)))
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.errors = []
        self.startup_exceptions = []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.client = None
        self.registration_id = None
        self.service_epoch = None

    def _run(self):
        def trace(frame, event, arg):
            if event == "exception" and len(self.startup_exceptions) < 12:
                error_type = arg[0]
                filename = frame.f_code.co_filename.replace("\\", "/").rsplit("/", 1)[-1]
                self.startup_exceptions.append(
                    (filename, frame.f_code.co_name, frame.f_lineno, error_type.__name__)
                )
            return trace

        try:
            sys.settrace(trace)
            self.host.start()
            sys.settrace(None)
            self.ready.set()
            while not self.stop.is_set():
                self.host.poll()
        except BaseException as exc:
            sys.settrace(None)
            self.errors.append(exc)
            self.ready.set()
        finally:
            sys.settrace(None)
            try:
                self.host.close()
            except BaseException as exc:
                self.errors.append(exc)

    def start(self):
        from bt_api_transport_zmq.server import CurveClientCredentials
        from bt_api_transport_zmq.service_v2 import ZmqServiceClient
        from bt_api_base.service_protocol import WireScope
        from tests.unit.test_service_host import _request

        self.thread.start()
        assert self.ready.wait(15), "Execution ParentServiceHost did not become ready"
        assert not self.errors, (self.errors, self.startup_exceptions)
        key_dir = self.state.risk_root / "keys"
        self.client = ZmqServiceClient(
            self.endpoint,
            self.identity,
            credentials=CurveClientCredentials(
                (key_dir / "execution_server.public").read_bytes().strip(),
                (key_dir / "strategy-execution-0.public").read_bytes().strip(),
                (key_dir / "strategy-execution-0.secret").read_bytes().strip(),
            ),
        )
        scope = WireScope(
            support.DOMAIN, account_ref=support.ACCOUNT,
            strategy_ref=self.strategy.actor_ref, run_id=self.run_id,
        )
        registration = self.client.request(
            _request(
                "service.register",
                {
                    "registration_key": self.registration_key,
                    "client_session_id": self.client_session_id,
                    "instance_id": self.instance_id,
                    "port": "service.execution_parent",
                    "scope_ref": self.scope_ref,
                    "operations": ["execution.parent.query", "execution.parent.submit"],
                },
                identity=self.strategy,
                scope=scope,
                schema_id="sdk.service.control",
            ),
            timeout=5.0,
        ).payload
        assert registration["ok"] is True, registration
        self.registration_id = registration["result"]["registration_id"]
        self.service_epoch = registration["result"]["service_epoch"]

    def submit(self, parent_ref):
        from bt_api_execution.frozen_algorithm_plan import algorithm_plan_digest
        from bt_api_base.service_protocol import WireScope
        from tests.unit.test_service_host import _request

        expires_at_ns = time.time_ns() + 600_000_000_000
        plan = {
            "schema_version": 1,
            "plan_version": "1",
            "build_id": "72.04-synthetic-build",
            "schedule": {
                "class": "PASS", "version": 1,
                "binding": {
                    "deadline_ns": expires_at_ns, "quantity_step": "1",
                    "parent_limit": "10", "price_step": "1", "retry_limit": 0,
                },
                "params": {},
            },
        }
        payload = {
            "request_id": "request-" + parent_ref,
            "order_type": "limit",
            "spec": {
                "account_scope": support.ACCOUNT,
                "strategy_scope": self.strategy.actor_ref,
                "execution_domain_ref": support.DOMAIN,
                "parent_ref": parent_ref,
                "registration_operation_id": "register-" + parent_ref,
                "risk_operation_id": "risk-" + parent_ref,
                "risk_selected": True,
                "instrument": self.instrument.to_dict(),
                "side": "buy", "offset": "open", "quantity": "1",
                "quantity_unit": "lots", "limit_price": "10", "time_in_force": "DAY",
                "creation_trading_day": support.TRADING_DAY,
                "allowed_trading_days": [support.TRADING_DAY],
                "expires_at_ns": expires_at_ns,
                "grant_revision": "execution-grant-v1",
                "target_position_ref": None,
                "plan_version": "1", "plan_digest": algorithm_plan_digest(plan),
            },
            "algorithm_plan": plan,
        }
        response = self.client.request(
            _request(
                "execution.parent.submit",
                {
                    "registration_id": self.registration_id,
                    "service_epoch": self.service_epoch,
                    "instance_id": self.instance_id,
                    "arguments": payload,
                },
                identity=self.strategy,
                scope=WireScope(
                    support.DOMAIN, account_ref=support.ACCOUNT,
                    strategy_ref=self.strategy.actor_ref, run_id=self.run_id,
                ),
                schema_id="sdk.service.call",
            ),
            timeout=5.0,
        ).payload
        assert response["ok"] is True, response
        assert response["result"]["status"] == "RECEIVED", response
        return response["result"]

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None
        if self.thread.is_alive():
            self.stop.set()
            self.thread.join(timeout=15)
            assert not self.thread.is_alive(), "Execution ParentServiceHost did not stop"


def _gateway_places(state):
    state.controls.put("stats")
    return support.next_message(state.messages, "stats")[1]


def _gateway_rows(state):
    return support.gateway_command_snapshot(state.gateway_root)


def _rows_by_key(rows):
    return {(row[0], row[1]): row for row in rows}


def _private_journal_identity(path):
    info = path.stat()
    device, file_index = info.st_dev, info.st_ino
    if type(device) is not int or type(file_index) is not int or file_index <= 0:
        raise AssertionError("synthetic_journal_file_identity_unavailable")
    return device, file_index


def _execution_journal_path(execution):
    paths = tuple(execution.journal_root.glob(".execution-journal-*.sqlite3"))
    if len(paths) != 1 or not paths[0].is_file():
        raise AssertionError("synthetic_execution_journal_not_unique")
    return paths[0]


def _runtime_closed(snapshot, *, strict_unregister_ack):
    return (
        getattr(snapshot, "state", None) == "closed"
        and getattr(snapshot, "thread_alive", None) is False
        and getattr(snapshot, "cleanup_failures", None) == 0
        and getattr(snapshot, "unregister_uncertainties", None) == 0
        and getattr(snapshot, "owner_cleanup_uncertainties", None) == 0
        and getattr(snapshot, "open_transport_count", None) == 0
        and type(getattr(snapshot, "bindings", None)) is tuple
        and all(getattr(binding, "active", None) is False for binding in snapshot.bindings)
        and (strict_unregister_ack is not True
             or getattr(snapshot, "strict_unregister_ack", None) is True)
    )


def _strict_gateway_runtime_closed(snapshot):
    from bt_api_py.service_runtime_cleanup import runtime_cleanup_confirmed

    # Strict close internally binds the unregister ACK and local cleanup.  Its
    # sticky Threaded capture intentionally leaves open_transport_count unknown;
    # do not turn that unknown value into a zero assertion.
    return (
        runtime_cleanup_confirmed(snapshot) is True
        and getattr(snapshot, "state", None) == "closed"
        and getattr(snapshot, "thread_alive", None) is False
        and getattr(snapshot, "strict_unregister_ack", None) is True
        and getattr(snapshot, "cleanup_failures", None) == 0
        and getattr(snapshot, "unregister_uncertainties", None) == 0
        and getattr(snapshot, "owner_cleanup_uncertainties", None) == 0
        and getattr(snapshot, "unknown_registration_count", None) == 0
        and type(getattr(snapshot, "bindings", None)) is tuple
        and not snapshot.bindings
    )


def _write_planned_restart_receipt(root, facts):
    expected = {
        "scenario", "phase", "legacy_same_pid_case_separate", "original_parent_sent_once",
        "r05_registry_positive_control_before_restart",
        "execution_risk_client_closed", "execution_parent_closed", "risk_clients_closed",
        "risk_order_client_closed", "risk_operator_client_closed", "gateway_client_closed",
        "risk_observer_binding_inactive_after_stop",
        "risk_process_offline_after_planned_stop",
        "risk_cursor_snapshot_present_after_planned_stop",
        "original_gateway_live_during_risk_stop", "risk_cli_normal_stop", "risk_post_cleanup_receipt",
        "original_gateway_exit_zero", "risk_process_offline_after_gateway_stop",
        "gateway_process_offline_after_stop", "risk_cursor_preserved_after_gateway_stop",
        "gateway_pid_changed", "risk_pid_changed",
        "gateway_journal_identity_same", "risk_journal_identity_same",
        "execution_journal_identity_same", "execution_host_recreated",
        "gateway_start_native_empty_confirmed", "old_native_replay_count",
        "old_native_replay_observation_complete", "new_native_send_count",
        "new_native_send_observation_complete", "new_r05_cut_current",
    }
    if (type(facts) is not dict or set(facts) != expected
            or facts["scenario"] != "planned_process_restart"
            or facts["legacy_same_pid_case_separate"] is not True
            or any(type(value) is not bool for key, value in facts.items()
                   if key not in {"scenario", "phase", "old_native_replay_count", "new_native_send_count"})
            or facts["phase"] not in {
                "initial_parent_sent", "clients_closed", "original_risk_stopped_gateway_live",
                "original_gateway_stopped", "gateway_restarted", "risk_restarted", "complete",
                "teardown_risk_stopped",
            }
            or type(facts["old_native_replay_count"]) is not int
            or not 0 <= facts["old_native_replay_count"] <= 100
            or type(facts["new_native_send_count"]) is not int
            or not 0 <= facts["new_native_send_count"] <= 100):
        raise ValueError("INVALID_PLANNED_RESTART_RECEIPT")
    path = root / "planned_restart_diagnostic.json"
    path.write_text(json.dumps(facts, sort_keys=True, separators=(",", ":")) + "\n",
                    encoding="utf-8")
    support._restrict_fixture_path(path, mode=0o600)
    print("PLANNED_RESTART_RECEIPT " + json.dumps(facts, sort_keys=True, separators=(",", ":")))


def _close_execution_parent_observed(execution):
    """Close the parent host and inspect its strict internal Risk-client cleanup."""
    if (execution.host.dispatcher is None
            or execution.host.dispatcher._client is None):
        raise AssertionError("execution_risk_client_unavailable_before_close")
    risk_runtime = execution.host.dispatcher._client._runtime
    caller_client = execution.client
    execution.close()
    snapshot, had_worker = risk_runtime.cleanup_observation(timeout_seconds=5)
    facts = {
        "parent_host_closed": execution.host.state == "closed",
        "parent_thread_stopped": execution.thread.is_alive() is False,
        "parent_client_closed": caller_client is not None and caller_client._closed is True,
        "risk_client_cleanup_confirmed": had_worker is True and _runtime_closed(
            snapshot, strict_unregister_ack=True
        ),
        "parent_host_errors_empty": not execution.errors,
    }
    return facts


def _close_risk_clients_observed(state):
    risk_snapshot = state.client.close(timeout_seconds=10)
    operator_snapshot = state.operator.close(timeout_seconds=10)
    strategy_snapshot = state.strategy.close(timeout_seconds=10)
    facts = {
        "risk_client_closed": _runtime_closed(risk_snapshot, strict_unregister_ack=True),
        "operator_client_closed": _runtime_closed(operator_snapshot, strict_unregister_ack=False),
        "gateway_client_closed": _strict_gateway_runtime_closed(strategy_snapshot),
    }
    state.client = state.operator = state.strategy = None
    return facts


def test_planned_gateway_then_risk_restart_reuses_journals_without_replaying_writes(
    tmp_path_factory,
):
    """Planned Risk close with a live Gateway, then same-journal Gateway/Risk restart."""
    root = support.retained_loopback_root(tmp_path_factory.getbasetemp())
    state = _Loopback(
        root, parent_contract_enabled=True, execution_parent_enabled=True,
        order_cut_enabled=True, strategy_strict_cleanup=True,
    )
    execution = None
    old_execution = None
    risk_processes = []
    gateway_processes = [state.gateway]
    original_risk_pid = None
    original_gateway_pid = state.gateway.pid
    receipt = {
        "scenario": "planned_process_restart",
        "phase": "initial_parent_sent",
        "legacy_same_pid_case_separate": True,
        "original_parent_sent_once": False,
        "r05_registry_positive_control_before_restart": False,
        "execution_risk_client_closed": False,
        "execution_parent_closed": False,
        "risk_clients_closed": False,
        "risk_order_client_closed": False,
        "risk_operator_client_closed": False,
        "gateway_client_closed": False,
        "risk_observer_binding_inactive_after_stop": False,
        "risk_process_offline_after_planned_stop": False,
        "risk_cursor_snapshot_present_after_planned_stop": False,
        "original_gateway_live_during_risk_stop": False,
        "risk_cli_normal_stop": False,
        "risk_post_cleanup_receipt": False,
        "original_gateway_exit_zero": False,
        "risk_process_offline_after_gateway_stop": False,
        "gateway_process_offline_after_stop": False,
        "risk_cursor_preserved_after_gateway_stop": False,
        "gateway_pid_changed": False,
        "risk_pid_changed": False,
        "gateway_journal_identity_same": False,
        "risk_journal_identity_same": False,
        "execution_journal_identity_same": False,
        "execution_host_recreated": False,
        "gateway_start_native_empty_confirmed": False,
        "old_native_replay_count": 0,
        "old_native_replay_observation_complete": False,
        "new_native_send_count": 0,
        "new_native_send_observation_complete": False,
        "new_r05_cut_current": False,
    }
    try:
        state.start_risk(owner_stop_file=True)
        original_risk = state.risk
        risk_processes.append(original_risk)
        original_risk_pid = original_risk.pid
        state.prime_trading_day()
        fresh_before = support.until(
            lambda: _r05_cut_snapshot(state.risk_root),
            lambda value: value is not None and value[0] is True
            and value[1][5] == 0 and value[1][6] == 1 and value[1][7] == 1
            and value[1][8] == 0 and value[2][0] >= value[1][2]
            and value[2][1] >= value[1][2],
            seconds=25,
        )
        assert fresh_before is not None and fresh_before[0] is True, "initial_r05_cut_not_fresh"
        observer_before_restart = support.gateway_observer_snapshot_facts(
            support.gateway_observer_snapshot(state.gateway_root)
        )
        receipt["r05_registry_positive_control_before_restart"] = (
            observer_before_restart["observer_binding_active"] is True
            and observer_before_restart["consumer_owner_present"] is True
            and observer_before_restart["consumer_owner_epoch_current"] is True
            and observer_before_restart["consumer_owner_matches_active_binding"] is True
        )
        assert receipt["r05_registry_positive_control_before_restart"] is True, (
            "initial_r05_registry_positive_control_failed"
        )

        execution = _ExecutionParentLoopback(state, root)
        execution.start()
        baseline_places = _gateway_places(state)
        baseline_rows = _rows_by_key(_gateway_rows(state))
        assert "client-direct-1" in baseline_places, "synthetic_seed_order_missing"
        execution.submit("parent-before-planned-restart")
        sent_rows = support.until(
            lambda: _rows_by_key(_gateway_rows(state)),
            lambda rows: len(rows) == len(baseline_rows) + 1
            and any(row[2] == "SENT" for key, row in rows.items()
                    if key not in baseline_rows),
            seconds=25,
        )
        assert sent_rows is not None, "initial_parent_dispatch_not_sent"
        first_keys = set(sent_rows) - set(baseline_rows)
        assert len(first_keys) == 1, "initial_parent_identity_not_unique"
        (first_key,) = first_keys
        first_row = sent_rows[first_key]
        assert first_row[2] == "SENT", "initial_parent_not_sent"
        first_client_id = json.loads(first_row[5]).get("client_order_id")
        assert type(first_client_id) is str and first_client_id, "initial_native_identity_missing"
        places_before = support.until(
            lambda: _gateway_places(state),
            lambda places: len(set(places) - set(baseline_places)) == 1,
            seconds=15,
        )
        assert places_before is not None, "initial_native_dispatch_missing"
        original_native_ids = set(places_before) - set(baseline_places)
        assert len(original_native_ids) == 1 and all(
            count == 1 for count in places_before.values()
        ), "initial_native_dispatch_not_exactly_once"
        state.ingest(first_client_id, "accepted", "1", "0")
        state.ingest(first_client_id, "filled", "1", "1")
        fresh_after_first = support.until(
            lambda: _r05_cut_snapshot(state.risk_root),
            lambda value: value is not None and value[0] is True
            and value[1][5] == 0 and value[1][6] == 1 and value[1][7] == 1
            and value[1][8] == 0 and value[2][0] >= value[1][2]
            and value[2][1] >= value[1][2],
            seconds=25,
        )
        assert fresh_after_first is not None and fresh_after_first[0] is True, (
            "initial_parent_cut_not_fresh"
        )
        receipt["original_parent_sent_once"] = True
        _write_planned_restart_receipt(state.gateway_root, receipt)

        gateway_path = state.gateway_root / "journal.sqlite"
        risk_path = state.risk_root / "state" / "risk.sqlite"
        execution_path = _execution_journal_path(execution)
        gateway_identity = _private_journal_identity(gateway_path)
        risk_identity = _private_journal_identity(risk_path)
        execution_identity = _private_journal_identity(execution_path)
        gateway_journal_id = state.journal_id
        original_gateway_generation = state.gateway_stop.generation
        original_gateway = state.gateway
        assert original_gateway.is_alive() is True and state.risk.poll() is None, (
            "original_services_not_live_before_ordered_close"
        )

        old_execution = execution
        prior_storage_id = execution.storage_id
        execution_client_facts = _close_execution_parent_observed(execution)
        execution = None
        receipt["execution_risk_client_closed"] = execution_client_facts[
            "risk_client_cleanup_confirmed"
        ]
        receipt["execution_parent_closed"] = (
            execution_client_facts["parent_host_closed"]
            and execution_client_facts["parent_thread_stopped"]
            and execution_client_facts["parent_client_closed"]
            and execution_client_facts["parent_host_errors_empty"]
        )
        client_facts = _close_risk_clients_observed(state)
        receipt["risk_order_client_closed"] = client_facts["risk_client_closed"]
        receipt["risk_operator_client_closed"] = client_facts["operator_client_closed"]
        receipt["risk_clients_closed"] = client_facts["risk_client_closed"] and client_facts[
            "operator_client_closed"
        ]
        receipt["gateway_client_closed"] = client_facts["gateway_client_closed"]
        receipt["phase"] = "clients_closed"
        _write_planned_restart_receipt(state.gateway_root, receipt)
        assert receipt["execution_risk_client_closed"] is True and receipt[
            "execution_parent_closed"
        ] is True, "execution_parent_or_risk_client_close_unconfirmed"
        assert receipt["risk_clients_closed"] is True, "risk_client_close_unconfirmed"
        assert receipt["gateway_client_closed"] is True, "strict_gateway_client_close_unconfirmed"
        assert original_gateway.is_alive() is True and original_risk.poll() is None, (
            "original_gateway_or_risk_stopped_before_client_close"
        )

        gateway_live_before_risk_stop = original_gateway.is_alive() is True
        risk_stop = support.stop_risk_cooperatively(original_risk, timeout=45.0)
        state.risk = None
        gateway_live_after_risk_stop = original_gateway.is_alive() is True
        receipt["original_gateway_live_during_risk_stop"] = (
            gateway_live_before_risk_stop and gateway_live_after_risk_stop
        )
        receipt["risk_cli_normal_stop"] = risk_stop["risk_cli_exit_zero"] is True
        receipt["risk_post_cleanup_receipt"] = (
            risk_stop["owner_stop_announcement_observed"] is True
            and risk_stop["post_cleanup_stopped_observed"] is True
            and risk_stop["marker_owner_only_and_empty"] is True
            and risk_stop["stdout_closed"] is True
            and risk_stop["unexpected_stdout_lines"] == 0
            and risk_stop["marker_present_after_exit"] is True
        )
        receipt["risk_process_offline_after_planned_stop"] = (
            original_risk.poll() is not None and original_risk.returncode == 0
        )
        assert (
            receipt["original_gateway_live_during_risk_stop"] is True
            and receipt["risk_cli_normal_stop"] is True
            and receipt["risk_post_cleanup_receipt"] is True
            and receipt["risk_process_offline_after_planned_stop"] is True
        ), "risk_did_not_close_normally_against_live_gateway"
        risk_cursor_after_planned_stop = _cursor(state.risk_root)
        receipt["risk_cursor_snapshot_present_after_planned_stop"] = (
            type(risk_cursor_after_planned_stop) is tuple
            and len(risk_cursor_after_planned_stop) == 2
            and type(risk_cursor_after_planned_stop[0]) is str
            and (risk_cursor_after_planned_stop[1] is None
                 or type(risk_cursor_after_planned_stop[1]) is str)
        )
        assert receipt["risk_cursor_snapshot_present_after_planned_stop"] is True, (
            "risk_cursor_snapshot_unavailable_after_risk_close"
        )
        stopped_observer = support.until(
            lambda: support.gateway_observer_snapshot_facts(
                support.gateway_observer_snapshot(state.gateway_root)
            ),
            lambda facts: facts["observer_binding_active"] is False,
            seconds=10,
        )
        assert stopped_observer is not None and stopped_observer[
            "observer_binding_active"
        ] is False, "original_risk_observer_remained_registered"
        receipt["risk_observer_binding_inactive_after_stop"] = (
            stopped_observer["observer_binding_active"] is False
        )
        receipt["phase"] = "original_risk_stopped_gateway_live"
        _write_planned_restart_receipt(state.gateway_root, receipt)

        state.gateway_stop.close()
        assert original_gateway.is_alive() is False and original_gateway.exitcode == 0, (
            "original_gateway_did_not_exit_normally"
        )
        receipt["original_gateway_exit_zero"] = True
        receipt["risk_process_offline_after_gateway_stop"] = (
            original_risk.poll() is not None and original_risk.returncode == 0
        )
        receipt["gateway_process_offline_after_stop"] = (
            original_gateway.is_alive() is False and original_gateway.exitcode == 0
        )
        risk_cursor_after_gateway_stop = _cursor(state.risk_root)
        receipt["risk_cursor_preserved_after_gateway_stop"] = (
            receipt["risk_cursor_snapshot_present_after_planned_stop"] is True
            and risk_cursor_after_gateway_stop == risk_cursor_after_planned_stop
        )
        receipt["phase"] = "original_gateway_stopped"
        _write_planned_restart_receipt(state.gateway_root, receipt)
        assert (
            receipt["risk_process_offline_after_gateway_stop"] is True
            and receipt["gateway_process_offline_after_stop"] is True
            and receipt["risk_cursor_preserved_after_gateway_stop"] is True
        ), "planned_outage_changed_persisted_risk_history_or_process_state"

        state.gateway = multiprocessing.get_context("spawn").Process(
            target=support.gateway_main,
            args=(
                str(state.gateway_root), state.gateway_endpoint, state.keys["gateway_server"],
                support.gateway_publics(state.keys, 1), 1, state.messages, state.controls,
                state.order_cut_enabled,
            ),
        )
        state.gateway_stop = support.SyntheticGatewayStop(
            state.gateway, state.controls, state.messages
        )
        state.gateway.start()
        gateway_processes.append(state.gateway)
        ready = support.next_message(state.messages, "ready", 40)
        assert ready[2] == gateway_journal_id, "gateway_journal_id_changed"
        assert ready[1] != original_gateway_pid, "gateway_pid_not_changed"
        assert ready[3] != original_gateway_generation, "gateway_generation_not_changed"
        state.gateway_stop.bind_ready(ready)
        receipt["gateway_pid_changed"] = True
        receipt["gateway_journal_identity_same"] = (
            _private_journal_identity(gateway_path) == gateway_identity
        )
        assert receipt["gateway_journal_identity_same"] is True, (
            "gateway_journal_file_identity_changed"
        )
        assert _rows_by_key(_gateway_rows(state)) == sent_rows, "gateway_rows_changed_on_restart"
        assert _gateway_places(state) == {}, "old_native_command_replayed_on_gateway_start"
        receipt["gateway_start_native_empty_confirmed"] = True
        receipt["phase"] = "gateway_restarted"
        _write_planned_restart_receipt(state.gateway_root, receipt)

        state.start_risk(owner_stop_file=True)
        restarted_risk = state.risk
        risk_processes.append(restarted_risk)
        assert restarted_risk.pid != original_risk_pid, "risk_pid_not_changed"
        receipt["risk_pid_changed"] = True
        receipt["risk_journal_identity_same"] = (
            _private_journal_identity(risk_path) == risk_identity
        )
        assert receipt["risk_journal_identity_same"] is True, "risk_journal_file_identity_changed"
        state.strategy = support.strategy_session(
            state.gateway_endpoint, state.keys, state.journal_id, strict_cleanup=True
        )
        strategy_snapshot = state.strategy.start(timeout_seconds=20)
        assert strategy_snapshot.healthy is True, "restarted_gateway_strategy_receiver_unhealthy"
        fresh_after_risk = support.until(
            lambda: _r05_cut_snapshot(state.risk_root),
            lambda value: value is not None and value[0] is True
            and value[1][1] == ready[3] and value[1][5] == 0
            and value[1][6] == 1 and value[1][7] == 1 and value[1][8] == 0
            and value[2][0] >= value[1][2] and value[2][1] >= value[1][2],
            seconds=40,
        )
        assert fresh_after_risk is not None and fresh_after_risk[0] is True, (
            "risk_restart_cut_not_fresh_for_gateway_epoch"
        )
        observer_after_risk = support.gateway_observer_snapshot_facts(
            support.gateway_observer_snapshot(state.gateway_root)
        )
        assert observer_after_risk["observer_binding_active"] is True, (
            "restarted_risk_observer_binding_missing"
        )
        assert observer_after_risk["consumer_owner_epoch_current"] is True, (
            "restarted_risk_consumer_owner_epoch_stale"
        )
        assert observer_after_risk["consumer_owner_matches_active_binding"] is True, (
            "restarted_risk_consumer_owner_binding_mismatch"
        )
        receipt["phase"] = "risk_restarted"
        receipt["new_r05_cut_current"] = True
        _write_planned_restart_receipt(state.gateway_root, receipt)

        execution = _ExecutionParentLoopback(state, root, reuse_storage_id=prior_storage_id)
        assert (
            execution is not old_execution
            and execution.host is not old_execution.host
            and execution.thread is not old_execution.thread
        ), "execution_parent_not_recreated"
        execution.start()
        assert execution.storage_id == prior_storage_id, "execution_storage_id_changed"
        receipt["execution_host_recreated"] = True
        receipt["execution_journal_identity_same"] = (
            _private_journal_identity(_execution_journal_path(execution)) == execution_identity
        )
        assert receipt["execution_journal_identity_same"] is True, (
            "execution_journal_file_identity_changed"
        )

        restored_rows = _rows_by_key(_gateway_rows(state))
        assert restored_rows == sent_rows, "persisted_gateway_rows_changed_after_risk_restart"
        assert _gateway_places(state) == {}, "old_native_command_replayed_after_risk_restart"
        for _ in range(10):
            time.sleep(0.05)
            observed = _gateway_places(state)
            if original_native_ids & set(observed):
                raise AssertionError("original_native_identity_replayed")
            if observed:
                raise AssertionError("unexpected_native_dispatch_before_new_parent")
        receipt["old_native_replay_count"] = 0
        receipt["old_native_replay_observation_complete"] = True

        execution.submit("parent-after-planned-restart")
        final_rows = support.until(
            lambda: _rows_by_key(_gateway_rows(state)),
            lambda rows: len(rows) == len(sent_rows) + 1
            and all(row[2] == "SENT" for row in rows.values()),
            seconds=25,
        )
        assert final_rows is not None, "new_parent_not_dispatched_after_planned_restart"
        new_keys = set(final_rows) - set(sent_rows)
        assert len(new_keys) == 1, "new_parent_dispatch_identity_not_unique"
        assert final_rows[first_key] == first_row, "original_parent_journal_row_changed"
        new_row = final_rows[next(iter(new_keys))]
        new_client_id = json.loads(new_row[5]).get("client_order_id")
        assert type(new_client_id) is str and new_client_id, "new_native_identity_missing"
        new_places = support.until(
            lambda: _gateway_places(state),
            lambda places: len(places) == 1 and all(count == 1 for count in places.values()),
            seconds=15,
        )
        assert new_places is not None and len(new_places) == 1, (
            "new_parent_native_dispatch_not_exactly_once"
        )
        assert not (original_native_ids & set(new_places)), "original_native_identity_replayed_with_new_parent"
        receipt["new_native_send_count"] = sum(new_places.values())
        receipt["new_native_send_observation_complete"] = True
        state.ingest(new_client_id, "accepted", "1", "0")
        state.ingest(new_client_id, "filled", "1", "1")
        fresh_after_new = support.until(
            lambda: _r05_cut_snapshot(state.risk_root),
            lambda value: value is not None and value[0] is True
            and value[1][1] == ready[3] and value[1][5] == 0
            and value[1][6] == 1 and value[1][7] == 1 and value[1][8] == 0
            and value[2][0] >= value[1][2] and value[2][1] >= value[1][2],
            seconds=25,
        )
        assert fresh_after_new is not None and fresh_after_new[0] is True, (
            "new_parent_cut_not_fresh"
        )
        receipt["phase"] = "complete"
        _write_planned_restart_receipt(state.gateway_root, receipt)
    finally:
        active_error = sys.exc_info()[1]
        cleanup_errors = []
        if execution is not None:
            try:
                if execution.host.state != "closed":
                    _close_execution_parent_observed(execution)
                else:
                    execution.close()
            except BaseException as error:
                cleanup_errors.append(error)
        for client_name in ("client", "operator"):
            client = getattr(state, client_name, None)
            if client is not None:
                try:
                    client.close(timeout_seconds=10)
                    setattr(state, client_name, None)
                except BaseException as error:
                    cleanup_errors.append(error)
        if state.strategy is not None:
            try:
                state.strategy.close(timeout_seconds=5)
                state.strategy = None
            except BaseException as error:
                cleanup_errors.append(error)
        if state.risk is not None:
            risk_to_stop = state.risk
            try:
                stop_control = getattr(risk_to_stop, "_iter72_risk_stop", None)
                if stop_control is not None and risk_to_stop.poll() is None:
                    gateway_live_before_stop = (
                        state.gateway is not None and state.gateway.is_alive() is True
                    )
                    risk_stop = support.stop_risk_cooperatively(risk_to_stop, timeout=45.0)
                    gateway_live_after_stop = (
                        state.gateway is not None and state.gateway.is_alive() is True
                    )
                    receipt["original_gateway_live_during_risk_stop"] = (
                        gateway_live_before_stop and gateway_live_after_stop
                    )
                    receipt["risk_cli_normal_stop"] = risk_stop["risk_cli_exit_zero"] is True
                    receipt["risk_post_cleanup_receipt"] = (
                        risk_stop["owner_stop_announcement_observed"] is True
                        and risk_stop["post_cleanup_stopped_observed"] is True
                        and risk_stop["marker_owner_only_and_empty"] is True
                        and risk_stop["stdout_closed"] is True
                        and risk_stop["unexpected_stdout_lines"] == 0
                        and risk_stop["marker_present_after_exit"] is True
                    )
                    if receipt["phase"] not in {
                        "original_risk_stopped_gateway_live", "gateway_restarted",
                        "risk_restarted", "complete",
                    }:
                        receipt["phase"] = "teardown_risk_stopped"
                    try:
                        _write_planned_restart_receipt(state.gateway_root, receipt)
                    except BaseException:
                        pass
                elif stop_control is None:
                    support.stop_risk(risk_to_stop)
                else:
                    cleanup_errors.append(AssertionError("synthetic_risk_stop_already_exited"))
                state.risk = None
            except BaseException as error:
                cleanup_errors.append(error)
        if state.gateway_stop is not None:
            try:
                state.gateway_stop.close()
            except BaseException as error:
                cleanup_errors.append(error)
        for process in risk_processes:
            try:
                if process.poll() is None or process.returncode != 0:
                    cleanup_errors.append(AssertionError("synthetic_risk_pid_not_reaped_cleanly"))
            except BaseException as error:
                cleanup_errors.append(error)
        for process in gateway_processes:
            try:
                if process.is_alive() is not False or process.exitcode != 0:
                    cleanup_errors.append(AssertionError("synthetic_gateway_pid_not_reaped_cleanly"))
            except BaseException as error:
                cleanup_errors.append(error)
        for ipc_queue in (state.messages, state.controls):
            if ipc_queue is not None:
                try:
                    ipc_queue.close()
                    ipc_queue.join_thread()
                except BaseException as error:
                    cleanup_errors.append(error)
        if cleanup_errors:
            categories = tuple(type(error).__name__ for error in cleanup_errors)
            if active_error is not None:
                active_error.add_note("Planned restart teardown categories: " + repr(categories))
            else:
                raise AssertionError(("planned_restart_teardown_failed", categories)) from None
