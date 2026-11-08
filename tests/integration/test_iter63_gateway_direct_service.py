"""Synthetic Gateway journal over a real ServiceHost, CURVE and owner thread."""

from __future__ import annotations

import queue
import threading

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

from bt_api_py.gateway_direct_service import (
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
    recovery_requested_operations,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceCallOutcomeUnknownError,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)
from bt_api_py.service_runtime_thread import ThreadedRuntimeError, ThreadedServiceClientRuntime
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.unit.test_iter63_gateway_direct_service import _order, _same_reservation

OPS = frozenset({"direct.reserve", "direct.release", "direct.command_status"})
DOMAIN = "paper:demo"
ACCOUNT = "account-a"
SERVICE = WireIdentity(WireRole.SERVICE, "gateway-principal", "gateway-service")
CLIENT_A = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-a", "strategy-a")
CLIENT_B = WireIdentity(WireRole.STRATEGY_CLIENT, "principal-b", "strategy-b")


def _service_config(endpoint):
    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": SERVICE.actor_ref,
            "scope_ref": "scope-a",
            "transport_ref": "server-transport",
            "registration_policy_ref": "direct-policy-v1",
            "service_requirements_ref": "direct-requirements-v1",
            "components": {
                "gateway": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": "gateway-a",
                        "bind_ports": ["service.direct_write"],
                        "options": {"role": "trading_owner"},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "server-transport": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": endpoint},
                        "security_profile_ref": "security-a",
                    }
                },
            },
        }
    )


def _client_config(endpoint, identity):
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": identity.actor_ref},
            "clients": {
                "gateway": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "broker.direct_write",
                                "service_id": SERVICE.actor_ref,
                                "instance_id": "gateway-a",
                                "scope_ref": "scope-a",
                                "account_ref": ACCOUNT,
                                "transport_ref": "client-transport",
                            }
                        ],
                        "options": {"request_timeout_ms": 2000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "client-transport": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": "security-a",
                    }
                },
            },
        }
    )


