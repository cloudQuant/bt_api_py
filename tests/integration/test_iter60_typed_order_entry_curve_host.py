"""Iteration 60 R2-B: typed order entry across a real local CURVE subprocess host.

The host is a spawned ``ServiceHost`` whose execution ``account_owner``
component declares ``command_bindings`` in its service config; the client is
assembled by ``assemble_service_execution`` from a client config whose
``broker.orders`` route declares the same ``command_binding``. Both sides
derive the binding through the single SDK derivation. Requests and replies
cross real CURVE-authenticated ZMQ sockets with strict v3 MessagePack
envelopes. INTEGRATION_SIM: local subprocess, temp SQLite, synthetic provider;
no real account, provider or network.
"""

from __future__ import annotations

import multiprocessing
import socket
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

zmq = pytest.importorskip("zmq")

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope  # noqa: E402

from bt_api_py.service_command_wire import build_submit_command  # noqa: E402
from bt_api_py.service_execution import (  # noqa: E402
    ServiceExecutionError,
    ServiceExecutionUnknownOutcomeError,
)
from bt_api_py.service_execution_assembly import (  # noqa: E402
    ServiceExecutionAssemblyError,
    assemble_service_execution,
)
from tests.unit._typed_command_bindings import declaration  # noqa: E402

_DOMAIN = "paper:typed-curve"
_SERVICE_ID = "typed-curve-execution"
_SERVICE_PRINCIPAL = "typed-curve-execution-principal"
_INSTANCE_ID = "typed-curve-owner"
_ACCOUNT = "typed-curve-account"
_STRATEGY = "typed-curve-strategy"
_PRINCIPAL = "typed-curve-strategy-principal"
_ORDER_SCOPE_REF = "typed-curve-orders-v1"
_QUERY_SCOPE_REF = "typed-curve-order-query-v1"
_TRANSPORT_REF = "typed-curve-rpc-v1"
_SERVER_SECURITY_REF = "typed-curve-server-v1"
_CLIENT_SECURITY_REF = "typed-curve-client-v1"
_ORDER_OPERATIONS = frozenset({"orders.submit", "orders.cancel"})
_QUERY_OPERATIONS = frozenset({"orders.query"})
_WIRE_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_DECLARATION = declaration(
    provider="paper",
    venue="paper",
    environment="simulation",
    account_id=_ACCOUNT,
    instruments=("BTC-USDT", "ETH-USDT"),
    price_currency="USDT",
)


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _service_config(endpoint: str, command_bindings: Any) -> dict[str, Any]:
    options: dict[str, Any] = {"role": "account_owner"}
    if command_bindings is not None:
        options["command_bindings"] = command_bindings
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": "typed-curve-service-v1",
        "transport_ref": "typed-curve-listener-v1",
        "registration_policy_ref": "typed-curve-registration-v1",
        "service_requirements_ref": "typed-curve-requirements-v1",
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE_ID,
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": options,
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "typed-curve-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SERVER_SECURITY_REF,
                    "options": {"high_water_mark": 32, "max_message_bytes": 65_536},
                }
            },
        },
    }


def _client_config(endpoint: str, command_binding: Any) -> dict[str, Any]:
    def route(mount: str, scope_ref: str) -> dict[str, Any]:
        return {
            "mount": mount,
            "service_id": _SERVICE_ID,
            "instance_id": _INSTANCE_ID,
            "scope_ref": scope_ref,
            "transport_ref": _TRANSPORT_REF,
            "account_ref": _ACCOUNT,
        }

    writer = route("broker.orders", _ORDER_SCOPE_REF)
    if command_binding is not None:
        writer["command_binding"] = command_binding
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": _STRATEGY},
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {"bindings": [writer, route("broker.order_query", _QUERY_SCOPE_REF)]},
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": _CLIENT_SECURITY_REF,
                }
            },
        },
    }


class _Provider:
    """Synthetic dispatch: accepts, or leaves the outcome unknown on request."""

    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.cancelled: list[str] = []

    def submit(self, intent: Any) -> Any:
        from bt_api_execution import ProviderObservation

        self.submitted.append(intent.intent_id)
        if "unknown" in intent.intent_id:
            raise TimeoutError("synthetic provider outcome is unknown")
        return ProviderObservation.accepted(intent.intent_id, "provider-" + intent.intent_id)

    def cancel(self, intent: Any) -> Any:
        from bt_api_execution import CancelObservation

        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


