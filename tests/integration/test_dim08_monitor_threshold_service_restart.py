"""Real Host and spawned-child coverage for the trusted Monitor rule service.

The data and credentials are synthetic/local, but calls use the normal parsed
ServiceClientRuntime, Registry grants, CURVE ServiceHost, Monitor ingress, and
the production MonitorThresholdResourceProvider.  The threshold consumer is a
real spawned Windows process owned by the Host resource provider.
"""

from __future__ import annotations

import json
import multiprocessing
import socket
import sqlite3
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_monitor import create_monitor_threshold_resource_provider
from bt_api_monitor.durable import DurableOutbox
from bt_api_monitor.service_ingress import MonitorIngressTarget
from bt_api_monitor.threshold_service import (
    THRESHOLD_ALERT_EVENT_TYPE,
    TrustedThresholdTarget,
)

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from tests.unit._spawned_process_helpers import (
    _bounded_reap_spawned_process,
    _receive,
    _start_process,
)

_DOMAIN = "dim08-threshold-restart"
_SCOPE_A = WireScope(_DOMAIN, account_ref="account-a", strategy_ref="strategy-a")
_SCOPE_B = WireScope(_DOMAIN, account_ref="account-b", strategy_ref="strategy-b")
_OUTBOX_A = "dim08:threshold:account-a:strategy-a"
_OUTBOX_B = "dim08:threshold:account-b:strategy-b"
_SERVICE_ID = "dim08-threshold-monitor"
_SERVICE_SCOPE = WireScope(_DOMAIN)
_SERVICE_SCOPE_REF = "dim08-threshold-monitor-v1"
_SECURITY_REF = "dim08-threshold-curve-v1"
_REGISTRATION_REF = "dim08-threshold-registration-v1"
_REQUIREMENTS_REF = "dim08-threshold-requirements-v1"
_MONITOR_INSTANCE = "dim08-threshold-ingress"
_MONITOR_SCOPE_REF = "dim08-threshold-ingress-v1"
_CLIENT_TRANSPORT_REF = "dim08-threshold-client-v1"
_SOURCE_EVENT_TYPE = "synthetic.quote.v1"
_CONSUMER_ID = "dim08-threshold-consumer"
_OPERATIONS = frozenset({"events.append"})
_MAX_MESSAGE_BYTES = 262_144
_CONSUMER_SCOPES = (_OUTBOX_A, _OUTBOX_B)


def _free_endpoint() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"
    finally:
        probe.close()


