"""Crash-window recovery for one source-bound strategy CLOSE.

The owner and provider are the existing DIM04/DIM01 synthetic SQLite
composition. Faults cover both the durable-proof-before-Risk window and the
Risk-commit-before-journal-ACK window; the provider observation is never
repeated during either recovery.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import time
import traceback
from dataclasses import replace
from typing import Any

import zmq
from bt_api_risk import DurableRiskGate
from bt_api_py import service_risk_order_entry as risk_entry
from tests.integration import test_dim01_identity_isolation_service as dim01
from tests.integration import test_dim04_strategy_account_isolation as dim04
from tests.integration import test_dim04_strategy_net_close_owner_path as net_close
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)


_INTENT = "dim04-net-close-crash-window-strategy-b"


class _CrashableHostControl:
    """Intercept only the test-owned hard-crash command in the DIM01 host loop."""

    def __init__(self, connection) -> None:
        self._connection = connection

    def send(self, value) -> None:
        self._connection.send(value)

    def poll(self, *args, **kwargs) -> bool:
        return self._connection.poll(*args, **kwargs)

    def recv(self, *args, **kwargs):
        value = self._connection.recv(*args, **kwargs)
        if type(value) is tuple and len(value) == 2 and value[0] == "crash_now":
            exit_code = value[1]
            if type(exit_code) is int and exit_code in {71, 72}:
                os._exit(exit_code)
            raise AssertionError(("unexpected test-owned crash code", exit_code))
        return value

    def close(self) -> None:
        self._connection.close()


def _crash_after_fault(record: dict[str, Any], exit_code: int) -> None:
    """Terminate this test's Host without its service close protocol."""

    assert exit_code in {71, 72}, exit_code
    process = record["process"]
    assert process.is_alive(), f"{record['label']} exited before injected process crash"
    record["pipe"].send(("crash_now", exit_code))
    process.join(15)
    assert not process.is_alive(), f"{record['label']} did not terminate at the crash boundary"
    assert process.exitcode == exit_code, (record["label"], process.exitcode)
    record["pipe"].close()
    process.close()
    record["closed"] = True


