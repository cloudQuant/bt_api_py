"""Execution journal relay against a spawned durable monitor ingress host."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import multiprocessing
import sqlite3
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import ExecutionScope
from bt_api_execution.journal_reader import ExecutionJournalReader
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.service_ingress import MonitorIngressTarget
from bt_api_monitor.threshold_service import (
    THRESHOLD_ALERT_EVENT_TYPE,
    TrustedThresholdTarget,
    create_monitor_threshold_resource_provider,
)

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_execution_monitor import (
    ExecutionMonitorPollResult,
    ServiceExecutionMonitorRelay,
    ServiceExecutionMonitorRelayError,
    execution_monitor_relay_configured,
)
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.unit.test_service_account_integration import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _client_command,
    _close_normally,
    _receive,
    _start_process,
)
from tests.unit.test_service_execution_integration import _strategy_process
from tests.unit.test_service_order_entry_integration import (
    _ACCOUNT,
    _EXECUTION_SCOPE,
    _STRATEGY,
    _TRADING_DAY,
    _endpoint,
)
from tests.unit.test_service_order_entry_integration import (
    _host_process as _execution_host_process,
)
from tests.unit.test_service_order_entry_integration import (
    _service_config as _execution_service_config,
)

_MONITOR_DOMAIN = "paper:execution-monitor-relay-integration"
_MONITOR_ID = "synthetic-execution-monitor-service"
_MONITOR_PRINCIPAL = "synthetic-execution-monitor-principal"
_MONITOR_INSTANCE = "execution-monitor-ingress"
_MONITOR_SCOPE_REF = "execution-monitor-ingress-v1"
_MONITOR_SECURITY_REF = "execution-monitor-server-curve-v1"
_RELAY_ID = "synthetic-execution-monitor-relay"
_RELAY_PRINCIPAL = "synthetic-execution-monitor-relay-principal"
_RELAY_SCOPE_REF = "execution-monitor-relay-client-v1"
_RELAY_SECURITY_REF = "execution-monitor-relay-client-curve-v1"
_OUTBOX_SCOPE = "outbox:execution-account-a"
_EXECUTION_EVENT_TYPE = "execution.lifecycle"
_CANCELLATION_EVENT_TYPE = "execution.cancellation"
_EVENT_TYPES = (_EXECUTION_EVENT_TYPE, _CANCELLATION_EVENT_TYPE)
_MONITOR_SCOPE = WireScope(
    _MONITOR_DOMAIN,
    account_ref=_ACCOUNT,
    strategy_ref=_STRATEGY,
)
_REQUEST_TIMEOUT_MS = 1_000
_HEARTBEAT_SECONDS = 5
_MAX_MESSAGE_BYTES = 65_536
_INGRESS_LIMITS = {"max_events": 64, "max_bytes": 262_144, "max_event_bytes": 16_384}
_READER_FIELDS = frozenset(
    {
        "sequence",
        "stream",
        "event_id",
        "intent_id",
        "cancel_id",
        "target_intent_id",
        "scope_key",
        "event_type",
        "state",
        "created_at_ns",
        "journal_generation",
    }
)


def _component(instance_id: str, options: dict[str, Any]) -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": ["service.telemetry_ingress"],
            "options": {"role": "durable_ingress", **options},
        },
    }


def _monitor_service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _MONITOR_ID,
        "scope_ref": "execution-monitor-service-root-v1",
        "transport_ref": "execution-monitor-listener-v1",
        "registration_policy_ref": "execution-monitor-registration-v1",
        "service_requirements_ref": "execution-monitor-requirements-v1",
        "components": {"monitor": _component(_MONITOR_INSTANCE, _INGRESS_LIMITS)},
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "execution-monitor-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _MONITOR_SECURITY_REF,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _relay_config(endpoint: str):
    """Build an ordinary service-role client config for the explicit relay process."""

    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": _RELAY_ID,
            "scope_ref": _RELAY_SCOPE_REF,
            "transport_ref": "execution-monitor-relay-listener-v1",
            "registration_policy_ref": "execution-monitor-relay-policy-v1",
            "service_requirements_ref": "execution-monitor-relay-requirements-v1",
            "components": {
                "monitor": _component("unused-execution-monitor-ingress", _INGRESS_LIMITS)
            },
            "clients": {
                "monitor": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "upstream.telemetry",
                                "service_id": _MONITOR_ID,
                                "instance_id": _MONITOR_INSTANCE,
                                "scope_ref": _MONITOR_SCOPE_REF,
                                "transport_ref": "execution-monitor-connect-v1",
                            }
                        ],
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                        },
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "execution-monitor-relay-listener-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": _endpoint()},
                        "security_profile_ref": "execution-monitor-relay-unused-server-v1",
                        "options": {
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    },
                    "execution-monitor-connect-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": _RELAY_SECURITY_REF,
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    },
                },
            },
        }
    )


def _monitor_host_process(
    control, raw_config, registry_path, outbox_path, keys, threshold_document=None
):
    config = parse_service_config(raw_config)
    identity = WireIdentity(WireRole.SERVICE, _MONITOR_PRINCIPAL, _MONITOR_ID)
    grant = ResourceGrant(
        _MONITOR_INSTANCE,
        "service.telemetry_ingress",
        _MONITOR_SCOPE_REF,
        _MONITOR_SCOPE,
        frozenset({"events.append"}),
    )
    authority = TrustedServiceAuthority(
        identity=identity,
        scope=_MONITOR_SCOPE,
        scope_ref=config.scope_ref,
        scope_version="execution-monitor-scope-v1",
        security_ref=_MONITOR_SECURITY_REF,
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="execution-monitor-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="execution-monitor-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals={keys["relay_public"]: _RELAY_PRINCIPAL},
        grants={
            _RELAY_PRINCIPAL: (
                PrincipalGrant(
                    WireIdentity(WireRole.SERVICE, _RELAY_PRINCIPAL, _RELAY_ID),
                    (grant,),
                ),
            )
        },
    )
    outbox = None
    host = None
    threshold_provider = None
    try:
        outbox = DurableOutbox(outbox_path)

        def resource_provider(component, _instance_id, _options, _scope):
            if component != "monitor":
                raise AssertionError("unexpected monitor component")
            return ComponentResources.borrowed(
                {
                    "outbox": outbox,
                    "targets": {_MONITOR_SCOPE: MonitorIngressTarget(_OUTBOX_SCOPE, _EVENT_TYPES)},
                },
                ready=lambda: True,
            )

        host_resource_provider = resource_provider
        if threshold_document is not None:
            trusted_target = TrustedThresholdTarget(
                "execution-relay",
                _MONITOR_SCOPE,
                MonitorIngressTarget(_OUTBOX_SCOPE, _EVENT_TYPES),
                producer_identity=WireIdentity(
                    WireRole.SERVICE, _RELAY_PRINCIPAL, _RELAY_ID
                ),
                source_provider=_EXECUTION_SCOPE.provider,
                source_environment=_EXECUTION_SCOPE.environment,
            )
            threshold_provider = create_monitor_threshold_resource_provider(
                resource_provider,
                monitor_instance_id=_MONITOR_INSTANCE,
                config_document=threshold_document,
                trusted_targets={trusted_target.target_ref: trusted_target},
                poll_interval_seconds=0.02,
            )
            host_resource_provider = threshold_provider
        host = ServiceHost(config, authority, registry_path, host_resource_provider)
        host.start()
        ready = ("ready", host.endpoint, host.state)
        if threshold_provider is not None:
            ready += (dict(threshold_provider.status),)
        control.send(ready)
        while True:
            host.serve_once(timeout_ms=25)
            if not control.poll():
                continue
            if control.recv() == ("close",):
                host.close()
                closed = ("closed", host.state)
                if threshold_provider is not None:
                    closed += (dict(threshold_provider.status),)
                control.send(closed)
                return
            raise AssertionError("unknown execution monitor host command")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        failure = ("error", getattr(exc, "code", type(exc).__name__))
        if threshold_provider is not None:
            failure += (dict(threshold_provider.status),)
        with suppress(Exception):
            control.send(failure)
        raise
    finally:
        control.close()


def _relay_process(
    control,
    endpoint,
    execution_store_path,
    checkpoint_path,
    keys,
    inject_checkpoint_failure: bool,
):
    import bt_api_py.service_execution_monitor as relay_module

    reader = None
    relay = None
    runtime = None
    checkpoint_advance = relay_module._advance_checkpoint
    try:
        config = _relay_config(endpoint)
        configured = execution_monitor_relay_configured(config, scope_ref=_MONITOR_SCOPE_REF)
        peer = WireIdentity(WireRole.SERVICE, _MONITOR_PRINCIPAL, _MONITOR_ID)
        runtime = ServiceClientRuntime(
            config,
            identity=WireIdentity(WireRole.SERVICE, _RELAY_PRINCIPAL, _RELAY_ID),
            scope_resolver=lambda _ref: _MONITOR_SCOPE,
            peer_resolver=lambda _service: peer,
            transport_factory=ZmqTransportFactory(
                {
                    _RELAY_SECURITY_REF: ClientCurveKeys(
                        keys["server_public"], keys["relay_public"], keys["relay_secret"]
                    )
                }
            ),
            requested_operations={"upstream.telemetry": frozenset({"events.append"})},
        )
        try:
            runtime.start()
        except Exception as exc:
            start_code = getattr(exc, "code", type(exc).__name__)
            runtime.close()
            control.send(
                (
                    "start-failed",
                    start_code,
                    runtime.state,
                    runtime.cleanup_failures,
                    runtime.unregister_uncertainties,
                    configured,
                )
            )
            command = control.recv()
            if command != ("close",):
                raise AssertionError("expected relay close after startup failure") from None
            control.send(
                (
                    "closed",
                    runtime.state,
                    runtime.cleanup_failures,
                    runtime.unregister_uncertainties,
                )
            )
            return

        reader = ExecutionJournalReader(execution_store_path, scope=_EXECUTION_SCOPE)
        execution_page = reader.read_page("execution", limit=100)
        cancellation_page = reader.read_page("cancellation", limit=100)
        first_event = next(
            iter(execution_page.events or cancellation_page.events),
            None,
        )
        field_names = (
            tuple(field.name for field in dataclasses.fields(first_event))
            if first_event is not None
            else ()
        )
        control.send(
            (
                "reader-ready",
                configured,
                reader.generation,
                reader.scope.key,
                len(execution_page.events),
                len(cancellation_page.events),
                field_names,
            )
        )
        relay = ServiceExecutionMonitorRelay(
            runtime,
            reader,
            scope_ref=_MONITOR_SCOPE_REF,
            checkpoint_path=checkpoint_path,
        )
        execution_checkpoint = relay.checkpoint("execution")
        cancellation_checkpoint = relay.checkpoint("cancellation")
        control.send(
            (
                "registered",
                configured,
                runtime.state,
                execution_checkpoint.sequence,
                cancellation_checkpoint.sequence,
            )
        )

        while True:
            command = control.recv()
            if command == ("close",):
                relay.close()
                relay = None
                reader.close()
                reader = None
                runtime.close()
                control.send(
                    (
                        "closed",
                        runtime.state,
                        runtime.cleanup_failures,
                        runtime.unregister_uncertainties,
                    )
                )
                return
            if command[0] == "poll":
                inject = command[1]
                if inject:
                    fired = False

                    def fail_one_local_checkpoint(*args, **kwargs):
                        nonlocal fired
                        if not fired:
                            fired = True
                            raise sqlite3.OperationalError("synthetic local checkpoint write")
                        return checkpoint_advance(*args, **kwargs)

                    relay_module._advance_checkpoint = fail_one_local_checkpoint
                try:
                    result = relay.poll(limit=32)
                except ServiceExecutionMonitorRelayError as exc:
                    last_poll = exc.last_poll
                    control.send(
                        (
                            "poll-failed",
                            exc.code,
                            None if last_poll is None else last_poll.remote_delivered,
                            None if last_poll is None else last_poll.checkpointed,
                            relay.checkpoint("execution").sequence,
                            relay.checkpoint("cancellation").sequence,
                            bool(inject and fired),
                        )
                    )
                else:
                    assert type(result) is ExecutionMonitorPollResult
                    control.send(
                        (
                            "polled",
                            result.execution_delivered,
                            result.cancellation_delivered,
                            result.remote_delivered,
                            result.checkpointed,
                            result.total_delivered,
                            relay.checkpoint("execution").sequence,
                            relay.checkpoint("cancellation").sequence,
                        )
                    )
                finally:
                    if inject:
                        relay_module._advance_checkpoint = checkpoint_advance
                continue
            if command[0] == "cursors":
                execution_checkpoint = relay.checkpoint("execution")
                cancellation_checkpoint = relay.checkpoint("cancellation")
                control.send(
                    (
                        "cursors",
                        execution_checkpoint.sequence,
                        execution_checkpoint.source_event_id,
                        cancellation_checkpoint.sequence,
                        cancellation_checkpoint.source_event_id,
                    )
                )
                continue
            raise AssertionError("unknown execution monitor relay command")
    except BaseException as exc:
        if relay is not None:
            with suppress(BaseException):
                relay.close()
        if reader is not None:
            with suppress(BaseException):
                reader.close()
        if runtime is not None and runtime.state != "closed":
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        relay_module._advance_checkpoint = checkpoint_advance
        control.close()


def _read_monitor_events(path: str):
    outbox = DurableOutbox(path, read_only=True)
    rows, has_more = outbox.read_page_types(_OUTBOX_SCOPE, _EVENT_TYPES, 0, 100)
    assert has_more is False
    return tuple(rows)


def _execution_threshold_config() -> bytes:
    policy = {
        "max_rule_configs": 8,
        "max_dimension_groups": 8,
        "max_window_events": 16,
        "max_label_bytes": 8_192,
        "max_alert_rows": 8,
        "alert_retention_seconds": 3_600,
    }
    rules = []
    for rule_id, event_type, selector, threshold_count in (
        (
            "execution-submit-admission",
            _EXECUTION_EVENT_TYPE,
            ("execution", "intent_recorded"),
            2,
        ),
        (
            "execution-cancel-admission",
            _CANCELLATION_EVENT_TYPE,
            ("cancellation", "cancel_intent_recorded"),
            1,
        ),
    ):
        stream, business_event_type = selector
        rules.append(
            {
                "rule_id": rule_id,
                "rule_version": 1,
                "target_ref": "execution-relay",
                "event_type": event_type,
                "window_seconds": 3_600,
                "threshold_count": threshold_count,
                "dimension_fields": ["account_ref", "strategy_ref"],
                "max_dimension_keys": 1,
                "max_window_events": 8,
                "allowed_lateness_seconds": 3_600,
                "late_event_policy": "ignore",
                "trading_day_field": None,
                "execution_event_selector": {
                    "schema": "sdk.execution_monitor_projection.v2",
                    "stream": stream,
                    "business_event_type": business_event_type,
                    "state": "PENDING_ADMISSION",
                },
            }
        )
    document = {
        "schema_version": 1,
        "consumer_id": "execution-admission-thresholds-v1",
        "outbox_max_events": 128,
        "outbox_max_bytes": 262_144,
        "storage_policy": policy,
        "rules": rules,
    }
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _read_monitor_rows(path: str):
    outbox = DurableOutbox(path, read_only=True)
    rows, has_more = outbox.read_page_types(
        _OUTBOX_SCOPE,
        (_EXECUTION_EVENT_TYPE, _CANCELLATION_EVENT_TYPE, THRESHOLD_ALERT_EVENT_TYPE),
        0,
        100,
    )
    assert has_more is False
    return tuple(rows), outbox


def _monitor_payload_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict) or hasattr(value, "items"):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_monitor_payload_keys(nested))
    elif isinstance(value, (tuple, list)):
        for nested in value:
            keys.update(_monitor_payload_keys(nested))
    return keys


def _update_journal_payloads_for_metadata_only_check(path: str) -> None:
    """Install invalid payload text after real SDK writes to prove it is not read."""

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE execution_outbox SET payload_json = ?",
            ("{invalid-json-private-sentinel",),
        )
        connection.execute(
            "UPDATE cancellation_outbox SET payload_json = ?",
            ("{invalid-json-private-sentinel",),
        )


def _checkpoint_file_digest(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_spawned_execution_journal_relay_replays_after_local_checkpoint_failure(
    tmp_path,
) -> None:
    context = multiprocessing.get_context("spawn")
    execution_endpoint = _endpoint()
    monitor_endpoint = _endpoint()
    execution_server_public, execution_server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    monitor_server_public, monitor_server_secret = zmq.curve_keypair()
    relay_public, relay_secret = zmq.curve_keypair()
    execution_keys = {
        "server_public": execution_server_public,
        "server_secret": execution_server_secret,
        "strategy_public": strategy_public,
    }
    monitor_keys = {
        "server_public": monitor_server_public,
        "server_secret": monitor_server_secret,
        "relay_public": relay_public,
        "relay_secret": relay_secret,
    }
    execution_registry_path = str(tmp_path / "execution-registry.sqlite3")
    execution_store_path = str(tmp_path / "execution.sqlite3")
    monitor_registry_path = str(tmp_path / "monitor-registry.sqlite3")
    monitor_outbox_path = str(tmp_path / "monitor-outbox.sqlite3")
    checkpoint_path = str(tmp_path / "execution-monitor-checkpoint.sqlite3")

    records = []
    forced = []
    cleanup_failures = []
    execution_host = strategy = monitor_host = relay_process = None
    try:
        execution_host = _start_process(
            context,
            _execution_host_process,
            (
                _execution_service_config(execution_endpoint),
                execution_registry_path,
                execution_keys,
                execution_store_path,
            ),
            "execution-monitor-journal-host",
        )
        records.append(execution_host)
        host_ready = _receive(execution_host["pipe"])
        assert host_ready[0] == "ready", host_ready
        assert host_ready[1] == execution_endpoint

        strategy = _start_process(
            context,
            _strategy_process,
            (execution_endpoint, strategy_public, strategy_secret, execution_server_public),
            "execution-monitor-journal-strategy",
        )
        records.append(strategy)
        assert _receive(strategy["pipe"])[0] == "ready"

        first_submit = _client_command(strategy, "submit", "intent-1")
        assert first_submit == (
            "submitted",
            "intent-1",
            "ACKED",
            "provider-order-1",
            1,
        )
        cancel = _client_command(strategy, "cancel")
        assert cancel == ("cancelled", "cancel-1", "ACKED", "intent-1")

        # The optional monitor service is still absent. Its client registration
        # fails locally while the already-committed execution and cancel facts
        # remain available in the execution journal.
        failed_start = _start_process(
            context,
            _relay_process,
            (
                monitor_endpoint,
                execution_store_path,
                checkpoint_path,
                monitor_keys,
                False,
            ),
            "execution-monitor-offline-relay",
        )
        records.append(failed_start)
        start_failed = _receive(failed_start["pipe"])
        assert start_failed[0] == "start-failed", start_failed
        assert start_failed[1]
        assert start_failed[2:5] == ("closed", 0, 0)
        assert start_failed[5] is True
        assert not Path(checkpoint_path).exists()
        offline_closed = _close_normally(failed_start, expected_tag="closed")
        assert offline_closed[1:] == ("closed", 0, 0)
        failed_start = None

        # The normal order path remains single-dispatch after the failed
        # optional relay startup and still has no monitor dependency.
        second_submit = _client_command(strategy, "submit", "intent-2")
        assert second_submit == (
            "submitted",
            "intent-2",
            "ACKED",
            "provider-order-1",
            1,
        )
        provider_counts_before_relay = _client_command(execution_host, "counts")
        assert provider_counts_before_relay[1] == ("intent-1", "intent-2")
        assert provider_counts_before_relay[2] == ("cancel-1",)
        assert len(provider_counts_before_relay[1]) == len(set(provider_counts_before_relay[1]))

        _update_journal_payloads_for_metadata_only_check(execution_store_path)
        reader = ExecutionJournalReader(execution_store_path, scope=_EXECUTION_SCOPE)
        execution_page = reader.read_page("execution", limit=100)
        cancellation_page = reader.read_page("cancellation", limit=100)
        assert execution_page.generation == cancellation_page.generation == reader.generation
        assert {event.intent_id for event in execution_page.events} >= {"intent-1", "intent-2"}
        assert {event.cancel_id for event in cancellation_page.events} == {"cancel-1"}
        assert all(event.scope_key == _EXECUTION_SCOPE.key for event in execution_page.events)
        assert all(event.scope_key == _EXECUTION_SCOPE.key for event in cancellation_page.events)
        other_scope = ExecutionScope(
            "PAPER", "simulation", "other-account", _STRATEGY, _TRADING_DAY
        )
        other_reader = ExecutionJournalReader(execution_store_path, scope=other_scope)
        assert other_reader.generation == reader.generation
        assert other_reader.read_page("execution").events == ()
        assert other_reader.read_page("cancellation").events == ()
        other_reader.close()
        reader.close()

        monitor_raw = _monitor_service_config(monitor_endpoint)
        monitor_host = _start_process(
            context,
            _monitor_host_process,
            (monitor_raw, monitor_registry_path, monitor_outbox_path, monitor_keys),
            "execution-monitor-ingress-host-r1",
        )
        records.append(monitor_host)
        monitor_ready = _receive(monitor_host["pipe"])
        assert monitor_ready[0] == "ready", monitor_ready
        assert monitor_ready[1] == monitor_endpoint

        relay_process = _start_process(
            context,
            _relay_process,
            (
                monitor_endpoint,
                execution_store_path,
                checkpoint_path,
                monitor_keys,
                True,
            ),
            "execution-monitor-relay-r1",
        )
        records.append(relay_process)
        relay_ready = _receive(relay_process["pipe"])
        assert relay_ready[0] == "reader-ready", relay_ready
        assert relay_ready[1] is True
        assert relay_ready[3] == _EXECUTION_SCOPE.key
        assert relay_ready[4] == len(execution_page.events)
        assert relay_ready[5] == len(cancellation_page.events)
        assert frozenset(relay_ready[6]) == _READER_FIELDS
        registered = _receive(relay_process["pipe"])
        assert registered == ("registered", True, "registered", 0, 0)

        injected_failure = _client_command(relay_process, "poll", True)
        assert injected_failure == (
            "poll-failed",
            "LOCAL_CHECKPOINT_FAILED",
            1,
            0,
            0,
            0,
            True,
        )
        first_remote_events = _read_monitor_events(monitor_outbox_path)
        assert len(first_remote_events) == 1
        assert first_remote_events[0].event.event_type == _EXECUTION_EVENT_TYPE
        assert (
            first_remote_events[0].event.data["payload"]["source_event_id"]
            == execution_page.events[0].event_id
        )
        first_remote_identity = (
            first_remote_events[0].event.event_id,
            first_remote_events[0].sequence,
        )

        failed_cursor_db_digest = _checkpoint_file_digest(checkpoint_path)
        relay_closed = _close_normally(relay_process, expected_tag="closed")
        assert relay_closed[1:] == ("closed", 0, 0)
        relay_process = None
        monitor_closed = _close_normally(monitor_host, expected_tag="closed")
        assert monitor_closed == ("closed", "closed")
        monitor_host = None

        # Re-registering while the Monitor is stopped must leave the durable
        # local cursor bytes unchanged; this is a startup failure, not a lost
        # ingress reply.
        offline_restart = _start_process(
            context,
            _relay_process,
            (
                monitor_endpoint,
                execution_store_path,
                checkpoint_path,
                monitor_keys,
                False,
            ),
            "execution-monitor-offline-relay-restart",
        )
        records.append(offline_restart)
        offline_restart_failed = _receive(offline_restart["pipe"])
        assert offline_restart_failed[0] == "start-failed"
        assert offline_restart_failed[2:5] == ("closed", 0, 0)
        assert offline_restart_failed[5] is True
        assert _checkpoint_file_digest(checkpoint_path) == failed_cursor_db_digest
        offline_restart_closed = _close_normally(offline_restart, expected_tag="closed")
        assert offline_restart_closed[1:] == ("closed", 0, 0)
        offline_restart = None

        # Restart Monitor on the same durable outbox, then replay the identical
        # source event ID. Its successful first append was already observed;
        # only the local checkpoint write failed.
        monitor_raw = _monitor_service_config(monitor_endpoint)
        monitor_host = _start_process(
            context,
            _monitor_host_process,
            (
                monitor_raw,
                monitor_registry_path,
                monitor_outbox_path,
                monitor_keys,
                _execution_threshold_config(),
            ),
            "execution-monitor-ingress-host-r2",
        )
        records.append(monitor_host)
        threshold_ready = _receive(monitor_host["pipe"])
        assert threshold_ready[:3] == ("ready", monitor_endpoint, "started")
        assert threshold_ready[3]["state"] == "ready"
        assert threshold_ready[3]["ready"] is True
        assert threshold_ready[3]["process_id"] is not None
        assert threshold_ready[3]["start_count"] == 1
        assert threshold_ready[3]["outstanding_pipe_count"] == 1

        relay_process = _start_process(
            context,
            _relay_process,
            (
                monitor_endpoint,
                execution_store_path,
                checkpoint_path,
                monitor_keys,
                False,
            ),
            "execution-monitor-relay-r2",
        )
        records.append(relay_process)
        assert _receive(relay_process["pipe"])[0] == "reader-ready"
        assert _receive(relay_process["pipe"]) == ("registered", True, "registered", 0, 0)

        recovered = _client_command(relay_process, "poll", False)
        assert recovered[0] == "polled"
        assert recovered[1] == len(execution_page.events)
        assert recovered[2] == len(cancellation_page.events)
        assert recovered[3] == recovered[1] + recovered[2]
        assert recovered[4] == recovered[3]
        assert recovered[5] == recovered[3]
        assert recovered[6] == execution_page.events[-1].sequence
        assert recovered[7] == cancellation_page.events[-1].sequence

        unchanged = _client_command(relay_process, "poll", False)
        assert unchanged == ("polled", 0, 0, 0, 0, 0, recovered[6], recovered[7])

        persisted = _read_monitor_events(monitor_outbox_path)
        expected_events = len(execution_page.events) + len(cancellation_page.events)
        assert len(persisted) == expected_events
        source_ids = [event.event.data["payload"]["source_event_id"] for event in persisted]
        expected_source_ids = [event.event_id for event in execution_page.events] + [
            event.event_id for event in cancellation_page.events
        ]
        assert len(source_ids) == len(set(source_ids))
        assert set(source_ids) == set(expected_source_ids)
        source_events = {
            event.event_id: event for event in execution_page.events + cancellation_page.events
        }
        expected_payloads = {
            source_event.event_id: {
                "schema": "sdk.execution_monitor_projection.v2",
                "journal_generation": source_event.journal_generation,
                "stream": source_event.stream,
                "source_sequence": source_event.sequence,
                "source_event_id": source_event.event_id,
                "source_created_at_ns": source_event.created_at_ns,
                "source_scope_key": _EXECUTION_SCOPE.key,
                "source_scope": _EXECUTION_SCOPE.to_payload(),
                "business_event_type": source_event.event_type,
                "state": source_event.state,
                "intent_id": source_event.intent_id,
                "cancel_id": source_event.cancel_id,
                "target_intent_id": source_event.target_intent_id,
            }
            for source_event in source_events.values()
        }
        by_source_id = {
            event.event.data["payload"]["source_event_id"]: event for event in persisted
        }
        replayed_first_remote = by_source_id[execution_page.events[0].event_id]
        assert (
            replayed_first_remote.event.event_id,
            replayed_first_remote.sequence,
        ) == first_remote_identity
        assert all(
            by_source_id[event.event_id].event.event_type == _EXECUTION_EVENT_TYPE
            for event in execution_page.events
        )
        assert all(
            by_source_id[event.event_id].event.event_type == _CANCELLATION_EVENT_TYPE
            for event in cancellation_page.events
        )
        assert all(
            not (
                {
                    "quantity",
                    "price",
                    "avg_price",
                    "filled_quantity",
                    "commission",
                }
                & _monitor_payload_keys(event.event.data["payload"])
            )
            for event in persisted
        )
        assert all(
            event.event.data["payload"]
            == expected_payloads[event.event.data["payload"]["source_event_id"]]
            for event in persisted
        )

        cursors = _client_command(relay_process, "cursors")
        assert cursors[0] == "cursors"
        assert cursors[1] == execution_page.events[-1].sequence
        assert cursors[2] == execution_page.events[-1].event_id
        assert cursors[3] == cancellation_page.events[-1].sequence
        assert cursors[4] == cancellation_page.events[-1].event_id

        # Monitoring cannot cause a legacy or repeated order dispatch: the
        # actual execution service still reports one call per submitted intent.
        provider_counts_after_relay = _client_command(execution_host, "counts")
        assert provider_counts_after_relay == provider_counts_before_relay
        assert provider_counts_after_relay[1] == ("intent-1", "intent-2")
        assert provider_counts_after_relay[2] == ("cancel-1",)

        submit_facts = [
            event
            for event in persisted
            if event.event.data["payload"]["business_event_type"] == "intent_recorded"
            and event.event.data["payload"]["state"] == "PENDING_ADMISSION"
        ]
        cancel_facts = [
            event
            for event in persisted
            if event.event.data["payload"]["business_event_type"]
            == "cancel_intent_recorded"
            and event.event.data["payload"]["state"] == "PENDING_ADMISSION"
        ]
        assert len(submit_facts) == 2
        assert len(cancel_facts) == 1
        assert sum(event.event.event_type == _EXECUTION_EVENT_TYPE for event in persisted) > len(
            submit_facts
        )
        assert sum(event.event.event_type == _CANCELLATION_EVENT_TYPE for event in persisted) > len(
            cancel_facts
        )

        deadline = time.monotonic() + 5.0
        threshold_rows = ()
        while time.monotonic() < deadline:
            all_rows, monitor_outbox = _read_monitor_rows(monitor_outbox_path)
            threshold_rows = tuple(
                row
                for row in all_rows
                if row.event.event_type == THRESHOLD_ALERT_EVENT_TYPE
            )
            threshold_checkpoint = monitor_outbox.checkpoint(
                "execution-admission-thresholds-v1", _OUTBOX_SCOPE
            )
            if (
                len(threshold_rows) == 2
                and all_rows
                and threshold_checkpoint == all_rows[-1].sequence
            ):
                break
            time.sleep(0.02)
        assert len(threshold_rows) == 2
        assert {row.event.data["rule_id"] for row in threshold_rows} == {
            "execution-submit-admission",
            "execution-cancel-admission",
        }
        alerts_by_rule = {row.event.data["rule_id"]: row for row in threshold_rows}
        submit_alert = alerts_by_rule["execution-submit-admission"].event.data
        cancel_alert = alerts_by_rule["execution-cancel-admission"].event.data
        assert submit_alert["source_event_type"] == _EXECUTION_EVENT_TYPE
        assert submit_alert["observed_count"] == 2
        assert submit_alert["threshold_count"] == 2
        assert submit_alert["dimensions"] == {
            "account_ref": _ACCOUNT,
            "strategy_ref": _STRATEGY,
        }
        assert submit_alert["source_event_id"] in {
            event.event.data["source_event_id"] for event in submit_facts
        }
        assert cancel_alert["source_event_type"] == _CANCELLATION_EVENT_TYPE
        assert cancel_alert["observed_count"] == 1
        assert cancel_alert["threshold_count"] == 1
        assert cancel_alert["dimensions"] == {
            "account_ref": _ACCOUNT,
            "strategy_ref": _STRATEGY,
        }
        assert cancel_alert["source_event_id"] in {
            event.event.data["source_event_id"] for event in cancel_facts
        }

        relay_closed = _close_normally(relay_process, expected_tag="closed")
        assert relay_closed[1:] == ("closed", 0, 0)
        relay_process = None
        monitor_closed = _close_normally(monitor_host, expected_tag="closed")
        assert monitor_closed[:2] == ("closed", "closed")
        threshold_closed = monitor_closed[2]
        assert threshold_closed["state"] == "closed"
        assert threshold_closed["ready"] is False
        assert threshold_closed["process_id"] is None
        assert threshold_closed["restart_count"] == 0
        assert threshold_closed["outstanding_pipe_count"] == 0
        assert threshold_closed["closed"] is True
        monitor_host = None
        strategy_closed = _close_normally(strategy, expected_tag="closed")
        assert strategy_closed == ("closed", "closed")
        strategy = None
        execution_host_closed = _close_normally(execution_host, expected_tag="closed")
        assert execution_host_closed == ("closed", "closed")
        execution_host = None
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, cleanup_failures)
        _assert_no_fallback_cleanup(forced, cleanup_failures)
