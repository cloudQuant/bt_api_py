"""Real CURVE/ServiceHost coverage for durable account-consumer progress.

The fixture uses only synthetic journal events and generated local keys. Two
strategy runtimes share one authenticated principal and account view while the
gateway keeps their delivery progress separate across a normal host restart.
"""

from __future__ import annotations

import multiprocessing
import socket
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import zmq
from bt_api_base.service_protocol import (
    WireEnvelope,
    WireHeader,
    WireIdentity,
    WireMessageKind,
    WireRole,
    WireScope,
)
from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
from bt_api_gateway.service_component import GatewayServiceAccountScope

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory

_SERVICE_ID = "synthetic-account-consumer-service"
_SERVICE_PRINCIPAL = "synthetic-account-consumer-service-principal"
_INSTANCE_ID = "synthetic-account-consumer-gateway"
_SCOPE_REF = "synthetic-account-consumer-scope-v1"
_LISTENER_REF = "synthetic-account-consumer-listener-v1"
_SECURITY_REF = "synthetic-account-consumer-curve-v1"
_CLIENT_PRINCIPAL = "synthetic-account-consumer-client"
_ACTOR_A = "strategy-consumer-a"
_ACTOR_B = "strategy-consumer-b"
_ACCOUNT_REF = "account-consumer-synthetic"
_DOMAIN_REF = "paper:account-consumer-synthetic"
_JOURNAL_ACCOUNT_SCOPE = "journal:account-consumer-synthetic"
_SHARED_FACT_SCOPE = "journal:account-consumer-shared-facts"
_REGISTRATION_POLICY_REF = "policy:account-consumer-v1"
_REQUIREMENTS_REF = "requirements:account-consumer-v1"
_MAX_MESSAGE_BYTES = 65_536
_REQUEST_TIMEOUT_MS = 3_000
_CONSUMER_OPERATIONS = frozenset({"account.read_after", "account.ack", "account.progress"})


def _available_tcp_endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"tcp://127.0.0.1:{port}"


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": "service-root-account-consumer-v1",
        "transport_ref": _LISTENER_REF,
        "registration_policy_ref": _REGISTRATION_POLICY_REF,
        "service_requirements_ref": _REQUIREMENTS_REF,
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE_ID,
                    "bind_ports": ["service.account_read"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": "synthetic-account-session-v1",
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _LISTENER_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SECURITY_REF,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _client_config(endpoint: str, strategy_ref: str):
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": strategy_ref},
            "clients": {
                "gateway": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "store.account",
                                "service_id": _SERVICE_ID,
                                "instance_id": _INSTANCE_ID,
                                "scope_ref": _SCOPE_REF,
                                "account_ref": _ACCOUNT_REF,
                                "transport_ref": "client-curve-v1",
                            }
                        ],
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "heartbeat_interval_seconds": 5,
                        },
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "client-curve-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": _SECURITY_REF,
                        "options": {
                            "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    }
                },
            },
        }
    )


def _wire_scope(strategy_ref: str) -> WireScope:
    return WireScope(
        _DOMAIN_REF,
        account_ref=_ACCOUNT_REF,
        strategy_ref=strategy_ref,
    )


def _grant(strategy_ref: str) -> PrincipalGrant:
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, _CLIENT_PRINCIPAL, strategy_ref)
    resource = ResourceGrant(
        _INSTANCE_ID,
        "service.account_read",
        _SCOPE_REF,
        _wire_scope(strategy_ref),
        _CONSUMER_OPERATIONS,
    )
    return PrincipalGrant(identity, (resource,))