def _serve(control: Any, endpoint: str, directory: str, keys: dict, command_bindings: Any) -> None:
    from bt_api_execution import ExecutionScope, SqliteExecutionStore

    from bt_api_py.service_components import ComponentResources
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

    provider = _Provider()
    store = None
    host = None
    try:
        config = parse_service_config(_service_config(endpoint, command_bindings))
        authority = TrustedServiceAuthority(
            identity=WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID),
            scope=WireScope(_DOMAIN),
            scope_ref=config.scope_ref,
            scope_version="typed-curve-scope-v1",
            security_ref=_SERVER_SECURITY_REF,
            security_version="curve-local-v1",
            registration_policy_ref=config.registration_policy_ref,
            registration_policy_version="typed-curve-policy-v1",
            service_requirements_ref=config.service_requirements_ref,
            service_requirements_version="typed-curve-requirements-v1",
            curve_public_key=keys["server_public"],
            curve_secret_key=keys["server_secret"],
            principals={keys["strategy_public"]: _PRINCIPAL},
            grants={
                _PRINCIPAL: (
                    PrincipalGrant(
                        WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY),
                        (
                            ResourceGrant(
                                _INSTANCE_ID,
                                "service.order_entry",
                                _ORDER_SCOPE_REF,
                                _WIRE_SCOPE,
                                _ORDER_OPERATIONS,
                            ),
                            ResourceGrant(
                                _INSTANCE_ID,
                                "service.order_query",
                                _QUERY_SCOPE_REF,
                                _WIRE_SCOPE,
                                _QUERY_OPERATIONS,
                            ),
                        ),
                    ),
                )
            },
        )
        store = SqliteExecutionStore(str(Path(directory) / "execution.sqlite3"))
        scope = ExecutionScope("PAPER", "simulation", _ACCOUNT, _STRATEGY, "20261104")

        def resource_provider(component: str, *_args: Any) -> Any:
            if component != "execution":
                raise RuntimeError("UNEXPECTED_COMPONENT")
            return ComponentResources.borrowed(
                {
                    "store": store,
                    "targets": {_WIRE_SCOPE: scope},
                    "dispatch": provider,
                    "admission": {"allow_unprotected": True},
                },
                ready=lambda: True,
            )

        host = ServiceHost(
            config, authority, str(Path(directory) / "registry.sqlite3"), resource_provider
        )
        host.start()
        control.send(("ready", host.endpoint))
        while True:
            host.serve_once(timeout_ms=20)
            if control.poll(0):
                control.recv()
                host.close()
                control.send(("closed", list(provider.submitted), list(provider.cancelled)))
                return
    except BaseException as error:  # pragma: no cover - reported to the parent
        code = getattr(error, "code", None)
        try:
            control.send(("error", code if type(code) is str else type(error).__name__))
        except Exception:
            pass
        if host is not None and host.state not in {"closed", "failed"}:
            try:
                host.close()
            except BaseException:
                pass
    finally:
        if store is not None:
            store.close()
        control.close()


class _Host:
    def __init__(self, tmp_path: Path, command_bindings: Any) -> None:
        self.keys = {}
        self.keys["server_public"], self.keys["server_secret"] = zmq.curve_keypair()
        self.keys["strategy_public"], self.keys["strategy_secret"] = zmq.curve_keypair()
        self.endpoint = _endpoint()
        context = multiprocessing.get_context("spawn")
        self.pipe, child = context.Pipe(duplex=True)
        self.process = context.Process(
            target=_serve,
            args=(child, self.endpoint, str(tmp_path), self.keys, command_bindings),
            name="typed-curve-execution-host",
        )
        self.process.start()
        child.close()
        if not self.pipe.poll(60.0):
            self.kill()
            raise AssertionError("host start timeout")
        self.ready = self.pipe.recv()

    def close(self) -> tuple[list[str], list[str]]:
        self.pipe.send(("close",))
        assert self.pipe.poll(60.0), "host close timeout"
        result = self.pipe.recv()
        self.process.join(30.0)
        assert result[0] == "closed", result
        assert self.process.exitcode == 0
        return result[1], result[2]

    def kill(self) -> None:
        self.process.join(1.0)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(5.0)


def _runtime_factory(keys: dict):
    from bt_api_py.service_runtime import (
        ClientCurveKeys,
        ServiceClientRuntime,
        ZmqTransportFactory,
    )

    def factory(config: Any) -> Any:
        return ServiceClientRuntime(
            config,
            identity=WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY),
            scope_resolver=lambda reference: {
                _ORDER_SCOPE_REF: _WIRE_SCOPE,
                _QUERY_SCOPE_REF: _WIRE_SCOPE,
            }[reference],
            peer_resolver=lambda _service_id: WireIdentity(
                WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID
            ),
            transport_factory=ZmqTransportFactory(
                {
                    _CLIENT_SECURITY_REF: ClientCurveKeys(
                        keys["server_public"], keys["strategy_public"], keys["strategy_secret"]
                    )
                }
            ),
            run_id="typed-curve-run-" + uuid.uuid4().hex[:8],
            requested_operations={
                "broker.orders": _ORDER_OPERATIONS,
                "broker.order_query": _QUERY_OPERATIONS,
            },
        )

    return factory


