"""Authenticated Host composition test for the restricted recovery-close route."""

from __future__ import annotations

from decimal import Decimal
from queue import Empty, Queue
import socket
from threading import Thread
from time import monotonic
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import (
    WireIdentity,
    WireRole,
    WireScope,
)
from bt_api_execution import (
    ExecutionAdmissionValidatorRegistry,
    ExecutionBasicAdmissionGate,
    ExecutionProviderValidatorV1,
    ExecutionScope,
    OrderIntent,
    OrderType,
    PositionEffect,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
    TimeInForce,
)

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_host import (
    ServiceHost,
    TrustedServiceAuthority,
)
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_SERVICE_ID = "recovery-execution-service"
_SERVICE_INSTANCE = "execution-account-a"
_PRINCIPAL = "curve-recovery-principal"
_ACCOUNT = "account-a"
_STRATEGY = "strategy-a"
_DOMAIN = "ctp:paper"
_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_RECOVERY = "orders.submit_recovery_close"
_SUBMIT = "orders.submit"


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    return f"tcp://127.0.0.1:{port}"


def _service_config(endpoint: str):
    from bt_api_py.service_config import parse_service_config

    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": _SERVICE_ID,
            "scope_ref": "service-scope-v1",
            "transport_ref": "listener-v1",
            "registration_policy_ref": "registration-policy-v1",
            "service_requirements_ref": "service-requirements-v1",
            "components": {
                "execution": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": _SERVICE_INSTANCE,
                        "bind_ports": ["service.order_entry"],
                        "options": {"role": "account_owner"},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "listener-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": endpoint},
                        "security_profile_ref": "listener-security-v1",
                        "options": {
                            "high_water_mark": 32,
                            "max_message_bytes": 65_536,
                        },
                    }
                },
            },
        }
    )


def _client_config(endpoint: str):
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": _STRATEGY},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "broker.orders",
                                "service_id": _SERVICE_ID,
                                "instance_id": _SERVICE_INSTANCE,
                                "scope_ref": "account-grant-v1",
                                "account_ref": _ACCOUNT,
                                "transport_ref": "execution-zmq",
                            }
                        ],
                        "options": {"request_timeout_ms": 4_000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "execution-zmq": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": "client-security-v1",
                    }
                },
            },
        }
    )


class _RiskAdmission:
    def __init__(self) -> None:
        self.reserved: list[str] = []
        self.claimed: list[str] = []
        self.settled: list[str] = []

    def reserve(self, intent: OrderIntent) -> str:
        permit_id = f"permit{len(self.reserved) + 1}"
        self.reserved.append(intent.intent_id)
        return permit_id

    def claim_for_dispatch(self, permit_id: str, intent: OrderIntent) -> None:
        assert permit_id.startswith("permit")
        self.claimed.append(intent.intent_id)

    def settle(self, permit_id: str) -> None:
        self.settled.append(permit_id)

    def release(self, _permit_id: str, _reason: str) -> None:
        return None


class _SyntheticRecoveryDispatch:
    def __init__(self, scope: ExecutionScope) -> None:
        self.scope = scope
        self.normal_submits: list[str] = []
        self.recovery_submits: list[str] = []
        self.prepared: list[tuple[str, str]] = []

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        self.normal_submits.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "synthetic-order")

    def submit_authorized(self, intent: OrderIntent, _authorization: object):
        self.normal_submits.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "synthetic-order")

    def cancel(self, _intent: object) -> None:
        return None

    def prepare_recovery_close(
        self,
        *,
        target_intent_id: str,
        recovery_intent_id: str,
        principal_ref: str,
        identity: WireIdentity,
        wire_scope: WireScope,
    ) -> OrderIntent:
        self.prepared.append((target_intent_id, recovery_intent_id))
        assert target_intent_id == "source-open-1"
        assert recovery_intent_id
        assert principal_ref == _PRINCIPAL
        assert identity.role is WireRole.STRATEGY_CLIENT
        assert wire_scope.account_ref == _ACCOUNT
        return OrderIntent(
            intent_id=recovery_intent_id,
            scope=self.scope,
            signal_id=f"signal-{recovery_intent_id}",
            instrument="rb2610",
            side=Side.SELL,
            position_effect=PositionEffect.CLOSE_TODAY,
            order_type=OrderType.MARKET,
            quantity=Decimal("1"),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
            metadata_version="synthetic-v1",
        )

    def submit_recovery_close_authorized(
        self, intent: OrderIntent, _authorization: object, position_proof: object
    ) -> ProviderObservation:
        assert position_proof is not None
        assert intent.reduce_only is True
        assert intent.position_effect is PositionEffect.CLOSE_TODAY
        self.recovery_submits.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "synthetic-close-order")