def _dim04_close_resolution_interruption_host_process(control, *host_arguments) -> None:
    if len(host_arguments) not in {7, 8}:
        raise AssertionError(("close-recovery Host argument shape", len(host_arguments)))
    base_host_arguments = host_arguments[:6]
    fault_stage = host_arguments[6]
    expected_receipt = host_arguments[7] if len(host_arguments) == 8 else None
    if fault_stage not in {"proof-before-risk", "risk-before-ack", "normal-replay"}:
        raise AssertionError(("unknown CLOSE recovery fault stage", fault_stage))
    if fault_stage == "normal-replay" and (
        type(expected_receipt) is not tuple or len(expected_receipt) != 4
    ):
        raise AssertionError(("missing expected terminal receipt projection", expected_receipt))

    original_resolve = dim04.DurableRiskGate.resolve_dispatch_close
    original_load_close_proof = dim04.RiskOrderJournal.load_terminal_close_resolution_proof
    original_attest_close_proof = dim04.RiskOrderJournal._attest_dispatch_exposure_resolution
    original_recover_close_proof = risk_entry.RiskOrderEntryService._recover_persisted_terminal_close
    original_host_process = dim01._host_process
    original_dispatch_request = dim01.ServiceHost._dispatch_request
    original_install_baseline = dim01._install_baseline_and_allocations
    resolution_attempts = 0
    wrapped_handler_keys: set[tuple[str, str]] = set()
    risk_path = host_arguments[3]
    journal_path = host_arguments[4]

    def _install_or_reuse_baseline(gate, journal) -> None:
        if fault_stage == "proof-before-risk":
            original_install_baseline(gate, journal)
            return

        # A cold owner must recover the original immutable account snapshot and
        # allocations. Re-observing revision 1 would produce a different
        # observation timestamp/fence and conflict with the accepted baseline;
        # refreshing it while a close claim is unresolved is also invalid.
        scopes_and_strategies = (
            (dim01._ACCOUNT_SPOT_A, (dim01._STRATEGY_A, dim01._STRATEGY_B)),
            (dim01._ACCOUNT_SPOT_B, (dim01._STRATEGY_A,)),
            (dim01._ACCOUNT_PERP_A, (dim01._STRATEGY_A,)),
        )
        expected_baseline_scopes = {scope.key for scope, _ in scopes_and_strategies}
        for scope, strategies in scopes_and_strategies:
            risk_baseline = dim04._read_rows(
                risk_path,
                "SELECT scope_key,account_revision,proof_sha256,source_snapshot_sha256,"
                "observed_at_ns,valid_until_ns FROM risk_gross_exposure_baselines "
                "WHERE scope_key=?",
                (scope.key,),
            )
            journal_baseline = dim04._read_rows(
                journal_path,
                "SELECT scope_key,account_revision,proof_sha256,source_snapshot_sha256,"
                "source_snapshot_json FROM risk_order_gross_baselines "
                "WHERE scope_key=? AND account_revision=1",
                (scope.key,),
            )
            if (
                len(risk_baseline) != 1
                or risk_baseline[0]["account_revision"] != 1
                or type(risk_baseline[0]["proof_sha256"]) is not str
                or len(risk_baseline[0]["proof_sha256"]) != 64
                or risk_baseline[0]["valid_until_ns"] <= time.time_ns()
                or len(journal_baseline) != 1
                or journal_baseline[0]["proof_sha256"] != risk_baseline[0]["proof_sha256"]
                or journal_baseline[0]["source_snapshot_sha256"]
                != risk_baseline[0]["source_snapshot_sha256"]
            ):
                raise AssertionError(
                    (
                        "cold recovery did not find the exact live persisted baseline in both stores",
                        scope.key,
                        risk_baseline,
                        journal_baseline,
                    )
                )
            source_snapshot = json.loads(journal_baseline[0]["source_snapshot_json"])
            expected_snapshot_strategies = list(strategies)
            actual_snapshot_strategies = [
                row.get("strategy_id") for row in source_snapshot.get("positions", ())
            ]
            if (
                source_snapshot.get("account_revision") != 1
                or source_snapshot.get("source_schema_version")
                != "dim01-synthetic-account-baseline-v1"
                or actual_snapshot_strategies != expected_snapshot_strategies
                or source_snapshot.get("valid_until_ns") != risk_baseline[0]["valid_until_ns"]
            ):
                raise AssertionError(
                    (
                        "cold recovery journal baseline no longer matches its source snapshot",
                        scope.key,
                        source_snapshot,
                        risk_baseline,
                    )
                )
            expected_strategy_rows = tuple(
                {"scope_key": scope.key, "strategy_id": strategy}
                for strategy in strategies
            )
            expected_sorted = tuple(sorted(expected_strategy_rows, key=lambda row: row["strategy_id"]))
            risk_strategy_rows = dim04._read_rows(
                risk_path,
                "SELECT scope_key,strategy_id FROM risk_gross_baseline_strategies "
                "WHERE scope_key=? ORDER BY strategy_id",
                (scope.key,),
            )
            if risk_strategy_rows != expected_sorted:
                raise AssertionError(
                    (
                        "cold recovery baseline strategy coverage changed",
                        scope.key,
                        risk_strategy_rows,
                        expected_sorted,
                    )
                )
            allocation_rows = dim04._read_rows(
                risk_path,
                "SELECT scope_key,strategy_id FROM risk_strategy_allocations "
                "WHERE scope_key=? ORDER BY strategy_id",
                (scope.key,),
            )
            if allocation_rows != expected_sorted:
                raise AssertionError(
                    ("cold recovery strategy allocations are incomplete", scope.key, allocation_rows)
                )

        risk_scope_rows = dim04._read_rows(
            risk_path,
            "SELECT scope_key FROM risk_gross_exposure_baselines ORDER BY scope_key",
        )
        journal_scope_rows = dim04._read_rows(
            journal_path,
            "SELECT DISTINCT scope_key FROM risk_order_gross_baselines ORDER BY scope_key",
        )
        expected_scope_rows = tuple({"scope_key": key} for key in sorted(expected_baseline_scopes))
        if risk_scope_rows != expected_scope_rows or journal_scope_rows != expected_scope_rows:
            raise AssertionError(
                (
                    "cold recovery found unexpected baseline scopes",
                    risk_scope_rows,
                    journal_scope_rows,
                    expected_scope_rows,
                )
            )

    def _interrupt_at_resolution(self, proof):
        nonlocal resolution_attempts
        resolution_attempts += 1
        proof_rows = dim04._read_rows(
            journal_path,
            "SELECT proof_sha256,source_order_revision,source_fill_revision "
            "FROM risk_order_terminal_close_proofs "
            "WHERE scope_key=? AND principal_ref=? AND intent_id=?",
            (proof.scope.key, dim01._PRINCIPAL_B, _INTENT),
        )
        if len(proof_rows) != 1 or proof_rows[0]["proof_sha256"] != proof.fingerprint:
            raise AssertionError(("full CLOSE proof was not durable before Risk resolution", proof_rows))
        if fault_stage == "proof-before-risk":
            if resolution_attempts != 1:
                raise AssertionError(("pre-Risk interruption repeated", resolution_attempts))
            prior_risk_rows = dim04._read_rows(
                risk_path,
                "SELECT permit_id FROM risk_dispatch_resolutions WHERE permit_id=?",
                (proof.permit_id,),
            )
            prior_close_rows = dim04._read_rows(
                risk_path,
                "SELECT permit_id FROM risk_close_order_resolutions WHERE permit_id=?",
                (proof.permit_id,),
            )
            if prior_risk_rows or prior_close_rows:
                raise AssertionError(
                    (
                        "Risk resolution preceded the injected pre-resolve cut",
                        prior_risk_rows,
                        prior_close_rows,
                    )
                )
            control.send(
                (
                    "synthetic_close_proof_persisted_before_risk",
                    proof.fingerprint,
                    proof_rows[0]["source_order_revision"],
                    proof_rows[0]["source_fill_revision"],
                )
            )
            raise RuntimeError("injected interruption after durable proof and before Risk resolution")

        control.send(
            (
                "synthetic_close_resolve_entered",
                fault_stage,
                proof.fingerprint,
            )
        )
        try:
            receipt = original_resolve(self, proof)
        except Exception as error:
            frames = tuple(
                (
                    os.path.basename(frame.f_code.co_filename),
                    frame.f_code.co_name,
                    line_number,
                )
                for frame, line_number in traceback.walk_tb(error.__traceback__)
            )
            cause = error.__cause__
            cause_facts = None
            if cause is not None:
                cause_facts = (
                    type(cause).__name__,
                    getattr(cause, "code", None),
                    tuple(
                        (
                            os.path.basename(frame.f_code.co_filename),
                            frame.f_code.co_name,
                            line_number,
                        )
                        for frame, line_number in traceback.walk_tb(cause.__traceback__)
                    ),
                )
            control.send(
                (
                    "synthetic_close_resolve_error",
                    type(error).__name__,
                    getattr(error, "code", None),
                    frames,
                    cause_facts,
                )
            )
            raise
        if (
            receipt.status.value != "TERMINAL_CLOSE_RESOLVED"
            or receipt.position_effect != "CLOSE"
            or receipt.dispatch_latch_active is not False
            or proof.observation.position_effect != "CLOSE"
            or receipt.proof_sha256 != proof.fingerprint
        ):
            raise AssertionError("Risk did not return the exact terminal CLOSE receipt")
        if fault_stage == "risk-before-ack":
            if resolution_attempts != 1:
                raise AssertionError(("post-Risk interruption repeated", resolution_attempts))
            risk_rows = dim04._read_rows(
                risk_path,
                "SELECT proof_kind,resolution_proof_sha256,exposure_proof_sha256 "
                "FROM risk_dispatch_resolutions WHERE permit_id=?",
                (proof.permit_id,),
            )
            close_rows = dim04._read_rows(
                risk_path,
                "SELECT proof_sha256,close_proof_sha256 "
                "FROM risk_close_order_resolutions WHERE permit_id=?",
                (proof.permit_id,),
            )
            freeze_rows = dim04._read_rows(
                risk_path,
                "SELECT active FROM risk_freezes WHERE scope_key=? AND cause_id=?",
                (proof.scope.key, proof.claim.cause_id),
            )
            if (
                risk_rows
                != (
                    {
                        "proof_kind": "TERMINAL_CLOSE",
                        "resolution_proof_sha256": proof.fingerprint,
                        "exposure_proof_sha256": receipt.exposure_proof_sha256,
                    },
                )
                or close_rows
                != (
                    {
                        "proof_sha256": proof.fingerprint,
                        "close_proof_sha256": receipt.exposure_proof_sha256,
                    },
                )
                or freeze_rows != ({"active": 0},)
            ):
                raise AssertionError(
                    ("Risk terminal CLOSE commit is not complete", risk_rows, close_rows, freeze_rows)
                )
            receipt_projection = (
                proof.fingerprint,
                receipt.exposure_proof_sha256,
                receipt.exposure_reservation_id,
                receipt.exposure_reservation_sha256,
            )
            control.send(
                (
                    "synthetic_close_risk_committed_before_ack",
                    *receipt_projection,
                )
            )
            os._exit(72)
        receipt_projection = (
            proof.fingerprint,
            receipt.exposure_proof_sha256,
            receipt.exposure_reservation_id,
            receipt.exposure_reservation_sha256,
        )
        if fault_stage != "normal-replay" or resolution_attempts != 1:
            raise AssertionError(("unexpected Risk close resolution", fault_stage, resolution_attempts))
        if receipt_projection != expected_receipt:
            raise AssertionError(("Risk close proof replay changed its durable receipt", receipt_projection))
        control.send(("synthetic_close_same_receipt_replayed", *receipt_projection))
        return receipt

    def _load_close_proof_with_diagnostic(self, *args, **kwargs):
        trace_state = {"line": None, "exceptions": []}
        previous_trace = sys.gettrace()

        def _trace_attestation(frame, event, value):
            if frame.f_code is original_attest_close_proof.__code__:
                if event == "line":
                    trace_state["line"] = frame.f_lineno
                elif event == "exception":
                    error_type, error_value, _traceback = value
                    trace_state["exceptions"].append(
                        (
                            frame.f_lineno,
                            error_type.__name__,
                            getattr(error_value, "code", None),
                        )
                    )
                elif event == "return":
                    control.send(
                        (
                            "dim04_test_close_proof_attestation_return",
                            trace_state["line"],
                            value is None,
                            tuple(trace_state["exceptions"]),
                        )
                    )
            return _trace_attestation

        sys.settrace(_trace_attestation)
        try:
            proof = original_load_close_proof(self, *args, **kwargs)
        except Exception as error:
            frames = tuple(
                (
                    os.path.basename(frame.f_code.co_filename),
                    frame.f_code.co_name,
                    line_number,
                )
                for frame, line_number in traceback.walk_tb(error.__traceback__)
            )
            control.send(
                (
                    "dim04_test_close_proof_load_error",
                    type(error).__name__,
                    getattr(error, "code", None),
                    frames,
                )
            )
            raise
        finally:
            sys.settrace(previous_trace)
        control.send(
            (
                "dim04_test_close_proof_load_result",
                proof is not None,
                None if proof is None else proof.fingerprint,
            )
        )
        return proof

    def _recover_close_proof_with_diagnostic(self, *args, **kwargs):
        handled, recovered = original_recover_close_proof(self, *args, **kwargs)
        control.send(
            (
                "dim04_test_close_proof_recovery_result",
                handled,
                recovered.state,
                recovered.latest_event.get("reason_code"),
            )
        )
        return handled, recovered

    dim04.DurableRiskGate.resolve_dispatch_close = _interrupt_at_resolution
    dim04.RiskOrderJournal.load_terminal_close_resolution_proof = _load_close_proof_with_diagnostic
    risk_entry.RiskOrderEntryService._recover_persisted_terminal_close = (
        _recover_close_proof_with_diagnostic
    )
    def _host_process_with_crash_interception(host_control, *arguments) -> None:
        return original_host_process(_CrashableHostControl(host_control), *arguments)

    def _dispatch_with_result_diagnostic(self, principal, request):
        registry = self._registry
        operation = request.header.operation
        if registry is not None and operation in {"orders.submit", "orders.query"}:
            handlers = registry._handlers
            for key, handler in tuple(handlers.items()):
                if key[1] != operation or key in wrapped_handler_keys:
                    continue

                def _handler_with_exception_diagnostic(
                    context, arguments, *, _handler=handler, _operation=operation
                ):
                    try:
                        return _handler(context, arguments)
                    except Exception as error:
                        frames = tuple(
                            (
                                os.path.basename(frame.f_code.co_filename),
                                frame.f_code.co_name,
                                line_number,
                            )
                            for frame, line_number in traceback.walk_tb(error.__traceback__)
                        )
                        control.send(
                            (
                                "dim04_test_handler_exception",
                                _operation,
                                type(error).__name__,
                                getattr(error, "code", None),
                                frames,
                            )
                        )
                        raise

                handlers[key] = _handler_with_exception_diagnostic
                wrapped_handler_keys.add(key)
        result = original_dispatch_request(self, principal, request)
        if operation in {"orders.submit", "orders.query"}:
            control.send(
                (
                    "dim04_test_dispatch_result",
                    operation,
                    dim04._strict_json_thaw(result),
                )
            )
        return result

    dim01._install_baseline_and_allocations = _install_or_reuse_baseline
    dim01._host_process = _host_process_with_crash_interception
    dim01.ServiceHost._dispatch_request = _dispatch_with_result_diagnostic
    try:
        net_close._dim04_net_close_host_process(
            control,
            *base_host_arguments,
            expected_provider_close_calls=1 if fault_stage == "proof-before-risk" else 0,
        )
        if fault_stage == "normal-replay" and resolution_attempts != 1:
            raise AssertionError(("terminal CLOSE recovery resolution count", resolution_attempts))
    finally:
        dim04.DurableRiskGate.resolve_dispatch_close = original_resolve
        dim04.RiskOrderJournal.load_terminal_close_resolution_proof = original_load_close_proof
        risk_entry.RiskOrderEntryService._recover_persisted_terminal_close = (
            original_recover_close_proof
        )
        dim01._install_baseline_and_allocations = original_install_baseline
        dim01._host_process = original_host_process
        dim01.ServiceHost._dispatch_request = original_dispatch_request