def _resource_provider(journal_path: str):
    def provider(component, _instance_id, _options, _scope):
        if component != "gateway":
            raise AssertionError("the fixture exposes only the gateway account reader")
        journal = GatewayEventJournal(journal_path)
        account = GatewayServiceAccountScope(
            account_ref=_ACCOUNT_REF,
            execution_domain_ref=_DOMAIN_REF,
            journal_account_scope=_JOURNAL_ACCOUNT_SCOPE,
            shared_account_facts_scope=_SHARED_FACT_SCOPE,
            shared_fact_strategy_refs=frozenset({_ACTOR_A, _ACTOR_B}),
        )
        principal = GatewayPrincipal(
            principal_id=_CLIENT_PRINCIPAL,
            account_scopes=frozenset({_JOURNAL_ACCOUNT_SCOPE}),
            strategy_scopes=frozenset({_SHARED_FACT_SCOPE}),
            allowed_kinds=frozenset({GatewayCommandKind.READ}),
        )
        return ComponentResources.borrowed(
            {
                "journal": journal,
                "accounts": {_ACCOUNT_REF: account},
                "principals": {_CLIENT_PRINCIPAL: principal},
            },
            ready=lambda: True,
        )

    return provider


def _service_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    journal_path: str,
    server_public: bytes,
    server_secret: bytes,
    client_public: bytes,
) -> None:
    host = None
    try:
        config = parse_service_config(raw_config)
        grants = {_CLIENT_PRINCIPAL: (_grant(_ACTOR_A), _grant(_ACTOR_B))}
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID),
                scope=WireScope(_DOMAIN_REF),
                scope_ref=config.scope_ref,
                scope_version="service-scope-v1",
                security_ref=_SECURITY_REF,
                security_version="curve-keys-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="policy-version-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="requirements-version-v1",
                curve_public_key=server_public,
                curve_secret_key=server_secret,
                principals={client_public: _CLIENT_PRINCIPAL},
                grants=grants,
            ),
            registry_path,
            _resource_provider(journal_path),
        )
        host.start()
        control.send(("ready", host.endpoint))
        while True:
            if control.poll(0.01):
                command = control.recv()
                if command == "close":
                    break
                raise RuntimeError("unknown local host control")
            host.serve_once(25)
        host.close()
        control.send(("closed", host.state))
    except BaseException as exc:
        if host is not None:
            with suppress(BaseException):
                host.close()
        code = getattr(exc, "code", type(exc).__name__)
        with suppress(Exception):
            control.send(("error", code))
        raise
    finally:
        control.close()


def _seed_journal(path: str) -> None:
    journal = GatewayEventJournal(path, clock=lambda: 1_800_000_000.0)
    for sequence in (1, 2):
        journal.append(
            _JOURNAL_ACCOUNT_SCOPE,
            _SHARED_FACT_SCOPE,
            f"consumer-event-{sequence}",
            "position_update",
            {"sequence": sequence, "source": "synthetic"},
            observed_at=1_800_000_000.0 + sequence,
        )


def _spawn_host(context, endpoint, registry_path, journal_path, server_keys, client_public):
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_service_process,
        args=(
            child,
            _service_config(endpoint),
            registry_path,
            journal_path,
            server_keys[0],
            server_keys[1],
            client_public,
        ),
        name="synthetic-account-consumer-service",
    )
    process.start()
    child.close()
    return {"process": process, "pipe": parent, "closed": False}


def _await_host(record) -> str:
    control = record["pipe"]
    assert control.poll(20), "spawned account service did not report startup"
    message = control.recv()
    assert message[0] == "ready", message
    return message[1]


def _close_host_normally(record) -> None:
    process = record["process"]
    control = record["pipe"]
    control.send("close")
    assert control.poll(15), "spawned account service did not report normal close"
    assert control.recv() == ("closed", "closed")
    process.join(15)
    assert not process.is_alive()
    assert process.exitcode == 0
    process.close()
    control.close()
    record["closed"] = True