def _component(instance_id: str, bind_ports: tuple[str, ...], options: dict[str, Any]):
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": list(bind_ports),
            "options": options,
        },
    }


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": "dim08-threshold-listener-v1",
        "registration_policy_ref": _REGISTRATION_REF,
        "service_requirements_ref": _REQUIREMENTS_REF,
        "components": {
            "monitor": _component(
                _MONITOR_INSTANCE,
                ("service.telemetry_ingress",),
                {
                    "role": "durable_ingress",
                    "max_events": 32,
                    "max_bytes": 1_048_576,
                    "max_event_bytes": 32_768,
                },
            )
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "dim08-threshold-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "high_water_mark": 64,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _client_config(endpoint: str) -> dict[str, Any]:
    """Build the repository's ordinary service-role relay client config.

    ``upstream.telemetry`` is intentionally a service-role mount.  Exact
    account/strategy scope is still authenticated by the Host Registry grant,
    so the integration does not weaken authorization to make a strategy
    client bypass the mount parser.
    """
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "dim08-threshold-test-client",
        "scope_ref": "dim08-threshold-test-client-root-v1",
        "transport_ref": "dim08-threshold-test-client-listener-v1",
        "registration_policy_ref": "dim08-threshold-test-client-registration-v1",
        "service_requirements_ref": "dim08-threshold-test-client-requirements-v1",
        "components": {
            "monitor": _component(
                "dim08-threshold-test-client-monitor",
                ("service.monitor_query",),
                {},
            )
        },
        "clients": {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "upstream.telemetry",
                            "service_id": _SERVICE_ID,
                            "instance_id": _MONITOR_INSTANCE,
                            "scope_ref": _MONITOR_SCOPE_REF,
                            "transport_ref": _CLIENT_TRANSPORT_REF,
                        }
                    ],
                    "options": {"request_timeout_ms": 2_000, "heartbeat_interval_seconds": 5},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "dim08-threshold-test-client-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:9"},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "high_water_mark": 64,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                },
                _CLIENT_TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "request_timeout_ms": 2_000,
                        "high_water_mark": 64,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _threshold_config() -> bytes:
    def rule(rule_id: str, target_ref: str):
        return {
            "rule_id": rule_id,
            "rule_version": 1,
            "target_ref": target_ref,
            "event_type": _SOURCE_EVENT_TYPE,
            "window_seconds": 300,
            "threshold_count": 1,
            "dimension_fields": (
                ["instrument_key", "group_ref"]
                if target_ref == "target-a"
                else ["instrument_key", "resource_pool_ref"]
            ),
            "max_dimension_keys": 2,
            "max_window_events": 2,
            "allowed_lateness_seconds": 5,
            "late_event_policy": "ignore",
            "trading_day_field": None,
        }

    return json.dumps(
        {
            "schema_version": 1,
            "consumer_id": _CONSUMER_ID,
            "outbox_max_events": 32,
            "outbox_max_bytes": 1_048_576,
            "storage_policy": {
                "max_rule_configs": 8,
                "max_dimension_groups": 4,
                "max_window_events": 4,
                "max_label_bytes": 8192,
                "max_alert_rows": 8,
                "alert_retention_seconds": 86_400,
            },
            "rules": [rule("quote-alert-a", "target-a"), rule("quote-alert-b", "target-b")],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _monitor_host_process(
    control,
    endpoint: str,
    registry_path: str,
    outbox_path: str,
    server_public: bytes,
    server_secret: bytes,
    client_a_public: bytes,
    client_b_public: bytes,
    config_document: bytes,
) -> None:
    host: ServiceHost | None = None
    provider = None
    outbox: DurableOutbox | None = None
    service_identity = WireIdentity(WireRole.SERVICE, "dim08-threshold-service", _SERVICE_ID)
    identity_a = WireIdentity(WireRole.SERVICE, "principal-a", "strategy-a")
    identity_b = WireIdentity(WireRole.SERVICE, "principal-b", "strategy-b")
    targets = {
        _SCOPE_A: MonitorIngressTarget(_OUTBOX_A, (_SOURCE_EVENT_TYPE,)),
        _SCOPE_B: MonitorIngressTarget(_OUTBOX_B, (_SOURCE_EVENT_TYPE,)),
    }
    trusted_targets = {
        "target-a": TrustedThresholdTarget("target-a", _SCOPE_A, targets[_SCOPE_A]),
        "target-b": TrustedThresholdTarget("target-b", _SCOPE_B, targets[_SCOPE_B]),
    }
    try:
        config = parse_service_config(_service_config(endpoint))
        outbox = DurableOutbox(outbox_path)

        def base_resource_provider(component, instance_id, options, _scope):
            if component != "monitor" or instance_id != _MONITOR_INSTANCE:
                raise AssertionError("unexpected component resource request")
            return ComponentResources.borrowed(
                {"outbox": outbox, "targets": targets}, ready=lambda: True
            )

        provider = create_monitor_threshold_resource_provider(
            base_resource_provider,
            monitor_instance_id=_MONITOR_INSTANCE,
            config_document=config_document,
            trusted_targets=trusted_targets,
            startup_timeout_seconds=15.0,
            shutdown_timeout_seconds=5.0,
            poll_interval_seconds=0.05,
        )
        grants = {
            "principal-a": (
                PrincipalGrant(
                    identity_a,
                    (
                        ResourceGrant(
                            _MONITOR_INSTANCE,
                            "service.telemetry_ingress",
                            _MONITOR_SCOPE_REF,
                            _SCOPE_A,
                            _OPERATIONS,
                        ),
                    ),
                ),
            ),
            "principal-b": (
                PrincipalGrant(
                    identity_b,
                    (
                        ResourceGrant(
                            _MONITOR_INSTANCE,
                            "service.telemetry_ingress",
                            _MONITOR_SCOPE_REF,
                            _SCOPE_B,
                            _OPERATIONS,
                        ),
                    ),
                ),
            ),
        }
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=service_identity,
                scope=_SERVICE_SCOPE,
                scope_ref=_SERVICE_SCOPE_REF,
                scope_version="dim08-threshold-scope-v1",
                security_ref=_SECURITY_REF,
                security_version="dim08-threshold-curve-v1",
                registration_policy_ref=_REGISTRATION_REF,
                registration_policy_version="dim08-threshold-registration-v1",
                service_requirements_ref=_REQUIREMENTS_REF,
                service_requirements_version="dim08-threshold-ready-v1",
                curve_public_key=server_public,
                curve_secret_key=server_secret,
                principals={client_a_public: "principal-a", client_b_public: "principal-b"},
                grants=grants,
            ),
            registry_path,
            provider,
        )
        host.start()
        control.send(("ready", host.endpoint, dict(provider.status)))
        while True:
            host.serve_once(timeout_ms=10)
            if not control.poll():
                continue
            command = control.recv()
            if command[0] == "status":
                with sqlite3.connect(outbox_path) as connection:
                    alert_rows = connection.execute(
                        """SELECT scope, COUNT(*) FROM monitor_outbox_events
                           WHERE event_type=? GROUP BY scope""",
                        (THRESHOLD_ALERT_EVENT_TYPE,),
                    ).fetchall()
                    delivery_rows = connection.execute(
                        """SELECT delivery_state, COUNT(*) FROM monitor_threshold_alert_outbox
                           GROUP BY delivery_state"""
                    ).fetchall()
                    checkpoints = connection.execute(
                        """SELECT scope, sequence FROM monitor_consumer_checkpoints
                           WHERE consumer_id=?""",
                        (_CONSUMER_ID,),
                    ).fetchall()
                control.send(
                    (
                        "status",
                        {
                            "worker": dict(provider.status),
                            "host_state": host.state,
                            "alert_events": dict(alert_rows),
                            "delivery_states": dict(delivery_rows),
                            "checkpoints": dict(checkpoints),
                        },
                    )
                )
            elif command[0] == "close":
                host.close()
                control.send(("closed", host.state, dict(provider.status)))
                return
            else:
                raise AssertionError("unsupported threshold integration command")
    except BaseException as error:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        control.close()


