"""Authenticated owner.cancel route keeps provider-unknown outcomes frozen."""

from __future__ import annotations

import socket
import threading
from decimal import Decimal
import pytest
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    ExecutionScope,
    ProviderObservation,
    SqliteExecutionStore,
)
from bt_api_risk import (
    AccountScope,
    CancelDispatchResolutionReceiptStatus,
    DurableRiskGate,
    RiskPolicy,
)
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution_risk_cancel import RiskExecutionCancelAdmissionGate
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)


_SERVICE_ID = "afi08-cancel-owner-service"
_INSTANCE_ID = "afi08-cancel-execution-owner"
_DOMAIN = "paper:afi08-cancel"
_ACCOUNT = "account-afi08-cancel"
_STRATEGY = "strategy-afi08-cancel"
_RUN_ID = "run-afi08-cancel-v1"
_SCOPE_REF = "afi08-cancel-exact-run-v1"
_LISTENER_REF = "afi08-cancel-listener-v1"
_CONNECT_REF = "afi08-cancel-connect-v1"
_SERVER_SECURITY_REF = "afi08-cancel-server-security-v1"
_STRATEGY_SECURITY_REF = "afi08-cancel-strategy-security-v1"
_OWNER_SECURITY_REF = "afi08-cancel-owner-security-v1"
_SERVICE_PRINCIPAL = "afi08-cancel-service-principal"
_STRATEGY_PRINCIPAL = "afi08-cancel-strategy-principal"
_STRATEGY_ACTOR = "strategy-afi08-cancel"
_OWNER_PRINCIPAL = "afi08-cancel-operator-principal"
_OWNER_ACTOR = "operator-afi08-cancel"


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _service_config(endpoint: str):
    return parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": _SERVICE_ID,
            "scope_ref": "afi08-cancel-service-scope-v1",
            "transport_ref": _LISTENER_REF,
            "registration_policy_ref": "afi08-cancel-registration-v1",
            "service_requirements_ref": "afi08-cancel-requirements-v1",
            "components": {
                "execution": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": _INSTANCE_ID,
                        "bind_ports": [
                            "service.order_entry",
                            "service.order_query",
                            "service.owner_control",
                        ],
                        "options": {"role": "account_owner"},
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
                        "security_profile_ref": _SERVER_SECURITY_REF,
                        "options": {
                            "high_water_mark": 32,
                            "max_message_bytes": 1_000_000,
                        },
                    }
                },
            },
        }
    )


def _client_config(endpoint: str, *, strategy: bool):
    if strategy:
        role = "strategy_client"
        version = {"client_config_schema_version": 1}
        registration = {"strategy_ref": _STRATEGY}
        mount = "broker.orders"
        security_ref = _STRATEGY_SECURITY_REF
    else:
        # A service-role runtime is required by the separate owner.cancel port.
        # Its monitor component is a parser/runtime placeholder only; no Host
        # or monitor service is created for this client config.
        role = "service"
        version = {"service_config_schema_version": 1}
        registration = {
            "service_id": "afi08-cancel-owner-client",
            "scope_ref": "afi08-cancel-owner-client-scope-v1",
            "transport_ref": "afi08-cancel-owner-client-listener-v1",
            "registration_policy_ref": "afi08-cancel-owner-client-registration-v1",
            "service_requirements_ref": "afi08-cancel-owner-client-requirements-v1",
        }
        mount = "upstream.owner_control"
        security_ref = _OWNER_SECURITY_REF
    common = {
        "process_role": role,
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": mount,
                            "service_id": _SERVICE_ID,
                            "instance_id": _INSTANCE_ID,
                            "scope_ref": _SCOPE_REF,
                            "transport_ref": _CONNECT_REF,
                            "account_ref": _ACCOUNT,
                        }
                    ],
                    "options": {
                        "request_timeout_ms": 3000,
                        "heartbeat_interval_seconds": 5,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _CONNECT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": security_ref,
                    "options": {
                        "request_timeout_ms": 3000,
                        "high_water_mark": 32,
                        "max_message_bytes": 1_000_000,
                    },
                }
            },
        },
    }
    if strategy:
        common.update(version)
        common["registration"] = registration
        return parse_client_config(common)
    common.update(registration)
    common.update(version)
    common["components"] = {
        "monitor": {
            "config_schema_version": 2,
            "settings": {
                "instance_id": "afi08-cancel-unused-client-monitor",
                "bind_ports": ["service.monitor_query"],
                "options": {},
            },
        }
    }
    common["communication"]["profiles"]["afi08-cancel-owner-client-listener-v1"] = {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": {"rpc_bind": "tcp://127.0.0.1:9"},
        "security_profile_ref": security_ref,
        "options": {"high_water_mark": 32, "max_message_bytes": 1_000_000},
    }
    return parse_service_config(common)