def _cleanup_host(record, failures: list[str]) -> None:
    if record is None or record["closed"]:
        return
    process = record["process"]
    control = record["pipe"]
    close_received = False
    if process.is_alive():
        try:
            control.send("close")
            if control.poll(10):
                response = control.recv()
                close_received = response == ("closed", "closed")
            process.join(10)
        except Exception as exc:
            failures.append(f"host_close:{type(exc).__name__}")
    if process.is_alive():
        failures.append("host_required_terminate_fallback")
        process.terminate()
        process.join(5)
    if process.is_alive() and hasattr(process, "kill"):
        failures.append("host_required_kill_fallback")
        process.kill()
        process.join(5)
    if process.exitcode is None:
        failures.append("host_remained_alive_after_bounded_cleanup")
    else:
        if not close_received or process.exitcode != 0:
            failures.append("host_did_not_complete_verified_normal_close")
        process.close()
    with suppress(Exception):
        control.close()


def _new_runtime(endpoint, keys: ClientCurveKeys, strategy_ref: str) -> ServiceClientRuntime:
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, _CLIENT_PRINCIPAL, strategy_ref)
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    config = _client_config(endpoint, strategy_ref)
    return ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _scope_ref: _wire_scope(strategy_ref),
        peer_resolver=lambda _service_id: service_identity,
        transport_factory=ZmqTransportFactory({_SECURITY_REF: keys}),
        run_id=uuid4().hex,
        requested_operations={"store.account": _CONSUMER_OPERATIONS},
    )


def _progress(reply: dict[str, Any]) -> dict[str, Any]:
    progress = reply["consumer_progress"]
    assert set(progress) == {"stream_epoch", "offered_cursor", "acknowledged_cursor"}
    assert isinstance(progress["stream_epoch"], str) and progress["stream_epoch"]
    return progress


def _direct_call(transport, identity, scope, binding, operation, arguments):
    request = WireEnvelope(
        WireHeader(
            protocol_version=2,
            kind=WireMessageKind.REQUEST,
            message_id=uuid4().hex,
            correlation_id=None,
            identity=identity,
            scope=scope,
            operation=operation,
            schema_id="sdk.service.call",
            schema_version=1,
            sent_at_utc=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        ),
        {
            "registration_id": binding.registration_id,
            "service_epoch": binding.service_epoch,
            "instance_id": binding.instance_id,
            "arguments": arguments,
        },
    )
    return transport.request(request, timeout=_REQUEST_TIMEOUT_MS / 1000)


def _close_runtime(runtime, failures: list[str]) -> None:
    if runtime is None or runtime.state == "closed":
        return
    try:
        runtime.close()
    except Exception as exc:
        failures.append(f"runtime_close:{type(exc).__name__}")
    if runtime.state != "closed" or runtime.cleanup_failures or runtime.unregister_uncertainties:
        failures.append("runtime_did_not_close_cleanly")