def _runtime_result_with_host_diagnostic(
    client: dict[str, Any], host: dict[str, Any], *command: Any
) -> dict[str, Any]:
    """Bind the SDK result to the corresponding real Host dispatch result."""

    operation = "orders.submit" if command[0] == "submit" else "orders.query"
    pending = host.setdefault("pending_host_events", [])
    diagnostics = host.setdefault("dispatch_diagnostics", [])
    handler_diagnostics = host.setdefault("handler_diagnostics", [])

    def _receive_dispatch_result() -> dict[str, Any]:
        for index, row in enumerate(diagnostics):
            if row[1] == operation:
                return diagnostics.pop(index)[2]
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if not host["pipe"].poll(remaining):
                break
            row = _receive(host["pipe"])
            if type(row) is tuple and row and row[0] == "dim04_test_handler_exception":
                handler_diagnostics.append(row)
                continue
            if type(row) is tuple and row and row[0] == "dim04_test_dispatch_result":
                diagnostics.append(row)
                if row[1] == operation:
                    return diagnostics.pop()[2]
            else:
                pending.append(row)
        raise AssertionError(("missing Host dispatch diagnostic", operation, diagnostics, pending))

    try:
        result = net_close._runtime_result(client, *command)
    except AssertionError as client_error:
        dispatch_result = _receive_dispatch_result()
        raise AssertionError(
            (
                "SDK request failed with this Host dispatch result",
                operation,
                dispatch_result,
                tuple(handler_diagnostics),
            )
        ) from client_error
    dispatch_result = _receive_dispatch_result()
    return result