class _Provider:
    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.cancelled: list[str] = []

    def submit(self, intent) -> ProviderObservation:
        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, "provider-order-afi08")

    def cancel(self, intent):
        self.cancelled.append(intent.cancel_id)
        raise TimeoutError("synthetic provider reply is unknown")


class _HostThread:
    """Keep ServiceHost start, serving, and close on one owning thread."""

    def __init__(self, host: ServiceHost) -> None:
        self.host = host
        self.stop_requested = threading.Event()
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, name="afi08-cancel-owner-host")

    def _run(self) -> None:
        try:
            self.host.start()
            self.ready.set()
            while not self.stop_requested.is_set():
                self.host.serve_once(timeout_ms=15)
        except BaseException as error:
            self.error = error
        finally:
            try:
                if self.host.state != "closed":
                    self.host.close()
            except BaseException as error:
                if self.error is None:
                    self.error = error
            self.ready.set()
            self.finished.set()

    def start(self) -> None:
        self.thread.start()
        assert self.ready.wait(10), "owner ServiceHost did not become ready"
        assert self.error is None, (
            self.error,
            self.host.summary.get("start_failure"),
            self.host.summary.get("component_states"),
        )
        assert self.host.state == "started", self.host.summary

    def close(self) -> None:
        self.stop_requested.set()
        assert self.finished.wait(10), "owner ServiceHost did not close"
        self.thread.join(1)
        assert not self.thread.is_alive()
        if self.error is not None:
            # Preserve the original startup assertion in ``start`` rather
            # than replacing it with the same host error during test cleanup.
            return
        assert self.error is None, self.error
        assert self.host.state == "closed", self.host.summary