def test_spawned_curve_host_persists_scoped_account_consumer_progress(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    endpoint = _available_tcp_endpoint()
    registry_path = str(tmp_path / "service-registry.sqlite3")
    journal_path = str(tmp_path / "account-journal.sqlite3")
    _seed_journal(journal_path)

    server_keys = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    client_keys = ClientCurveKeys(server_keys[0], client_public, client_secret)
    host_records = []
    runtimes = []
    direct_transport = None
    cleanup_failures: list[str] = []
    try:
        first_host = _spawn_host(
            context, endpoint, registry_path, journal_path, server_keys, client_public
        )
        host_records.append(first_host)
        assert _await_host(first_host) == endpoint

        actor_a = _new_runtime(endpoint, client_keys, _ACTOR_A)
        actor_b = _new_runtime(endpoint, client_keys, _ACTOR_B)
        runtimes.extend((actor_a, actor_b))
        actor_a_receipt = actor_a.start()
        actor_b_receipt = actor_b.start()
        assert actor_a_receipt.degraded_count == actor_b_receipt.degraded_count == 0
        binding_a = actor_a.bindings[0]
        binding_b = actor_b.bindings[0]
        assert binding_a.operations == binding_b.operations == _CONSUMER_OPERATIONS
        assert binding_a.scope.run_id != binding_b.scope.run_id
        old_service_epoch = binding_a.service_epoch

        # A legacy read is still non-tracking and must not implicitly create a
        # durable consumer. Tracking reads by both strategies use one shared
        # account-facts stream but separate authenticated consumer identities.
        b_untracked = actor_b.call("store.account", "account.read_after", {"cursor": 0, "limit": 1})
        assert "consumer_progress" not in b_untracked
        client_factory = ZmqTransportFactory({_SECURITY_REF: client_keys})
        service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        profile = _client_config(endpoint, _ACTOR_B).communication["profiles"]["client-curve-v1"]
        direct_transport = client_factory(profile, service_identity)
        no_consumer = _direct_call(
            direct_transport,
            WireIdentity(WireRole.STRATEGY_CLIENT, _CLIENT_PRINCIPAL, _ACTOR_B),
            binding_b.scope,
            binding_b,
            "account.progress",
            {},
        )
        assert no_consumer.payload["ok"] is False
        assert no_consumer.payload["code"] == "HANDLER_FAILED"

        a_first = actor_a.call(
            "store.account",
            "account.read_after",
            {"cursor": 0, "limit": 1, "track_delivery": True},
        )
        b_first = actor_b.call(
            "store.account",
            "account.read_after",
            {"cursor": 0, "limit": 1, "track_delivery": True},
        )
        assert [event["event_id"] for event in a_first["events"]] == ["consumer-event-1"]
        assert b_first["events"] == a_first["events"]
        progress_a = _progress(a_first)
        progress_b = _progress(b_first)
        assert progress_a["stream_epoch"] == progress_b["stream_epoch"]
        assert progress_a["offered_cursor"] == progress_b["offered_cursor"] == 1
        assert progress_a["acknowledged_cursor"] == progress_b["acknowledged_cursor"] == 0
        stream_epoch = progress_a["stream_epoch"]

        for bad_arguments in (
            {"stream_epoch": "wrong-stream-epoch", "cursor": 1},
            {"stream_epoch": stream_epoch, "cursor": 2},
        ):
            rejected = _direct_call(
                direct_transport,
                WireIdentity(WireRole.STRATEGY_CLIENT, _CLIENT_PRINCIPAL, _ACTOR_A),
                binding_a.scope,
                binding_a,
                "account.ack",
                bad_arguments,
            )
            assert rejected.payload["ok"] is False
            assert rejected.payload["code"] == "HANDLER_FAILED"
            unchanged = _progress(actor_a.call("store.account", "account.progress", {}))
            assert unchanged == {
                "stream_epoch": stream_epoch,
                "offered_cursor": 1,
                "acknowledged_cursor": 0,
            }

        ack_one = actor_a.call(
            "store.account",
            "account.ack",
            {"stream_epoch": stream_epoch, "cursor": 1},
        )
        assert _progress(ack_one) == {
            "stream_epoch": stream_epoch,
            "offered_cursor": 1,
            "acknowledged_cursor": 1,
        }
        assert (
            _progress(
                actor_a.call(
                    "store.account", "account.ack", {"stream_epoch": stream_epoch, "cursor": 0}
                )
            )["acknowledged_cursor"]
            == 1
        )
        assert (
            _progress(actor_b.call("store.account", "account.progress", {}))["acknowledged_cursor"]
            == 0
        )

        cross_scope = _direct_call(
            direct_transport,
            WireIdentity(WireRole.STRATEGY_CLIENT, _CLIENT_PRINCIPAL, _ACTOR_A),
            replace(binding_a.scope, strategy_ref=_ACTOR_B),
            binding_a,
            "account.ack",
            {"stream_epoch": stream_epoch, "cursor": 1},
        )
        assert cross_scope.payload["ok"] is False
        assert cross_scope.payload["code"] == "SCOPE_DENIED"
        assert (
            _progress(actor_a.call("store.account", "account.progress", {}))["acknowledged_cursor"]
            == 1
        )
        assert (
            _progress(actor_b.call("store.account", "account.progress", {}))["acknowledged_cursor"]
            == 0
        )

        # ACK changes consumer metadata only; it does not delete source events
        # or alter the other consumer's offered/acknowledged positions.
        retained = actor_b.call("store.account", "account.read_after", {"cursor": 0, "limit": 10})
        assert [event["event_id"] for event in retained["events"]] == [
            "consumer-event-1",
            "consumer-event-2",
        ]
        assert _progress(actor_b.call("store.account", "account.progress", {})) == {
            "stream_epoch": stream_epoch,
            "offered_cursor": 1,
            "acknowledged_cursor": 0,
        }

        direct_transport.close()
        direct_transport = None
        for runtime in (actor_a, actor_b):
            runtime.close()
            assert runtime.state == "closed"
            assert runtime.cleanup_failures == 0
            assert runtime.unregister_uncertainties == 0
        _close_host_normally(first_host)

        second_host = _spawn_host(
            context, endpoint, registry_path, journal_path, server_keys, client_public
        )
        host_records.append(second_host)
        assert _await_host(second_host) == endpoint
        actor_a_restarted = _new_runtime(endpoint, client_keys, _ACTOR_A)
        actor_b_restarted = _new_runtime(endpoint, client_keys, _ACTOR_B)
        runtimes.extend((actor_a_restarted, actor_b_restarted))
        actor_a_restarted.start()
        actor_b_restarted.start()
        binding_a_restarted = actor_a_restarted.bindings[0]
        assert binding_a_restarted.service_epoch != old_service_epoch
        assert binding_a_restarted.scope.run_id != binding_a.scope.run_id

        persisted_a = _progress(actor_a_restarted.call("store.account", "account.progress", {}))
        persisted_b = _progress(actor_b_restarted.call("store.account", "account.progress", {}))
        assert persisted_a == {
            "stream_epoch": stream_epoch,
            "offered_cursor": 1,
            "acknowledged_cursor": 1,
        }
        assert persisted_b == {
            "stream_epoch": stream_epoch,
            "offered_cursor": 1,
            "acknowledged_cursor": 0,
        }

        resumed = actor_a_restarted.call(
            "store.account",
            "account.read_after",
            {
                "cursor": 1,
                "stream_epoch": stream_epoch,
                "limit": 1,
                "track_delivery": True,
            },
        )
        assert [event["event_id"] for event in resumed["events"]] == ["consumer-event-2"]
        assert _progress(resumed) == {
            "stream_epoch": stream_epoch,
            "offered_cursor": 2,
            "acknowledged_cursor": 1,
        }
        final_ack = actor_a_restarted.call(
            "store.account",
            "account.ack",
            {"stream_epoch": stream_epoch, "cursor": 2},
        )
        assert _progress(final_ack)["acknowledged_cursor"] == 2
        retained_after_ack = actor_a_restarted.call(
            "store.account", "account.read_after", {"cursor": 0, "limit": 10}
        )
        assert [event["event_id"] for event in retained_after_ack["events"]] == [
            "consumer-event-1",
            "consumer-event-2",
        ]
        assert (
            _progress(actor_a_restarted.call("store.account", "account.progress", {}))[
                "acknowledged_cursor"
            ]
            == 2
        )

        for runtime in (actor_a_restarted, actor_b_restarted):
            runtime.close()
            assert runtime.state == "closed"
            assert runtime.cleanup_failures == 0
            assert runtime.unregister_uncertainties == 0
        _close_host_normally(second_host)
    finally:
        if direct_transport is not None:
            with suppress(Exception):
                direct_transport.close()
        for runtime in runtimes:
            _close_runtime(runtime, cleanup_failures)
        for record in host_records:
            _cleanup_host(record, cleanup_failures)
        if cleanup_failures:
            raise AssertionError(f"local service cleanup failed: {tuple(cleanup_failures)}")