def _receive_host_event(host: dict[str, Any]):
    pending = host.setdefault("pending_host_events", [])
    if pending:
        return pending.pop(0)
    while True:
        row = _receive(host["pipe"])
        if type(row) is tuple and row and row[0] == "dim04_test_handler_exception":
            host.setdefault("handler_diagnostics", []).append(row)
            raise AssertionError(("Host handler raised before expected event", row))
        if type(row) is tuple and row and row[0] == "dim04_test_dispatch_result":
            host.setdefault("dispatch_diagnostics", []).append(row)
            continue
        return row


def _read_rows(database_path: str, query: str, parameters: tuple[Any, ...] = ()):
    return dim04._read_rows(database_path, query, parameters)


def test_dim04_close_recovers_exact_risk_commit_before_provider_query(tmp_path) -> None:
    """Cold owner reconstruction completes each persisted CLOSE recovery window."""

    context = multiprocessing.get_context("spawn")
    endpoint = dim01._endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    key_material = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_a_public": zmq.curve_keypair()[0],
        "strategy_b_public": strategy_public,
    }
    paths = {
        "registry": str(tmp_path / "dim04-close-recovery-registry.sqlite3"),
        "risk": str(tmp_path / "dim04-close-recovery-risk.sqlite3"),
        "journal": str(tmp_path / "dim04-close-recovery-journal.sqlite3"),
        "provider": str(tmp_path / "dim04-close-recovery-provider.sqlite3"),
    }
    net_close._seed_synthetic_net_position(paths["provider"])
    _, before_a, before_b = dim04._seed_shared_account_close(
        paths["risk"], paths["journal"]
    )
    host = None
    client = None
    hosts: list[dict[str, Any]] = []
    clients: list[dict[str, Any]] = []
    forced: list[str] = []
    failures: list[str] = []

    def start_host(fault_stage: str, expected_receipt: tuple[Any, ...] | None = None):
        arguments = (
            dim01._service_config(endpoint),
            paths["registry"],
            key_material,
            paths["risk"],
            paths["journal"],
            paths["provider"],
            fault_stage,
        )
        if expected_receipt is not None:
            arguments += (expected_receipt,)
        host_record = _start_process(
            context,
            _dim04_close_resolution_interruption_host_process,
            arguments,
            "dim04-close-recovery-owner-host-" + fault_stage,
        )
        hosts.append(host_record)
        assert _receive(host_record["pipe"]) == (
            "synthetic_close_contract",
            dim01._EXCHANGE_SPOT_A_ALIAS,
            dim01._PROFILE_SPOT_ROTATED,
            ("OPEN", "CLOSE"),
        )
        assert _receive(host_record["pipe"]) == (
            "synthetic_provider_fixture",
            "bt_api.plugins",
            "empty",
        )
        startup_replay_events = []
        while True:
            startup_event = _receive(host_record["pipe"])
            if startup_event[0] == "synthetic_close_risk_committed_before_ack":
                assert fault_stage == "risk-before-ack", startup_event
                process = host_record["process"]
                process.join(15)
                assert not process.is_alive(), "Risk-before-ACK recovery Host did not crash at the cut"
                assert process.exitcode == 72, (host_record["label"], process.exitcode)
                host_record["startup_crash_event"] = startup_event
                _assert_close_proof_reloaded(host_record, startup_event[1])
                host_record["pipe"].close()
                process.close()
                host_record["closed"] = True
                return host_record
            if startup_event[0] == "synthetic_close_same_receipt_replayed":
                startup_replay_events.append(startup_event)
                host_record.setdefault("pending_host_events", []).append(startup_event)
                continue
            if startup_event[0] in {
                "dim04_test_close_proof_load_error",
                "dim04_test_close_proof_load_result",
                "dim04_test_close_proof_attestation_return",
                "dim04_test_close_proof_recovery_result",
                "synthetic_close_resolve_entered",
                "synthetic_close_resolve_error",
            }:
                host_record.setdefault("startup_diagnostics", []).append(startup_event)
                continue
            if startup_event[0] == "error":
                ready = startup_event
                break
            ready = startup_event
            break
        if fault_stage == "normal-replay":
            resolve_errors = tuple(
                (
                    row[1],
                    row[2],
                    tuple((frame[1], frame[2]) for frame in row[3][-4:]),
                    None
                    if row[4] is None
                    else (
                        row[4][0],
                        row[4][1],
                        tuple((frame[1], frame[2]) for frame in row[4][2][-4:]),
                    ),
                )
                for row in host_record.get("startup_diagnostics", ())
                if row[0] == "synthetic_close_resolve_error"
            )
            assert not resolve_errors, ("exact Risk CLOSE replay raised", resolve_errors)
            assert len(startup_replay_events) == 1, (
                {
                    "replay_events": startup_replay_events,
                    "diagnostics": tuple(
                        (
                            (
                                row[0],
                                row[1],
                                row[2],
                                tuple((frame[1], frame[2]) for frame in row[3][-4:]),
                            )
                            if row[0] == "synthetic_close_resolve_error"
                            else (row[0], *row[1:3])
                        )
                        for row in host_record.get("startup_diagnostics", ())
                    ),
                }
            )
            assert startup_replay_events == [
                ("synthetic_close_same_receipt_replayed", *expected_receipt)
            ], startup_replay_events
            _assert_close_proof_reloaded(host_record, expected_receipt[0], resolved=True)
        elif fault_stage == "risk-before-ack":
            raise AssertionError(
                (
                    "cold recovery reached readiness without the required post-Risk/pre-ACK cut",
                    ready,
                    host_record.get("startup_diagnostics", ()),
                )
            )
        if ready[0] == "error":
            process = host_record["process"]
            process.join(15)
            assert not process.is_alive(), "failed DIM04 recovery ServiceHost did not exit"
            exit_code = process.exitcode
            host_record["pipe"].close()
            process.close()
            host_record["closed"] = True
            raise AssertionError(f"DIM04 recovery ServiceHost startup failed: {ready!r} ({exit_code})")
        assert ready == ("ready", endpoint, "started"), ready
        return host_record

    def _assert_close_proof_reloaded(
        host_record: dict[str, Any], fingerprint: str, *, resolved: bool = False
    ) -> None:
        diagnostics = host_record.get("startup_diagnostics", [])
        assert any(
            row[0] == "dim04_test_close_proof_attestation_return"
            and row[2] is False
            and row[3] == ()
            for row in diagnostics
        ), diagnostics
        assert (
            "dim04_test_close_proof_load_result",
            True,
            fingerprint,
        ) in diagnostics, diagnostics
        load_errors = [
            row for row in diagnostics if row[0] == "dim04_test_close_proof_load_error"
        ]
        assert not load_errors, load_errors
        if resolved:
            assert (
                "dim04_test_close_proof_recovery_result",
                True,
                "ACKED_TRACKED",
                None,
            ) in diagnostics, diagnostics

    def start_client():
        client_record = _start_process(
            context,
            net_close._net_close_runtime_client,
            (
                endpoint,
                strategy_public,
                strategy_secret,
                server_public,
                dim01._STRATEGY_B,
                dim01._PRINCIPAL_B,
                dim01._ROUTE_SPOT_A_STRATEGY_B,
            ),
            "dim04-close-resolution-recovery-strategy-runtime",
        )
        clients.append(client_record)
        client_ready = _receive(client_record["pipe"])
        assert client_ready[0] == "ready" and client_ready[1], client_ready
        return client_record

    try:
        # First process dies after the provider result and complete source-bound
        # close proof are durable, but before Risk has written a resolution.
        host = start_host("proof-before-risk")
        client = start_client()

        interrupted = _runtime_result_with_host_diagnostic(
            client, host, "submit", _INTENT, "SELL", "CLOSE", True
        )
        assert interrupted["ok"] is True and interrupted["code"] == "OK", interrupted
        assert interrupted["result"]["record"]["state"] == "UNKNOWN", interrupted
        provider_write = _receive_host_event(host)
        assert provider_write[0] == "synthetic_close_written", provider_write
        proof_cut = _receive_host_event(host)
        assert proof_cut[0] == "synthetic_close_proof_persisted_before_risk", proof_cut
        assert type(proof_cut[1]) is str and len(proof_cut[1]) == 64, proof_cut
        assert proof_cut[2:] == (5, 6), proof_cut

        # The first submit made one exact provider order/fill observation. The
        # recovery below must reuse the stored proof and Risk receipt, without
        # performing another provider query or order submission.
        orders_before_recovery, counters_before_recovery = dim04._provider_evidence(
            paths["provider"]
        )
        assert len(orders_before_recovery) == 1, orders_before_recovery
        assert counters_before_recovery == {"fill_queries": 1, "order_queries": 1}, counters_before_recovery
        assert provider_write[1] == orders_before_recovery[0]["client_order_id"], provider_write

        assert _close_normally(client, expected_tag="closed") == ("closed", "closed")
        client = None
        _crash_after_fault(host, 71)
        host = None
        assert _read_rows(
            paths["journal"],
            "SELECT proof_sha256 FROM risk_order_terminal_close_proofs "
            "WHERE scope_key=? AND principal_ref=? AND intent_id=?",
            (dim01._ACCOUNT_SPOT_A.key, dim01._PRINCIPAL_B, _INTENT),
        ) == ({"proof_sha256": proof_cut[1]},)
        command_rows = _read_rows(
            paths["journal"],
            "SELECT payload_json FROM risk_order_events "
            "WHERE scope_key=? AND principal_ref=? AND intent_id=? "
            "AND event_type='RESERVED'",
            (dim01._ACCOUNT_SPOT_A.key, dim01._PRINCIPAL_B, _INTENT),
        )
        reserved_payload = (
            json.loads(command_rows[0]["payload_json"])
            if len(command_rows) == 1
            else {}
        )
        assert type(reserved_payload.get("permit_id")) is str, command_rows
        assert _read_rows(
            paths["risk"],
            "SELECT permit_id FROM risk_dispatch_resolutions WHERE permit_id=?",
            (reserved_payload["permit_id"],),
        ) == ()

        # A fresh owner over the same SQLite files replays that proof during
        # startup. It dies after Risk commits the exact CLOSE receipt but
        # before the journal ACK is appended.
        host = start_host("risk-before-ack")
        risk_cut = host["startup_crash_event"]
        assert risk_cut[0] == "synthetic_close_risk_committed_before_ack", risk_cut
        assert risk_cut[1] == proof_cut[1], risk_cut
        assert type(risk_cut[2]) is str and len(risk_cut[2]) == 64, risk_cut
        assert risk_cut[3] and len(risk_cut[4]) == 64, risk_cut
        expected_receipt = tuple(risk_cut[1:])
        host = None
        persisted_risk = _read_rows(
            paths["risk"],
            "SELECT proof_kind,resolution_proof_sha256,exposure_proof_sha256 "
            "FROM risk_dispatch_resolutions WHERE resolution_proof_sha256=?",
            (proof_cut[1],),
        )
        assert persisted_risk == (
            {
                "proof_kind": "TERMINAL_CLOSE",
                "resolution_proof_sha256": proof_cut[1],
                "exposure_proof_sha256": risk_cut[2],
            },
        ), persisted_risk
        assert _read_rows(
            paths["journal"],
            "SELECT event_type FROM risk_order_events "
            "WHERE scope_key=? AND principal_ref=? AND intent_id=? AND event_type='ACKED_TRACKED'",
            (dim01._ACCOUNT_SPOT_A.key, dim01._PRINCIPAL_B, _INTENT),
        ) == ()

        # A third process repeats only the same Risk proof. The persisted
        # receipt is idempotently returned and the journal ACK is completed.
        host = start_host("normal-replay", expected_receipt)
        client = start_client()
        recovered = _runtime_result_with_host_diagnostic(
            client, host, "query", _INTENT
        )
        assert recovered["ok"] is True and recovered["code"] == "OK", recovered
        assert recovered["result"]["found"] is True, recovered
        record = recovered["result"]["record"]
        assert record["state"] == "FILLED", record
        assert record["filled_quantity"] == "0.75", record
        assert record["average_price"] == "20", record
        assert record["dispatch_attempts"] == 1, record
        assert record["provider_order_id"] == orders_before_recovery[0]["provider_order_id"], record
        replay = _receive_host_event(host)
        assert replay == ("synthetic_close_same_receipt_replayed", *expected_receipt), replay

        journal_events = _read_rows(
            paths["journal"],
            "SELECT event_type,payload_json FROM risk_order_events "
            "WHERE scope_key=? AND principal_ref=? AND intent_id=? ORDER BY revision",
            (dim01._ACCOUNT_SPOT_A.key, dim01._PRINCIPAL_B, _INTENT),
        )
        event_types = [row["event_type"] for row in journal_events]
        assert event_types.count("ORDER_OBSERVATION") == 1, event_types
        assert event_types.count("FILL_OBSERVATION") == 1, event_types
        assert event_types.count("UNKNOWN") == 1, event_types
        assert event_types.count("ACKED_TRACKED") == 1, event_types
        assert "PROVIDER_QUERY" not in event_types, event_types
        ack = json.loads(journal_events[-1]["payload_json"])
        assert ack["proof_sha256"] == proof_cut[1], ack
        assert ack["close_proof_sha256"] == risk_cut[2], ack
        assert ack["close_reference_id"] == risk_cut[3], ack
        assert ack["close_reference_sha256"] == risk_cut[4], ack
        assert ack["dispatch_attempt_count"] == 1, ack

        proof_rows = _read_rows(
            paths["journal"],
            "SELECT proof_sha256,source_order_revision,source_fill_revision "
            "FROM risk_order_terminal_close_proofs WHERE scope_key=? AND principal_ref=? AND intent_id=?",
            (dim01._ACCOUNT_SPOT_A.key, dim01._PRINCIPAL_B, _INTENT),
        )
        assert proof_rows == (
            {
                "proof_sha256": proof_cut[1],
                "source_order_revision": 5,
                "source_fill_revision": 6,
            },
        ), proof_rows
        close_rows = _read_rows(
            paths["risk"],
            "SELECT permit_id,strategy_id,position_effect,terminal_state,requested_quantity,filled_quantity "
            "FROM risk_close_order_resolutions WHERE permit_id=?",
            (record["permit_reference"],),
        )
        assert len(close_rows) == 1, close_rows
        assert close_rows[0]["strategy_id"] == dim01._STRATEGY_B, close_rows
        assert close_rows[0]["position_effect"] == "CLOSE", close_rows
        assert close_rows[0]["terminal_state"] == "FILLED", close_rows
        assert close_rows[0]["requested_quantity"] == close_rows[0]["filled_quantity"] == "0.75", close_rows
        resolution_rows = _read_rows(
            paths["risk"],
            "SELECT proof_kind,dispatch_attempt_count FROM risk_dispatch_resolutions WHERE permit_id=?",
            (record["permit_reference"],),
        )
        assert resolution_rows == ({"proof_kind": "TERMINAL_CLOSE", "dispatch_attempt_count": 1},)

        orders_after_recovery, counters_after_recovery = dim04._provider_evidence(
            paths["provider"]
        )
        assert orders_after_recovery == orders_before_recovery, orders_after_recovery
        assert counters_after_recovery == counters_before_recovery, counters_after_recovery
        assert dim01._client_command(host, "provider_write_count") == (
            "provider_write_count",
            1,
        )

        verification_journal = dim04.RiskOrderJournal(
            paths["journal"], writer_id=dim04._OWNER_JOURNAL_ID
        )
        verification_gate = DurableRiskGate(
            paths["risk"],
            dim04._risk_policy(verification_journal),
            execution_journal_authority=verification_journal,
            gross_exposure_authority=verification_journal,
        )
        try:
            receipt = verification_gate.dispatch_close_resolution_receipt(
                record["permit_reference"]
            )
            assert receipt.status.value == "TERMINAL_CLOSE_RESOLVED", receipt
            assert receipt.dispatch_latch_active is False, receipt
            assert receipt.proof_sha256 == proof_cut[1], receipt
            assert receipt.exposure_proof_sha256 == risk_cut[2], receipt
            assert dim04._snapshot_signature(verification_gate, dim01._STRATEGY_A) == before_a
            after_b = dim04._snapshot_signature(verification_gate, dim01._STRATEGY_B)
            assert after_b[2] == dim04._CLOSE_QUANTITY, after_b
            assert after_b[3] == before_b[3] - dim04._CLOSE_QUANTITY, after_b

            with verification_journal.writer_fence(dim01._ACCOUNT_SPOT_A) as verify_fence:
                replay_proof = verification_journal.load_terminal_close_resolution_proof(
                    verify_fence,
                    principal_ref=dim01._PRINCIPAL_B,
                    intent_id=_INTENT,
                )
            assert replay_proof is not None, "the source-bound CLOSE proof was not reloaded"
            other_digest = (
                ("0" if replay_proof.reconciliation_evidence_sha256[0] != "0" else "1")
                + replay_proof.reconciliation_evidence_sha256[1:]
            )
            different_proof = replace(
                replay_proof,
                reconciliation_evidence_sha256=other_digest,
            )
            assert different_proof.fingerprint != replay_proof.fingerprint
            before_rejected_replay = (
                _read_rows(
                    paths["risk"],
                    "SELECT proof_kind,resolution_proof_sha256,exposure_proof_sha256 "
                    "FROM risk_dispatch_resolutions WHERE permit_id=?",
                    (record["permit_reference"],),
                ),
                _read_rows(
                    paths["risk"],
                    "SELECT proof_sha256,close_proof_sha256,terminal_state,requested_quantity,filled_quantity "
                    "FROM risk_close_order_resolutions WHERE permit_id=?",
                    (record["permit_reference"],),
                ),
                _read_rows(
                    paths["risk"],
                    "SELECT active,blocking,reason FROM risk_freezes "
                    "WHERE scope_key=? AND cause_id=?",
                    (dim01._ACCOUNT_SPOT_A.key, receipt.claim.cause_id),
                ),
                dim04._snapshot_signature(verification_gate, dim01._STRATEGY_A),
                dim04._snapshot_signature(verification_gate, dim01._STRATEGY_B),
            )
            try:
                verification_gate.resolve_dispatch_close(different_proof)
            except Exception as error:
                assert getattr(error, "code", None) == "DISPATCH_PROOF_REPLAYED", error
            else:
                raise AssertionError("a different proof reused the committed CLOSE receipt")
            after_rejected_replay = (
                _read_rows(
                    paths["risk"],
                    "SELECT proof_kind,resolution_proof_sha256,exposure_proof_sha256 "
                    "FROM risk_dispatch_resolutions WHERE permit_id=?",
                    (record["permit_reference"],),
                ),
                _read_rows(
                    paths["risk"],
                    "SELECT proof_sha256,close_proof_sha256,terminal_state,requested_quantity,filled_quantity "
                    "FROM risk_close_order_resolutions WHERE permit_id=?",
                    (record["permit_reference"],),
                ),
                _read_rows(
                    paths["risk"],
                    "SELECT active,blocking,reason FROM risk_freezes "
                    "WHERE scope_key=? AND cause_id=?",
                    (dim01._ACCOUNT_SPOT_A.key, receipt.claim.cause_id),
                ),
                dim04._snapshot_signature(verification_gate, dim01._STRATEGY_A),
                dim04._snapshot_signature(verification_gate, dim01._STRATEGY_B),
            )
            assert after_rejected_replay == before_rejected_replay, (
                "a different CLOSE proof changed durable Risk or strategy state",
                before_rejected_replay,
                after_rejected_replay,
            )
            assert verification_gate.dispatch_close_resolution_receipt(
                record["permit_reference"]
            ) == receipt
        finally:
            verification_gate.close()

        assert _close_normally(client, expected_tag="closed") == ("closed", "closed")
        client = None
        assert _close_normally(host, expected_tag="closed") == ("closed", "closed")
        host = None
    finally:
        if client is not None:
            _bounded_reap_spawned_process(client, forced, failures)
        if host is not None:
            _bounded_reap_spawned_process(host, forced, failures)
        for record in clients:
            if not record["closed"]:
                _bounded_reap_spawned_process(record, forced, failures)
        for record in hosts:
            if not record["closed"]:
                _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