@pytest.mark.integration
def test_authenticated_owner_cancel_unknown_replay_never_redispatches(tmp_path) -> None:
    endpoint = _endpoint()
    config = _service_config(endpoint)
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    owner_public, owner_secret = zmq.curve_keypair()
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    strategy_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT, _STRATEGY_PRINCIPAL, _STRATEGY_ACTOR
    )
    owner_identity = WireIdentity(WireRole.SERVICE, _OWNER_PRINCIPAL, _OWNER_ACTOR)
    wire_scope = WireScope(
        _DOMAIN,
        account_ref=_ACCOUNT,
        strategy_ref=_STRATEGY,
        run_id=_RUN_ID,
    )
    target_scope = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
    execution_scope = ExecutionScope(
        "PAPER", "simulation", _ACCOUNT, _STRATEGY, "afi08-owner-cancel-session"
    )
    strategy_grant = ResourceGrant(
        _INSTANCE_ID,
        "service.order_entry",
        _SCOPE_REF,
        target_scope,
        frozenset({"orders.submit"}),
    )
    owner_grant = ResourceGrant(
        _INSTANCE_ID,
        "service.owner_control",
        _SCOPE_REF,
        wire_scope,
        frozenset({"owner.cancel"}),
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=WireScope(_DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="afi08-cancel-service-scope-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="curve-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="afi08-cancel-registration-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="afi08-cancel-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={
            strategy_public: _STRATEGY_PRINCIPAL,
            owner_public: _OWNER_PRINCIPAL,
        },
        grants={
            _STRATEGY_PRINCIPAL: (PrincipalGrant(strategy_identity, (strategy_grant,)),),
            _OWNER_PRINCIPAL: (PrincipalGrant(owner_identity, (owner_grant,)),),
        },
    )
    execution_store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    risk_journal = RiskOrderJournal(
        tmp_path / "risk-order-journal.sqlite3", writer_id="afi08-cancel-journal"
    )
    risk_scope = AccountScope("PAPER", _ACCOUNT, "simulation")
    risk_gate = DurableRiskGate(
        tmp_path / "risk.sqlite3",
        RiskPolicy(
            "afi08-cancel-risk-v1",
            Decimal("0"),
            max_increase_count=0,
            permit_ttl_seconds=30,
            notional_unit="USD",
        ),
        execution_journal_authority=risk_journal,
    )
    cancel_gate = RiskExecutionCancelAdmissionGate(
        risk_gate,
        risk_journal,
        execution_store,
        risk_scope=risk_scope,
        execution_scope=execution_scope,
        wire_scope_template=target_scope,
    )
    provider = _Provider()
    quota_calls: list[tuple[object, ...]] = []

    def quota_admission(*args) -> None:
        quota_calls.append(args)
        return None

    def resource_provider(component, instance_id, _options, _scope):
        assert component == "execution" and instance_id == _INSTANCE_ID
        return ComponentResources.borrowed(
            {
                "store": execution_store,
                "targets": {target_scope: execution_scope},
                "dispatch": provider,
                "admission": {
                    "cancel_gate": cancel_gate,
                    "cancel_quota_admission": quota_admission,
                    # The synthetic target order uses the existing local owner
                    # entry path without introducing a separate order-policy
                    # fixture. Cancellation itself always uses the real Risk gate.
                    "allow_unprotected": True,
                },
            },
            ready=lambda: True,
        )

    host = ServiceHost(
        config,
        authority,
        tmp_path / "registry.sqlite3",
        resource_provider,
    )
    host_thread = _HostThread(host)
    strategy_runtime = owner_runtime = None
    strategy_started = owner_started = False
    try:
        host_thread.start()
        strategy_runtime = ServiceClientRuntime(
            _client_config(endpoint, strategy=True),
            identity=strategy_identity,
            scope_resolver=lambda _ref: target_scope,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {
                    _STRATEGY_SECURITY_REF: ClientCurveKeys(
                        server_public, strategy_public, strategy_secret
                    )
                }
            ),
            run_id=_RUN_ID,
            requested_operations={"broker.orders": frozenset({"orders.submit"})},
        )
        strategy_summary = strategy_runtime.start()
        strategy_started = True
        assert strategy_summary.degraded_count == 0
        assert len(strategy_summary.bindings) == 1
        assert strategy_summary.bindings[0].scope == wire_scope

        owner_runtime = ServiceClientRuntime(
            _client_config(endpoint, strategy=False),
            identity=owner_identity,
            scope_resolver=lambda _ref: wire_scope,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {
                    _OWNER_SECURITY_REF: ClientCurveKeys(
                        server_public, owner_public, owner_secret
                    )
                }
            ),
            requested_operations={"upstream.owner_control": frozenset({"owner.cancel"})},
        )
        owner_summary = owner_runtime.start()
        owner_started = True
        assert owner_summary.degraded_count == 0
        assert len(owner_summary.bindings) == 1
        assert owner_summary.bindings[0].scope == wire_scope
        assert owner_summary.bindings[0].operations == frozenset({"owner.cancel"})

        wrong_run_runtime = ServiceClientRuntime(
            _client_config(endpoint, strategy=False),
            identity=owner_identity,
            scope_resolver=lambda _ref: WireScope(
                _DOMAIN,
                account_ref=_ACCOUNT,
                strategy_ref=_STRATEGY,
                run_id="run-afi08-cancel-other-run",
            ),
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {
                    _OWNER_SECURITY_REF: ClientCurveKeys(
                        server_public, owner_public, owner_secret
                    )
                }
            ),
            requested_operations={"upstream.owner_control": frozenset({"owner.cancel"})},
        )
        try:
            with pytest.raises(ServiceRuntimeError, match="SCOPE_DENIED"):
                wrong_run_runtime.start()
        finally:
            wrong_run_runtime.close()

        submitted_reply = strategy_runtime.call(
            "broker.orders",
            "orders.submit",
            {
                "intent_id": "afi08-cancel-target-order",
                "signal_id": "afi08-cancel-target-signal",
                "instrument": "BTC-USDT",
                "side": "BUY",
                "position_effect": "OPEN",
                "order_type": "LIMIT",
                "quantity": "1",
                "price": "50000",
                "time_in_force": "GTC",
                "reduce_only": False,
                "metadata_version": "afi08-cancel-target-v1",
                "deadline_ns": None,
                "tags": {},
            },
            account_ref=_ACCOUNT,
            scope_ref=_SCOPE_REF,
        )
        assert submitted_reply["ok"] is True, submitted_reply
        submitted = submitted_reply["result"]
        assert submitted.get("found") is True, submitted
        assert submitted["record"]["state"] == "ACKED"
        assert submitted["record"]["provider_order_id"] == "provider-order-afi08"
        assert provider.submitted == ["afi08-cancel-target-order"]

        first_reply = owner_runtime.call(
            "upstream.owner_control",
            "owner.cancel",
            {"command_id": "afi08-cancel-command-1"},
            account_ref=_ACCOUNT,
            scope_ref=_SCOPE_REF,
        )
        assert first_reply["code"] == "OWNER_CANCEL_UNKNOWN", first_reply
        first = first_reply["result"]
        assert first["outcome"] == "UNKNOWN"
        assert first["run_id"] == _RUN_ID
        assert first["total_count"] == 1 and first["selected_count"] == 1
        assert first["items"][0]["cancel_state"] == "UNKNOWN"
        assert first["items"][0]["dispatch_attempts"] == 1
        assert first["items"][0]["review_required"] is True
        cancel_id = first["items"][0]["cancel_id"]
        assert type(cancel_id) is str and cancel_id
        assert provider.cancelled == [cancel_id]
        assert len(quota_calls) == 1
        account, strategy, run_id, instrument, operation, deadline = quota_calls[0]
        assert (account, strategy, run_id, instrument, operation) == (
            _ACCOUNT,
            _STRATEGY,
            _RUN_ID,
            "BTC-USDT",
            "CANCEL",
        )
        assert type(deadline) is int and deadline > 0

        cancel_record = execution_store.get_cancel(cancel_id, scope=execution_scope)
        assert cancel_record is not None and cancel_record.permit_reference is not None
        risk_receipt = risk_gate.cancel_dispatch_resolution_receipt(
            cancel_record.permit_reference
        )
        assert risk_receipt.status is CancelDispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE
        assert risk_receipt.dispatch_latch_active is True
        assert risk_receipt.proof is None

        replay_reply = owner_runtime.call(
            "upstream.owner_control",
            "owner.cancel",
            {"command_id": "afi08-cancel-command-1"},
            account_ref=_ACCOUNT,
            scope_ref=_SCOPE_REF,
        )
        assert replay_reply["code"] == "OWNER_CANCEL_UNKNOWN", replay_reply
        replay = replay_reply["result"]
        assert replay["outcome"] == "UNKNOWN"
        assert replay["items"][0]["cancel_id"] == cancel_id
        assert replay["items"][0]["cancel_state"] == "UNKNOWN"
        assert provider.cancelled == [cancel_id]
        assert len(quota_calls) == 1

        later_reply = owner_runtime.call(
            "upstream.owner_control",
            "owner.cancel",
            {"command_id": "afi08-cancel-command-2"},
            account_ref=_ACCOUNT,
            scope_ref=_SCOPE_REF,
        )
        assert later_reply["code"] == "OWNER_CANCEL_UNKNOWN", later_reply
        later_command = later_reply["result"]
        assert later_command["outcome"] == "UNKNOWN"
        assert later_command["items"][0]["cancel_id"] == cancel_id
        assert later_command["items"][0]["cancel_state"] == "UNKNOWN"
        assert later_command["items"][0]["dispatch_attempts"] == 1
        assert provider.cancelled == [cancel_id]
        assert len(quota_calls) == 1
        final_receipt = risk_gate.cancel_dispatch_resolution_receipt(
            cancel_record.permit_reference
        )
        assert final_receipt == risk_receipt
    finally:
        if owner_runtime is not None and owner_started:
            owner_runtime.close()
        if strategy_runtime is not None and strategy_started:
            strategy_runtime.close()
        host_thread.close()
        risk_gate.close()
        execution_store.close()