def _submit_kwargs(intent_id: str, **changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = dict(
        intent_id=intent_id,
        signal_id="signal-" + intent_id,
        instrument="BTC-USDT",
        side="BUY",
        position_effect="OPEN",
        order_type="LIMIT",
        quantity=Decimal("2"),
        price=Decimal("50000"),
        metadata_version="typed-curve-metadata-v1",
    )
    values.update(changes)
    return values


@pytest.mark.integration
def test_typed_order_entry_over_curve_subprocess_host(tmp_path):
    host = _Host(tmp_path, [_DECLARATION])
    assert host.ready[0] == "ready", host.ready
    assembly = None
    try:
        assembly = assemble_service_execution(
            _client_config(host.endpoint, _DECLARATION),
            runtime_factory=_runtime_factory(host.keys),
        )
        assembly.claim_owned_runtimes()
        assembly.start()
        client = assembly.execution_client

        # Submit: one typed part, typed result bound to the durable record.
        first = client.submit(**_submit_kwargs("intent-1"))
        assert first.provider_order_id == "provider-intent-1"
        assert first.state not in {"UNKNOWN", "REJECTED"}, first.state

        # Duplicate submit (new request id, same content): replayed, no redispatch.
        replay = client.submit(**_submit_kwargs("intent-1"))
        assert replay.intent_id == "intent-1"
        assert replay.provider_order_id == first.provider_order_id

        # Same key, different content: INTENT_CONFLICT, nothing dispatched.
        with pytest.raises(ServiceExecutionError) as conflict:
            client.submit(**_submit_kwargs("intent-1", quantity=Decimal("3")))
        assert conflict.value.code == "INTENT_CONFLICT"

        # Cancel: the instrument is remembered from the typed submit.
        cancelled = client.cancel(
            cancel_id="cancel-1",
            target_intent_id="intent-1",
            provider_order_id="provider-intent-1",
            metadata_version="typed-curve-metadata-v1",
        )
        assert cancelled.cancel_id == "cancel-1"

        # Unknown provider outcome: typed result is ``unknown`` and maps to
        # the reconcile-before-retry error, never to acceptance.
        binding = client._command_binding
        scope = client._command_scope()
        command = build_submit_command(
            binding,
            client.build_submit_payload(**_submit_kwargs("intent-unknown-1")),
            scope=scope,
        )
        with pytest.raises(ServiceExecutionUnknownOutcomeError):
            client.submit_command(command)
        view = client.submit(**_submit_kwargs("intent-unknown-2"))
        assert view.state == "UNKNOWN"

        # A generic SDK map on the typed host is refused before any work.
        generic = assembly.runtime.call(
            "broker.orders",
            "orders.submit",
            dict(client.build_submit_payload(**_submit_kwargs("intent-generic"))),
            scope_ref=_ORDER_SCOPE_REF,
        )
        assert (generic["ok"], generic["code"]) == (False, "COMMAND_TYPED_REQUIRED")
    finally:
        if assembly is not None and assembly.state not in {"unclaimed", "released"}:
            assembly.close()
        submitted, cancelled_ids = host.close()
    assert submitted == ["intent-1", "intent-unknown-1", "intent-unknown-2"]
    assert cancelled_ids == ["cancel-1"]


@pytest.mark.integration
def test_curve_host_refuses_to_start_on_a_foreign_binding_declaration(tmp_path):
    foreign = declaration(
        provider="paper",
        venue="paper",
        environment="simulation",
        account_id="another-account",
        instruments=("BTC-USDT",),
    )
    host = _Host(tmp_path, [foreign])
    try:
        assert host.ready == ("error", "COMMAND_BINDING_MISSING")
    finally:
        host.kill()


def test_client_without_writer_binding_cannot_assemble():
    with pytest.raises(ServiceExecutionAssemblyError) as error:
        assemble_service_execution(
            _client_config("tcp://127.0.0.1:1", None),
            runtime_factory=lambda _config: pytest.fail("runtime must not be created"),
        )
    assert error.value.code == "COMMAND_BINDING_REQUIRED"