def _client_runtime(
    endpoint: str,
    *,
    identity: WireIdentity,
    scope: WireScope,
    server_public: bytes,
    client_public: bytes,
    client_secret: bytes,
) -> ServiceClientRuntime:
    config = parse_service_config(_client_config(endpoint))
    return ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _scope_ref: scope,
        peer_resolver=lambda _service_id: WireIdentity(
            WireRole.SERVICE, "dim08-threshold-service", _SERVICE_ID
        ),
        transport_factory=ZmqTransportFactory(
            {
                _SECURITY_REF: ClientCurveKeys(
                    server_key=server_public,
                    public_key=client_public,
                    secret_key=client_secret,
                )
            }
        ),
        requested_operations={"upstream.telemetry": _OPERATIONS},
    )


def _append_quote(
    runtime: ServiceClientRuntime,
    event_id: str,
    instrument_id: str,
    *,
    labels: dict[str, str],
):
    data = {
        "instrument_key": {"instrument_id": instrument_id},
        "order_id": f"synthetic-order:{event_id}",
        **labels,
    }
    return runtime.call(
        "upstream.telemetry",
        "events.append",
        {
            "schema_version": 1,
            "event_id": event_id,
            "event_type": _SOURCE_EVENT_TYPE,
            "occurred_at": time.time(),
            "data": data,
        },
        scope_ref=_MONITOR_SCOPE_REF,
    )


