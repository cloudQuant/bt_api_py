"""Strict account-facts registration lifecycle across a Gateway restart.

This is bounded local evidence only: two spawned synthetic CURVE Gateways,
one exact ``upstream.account`` facts registration, and no Risk host, strategy
orders, provider session, credentials, or account input. The test covers the
authenticated facts-registration lifecycle and exact predecessor unregister
ACK only; it does not read or ACK a fact page, prove cursor continuity, or
claim that the retained retirement tombstone was erased. The
old runtime sees one classified transport failure while Gateway is absent.
Its one strict close is delayed until the successor Gateway's owned ready
handshake, and an unobserved predecessor ACK keeps the successor in custody.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import queue
import sqlite3
import time
from collections.abc import Mapping
from contextlib import suppress

from bt_api_transport_zmq.service_v2 import (
    RequestNotSentError,
    RequestOutcomeUnknownError,
    ZmqServiceClient,
    ZmqServiceContextPool,
)

from tests.integration import _iter64_recovery_support as support
from tests.integration.test_service_account_instrument_metadata import _free_endpoint

_FACTS_OPERATIONS = frozenset({"account.facts.read_after.v2", "account.facts.ack"})
_TRACE_OPERATIONS = frozenset({"service.register", "service.renew", "service.unregister"})
# Keep the child trace's positional wire projection explicit. The Registry
# epoch is independent of the Gateway journal/owner generation in ready.
_TRACE_GENERATION = 0
_TRACE_OPERATION = 1
_TRACE_FACTS_REGISTRATION = 2
_TRACE_REGISTRATION_ID = 3
_TRACE_REPLY_SERVICE_EPOCH = 4
_TRACE_REPLY_OK = 5
_TRACE_REPLY_CODE = 6
_TRACE_DISCOVERED_EPOCH = 7
_TRACE_READY = 8
_TRACE_UNREGISTERED = 9
_TRACE_IDENTITY_MATCH = 10
_TRACE_SCOPE_MATCH = 11
_TRACE_REGISTRY_EPOCH = 12
_TRACE_ACTIVE_SESSION_COUNT = 13
_TRACE_REGISTRATION_ACTIVE = 14
_TRACE_ROW_LENGTH = 15
_DIAGNOSTIC_RUNTIMES: list[object] = []
_DIAGNOSTIC_GATEWAYS: list[object] = []
_DIAGNOSTIC_CHANNELS: list[object] = []
_DIAGNOSTIC_PHASES = frozenset({
    "test_begin",
    "gateway1_start_begin", "gateway1_start_error", "gateway1_ready",
    "old_runtime_start_begin", "old_runtime_start_error", "old_runtime_start_end",
    "gateway1_stop_begin", "gateway1_stop_error", "gateway1_stop_end",
    "renewal_wait_begin", "renewal_wait_end",
    "gateway2_start_begin", "gateway2_start_error", "gateway2_ready",
    "old_runtime_close_begin", "old_runtime_close_error", "old_runtime_close_end",
    "old_unregister_begin", "old_unregister_error", "old_unregister_end",
    "old_unregister_request_begin", "old_unregister_request_error",
    "old_unregister_request_reply",
    "replacement_unregister_request_begin",
    "replacement_unregister_request_error",
    "replacement_unregister_request_reply",
    "old_unregister_transport_close_begin", "old_unregister_transport_close_error",
    "old_unregister_transport_close_end",
    "old_unregister_context_release_begin", "old_unregister_context_release_error",
    "old_unregister_context_release_end",
    "old_unregister_context_term_begin", "old_unregister_context_term_error",
    "old_unregister_context_term_end",
    "old_runtime_transport_close_begin", "old_runtime_transport_close_error",
    "old_runtime_transport_close_end",
    "old_runtime_context_release_begin", "old_runtime_context_release_error",
    "old_runtime_context_release_end",
    "old_runtime_context_term_begin", "old_runtime_context_term_error",
    "old_runtime_context_term_end",
    "predecessor_ack_wait_begin", "predecessor_ack_wait_end",
    "replacement_runtime_start_begin", "replacement_runtime_start_error",
    "replacement_runtime_start_end",
    "replacement_runtime_close_begin", "replacement_runtime_close_error",
    "replacement_runtime_close_end",
    "replacement_unregister_begin", "replacement_unregister_error",
    "replacement_unregister_end",
    "replacement_unregister_transport_close_begin",
    "replacement_unregister_transport_close_error",
    "replacement_unregister_transport_close_end",
    "replacement_unregister_context_release_begin",
    "replacement_unregister_context_release_error",
    "replacement_unregister_context_release_end",
    "replacement_unregister_context_term_begin",
    "replacement_unregister_context_term_error",
    "replacement_unregister_context_term_end",
    "replacement_runtime_transport_close_begin",
    "replacement_runtime_transport_close_error",
    "replacement_runtime_transport_close_end",
    "replacement_runtime_context_release_begin",
    "replacement_runtime_context_release_error",
    "replacement_runtime_context_release_end",
    "replacement_runtime_context_term_begin",
    "replacement_runtime_context_term_error",
    "replacement_runtime_context_term_end",
    "gateway2_stop_begin", "gateway2_stop_error", "gateway2_stop_end",
    "final_cleanup_begin",
    "final_gateway_cleanup_begin", "final_gateway_cleanup_error",
    "final_gateway_cleanup_end",
    "channel_close_begin", "channel_close_end",
    "channel_join_begin", "channel_join_end",
    "test_complete",
})


def _need(condition, code: str) -> None:
    if condition is not True:
        raise AssertionError(code)


def _write_diagnostic_phase(path, phase: str) -> None:
    """Atomically persist one bounded phase enum for an external timeout audit."""
    _need(phase in _DIAGNOSTIC_PHASES, "diagnostic_phase_not_allowed")
    encoded = (json.dumps(
        {"schema_version": 1, "phase": phase},
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n").encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    descriptor = None
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
        os.replace(temporary, path)
    except OSError:
        # The diagnostic is observational. Losing it must not change cleanup,
        # turn an uncertain close into a retry, or mask the lifecycle result.
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        with suppress(OSError):
            os.remove(temporary)


def _gateway_main_with_registry_trace(
    root,
    endpoint,
    server_keys,
    publics,
    strategy_count,
    messages,
    controls,
    order_cut_enabled,
    trace_events,
    generation,
    trace_instance_id,
):
    """Trace only finite Risk control facts from this synthetic Gateway child."""
    from bt_api_py.service_registry import ServiceRegistry

    original_handle = ServiceRegistry.handle
    observed_instance_id = trace_instance_id

    def traced_handle(self, principal, request, *args, **kwargs):
        nonlocal observed_instance_id
        reply = original_handle(self, principal, request, *args, **kwargs)
        operation = request.header.operation
        if principal == support.RISK.principal_ref and operation in _TRACE_OPERATIONS:
            payload = request.payload
            operations = payload.get("operations")
            facts_registration = (
                type(operations) in (tuple, list)
                and len(operations) == len(_FACTS_OPERATIONS)
                and frozenset(operations) == _FACTS_OPERATIONS
            )
            body = reply if type(reply) is dict else {}
            result = body.get("result") if type(body.get("result")) is dict else {}
            registration_id = payload.get("registration_id")
            service_epoch = payload.get("service_epoch")
            candidate_instance_id = payload.get("instance_id")
            discovered_epoch = result.get("service_epoch")
            if type(registration_id) is not str:
                registration_id = result.get("registration_id")
            if type(service_epoch) is not str:
                service_epoch = result.get("service_epoch")
            if type(candidate_instance_id) is str and candidate_instance_id:
                observed_instance_id = candidate_instance_id
            active_session_count = None
            registration_active = None
            if type(observed_instance_id) is str and observed_instance_id:
                try:
                    active_registration_ids = self.active_registration_ids(
                        instance_id=observed_instance_id
                    )
                except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                    pass
                else:
                    active_session_count = len(active_registration_ids)
                    registration_active = registration_id in active_registration_ids
            registry_epoch = self._epoch if type(self._epoch) is str else None
            trace_events.put((
                generation,
                operation,
                facts_registration,
                registration_id if type(registration_id) is str else None,
                service_epoch if type(service_epoch) is str else None,
                body.get("ok") is True,
                body.get("code") if type(body.get("code")) is str else None,
                discovered_epoch if type(discovered_epoch) is str else None,
                result.get("ready") is True,
                result.get("registration_id") == registration_id
                and result.get("unregistered") is True,
                request.header.identity == support.RISK,
                request.header.scope == support.ACCOUNT_ONLY,
                registry_epoch,
                active_session_count,
                registration_active,
            ))
        return reply

    ServiceRegistry.handle = traced_handle
    try:
        support.gateway_main(
            root,
            endpoint,
            server_keys,
            publics,
            strategy_count,
            messages,
            controls,
            order_cut_enabled,
        )
    finally:
        ServiceRegistry.handle = original_handle


def _start_gateway(
    context, *, root, endpoint, keys, messages, controls, trace_events, generation,
    custody, trace_instance_id=None,
):
    process = context.Process(
        target=_gateway_main_with_registry_trace,
        args=(
            str(root),
            endpoint,
            keys["gateway_server"],
            support.gateway_publics(keys, 1),
            1,
            messages,
            controls,
            True,
            trace_events,
            generation,
            trace_instance_id,
        ),
    )
    owner = support.SyntheticGatewayStop(process, controls, messages)
    custody.append((process, owner, None))
    process.start()
    ready = support.next_message(messages, "ready", 40)
    _need(ready[1] == process.pid, "gateway_ready_pid_mismatch")
    owner.bind_ready(ready)
    custody[-1] = (process, owner, ready)
    return process, owner, ready


def _facts_runtime(config, endpoint, keys):
    from bt_api_py.risk_service_config import load_risk_service_config
    from bt_api_py.risk_service_host import _gateway_client_config
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_runtime import (
        ClientCurveKeys,
        ServiceClientRuntime,
        ZmqTransportFactory,
    )

    risk_config_path = support.risk_files(
        config,
        keys,
        1,
        endpoint,
        _free_endpoint(),
        parent_contract_enabled=True,
        execution_enabled=True,
        order_cut_enabled=True,
    )
    risk_config = load_risk_service_config(risk_config_path)
    client_config = _gateway_client_config(risk_config, "upstream.account")
    raw = client_config.to_public_dict()
    raw["clients"]["gateway"]["settings"]["options"] = {
        "request_timeout_ms": 300,
        "heartbeat_interval_seconds": 1,
    }
    raw["communication"]["profiles"]["gateway-client"]["options"] = {
        "request_timeout_ms": 300,
    }
    client_config = parse_service_config(raw)
    security_ref = raw["communication"]["profiles"]["gateway-client"]["security_profile_ref"]
    factory = ZmqTransportFactory(
        {
            security_ref: ClientCurveKeys(
                keys["gateway_server"][0],
                keys["risk_delegate"][0],
                keys["risk_delegate"][1],
            )
        },
        strict_single_connection=True,
    )
    return ServiceClientRuntime(
        client_config,
        identity=support.RISK,
        scope_resolver=lambda scope_ref: support.ACCOUNT_ONLY if scope_ref == "scope-a" else None,
        peer_resolver=lambda service_id: support.GATEWAY
        if service_id == support.GATEWAY.actor_ref else None,
        transport_factory=factory,
        requested_operations={"upstream.account": _FACTS_OPERATIONS},
        require_unregister_ack=True,
    )


def _install_close_phase_trace(monkeypatch, phase_path, role_for_runtime):
    """Trace bounded unregister/transport/context phases without raw identities."""
    from bt_api_py.service_runtime import ServiceClientRuntime

    original_unregister = ServiceClientRuntime._unregister
    original_rpc = ServiceClientRuntime._rpc
    original_client_close = ZmqServiceClient.close
    original_context_release = ZmqServiceContextPool._release
    original_context_terminate = ZmqServiceContextPool._terminate_context_locked
    state = {"runtime_close": None, "unregister": None, "client_close": None}
    unregister_rpc_events = []

    def traced_unregister(runtime, *args, **kwargs):
        role = role_for_runtime(runtime)
        if role not in {"old", "replacement"}:
            return original_unregister(runtime, *args, **kwargs)
        previous = state["unregister"]
        state["unregister"] = role
        _write_diagnostic_phase(phase_path, f"{role}_unregister_begin")
        try:
            result = original_unregister(runtime, *args, **kwargs)
        except BaseException:
            _write_diagnostic_phase(phase_path, f"{role}_unregister_error")
            raise
        else:
            _write_diagnostic_phase(phase_path, f"{role}_unregister_end")
            return result
        finally:
            state["unregister"] = previous

    def traced_rpc(runtime, *args, **kwargs):
        operation = args[3] if len(args) > 3 else kwargs.get("operation")
        role = state["unregister"]
        if role not in {"old", "replacement"} or operation != "service.unregister":
            return original_rpc(runtime, *args, **kwargs)
        prefix = f"{role}_unregister_request"
        index = len(unregister_rpc_events)
        unregister_rpc_events.append((role, "started", False))
        _write_diagnostic_phase(phase_path, prefix + "_begin")
        try:
            result = original_rpc(runtime, *args, **kwargs)
        except BaseException:
            unregister_rpc_events[index] = (role, "error", False)
            _write_diagnostic_phase(phase_path, prefix + "_error")
            raise
        else:
            payload = args[4] if len(args) > 4 else kwargs.get("payload")
            exact_ack = (
                isinstance(payload, Mapping)
                and isinstance(result, Mapping)
                and result.get("registration_id") == payload.get("registration_id")
                and result.get("unregistered") is True
            )
            unregister_rpc_events[index] = (role, "reply", exact_ack)
            _write_diagnostic_phase(phase_path, prefix + "_reply")
            return result

    def traced_client_close(client):
        role = state["unregister"] or state["runtime_close"]
        if role not in {"old", "replacement"}:
            return original_client_close(client)
        kind = "unregister" if state["unregister"] is not None else "runtime"
        prefix = f"{role}_{kind}"
        previous = state["client_close"]
        state["client_close"] = prefix
        _write_diagnostic_phase(phase_path, f"{prefix}_transport_close_begin")
        try:
            result = original_client_close(client)
        except BaseException:
            _write_diagnostic_phase(phase_path, f"{prefix}_transport_close_error")
            raise
        else:
            _write_diagnostic_phase(phase_path, f"{prefix}_transport_close_end")
            return result
        finally:
            state["client_close"] = previous

    def traced_context_release(pool, lease):
        prefix = state["client_close"]
        if prefix is None:
            return original_context_release(pool, lease)
        _write_diagnostic_phase(phase_path, f"{prefix}_context_release_begin")
        try:
            result = original_context_release(pool, lease)
        except BaseException:
            _write_diagnostic_phase(phase_path, f"{prefix}_context_release_error")
            raise
        else:
            _write_diagnostic_phase(phase_path, f"{prefix}_context_release_end")
            return result

    def traced_context_terminate(pool, context, generation):
        prefix = state["client_close"]
        if prefix is None:
            return original_context_terminate(pool, context, generation)
        _write_diagnostic_phase(phase_path, f"{prefix}_context_term_begin")
        try:
            result = original_context_terminate(pool, context, generation)
        except BaseException:
            _write_diagnostic_phase(phase_path, f"{prefix}_context_term_error")
            raise
        else:
            _write_diagnostic_phase(phase_path, f"{prefix}_context_term_end")
            return result

    monkeypatch.setattr(ServiceClientRuntime, "_unregister", traced_unregister)
    monkeypatch.setattr(ServiceClientRuntime, "_rpc", traced_rpc)
    monkeypatch.setattr(ZmqServiceClient, "close", traced_client_close)
    monkeypatch.setattr(ZmqServiceContextPool, "_release", traced_context_release)
    monkeypatch.setattr(
        ZmqServiceContextPool, "_terminate_context_locked", traced_context_terminate
    )

    def close_runtime(runtime, role: str) -> None:
        _need(role in {"old", "replacement"}, "diagnostic_runtime_role_invalid")
        _need(role_for_runtime(runtime) == role, "diagnostic_runtime_role_mismatch")
        previous = state["runtime_close"]
        state["runtime_close"] = role
        _write_diagnostic_phase(phase_path, f"{role}_runtime_close_begin")
        try:
            runtime.close()
        except BaseException:
            _write_diagnostic_phase(phase_path, f"{role}_runtime_close_error")
            raise
        else:
            _write_diagnostic_phase(phase_path, f"{role}_runtime_close_end")
        finally:
            state["runtime_close"] = previous

    return close_runtime, unregister_rpc_events


def _trace_request_outcomes(monkeypatch, old_registration_id):
    """Record bounded outage outcomes without weakening later transport checks."""
    original_request = ZmqServiceClient.request
    observed = []

    def traced_request(client, request, *args, **kwargs):
        operation = request.header.operation
        payload = request.payload
        if (request.header.identity == support.RISK
                and operation in {"service.renew", "service.unregister"}
                and payload.get("registration_id") == old_registration_id):
            index = len(observed)
            observed.append((operation, "started", False))
            try:
                reply = original_request(client, request, *args, **kwargs)
            except BaseException as error:
                if type(error) is RequestNotSentError:
                    kind = "request_not_sent"
                elif type(error) is RequestOutcomeUnknownError:
                    kind = "outcome_unknown"
                else:
                    kind = "unexpected"
                cause_code = getattr(error, "cause_code", None)
                if type(cause_code) is not str or not cause_code:
                    cause_code = None
                observed[index] = (
                    operation,
                    "transport_error",
                    kind,
                    type(error).__name__,
                    cause_code,
                )
                raise
            body = reply.payload if type(reply.payload) is dict else {}
            result = body.get("result") if type(body.get("result")) is dict else {}
            exact_ack = (
                body.get("ok") is True
                and body.get("code") == "OK"
                and result.get("registration_id") == old_registration_id
                and result.get("unregistered") is True
            )
            observed[index] = (operation, "reply", exact_ack)
            return reply
        return original_request(client, request, *args, **kwargs)

    monkeypatch.setattr(ZmqServiceClient, "request", traced_request)

    def restore_request_trace():
        _need(ZmqServiceClient.request is traced_request,
              "outage_request_trace_binding_changed")
        monkeypatch.setattr(ZmqServiceClient, "request", original_request)

    return observed, restore_request_trace


def _drain_trace(trace_events, rows):
    while True:
        try:
            rows.append(trace_events.get_nowait())
        except queue.Empty:
            return rows


def _await_trace(trace_events, rows, predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _drain_trace(trace_events, rows)
        if predicate(rows):
            return True
        time.sleep(0.02)
    _drain_trace(trace_events, rows)
    return predicate(rows)


def _stop_gateway(process, owner) -> None:
    if not owner._closed:
        if owner._attempted:
            raise RuntimeError("gateway_close_attempt_already_uncertain")
        owner.close()
    _need(process.is_alive() is False and process.exitcode == 0, "gateway_exit_not_clean")
    process.close()


def _retain_gateway(gateway) -> None:
    _process, owner, _ready = gateway
    if not any(item[1] is owner for item in _DIAGNOSTIC_GATEWAYS):
        _DIAGNOSTIC_GATEWAYS.append(gateway)


def _retain_gateway_channels(channels) -> None:
    for channel in channels:
        if not any(item is channel for item in _DIAGNOSTIC_CHANNELS):
            _DIAGNOSTIC_CHANNELS.append(channel)


def _dispose_local_runtime(runtime, close_attempted: bool, close_call=None) -> None:
    if runtime is None or close_attempted:
        return
    try:
        if close_call is None:
            runtime.close()
        else:
            close_call(runtime)
    except BaseException:
        # This is a raw local client with no sticky callbacks, not a retained
        # Risk host. Preserve its finite evidence and never retry close.
        _retain_runtime(runtime)


def _retain_runtime(runtime) -> None:
    if runtime is not None and not any(item is runtime for item in _DIAGNOSTIC_RUNTIMES):
        _DIAGNOSTIC_RUNTIMES.append(runtime)


def _runtime_cleanup_confirmed(runtime) -> bool:
    return (
        runtime is not None
        and runtime.state == "closed"
        and runtime.unregister_uncertainties == 0
        and runtime.cleanup_failures == 0
        and runtime.unknown_registration_count == 0
        and runtime.open_transport_count == 0
        and not runtime.bindings
    )


def _runtime_cleanup_projection(runtime):
    return {
        "state": runtime.state,
        "sticky_cleanup_enabled": runtime.sticky_cleanup_enabled,
        "binding_count": len(runtime.bindings),
        "open_transport_count": runtime.open_transport_count,
        "unregister_uncertainties": runtime.unregister_uncertainties,
        "unknown_registration_count": runtime.unknown_registration_count,
        "cleanup_failures": runtime.cleanup_failures,
    }


def _predecessor_server_ack(
    row, registration_id, service_epoch, successor_generation,
    successor_service_epoch=None,
) -> bool:
    registry_epoch = (
        row[_TRACE_REGISTRY_EPOCH]
        if type(row) is tuple and len(row) == _TRACE_ROW_LENGTH
        else None
    )
    return (
        type(row) is tuple
        and len(row) == _TRACE_ROW_LENGTH
        and row[_TRACE_GENERATION] == successor_generation
        and row[_TRACE_OPERATION] == "service.unregister"
        and row[_TRACE_REGISTRATION_ID] == registration_id
        and row[_TRACE_REPLY_SERVICE_EPOCH] == service_epoch
        and row[_TRACE_REPLY_OK] is True
        and row[_TRACE_REPLY_CODE] == "OK"
        and row[_TRACE_UNREGISTERED] is True
        and row[_TRACE_IDENTITY_MATCH] is True
        and row[_TRACE_SCOPE_MATCH] is True
        and type(registry_epoch) is str
        and bool(registry_epoch)
        and registry_epoch != service_epoch
        and (
            successor_service_epoch is None
            or registry_epoch == successor_service_epoch
        )
        and row[_TRACE_ACTIVE_SESSION_COUNT] == 0
        and row[_TRACE_REGISTRATION_ACTIVE] is False
    )


def _registration_trace_diagnostic(rows, generation, binding):
    raw_rows = [
        row for row in rows
        if type(row) is tuple
        and len(row) >= 2
        and row[_TRACE_GENERATION] == generation
        and row[_TRACE_OPERATION] == "service.register"
    ]
    valid_rows = [row for row in raw_rows if len(row) == _TRACE_ROW_LENGTH]
    matching_rows = [
        row for row in valid_rows
        if row[_TRACE_REGISTRATION_ID] == binding.registration_id
    ]
    evidence_rows = matching_rows or valid_rows
    return {
        "register_trace_seen": bool(raw_rows),
        "trace_row_shape_valid": bool(valid_rows) and len(valid_rows) == len(raw_rows),
        "registration_id_matches_binding": bool(matching_rows),
        "facts_operations_exact": any(
            row[_TRACE_FACTS_REGISTRATION] is True for row in evidence_rows
        ),
        "reply_ok": any(row[_TRACE_REPLY_OK] is True for row in evidence_rows),
        "reply_epoch_matches_binding": any(
            row[_TRACE_REPLY_SERVICE_EPOCH] == binding.service_epoch
            for row in evidence_rows
        ),
        "registry_epoch_matches_binding": any(
            row[_TRACE_REGISTRY_EPOCH] == binding.service_epoch
            for row in evidence_rows
        ),
        "identity_matches": any(
            row[_TRACE_IDENTITY_MATCH] is True for row in evidence_rows
        ),
        "scope_matches": any(
            row[_TRACE_SCOPE_MATCH] is True for row in evidence_rows
        ),
        "active_session_count_is_one": any(
            row[_TRACE_ACTIVE_SESSION_COUNT] == 1 for row in evidence_rows
        ),
        "registration_is_active": any(
            row[_TRACE_REGISTRATION_ACTIVE] is True for row in evidence_rows
        ),
    }


def _exact_predecessor_client_ack(row) -> bool:
    return (
        type(row) is tuple
        and len(row) == 3
        and row[0] == "old"
        and row[1] == "reply"
        and row[2] is True
    )


def test_strict_account_facts_registration_lifecycle_retires_after_gateway_ready(
    tmp_path, monkeypatch
):
    """A due facts renew fails in the outage; exact cleanup ACK follows restart."""
    phase_path = tmp_path / "account-facts-lifecycle-phase.json"
    _write_diagnostic_phase(phase_path, "test_begin")
    context = multiprocessing.get_context("spawn")
    gateway_root = tmp_path / "gateway"
    gateway_root.mkdir()
    support._restrict_fixture_path(gateway_root, mode=0o700)
    risk_config_root = tmp_path / "risk-config"
    risk_config_root.mkdir()
    keys = support.keypairs(1, execution_enabled=True)
    endpoint = _free_endpoint()
    messages, controls = context.Queue(), context.Queue()
    trace_events = context.Queue()
    gateways = []
    active_gateway = None
    active_gateway_ready = False
    successor_gateway_ready = False
    predecessor_ack_observed = False
    preserve_channels = False
    old_runtime = replacement_runtime = None
    old_binding = None
    successor_service_epoch = None
    request_events = []
    successor_events = []
    old_close_attempted = replacement_close_attempted = False

    def runtime_role(runtime):
        if runtime is old_runtime:
            return "old"
        if runtime is replacement_runtime:
            return "replacement"
        return None

    close_runtime, unregister_rpc_events = _install_close_phase_trace(
        monkeypatch, phase_path, runtime_role
    )
    try:
        _write_diagnostic_phase(phase_path, "gateway1_start_begin")
        try:
            first = _start_gateway(
                context,
                root=gateway_root,
                endpoint=endpoint,
                keys=keys,
                messages=messages,
                controls=controls,
                trace_events=trace_events,
                generation=1,
                custody=gateways,
            )
        except BaseException:
            _write_diagnostic_phase(phase_path, "gateway1_start_error")
            raise
        _write_diagnostic_phase(phase_path, "gateway1_ready")
        active_gateway = first
        active_gateway_ready = True
        first_ready = first[2]
        old_runtime = _facts_runtime(risk_config_root, endpoint, keys)
        _need(old_runtime.sticky_cleanup_enabled is False,
              "old_runtime_sticky_cleanup_callbacks_enabled")
        _write_diagnostic_phase(phase_path, "old_runtime_start_begin")
        try:
            old_runtime.start()
        except BaseException:
            _write_diagnostic_phase(phase_path, "old_runtime_start_error")
            raise
        _write_diagnostic_phase(phase_path, "old_runtime_start_end")
        (old_binding,) = old_runtime.bindings
        _need(old_binding.mount == "upstream.account", "facts_mount_mismatch")
        _need(old_binding.operations == _FACTS_OPERATIONS, "facts_operations_mismatch")
        _need(type(old_binding.registration_id) is str and bool(old_binding.registration_id),
              "old_facts_registration_missing")
        _need(type(old_binding.service_epoch) is str and bool(old_binding.service_epoch),
              "old_facts_epoch_missing")
        _need(old_binding.heartbeat_interval_seconds == 1.0, "heartbeat_not_shortened")
        first_events = []
        old_registration_observed = _await_trace(
            trace_events,
            first_events,
            lambda rows: any(
                type(row) is tuple
                and len(row) == _TRACE_ROW_LENGTH
                and row[_TRACE_GENERATION] == 1
                and row[_TRACE_OPERATION] == "service.register"
                and row[_TRACE_FACTS_REGISTRATION] is True
                and row[_TRACE_REGISTRATION_ID] == old_binding.registration_id
                and row[_TRACE_REPLY_SERVICE_EPOCH] == old_binding.service_epoch
                and row[_TRACE_REPLY_OK] is True
                and row[_TRACE_IDENTITY_MATCH] is True
                and row[_TRACE_SCOPE_MATCH] is True
                and row[_TRACE_REGISTRY_EPOCH] == old_binding.service_epoch
                and row[_TRACE_ACTIVE_SESSION_COUNT] == 1
                and row[_TRACE_REGISTRATION_ACTIVE] is True
                for row in rows
            ),
        )
        if not old_registration_observed:
            print(
                "ITER72_GATEWAY_REGISTRATION_TRACE_DIAGNOSTIC",
                _registration_trace_diagnostic(first_events, 1, old_binding),
            )
        _need(old_registration_observed, "old_facts_registration_not_observed")

        request_events, restore_request_trace = _trace_request_outcomes(
            monkeypatch, old_binding.registration_id
        )
        _write_diagnostic_phase(phase_path, "gateway1_stop_begin")
        try:
            _stop_gateway(first[0], first[1])
        except BaseException:
            _write_diagnostic_phase(phase_path, "gateway1_stop_error")
            raise
        _write_diagnostic_phase(phase_path, "gateway1_stop_end")
        active_gateway = None
        active_gateway_ready = False
        successor_events = []

        try:
            _write_diagnostic_phase(phase_path, "renewal_wait_begin")
            deadline = old_binding.renewal_due_at + 2.0
            while time.monotonic() < deadline and not any(
                row[0] == "service.renew" and row[1] == "transport_error"
                for row in request_events
            ):
                with suppress(Exception):
                    old_runtime.maintain()
                if not any(
                    row[0] == "service.renew" and row[1] == "transport_error"
                    for row in request_events
                ):
                    time.sleep(0.02)
            renew_events = [row for row in request_events if row[0] == "service.renew"]
            _need(len(renew_events) == 1, "one_due_renew_not_observed")
            _need(renew_events[0][1] == "transport_error", "renew_did_not_fail_in_outage")
            renew_kind, renew_error_type, renew_cause = renew_events[0][2:5]
            _need(
                renew_kind in {"request_not_sent", "outcome_unknown"},
                "renew_transport_error_type_unexpected_" + renew_error_type,
            )
            expected_error_type = {
                "request_not_sent": RequestNotSentError.__name__,
                "outcome_unknown": RequestOutcomeUnknownError.__name__,
            }[renew_kind]
            _need(renew_error_type == expected_error_type,
                  "renew_transport_error_class_mismatch")
            allowed_causes = {
                "request_not_sent": {"send_timeout", "send_not_queued"},
                "outcome_unknown": {"response_timeout", "receive_transport_error"},
            }
            _need(renew_cause in allowed_causes[renew_kind],
                  "renew_transport_cause_not_gateway_outage")
            expected_state = "failed" if renew_kind == "request_not_sent" else "registered"
            _need(old_runtime.state == expected_state,
                  "old_runtime_renewal_failure_phase_mismatch")
            _need(not any(row[0] == "service.unregister" for row in request_events),
                  "unregister_attempted_while_gateway_down")
            retained_bindings = old_runtime.bindings
            _need(len(retained_bindings) == 1, "old_facts_binding_not_retained_after_renew")
            _need(retained_bindings[0].registration_id == old_binding.registration_id,
                  "old_facts_registration_identity_lost_after_renew")
            _need(retained_bindings[0].service_epoch == old_binding.service_epoch,
                  "old_facts_epoch_lost_after_renew")
            _need(old_runtime.open_transport_count == 1,
                  "old_facts_transport_custody_lost_after_renew")
            _need(old_runtime.unregister_uncertainties == 0,
                  "old_unregister_uncertainty_before_close")
            _need(old_runtime.unknown_registration_count == 0,
                  "old_unknown_registration_before_close")
            _need(old_runtime.cleanup_failures == 0,
                  "old_cleanup_failure_before_close")
            renewal_evidence = {
                "category": renew_kind,
                "type": renew_error_type,
                "cause": renew_cause,
                "phase": "after_due_renewal_before_close",
                "runtime_state": old_runtime.state,
                "binding_retained": len(retained_bindings) == 1,
                "transport_count": old_runtime.open_transport_count,
                "unregister_uncertainties": old_runtime.unregister_uncertainties,
                "unknown_registrations": old_runtime.unknown_registration_count,
                "cleanup_failures": old_runtime.cleanup_failures,
            }
            # Strict registration intentionally rejects a monkeypatched client
            # request method. The outage observer has completed its only job,
            # so restore the trusted method before constructing the replacement.
            restore_request_trace()
            _write_diagnostic_phase(phase_path, "renewal_wait_end")

            _write_diagnostic_phase(phase_path, "gateway2_start_begin")
            try:
                second = _start_gateway(
                    context,
                    root=gateway_root,
                    endpoint=endpoint,
                    keys=keys,
                    messages=messages,
                    controls=controls,
                    trace_events=trace_events,
                    generation=2,
                    custody=gateways,
                    trace_instance_id=old_binding.instance_id,
                )
            except BaseException:
                _write_diagnostic_phase(phase_path, "gateway2_start_error")
                raise
            _write_diagnostic_phase(phase_path, "gateway2_ready")
            active_gateway = second
            active_gateway_ready = True
            successor_gateway_ready = True
            second_ready = second[2]
            _need(second_ready[2] == first_ready[2], "gateway_journal_not_preserved")
            _need(second_ready[3] != first_ready[3], "gateway_generation_not_advanced")

            # The owned process ready handshake is the separate readiness
            # observation. Only now is the original strict close called once.
            old_close_attempted = True
            try:
                close_runtime(old_runtime, "old")
            except BaseException:
                if not _runtime_cleanup_confirmed(old_runtime):
                    _retain_runtime(old_runtime)
                raise
            if not _runtime_cleanup_confirmed(old_runtime):
                _retain_runtime(old_runtime)
            _need(old_runtime.state == "closed", "old_facts_runtime_not_closed")
            _need(old_runtime.unregister_uncertainties == 0, "old_unregister_uncertain")
            _need(old_runtime.cleanup_failures == 0, "old_cleanup_failed")
            _need(old_runtime.unknown_registration_count == 0, "old_registration_unknown")
            _need(old_runtime.open_transport_count == 0, "old_transport_still_open")
            _need(not old_runtime.bindings, "old_facts_binding_still_retained")
            _write_diagnostic_phase(phase_path, "predecessor_ack_wait_begin")
            old_wire_ack = _await_trace(
                trace_events,
                successor_events,
                lambda rows: any(
                    _predecessor_server_ack(
                        row, old_binding.registration_id,
                        old_binding.service_epoch, successor_generation=2,
                    )
                    for row in rows
                ),
            )
            _write_diagnostic_phase(phase_path, "predecessor_ack_wait_end")
            _need(old_wire_ack, "successor_did_not_ack_exact_old_facts_registration")
            old_request_ack = [
                row for row in unregister_rpc_events
                if _exact_predecessor_client_ack(row)
            ]
            _need(len(old_request_ack) == 1 and old_request_ack[0][2] is True,
                  "old_runtime_did_not_observe_exact_unregister_ack")
            server_ack_rows = [
                row for row in successor_events
                if _predecessor_server_ack(
                    row, old_binding.registration_id, old_binding.service_epoch,
                    successor_generation=2,
                )
            ]
            _need(len(server_ack_rows) == 1, "predecessor_unregister_ack_not_unique")
            predecessor_ack_observed = True

            replacement_runtime = _facts_runtime(risk_config_root, endpoint, keys)
            _need(replacement_runtime.sticky_cleanup_enabled is False,
                  "replacement_runtime_sticky_cleanup_callbacks_enabled")
            _write_diagnostic_phase(phase_path, "replacement_runtime_start_begin")
            try:
                replacement_runtime.start()
            except BaseException:
                _write_diagnostic_phase(phase_path, "replacement_runtime_start_error")
                raise
            _write_diagnostic_phase(phase_path, "replacement_runtime_start_end")
            (replacement_binding,) = replacement_runtime.bindings
            current_epoch = replacement_binding.service_epoch
            successor_service_epoch = current_epoch
            _need(replacement_binding.mount == "upstream.account", "replacement_mount_mismatch")
            _need(replacement_binding.operations == _FACTS_OPERATIONS,
                  "replacement_operations_mismatch")
            _need(replacement_binding.registration_id != old_binding.registration_id,
                  "replacement_reused_registration_id")
            _need(current_epoch != old_binding.service_epoch, "replacement_epoch_not_current")
            facts_registered = _await_trace(
                trace_events,
                successor_events,
                lambda rows: any(
                    type(row) is tuple
                    and len(row) == _TRACE_ROW_LENGTH
                    and row[_TRACE_GENERATION] == 2
                    and row[_TRACE_OPERATION] == "service.register"
                    and row[_TRACE_FACTS_REGISTRATION] is True
                    and row[_TRACE_REGISTRATION_ID] == replacement_binding.registration_id
                    and row[_TRACE_REPLY_SERVICE_EPOCH] == current_epoch
                    and row[_TRACE_REPLY_OK] is True
                    and row[_TRACE_IDENTITY_MATCH] is True
                    and row[_TRACE_SCOPE_MATCH] is True
                    and row[_TRACE_REGISTRY_EPOCH] == current_epoch
                    and row[_TRACE_ACTIVE_SESSION_COUNT] == 1
                    and row[_TRACE_REGISTRATION_ACTIVE] is True
                    for row in rows
                ),
            )
            if not facts_registered:
                print(
                    "ITER72_GATEWAY_REGISTRATION_TRACE_DIAGNOSTIC",
                    _registration_trace_diagnostic(
                        successor_events, 2, replacement_binding
                    ),
                )
            _need(facts_registered, "replacement_facts_registration_not_observed")
            server_ack_rows = [
                row for row in server_ack_rows
                if _predecessor_server_ack(
                    row, old_binding.registration_id, old_binding.service_epoch,
                    successor_generation=2,
                    successor_service_epoch=current_epoch,
                )
            ]
            _need(len(server_ack_rows) == 1,
                  "predecessor_ack_epoch_not_bound_to_replacement_registration")
            old_ack_index = next(
                index for index, row in enumerate(successor_events)
                if _predecessor_server_ack(
                    row, old_binding.registration_id, old_binding.service_epoch,
                    successor_generation=2,
                    successor_service_epoch=current_epoch,
                )
            )
            replacement_index = next(
                index for index, row in enumerate(successor_events)
                if type(row) is tuple and len(row) == _TRACE_ROW_LENGTH
                and row[_TRACE_GENERATION] == 2
                and row[_TRACE_OPERATION] == "service.register"
                and row[_TRACE_FACTS_REGISTRATION] is True
                and row[_TRACE_REGISTRATION_ID] == replacement_binding.registration_id
                and row[_TRACE_REPLY_SERVICE_EPOCH] == current_epoch
                and row[_TRACE_IDENTITY_MATCH] is True
                and row[_TRACE_SCOPE_MATCH] is True
                and row[_TRACE_REGISTRY_EPOCH] == current_epoch
                and row[_TRACE_ACTIVE_SESSION_COUNT] == 1
                and row[_TRACE_REGISTRATION_ACTIVE] is True
            )
            _need(old_ack_index < replacement_index,
                  "replacement_preceded_old_cleanup_ack")

            replacement_close_attempted = True
            try:
                close_runtime(replacement_runtime, "replacement")
            except BaseException:
                if not _runtime_cleanup_confirmed(replacement_runtime):
                    _retain_runtime(replacement_runtime)
                raise
            if not _runtime_cleanup_confirmed(replacement_runtime):
                _retain_runtime(replacement_runtime)
            _need(_runtime_cleanup_confirmed(replacement_runtime),
                  "replacement_runtime_cleanup_not_confirmed")
            _need(replacement_runtime.cleanup_failures == 0,
                  "replacement_runtime_cleanup_failed")
            _need(replacement_runtime.unregister_uncertainties == 0,
                  "replacement_unregister_uncertain")
            _need(replacement_runtime.unknown_registration_count == 0,
                  "replacement_registration_unknown")
            _need(replacement_runtime.open_transport_count == 0,
                  "replacement_transport_still_open")
            _need(not replacement_runtime.bindings,
                  "replacement_bindings_still_retained")

            print("ITER72_GATEWAY_FACTS_REGISTRATION_LIFECYCLE", {
                "renew_transport": renewal_evidence,
                "predecessor_ack": {
                    "server_ack_count": len(server_ack_rows),
                    "client_ack_count": len(old_request_ack),
                    "successor_registry_epoch_matches_replacement": (
                        server_ack_rows[0][_TRACE_REGISTRY_EPOCH] == current_epoch
                    ),
                    "old_registration_active": server_ack_rows[0][
                        _TRACE_REGISTRATION_ACTIVE
                    ],
                    "active_session_count_after_ack": server_ack_rows[0][
                        _TRACE_ACTIVE_SESSION_COUNT
                    ],
                },
                "replacement_registration": {
                    "registered_after_ack": old_ack_index < replacement_index,
                    "service_epoch_matches_successor_registry": (
                        replacement_binding.service_epoch
                        == successor_events[replacement_index][_TRACE_REGISTRY_EPOCH]
                    ),
                    "registration_active": successor_events[replacement_index][
                        _TRACE_REGISTRATION_ACTIVE
                    ],
                    "active_session_count": successor_events[replacement_index][
                        _TRACE_ACTIVE_SESSION_COUNT
                    ],
                },
                "old_runtime_cleanup": _runtime_cleanup_projection(old_runtime),
                "replacement_runtime_cleanup": _runtime_cleanup_projection(replacement_runtime),
            })
        finally:
            if old_runtime is not None and not old_close_attempted:
                if active_gateway_ready:
                    old_close_attempted = True
                    _dispose_local_runtime(
                        old_runtime,
                        False,
                        lambda runtime: close_runtime(runtime, "old"),
                    )
                    if not _runtime_cleanup_confirmed(old_runtime):
                        _retain_runtime(old_runtime)
                    if successor_gateway_ready and old_binding is not None:
                        _await_trace(
                            trace_events,
                            successor_events,
                            lambda rows: any(
                                _predecessor_server_ack(
                                    row, old_binding.registration_id,
                                    old_binding.service_epoch, successor_generation=2,
                                    successor_service_epoch=successor_service_epoch,
                                )
                                for row in rows
                            ),
                        )
                        server_acks = [
                            row for row in successor_events
                            if _predecessor_server_ack(
                                row, old_binding.registration_id, old_binding.service_epoch,
                                successor_generation=2,
                                successor_service_epoch=successor_service_epoch,
                            )
                        ]
                        client_acks = [
                            row for row in unregister_rpc_events
                            if _exact_predecessor_client_ack(row)
                        ]
                        predecessor_ack_observed = (
                            len(server_acks) == 1 and len(client_acks) == 1
                        )
                        if not predecessor_ack_observed:
                            _retain_runtime(old_runtime)
                else:
                    # An outage with no successor readiness is not a safe
                    # point to make an unregister attempt. Keep the raw client
                    # attributable and do not manufacture a cleanup result.
                    _retain_runtime(old_runtime)
            if replacement_runtime is not None and not replacement_close_attempted:
                if active_gateway_ready:
                    replacement_close_attempted = True
                    _dispose_local_runtime(
                        replacement_runtime,
                        False,
                        lambda runtime: close_runtime(runtime, "replacement"),
                    )
                    if not _runtime_cleanup_confirmed(replacement_runtime):
                        _retain_runtime(replacement_runtime)
                else:
                    _retain_runtime(replacement_runtime)

        _need(predecessor_ack_observed,
              "successor_gateway_close_blocked_without_predecessor_ack")
        _write_diagnostic_phase(phase_path, "gateway2_stop_begin")
        try:
            _stop_gateway(second[0], second[1])
        except BaseException:
            _write_diagnostic_phase(phase_path, "gateway2_stop_error")
            raise
        _write_diagnostic_phase(phase_path, "gateway2_stop_end")
        active_gateway = None
        active_gateway_ready = False
    finally:
        _write_diagnostic_phase(phase_path, "final_cleanup_begin")
        for runtime in (old_runtime, replacement_runtime):
            if runtime is not None and not _runtime_cleanup_confirmed(runtime):
                _retain_runtime(runtime)
        if (successor_gateway_ready and not predecessor_ack_observed
                and old_binding is not None):
            _await_trace(
                trace_events,
                successor_events,
                lambda rows: any(
                    _predecessor_server_ack(
                        row, old_binding.registration_id, old_binding.service_epoch,
                        successor_generation=2,
                        successor_service_epoch=successor_service_epoch,
                    )
                    for row in rows
                ),
            )
            server_acks = [
                row for row in successor_events
                if _predecessor_server_ack(
                    row, old_binding.registration_id, old_binding.service_epoch,
                    successor_generation=2,
                    successor_service_epoch=successor_service_epoch,
                )
            ]
            client_acks = [
                row for row in unregister_rpc_events
                if _exact_predecessor_client_ack(row)
            ]
            predecessor_ack_observed = len(server_acks) == 1 and len(client_acks) == 1
            if not predecessor_ack_observed:
                _retain_runtime(old_runtime)
        uncertain_runtime = any(
            runtime is not None and not _runtime_cleanup_confirmed(runtime)
            for runtime in (old_runtime, replacement_runtime)
        )
        preserved_owners = set()
        predecessor_barrier_unmet = (
            successor_gateway_ready and not predecessor_ack_observed
        )
        if ((uncertain_runtime or predecessor_barrier_unmet)
                and active_gateway_ready and active_gateway is not None):
            _retain_gateway(active_gateway)
            preserved_owners.add(id(active_gateway[1]))
            _retain_gateway_channels((messages, controls, trace_events))
            preserve_channels = True
        for process, owner, _ready in reversed(gateways):
            if id(owner) in preserved_owners:
                continue
            if owner._closed:
                with suppress(ValueError, OSError):
                    process.close()
                continue
            if owner._attempted:
                _retain_gateway((process, owner, _ready))
                _retain_gateway_channels((messages, controls, trace_events))
                preserve_channels = True
                continue
            _write_diagnostic_phase(phase_path, "final_gateway_cleanup_begin")
            try:
                if owner._bound:
                    owner.close()
                else:
                    owner.terminate_after_constructor_failure()
            except BaseException:
                _write_diagnostic_phase(phase_path, "final_gateway_cleanup_error")
                _retain_gateway((process, owner, _ready))
                _retain_gateway_channels((messages, controls, trace_events))
                preserve_channels = True
            else:
                _write_diagnostic_phase(phase_path, "final_gateway_cleanup_end")
                if not owner._closed:
                    _retain_gateway((process, owner, _ready))
                    _retain_gateway_channels((messages, controls, trace_events))
                    preserve_channels = True
                    continue
                with suppress(ValueError, OSError):
                    process.close()
        if not preserve_channels:
            for channel in (messages, controls, trace_events):
                _write_diagnostic_phase(phase_path, "channel_close_begin")
                channel.close()
                _write_diagnostic_phase(phase_path, "channel_close_end")
                _write_diagnostic_phase(phase_path, "channel_join_begin")
                channel.join_thread()
                _write_diagnostic_phase(phase_path, "channel_join_end")
    _write_diagnostic_phase(phase_path, "test_complete")