def test_started_host_serves_only_explicit_recovery_close_while_owner_is_degraded(
    tmp_path,
) -> None:
    endpoint = _endpoint()
    service_config = _service_config(endpoint)
    client_public, client_secret = zmq.curve_keypair()
    server_public, server_secret = zmq.curve_keypair()
    client_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT, _PRINCIPAL, "strategy-client-a"
    )
    service_identity = WireIdentity(
        WireRole.SERVICE, "execution-service-principal", _SERVICE_ID
    )
    scope_template = _SCOPE
    execution_scope = ExecutionScope(
        "SYNTHETIC_CTP", "paper", _ACCOUNT, _STRATEGY, "20261021"
    )
    store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    dispatch = _SyntheticRecoveryDispatch(execution_scope)
    risk = _RiskAdmission()
    basic_validator = ExecutionProviderValidatorV1(
        provider_id=execution_scope.provider,
        validator_id="recovery-host-integration-v1",
        version="1",
        dispatch_port=dispatch,
        instrument_map={"rb2610": "rb2610"},
        quantity_steps={"rb2610": Decimal("1")},
        max_quantities={"rb2610": Decimal("10")},
        price_ticks={},
        supported_order_types=(OrderType.MARKET,),
        supported_time_in_force=(TimeInForce.GTC,),
        supported_actions=((Side.SELL, PositionEffect.CLOSE_TODAY),),
        supports_reduce_only=True,
    )
    basic_gate = ExecutionBasicAdmissionGate(
        store,
        ExecutionAdmissionValidatorRegistry((basic_validator,)),
    )
    owner_ready = [True]
    recovery_ready = [True]
    resources = ComponentResources.borrowed(
        {
            "store": store,
            "targets": {scope_template: execution_scope},
            "dispatch": dispatch,
            "admission": {
                "order_gate": risk,
                "basic_admission_gate": basic_gate,
                "position_baseline_authorizer": lambda *_args: object(),
                "recovery_close_authorizer": lambda *_args: object(),
            },
        },
        ready=lambda: owner_ready[0],
        operation_readiness={_RECOVERY: lambda: recovery_ready[0]},
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=WireScope(_DOMAIN),
        scope_ref=service_config.scope_ref,
        scope_version="scope-v1",
        security_ref="listener-security-v1",
        security_version="curve-v1",
        registration_policy_ref=service_config.registration_policy_ref,
        registration_policy_version="policy-v1",
        service_requirements_ref=service_config.service_requirements_ref,
        service_requirements_version="requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: _PRINCIPAL},
        grants={
            _PRINCIPAL: (
                PrincipalGrant(
                    client_identity,
                    (
                        ResourceGrant(
                            _SERVICE_INSTANCE,
                            "service.order_entry",
                            "account-grant-v1",
                            scope_template,
                            frozenset({_SUBMIT, _RECOVERY}),
                        ),
                    ),
                ),
            )
        },
    )

    def resource_provider(component, instance_id, _options, scope):
        assert (component, instance_id, scope) == (
            "execution",
            _SERVICE_INSTANCE,
            authority.scope,
        )
        return resources

    host = ServiceHost(
        service_config,
        authority,
        tmp_path / "registry.sqlite3",
        resource_provider,
    )
    client_config = _client_config(endpoint)
    runtime_factory = ZmqTransportFactory(
        {
            "client-security-v1": ClientCurveKeys(
                server_public, client_public, client_secret
            )
        }
    )
    commands: Queue[str] = Queue()
    events: Queue[tuple[Any, ...]] = Queue()

    def client_worker() -> None:
        runtime = ServiceClientRuntime(
            client_config,
            identity=client_identity,
            scope_resolver=lambda _ref: scope_template,
            peer_resolver=lambda _service: service_identity,
            transport_factory=runtime_factory,
            run_id="run-a",
            requested_operations={
                "broker.orders": frozenset({_SUBMIT, _RECOVERY})
            },
        )
        try:
            summary = runtime.start()
            events.put(("started", summary.bindings[0].operations))
            while True:
                command = commands.get()
                if command == "close":
                    runtime.close()
                    events.put(("closed", runtime.state))
                    return
                operation = _SUBMIT if command == "ordinary" else _RECOVERY
                arguments = (
                    {}
                    if command == "ordinary"
                    else {
                        "target_intent_id": "source-open-1",
                        "recovery_intent_id": "recovery-close-1",
                    }
                )
                try:
                    result = runtime.call(
                        "broker.orders",
                        operation,
                        arguments,
                        account_ref=_ACCOUNT,
                        scope_ref="account-grant-v1",
                    )
                except ServiceRuntimeError as error:
                    events.put(("call", command, "error", error.code))
                else:
                    events.put(("call", command, "ok", result))
        except BaseException as error:
            events.put(("worker-error", type(error).__name__, getattr(error, "code", None)))
            if runtime.state not in {"closed", "close_failed"}:
                try:
                    runtime.close()
                except BaseException:
                    pass

    client_thread: Thread | None = None

    def next_event(timeout: float = 8.0) -> tuple[Any, ...]:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            try:
                return events.get_nowait()
            except Empty:
                host.serve_once(20)
        raise AssertionError("client did not complete its authenticated Host exchange")

    try:
        host.start()
        assert host.healthy is True
        assert host._registry is not None and host._registry.ready is True
        owner_component = host._components[0]
        entry_capability = next(
            capability
            for capability in owner_component.capabilities
            if capability.port == "service.order_entry"
        )
        assert _SUBMIT in entry_capability.operations
        assert _RECOVERY in entry_capability.operations
        assert _RECOVERY in owner_component.handlers

        client_thread = Thread(target=client_worker, name="recovery-client-test")
        client_thread.start()
        started = next_event()
        assert started[0] == "started", started
        assert started[1] == frozenset({_SUBMIT, _RECOVERY})

        owner_ready[0] = False
        host.serve_once(0)
        assert host.healthy is False
        assert host._registry is not None and host._registry.ready is False
        commands.put("ordinary")
        ordinary = next_event()
        assert ordinary == ("call", "ordinary", "error", "POLICY_REQUIREMENT_UNMET")
        assert dispatch.normal_submits == []

        commands.put("recovery")
        recovery = next_event()
        assert recovery[:3] == ("call", "recovery", "ok"), (
            recovery,
            dispatch.prepared,
            risk.reserved,
            risk.claimed,
            dispatch.recovery_submits,
        )
        assert recovery[3]["ok"] is True
        assert recovery[3]["result"]["found"] is True
        assert dispatch.prepared == [("source-open-1", "recovery-close-1")]
        assert dispatch.recovery_submits == ["recovery-close-1"]
        assert risk.reserved == ["recovery-close-1"]
        assert risk.claimed == ["recovery-close-1"]

        recovery_ready[0] = False
        commands.put("recovery-disabled")
        disabled = next_event()
        assert disabled == (
            "call",
            "recovery-disabled",
            "error",
            "POLICY_REQUIREMENT_UNMET",
        )
        assert dispatch.recovery_submits == ["recovery-close-1"]
        assert host._registry is not None and host._registry.ready is False

        commands.put("close")
        closed = next_event()
        assert closed == ("closed", "closed")
        client_thread.join(timeout=2)
        assert not client_thread.is_alive()
    finally:
        if client_thread is not None and client_thread.is_alive():
            commands.put("close")
            deadline = monotonic() + 3
            while client_thread.is_alive() and monotonic() < deadline:
                try:
                    host.serve_once(20)
                except Exception:
                    break
                client_thread.join(timeout=0.02)
        host.close()
        store.close()