def test_real_host_strategy_scope_runclaim_and_lost_reply(tmp_path):
    endpoint = _free_endpoint()
    server_public, server_secret = zmq.curve_keypair()
    a_public, a_secret = zmq.curve_keypair()
    b_public, b_secret = zmq.curve_keypair()
    service_config = _service_config(endpoint)

    def grant(identity):
        return PrincipalGrant(
            identity,
            (
                ResourceGrant(
                    "gateway-a",
                    "service.direct_write",
                    "scope-a",
                    WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref=identity.actor_ref),
                    OPS,
                ),
            ),
        )

    authority = TrustedServiceAuthority(
        identity=SERVICE,
        scope=WireScope(DOMAIN),
        scope_ref="scope-a",
        scope_version="scope-v1",
        security_ref="security-a",
        security_version="security-v1",
        registration_policy_ref="direct-policy-v1",
        registration_policy_version="direct-policy-v1",
        service_requirements_ref="direct-requirements-v1",
        service_requirements_version="direct-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={a_public: CLIENT_A.principal_ref, b_public: CLIENT_B.principal_ref},
        grants={
            CLIENT_A.principal_ref: (grant(CLIENT_A),),
            CLIENT_B.principal_ref: (grant(CLIENT_B),),
        },
    )
    ready = queue.Queue()
    controls = queue.Queue()
    journals = []
    stop = threading.Event()

    def serve():
        host = None
        try:

            def resources(*_):
                journal = GatewayDirectCommandJournal.create(
                    tmp_path / "direct.sqlite", ACCOUNT, generation="g1"
                )
                journals.append(journal)
                binding = GatewayDirectServiceBinding(
                    journal,
                    {
                        CLIENT_A.principal_ref: GatewayDirectGrant("strategy-a", frozenset({"rb"})),
                        CLIENT_B.principal_ref: GatewayDirectGrant("strategy-b", frozenset({"sa"})),
                    },
                )
                return ComponentResources.borrowed({"binding": binding}, ready=lambda: True)

            host = ServiceHost(service_config, authority, tmp_path / "registry.sqlite", resources)
            host.start()
            ready.put(("ready", None))
            while not stop.is_set():
                try:
                    command = controls.get_nowait()
                except queue.Empty:
                    command = None
                if command is not None:
                    command(host)
                host.serve_once(10)
        except BaseException as error:
            ready.put(("error", getattr(error, "code", type(error).__name__)))
        finally:
            if host is not None and host.state == "started":
                host.close()

    host_thread = threading.Thread(target=serve, daemon=True)
    host_thread.start()
    runtime_a = runtime_b = duplicate = historical = recovery = normal_stopped = None
    try:
        assert ready.get(timeout=15) == ("ready", None)
        errors = queue.Queue()
        dropped = queue.Queue()

        def make_runtime(
            identity, public, secret, run_id, drop=False, status_only=False, recover=False
        ):
            def factory():
                transport_factory = ZmqTransportFactory(
                    {"security-a": ClientCurveKeys(server_public, public, secret)}
                )
                if drop:

                    class DropOnce:
                        def __init__(self, wrapped):
                            self.wrapped = wrapped
                            self.dropped = False

                        def request(self, request, *, timeout):
                            reply = self.wrapped.request(request, timeout=timeout)
                            if request.header.operation == "direct.reserve" and not self.dropped:
                                self.dropped = True
                                dropped.put(reply.payload)
                                raise TimeoutError("synthetic lost reply")
                            return reply

                        def close(self):
                            self.wrapped.close()

                    raw_factory = transport_factory

                    def transport_factory(*args):
                        return DropOnce(raw_factory(*args))

                runtime = ServiceClientRuntime(
                    _client_config(endpoint, identity),
                    identity=identity,
                    scope_resolver=lambda _: WireScope(DOMAIN, account_ref=ACCOUNT),
                    peer_resolver=lambda _: SERVICE,
                    transport_factory=transport_factory,
                    run_id=run_id,
                    requested_operations=(
                        recovery_requested_operations()
                        if recover
                        else {"broker.direct_write": frozenset({"direct.command_status"})}
                        if status_only
                        else None
                    ),
                )
                original_start = runtime.start

                def start():
                    try:
                        return original_start()
                    except ServiceRuntimeError as error:
                        errors.put(error.code)
                        raise

                runtime.start = start
                return runtime

            return ThreadedServiceClientRuntime(factory)

        runtime_a = make_runtime(CLIENT_A, a_public, a_secret, "run-a", drop=True)
        runtime_b = make_runtime(CLIENT_B, b_public, b_secret, "run-b")
        runtime_a.start(timeout_seconds=10)
        runtime_b.start(timeout_seconds=10)
        # Storage-only composition must not advertise a nonfunctional dispatch route.
        with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
            runtime_a.call("broker.direct_write", "direct.dispatch", {}, timeout_seconds=10)
        duplicate = make_runtime(CLIENT_A, a_public, a_secret, "other-run")
        with pytest.raises(ThreadedRuntimeError, match="START_FAILED"):
            duplicate.start(timeout_seconds=10)
        assert errors.get(timeout=2) == "DUPLICATE_ACTIVE_RUN"
        args = {"request_key": "key-a", "payload": _order(key="key-a"), "ttl_seconds": 10}
        with pytest.raises(ThreadedRuntimeError, match="CALL_OUTCOME_UNKNOWN"):
            runtime_a.call("broker.direct_write", "direct.reserve", args, timeout_seconds=10)
        lost_reply = dropped.get(timeout=2)
        assert lost_reply["result"]["status"] == "OK", lost_reply
        initial = runtime_a.call(
            "broker.direct_write",
            "direct.command_status",
            {"request_key": "key-a"},
            timeout_seconds=10,
        )
        assert initial["status"] == "OK" and initial["record"]["state"] == "RESERVED", initial
        historical = make_runtime(CLIENT_A, a_public, a_secret, "later-run", status_only=True)
        historical.start(timeout_seconds=10)
        _same_reservation(
            historical.call(
                "broker.direct_write",
                "direct.command_status",
                {"request_key": "key-a"},
                timeout_seconds=10,
            ),
            initial,
        )
        _same_reservation(
            runtime_a.call("broker.direct_write", "direct.reserve", args, timeout_seconds=10),
            initial,
        )
        altered = dict(args, payload=_order(key="key-a", quantity="2"))
        assert (
            runtime_a.call("broker.direct_write", "direct.reserve", altered, timeout_seconds=10)[
                "code"
            ]
            == "REQUEST_CONFLICT"
        )
        assert (
            runtime_b.call(
                "broker.direct_write",
                "direct.command_status",
                {"request_key": "key-a"},
                timeout_seconds=10,
            )["code"]
            == "NOT_FOUND"
        )
        fault_ready = threading.Event()

        def fault_after_commit(_host):
            journal = journals[0]
            original = journal._commit

            def fail_after_commit(db):
                original(db)
                journal._commit = original
                raise RuntimeError("synthetic post-commit fault")

            journal._commit = fail_after_commit
            fault_ready.set()

        controls.put(fault_after_commit)
        assert fault_ready.wait(3)
        with pytest.raises(ServiceCallOutcomeUnknownError, match="BUSINESS_OUTCOME_UNKNOWN"):
            runtime_a.call(
                "broker.direct_write",
                "direct.reserve",
                {
                    "request_key": "key-commit-fault",
                    "payload": _order(key="key-commit-fault"),
                    "ttl_seconds": 10,
                },
                timeout_seconds=10,
            )
        assert (
            runtime_a.call(
                "broker.direct_write",
                "direct.command_status",
                {"request_key": "key-commit-fault"},
                timeout_seconds=10,
            )["record"]["state"]
            == "RESERVED"
        )
        stopped = queue.Queue()

        def stop_strategy(host):
            stopped.put(
                host._registry.stop_strategy_new(
                    WireScope(
                        DOMAIN, account_ref=ACCOUNT, strategy_ref="strategy-a", run_id="run-a"
                    ),
                    "stop-a",
                )
            )

        controls.put(stop_strategy)
        assert stopped.get(timeout=3)["status"] == "applied"
        with pytest.raises(ServiceRuntimeError, match="STRATEGY_STOPPED"):
            runtime_a.call(
                "broker.direct_write",
                "direct.reserve",
                dict(args, request_key="key-after-stop"),
                timeout_seconds=10,
            )
        _same_reservation(
            runtime_a.call(
                "broker.direct_write",
                "direct.command_status",
                {"request_key": "key-a"},
                timeout_seconds=10,
            ),
            initial,
        )
        runtime_a.close(timeout_seconds=10)
        normal_stopped = make_runtime(CLIENT_A, a_public, a_secret, "run-a")
        with pytest.raises(ThreadedRuntimeError, match="START_FAILED"):
            normal_stopped.start(timeout_seconds=10)
        assert errors.get(timeout=2) == "STRATEGY_STOPPED"
        recovery = make_runtime(CLIENT_A, a_public, a_secret, "run-a", recover=True)
        recovery.start(timeout_seconds=10)
        _same_reservation(
            recovery.call(
                "broker.direct_write",
                "direct.command_status",
                {"request_key": "key-a"},
                timeout_seconds=10,
            ),
            initial,
        )
        with pytest.raises(ServiceRuntimeError, match="AMBIGUOUS_OR_MISSING_ROUTE"):
            recovery.call("broker.direct_write", "direct.reserve", args, timeout_seconds=10)
        record = initial["record"]
        release = {
            "request_key": "key-a",
            "reservation_id": record["reservation_id"],
            "digest": record["digest"],
            "expected_version": record["version"],
            "operation_id": "release-a",
        }
        released = recovery.call(
            "broker.direct_write", "direct.release", release, timeout_seconds=10
        )
        assert released["record"]["state"] == "RELEASED"
        assert (
            recovery.call("broker.direct_write", "direct.release", release, timeout_seconds=10)
            == released
        )
        acknowledged = threading.Event()

        def revoke(host):
            host._registry._grants = {CLIENT_B.principal_ref: (grant(CLIENT_B),)}
            acknowledged.set()

        controls.put(revoke)
        assert acknowledged.wait(3)
        with pytest.raises(ServiceRuntimeError, match="DENIED"):
            recovery.call(
                "broker.direct_write",
                "direct.command_status",
                {"request_key": "key-a"},
                timeout_seconds=10,
            )
    finally:
        for runtime in (recovery, normal_stopped, historical, duplicate, runtime_b, runtime_a):
            if runtime is not None:
                runtime.close(timeout_seconds=10)
        stop.set()
        host_thread.join(10)
        assert not host_thread.is_alive()