def _host_command(record, *command):
    process = record["process"]
    assert process.is_alive(), "DIM08 threshold ServiceHost exited before command"
    record["pipe"].send(command)
    result = _receive(record["pipe"], timeout=10.0)
    assert result[0] != "error", result
    return result


def _host_status(record) -> dict[str, Any]:
    result = _host_command(record, "status")
    assert result[0] == "status", result
    return result[1]


def _wait_status(record, predicate, timeout_seconds: float = 15.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = _host_status(record)
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f"threshold worker did not reach the expected durable state: {last}")


def _normal_host_close(record) -> tuple[Any, ...]:
    record["pipe"].send(("close",))
    result = _receive(record["pipe"], timeout=15.0)
    assert result[0] == "closed" and result[1] == "closed", result
    assert result[2]["closed"] is True and result[2]["ready"] is False, result
    process = record["process"]
    process.join(15)
    assert not process.is_alive(), "threshold ServiceHost process did not exit normally"
    assert process.exitcode == 0, f"threshold ServiceHost exit={process.exitcode}"
    record["pipe"].close()
    process.close()
    record["closed"] = True
    return result


def test_servicehost_threshold_consumer_restarts_idempotently_with_quota_and_scope_isolation(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_a_public, client_a_secret = zmq.curve_keypair()
    client_b_public, client_b_secret = zmq.curve_keypair()
    database = tmp_path / "dim08-threshold.sqlite"
    registry = tmp_path / "dim08-threshold-registry.sqlite"
    process = None
    runtime_a = runtime_b = denied_runtime = None
    cleanup_failures: list[str] = []
    forced_cleanup: list[str] = []
    primary_error: BaseException | None = None

    try:
        process = _start_process(
            context,
            _monitor_host_process,
            (
                endpoint,
                str(registry),
                str(database),
                server_public,
                server_secret,
                client_a_public,
                client_b_public,
                _threshold_config(),
            ),
            "dim08-threshold-monitor-host",
        )
        ready = _receive(process["pipe"], timeout=20.0)
        assert ready[0] == "ready" and ready[1] == endpoint, ready
        initial_worker = ready[2]
        assert initial_worker["ready"] is True and initial_worker["start_count"] == 1
        initial_pid = initial_worker["process_id"]
        assert type(initial_pid) is int and initial_pid > 0

        identity_a = WireIdentity(WireRole.SERVICE, "principal-a", "strategy-a")
        identity_b = WireIdentity(WireRole.SERVICE, "principal-b", "strategy-b")
        runtime_a = _client_runtime(
            endpoint,
            identity=identity_a,
            scope=_SCOPE_A,
            server_public=server_public,
            client_public=client_a_public,
            client_secret=client_a_secret,
        )
        registration_a = runtime_a.start()
        assert registration_a.degraded_count == 0
        # The SQLite trigger creates a deterministic lost-delivery-ACK fault:
        # alert append commits, then its delivered receipt fails.  The actual
        # spawned child exits nonzero and the production supervisor restarts
        # it. Install the fault before appending the source event so the worker
        # cannot race ahead and successfully deliver the alert first.
        with sqlite3.connect(database) as connection:
            connection.execute(
                """CREATE TRIGGER fail_threshold_delivery
                   BEFORE UPDATE OF delivery_state ON monitor_threshold_alert_outbox
                   WHEN NEW.delivery_state='DELIVERED'
                   BEGIN SELECT RAISE(ABORT, 'controlled delivery receipt failure'); END"""
            )
        first = _append_quote(
            runtime_a,
            "quote-a-1",
            "contract-a",
            labels={"group_ref": "group-a-1"},
        )
        assert type(first["sequence"]) is int
        runtime_a.close()
        runtime_a = None
        crashed = _wait_status(
            process,
            lambda state: (
                state["worker"]["restart_count"] >= 1
                and state["worker"]["last_exit_code"] not in (None, 0)
                and state["alert_events"].get(_OUTBOX_A, 0) == 1
                and state["delivery_states"].get("PENDING", 0) == 1
            ),
        )
        assert crashed["worker"]["last_failure_code"] == "PERSISTENCE_FAILURE"
        assert crashed["worker"]["process_id"] != initial_pid
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER fail_threshold_delivery")

        recovered = _wait_status(
            process,
            lambda state: (
                state["worker"]["ready"] is True
                and state["worker"]["start_count"] >= 2
                and state["delivery_states"].get("PENDING", 0) == 0
                and state["delivery_states"].get("DELIVERED", 0) == 1
                and state["alert_events"].get(_OUTBOX_A, 0) == 1
                and state["checkpoints"].get(_OUTBOX_A, 0) >= first["sequence"]
            ),
        )
        assert recovered["worker"]["process_id"] not in (None, initial_pid)

        runtime_a = _client_runtime(
            endpoint,
            identity=identity_a,
            scope=_SCOPE_A,
            server_public=server_public,
            client_public=client_a_public,
            client_secret=client_a_secret,
        )
        assert runtime_a.start().degraded_count == 0
        second_a = _append_quote(
            runtime_a,
            "quote-a-2",
            "contract-a",
            labels={"group_ref": "group-a-2"},
        )
        runtime_a.close()
        runtime_a = None

        # Authenticated Registry scope A cannot register with B's exact
        # account/strategy scope, even though both targets share one ServiceHost.
        denied_runtime = _client_runtime(
            endpoint,
            identity=identity_a,
            scope=_SCOPE_B,
            server_public=server_public,
            client_public=client_a_public,
            client_secret=client_a_secret,
        )
        with pytest.raises(ServiceRuntimeError) as denied:
            denied_runtime.start()
        assert denied.value.code == "SCOPE_DENIED"
        denied_runtime.close()
        denied_runtime = None

        runtime_b = _client_runtime(
            endpoint,
            identity=identity_b,
            scope=_SCOPE_B,
            server_public=server_public,
            client_public=client_b_public,
            client_secret=client_b_secret,
        )
        assert runtime_b.start().degraded_count == 0
        second = _append_quote(
            runtime_b,
            "quote-b-1",
            "contract-b",
            labels={"resource_pool_ref": "pool-b-1"},
        )
        assert type(second["sequence"]) is int and second["sequence"] > first["sequence"]
        second_b = _append_quote(
            runtime_b,
            "quote-b-2",
            "contract-b",
            labels={"resource_pool_ref": "pool-b-2"},
        )
        assert type(second_b["sequence"]) is int and second_b["sequence"] > second["sequence"]
        runtime_b.close()
        runtime_b = None

        both_alerts = _wait_status(
            process,
            lambda state: (
                state["alert_events"].get(_OUTBOX_A, 0) == 2
                and state["alert_events"].get(_OUTBOX_B, 0) == 2
                and state["delivery_states"].get("DELIVERED", 0) == 4
            ),
        )
        assert both_alerts["checkpoints"].get(_OUTBOX_A, 0) >= second_a["sequence"]
        assert both_alerts["checkpoints"].get(_OUTBOX_B, 0) >= second_b["sequence"]

        # Per-rule cardinality is a production fail-closed quota.  The new
        # label remains pending and cannot produce another alert.
        runtime_a = _client_runtime(
            endpoint,
            identity=identity_a,
            scope=_SCOPE_A,
            server_public=server_public,
            client_public=client_a_public,
            client_secret=client_a_secret,
        )
        assert runtime_a.start().degraded_count == 0
        overflow = _append_quote(
            runtime_a,
            "quote-a-overflow",
            "contract-a",
            labels={"group_ref": "group-a-overflow"},
        )
        runtime_a.close()
        runtime_a = None
        quota_state = _wait_status(
            process,
            lambda state: (
                state["worker"]["state"] == "restart_limit"
                and state["worker"]["last_failure_code"] == "RULE_OR_CAPACITY_FAILURE"
                and state["worker"]["restart_count"] >= 1
                and state["alert_events"].get(_OUTBOX_A, 0) == 2
                and state["alert_events"].get(_OUTBOX_B, 0) == 2
            ),
            timeout_seconds=20.0,
        )
        assert quota_state["checkpoints"].get(_OUTBOX_A, 0) < overflow["sequence"]

        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM monitor_outbox_events WHERE event_type=? AND scope=?",
                (THRESHOLD_ALERT_EVENT_TYPE, _OUTBOX_A),
            ).fetchone()[0] == 2
            assert connection.execute(
                "SELECT COUNT(*) FROM monitor_outbox_events WHERE event_type=? AND scope=?",
                (THRESHOLD_ALERT_EVENT_TYPE, _OUTBOX_B),
            ).fetchone()[0] == 2
            assert connection.execute(
                "SELECT COUNT(*) FROM monitor_threshold_rule_groups"
            ).fetchone()[0] == 4
            assert connection.execute(
                "SELECT COUNT(*) FROM monitor_outbox_events WHERE event_id LIKE ?",
                ("service-event:%",),
            ).fetchone()[0] == 5
            dimensions = connection.execute(
                """SELECT scope, dimensions_json FROM monitor_threshold_alert_outbox
                   WHERE delivery_state='DELIVERED' ORDER BY scope, dimensions_json"""
            ).fetchall()
        dimensions_by_scope: dict[str, list[dict[str, str]]] = {}
        for scope, encoded in dimensions:
            dimensions_by_scope.setdefault(scope, []).append(json.loads(encoded))
        assert dimensions_by_scope[_OUTBOX_A] == [
            {
                "group_ref": "group-a-1",
                "instrument_key": '{"instrument_id":"contract-a"}',
            },
            {
                "group_ref": "group-a-2",
                "instrument_key": '{"instrument_id":"contract-a"}',
            },
        ]
        assert dimensions_by_scope[_OUTBOX_B] == [
            {
                "instrument_key": '{"instrument_id":"contract-b"}',
                "resource_pool_ref": "pool-b-1",
            },
            {
                "instrument_key": '{"instrument_id":"contract-b"}',
                "resource_pool_ref": "pool-b-2",
            },
        ]
        assert all(
            "order_id" not in dimensions
            for scoped_dimensions in dimensions_by_scope.values()
            for dimensions in scoped_dimensions
        )
    except BaseException as error:
        primary_error = error
        raise
    finally:
        for runtime in (runtime_a, runtime_b, denied_runtime):
            if runtime is not None and runtime.state not in {"closed", "failed", "close_failed"}:
                try:
                    runtime.close()
                except Exception:
                    cleanup_failures.append("CLIENT_RUNTIME_CLOSE_FAILED")
        if process is not None and not process["closed"]:
            try:
                closed = _host_command(process, "close")
                assert closed[0:2] == ("closed", "closed"), closed
                assert closed[2]["closed"] is True and closed[2]["ready"] is False, closed
                process["process"].join(15)
                if process["process"].is_alive() or process["process"].exitcode != 0:
                    cleanup_failures.append("HOST_PROCESS_EXIT_UNCONFIRMED")
                else:
                    process["pipe"].close()
                    process["process"].close()
                    process["closed"] = True
            except Exception:
                _bounded_reap_spawned_process(process, forced_cleanup, cleanup_failures)
        if primary_error is not None:
            with suppress(Exception):
                primary_error.dim08_threshold_cleanup = {
                    "failures": tuple(cleanup_failures),
                    "forced": tuple(forced_cleanup),
                }
        elif cleanup_failures or forced_cleanup:
            raise AssertionError(
                f"DIM08 threshold integration cleanup failed: "
                f"failures={tuple(cleanup_failures)}, forced={tuple(forced_cleanup)}"
            )
