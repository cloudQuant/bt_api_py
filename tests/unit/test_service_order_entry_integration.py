"""Managed order submission and cancellation over spawned hosts and CURVE RPC."""

from __future__ import annotations

import multiprocessing
import socket
from contextlib import suppress
from typing import Any

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    AdmissionDenied,
    CancelDispatchClaimReceiptV1,
    CancelObservation,
    ExecutionScope,
    OrderIntent,
    ProviderObservation,
    SqliteExecutionStore,
)

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_DOMAIN = "paper:execution-order-integration"
_SERVICE_ID = "synthetic-execution-service"
_SERVICE_PRINCIPAL = "synthetic-execution-principal"
_SERVICE_INSTANCE = "execution-owner"
_SERVICE_SCOPE_REF = "execution-service-root-v1"
_SERVER_SECURITY_REF = "execution-server-curve-v1"
_ACCOUNT = "account-a"
_STRATEGY = "strategy-execution-a"
_PRINCIPAL = "execution-strategy-a-principal"
_ORDER_SCOPE_REF = "strategy-orders-a-v1"
_QUERY_SCOPE_REF = "strategy-order-query-a-v1"
_REQUEST_TIMEOUT_MS = 2000
_MAX_MESSAGE_BYTES = 65_536
_TRADING_DAY = "20261014"

_SCOPE = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY)
_EXECUTION_SCOPE = ExecutionScope("PAPER", "simulation", _ACCOUNT, _STRATEGY, _TRADING_DAY)


class _SyntheticProvider:
    """Provider adapter owned by the service process; records every dispatch."""

    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.cancelled: list[str] = []
        self.fail_next_submit = False

    def submit(self, intent: OrderIntent) -> ProviderObservation:
        self.submitted.append(intent.intent_id)
        if self.fail_next_submit:
            self.fail_next_submit = False
            raise TimeoutError("synthetic provider timeout")
        return ProviderObservation.accepted(intent.intent_id, "provider-order-1")

    def cancel(self, intent) -> CancelObservation:
        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id, intent.target_intent_id, intent.provider_order_id
        )


class _SyntheticGate:
    """Synthetic admission gate that can be told to deny the next reservation."""

    def __init__(self) -> None:
        self.deny_next = False
        self.reserved: list[str] = []
        self.claimed: list[tuple[str, str]] = []
        self.claimed_cancels: list[tuple[str, str]] = []

    def reserve(self, intent):
        if self.deny_next:
            self.deny_next = False
            raise AdmissionDenied()
        reference = getattr(intent, "intent_id", None) or intent.cancel_id
        self.reserved.append(reference)
        return _Permit(f"permit-{reference}")

    def validate(self, permit_id: str, intent):
        return _Permit(permit_id)

    def claim_for_dispatch(self, permit_id: str, intent: OrderIntent):
        self.claimed.append((permit_id, intent.intent_id))
        return _Permit(permit_id)

    def claim_before_dispatch(self, permit_id: str, intent):
        self.claimed_cancels.append((permit_id, intent.cancel_id))
        return CancelDispatchClaimReceiptV1(
            permit_reference=permit_id,
            scope_key=intent.scope.key,
            cancel_id=intent.cancel_id,
            intent_fingerprint=intent.fingerprint,
        )

    def release(self, permit_id: str, reason: str) -> None:
        return None

    def settle(self, permit_id: str) -> None:
        return None


class _Permit:
    def __init__(self, permit_id: str) -> None:
        self.permit_id = permit_id


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": _SERVICE_SCOPE_REF,
        "transport_ref": "execution-listener-v1",
        "registration_policy_ref": "execution-registration-v1",
        "service_requirements_ref": "execution-requirements-v1",
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _SERVICE_INSTANCE,
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": {"role": "account_owner"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "execution-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _SERVER_SECURITY_REF,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _strategy_config(endpoint: str, security_ref: str):
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
                                "scope_ref": _ORDER_SCOPE_REF,
                                "transport_ref": "execution-connect-v1",
                                "account_ref": _ACCOUNT,
                            },
                            {
                                "mount": "broker.order_query",
                                "service_id": _SERVICE_ID,
                                "instance_id": _SERVICE_INSTANCE,
                                "scope_ref": _QUERY_SCOPE_REF,
                                "transport_ref": "execution-connect-v1",
                                "account_ref": _ACCOUNT,
                            },
                        ],
                        "options": {"request_timeout_ms": _REQUEST_TIMEOUT_MS},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "execution-connect-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": security_ref,
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


def _submit_arguments(intent_id: str = "intent-1") -> dict[str, Any]:
    return {
        "intent_id": intent_id,
        "signal_id": f"signal-{intent_id}",
        "instrument": "BTC-USDT",
        "side": "BUY",
        "position_effect": "OPEN",
        "order_type": "LIMIT",
        "quantity": "2",
        "price": "50000",
        "time_in_force": "GTC",
        "reduce_only": False,
        "metadata_version": "metadata-v1",
        "deadline_ns": None,
        "tags": {},
    }


def _cancel_arguments() -> dict[str, Any]:
    return {
        "cancel_id": "cancel-1",
        "target_intent_id": "intent-1",
        "provider_order_id": "provider-order-1",
        "metadata_version": "metadata-v1",
        "tags": {},
    }


def _plain(value: Any) -> Any:
    """Detach frozen wire mappings so a spawned pipe can pickle them."""

    if isinstance(value, dict) or hasattr(value, "items"):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _host_process(control, raw_config, registry_path, keys, store_path):
    config = parse_service_config(raw_config)
    provider = _SyntheticProvider()
    gate = _SyntheticGate()
    store = None
    host = None
    try:
        authority = TrustedServiceAuthority(
            identity=WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID),
            scope=WireScope(_DOMAIN),
            scope_ref=config.scope_ref,
            scope_version="execution-service-scope-v1",
            security_ref=_SERVER_SECURITY_REF,
            security_version="curve-key-v1",
            registration_policy_ref=config.registration_policy_ref,
            registration_policy_version="execution-policy-v1",
            service_requirements_ref=config.service_requirements_ref,
            service_requirements_version="execution-requirements-v1",
            curve_public_key=keys["server_public"],
            curve_secret_key=keys["server_secret"],
            principals={keys["strategy_public"]: _PRINCIPAL},
            grants={
                _PRINCIPAL: (
                    PrincipalGrant(
                        WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY),
                        (
                            ResourceGrant(
                                _SERVICE_INSTANCE,
                                "service.order_entry",
                                _ORDER_SCOPE_REF,
                                _SCOPE,
                                frozenset({"orders.submit", "orders.cancel"}),
                            ),
                            ResourceGrant(
                                _SERVICE_INSTANCE,
                                "service.order_query",
                                _QUERY_SCOPE_REF,
                                _SCOPE,
                                frozenset({"orders.query"}),
                            ),
                        ),
                    ),
                )
            },
        )

        def resource_provider(component, _instance_id, _options, _scope):
            if component != "execution":
                raise AssertionError("unexpected service component")
            return ComponentResources.borrowed(
                {
                    "store": store,
                    "targets": {_SCOPE: _EXECUTION_SCOPE},
                    "dispatch": provider,
                    "admission": {"order_gate": gate, "cancel_gate": gate},
                },
                ready=lambda: True,
            )

        store = SqliteExecutionStore(store_path)
        host = ServiceHost(config, authority, registry_path, resource_provider)
        host.start()
        control.send(("ready", host.endpoint, host.state))
        while True:
            host.serve_once(timeout_ms=25)
            if not control.poll():
                continue
            command = control.recv()
            if command == ("close",):
                host.close()
                store.close()
                store = None
                control.send(("closed", host.state))
                return
            if command == ("counts",):
                control.send(
                    (
                        "counts",
                        tuple(provider.submitted),
                        tuple(provider.cancelled),
                        tuple(gate.reserved),
                        tuple(gate.claimed),
                    )
                )
                continue
            if command == ("fail_next_submit",):
                provider.fail_next_submit = True
                control.send(("armed",))
                continue
            if command == ("deny_next",):
                gate.deny_next = True
                control.send(("armed",))
                continue
            raise AssertionError("unknown local execution host command")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        if store is not None:
            with suppress(BaseException):
                store.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _strategy_process(control, endpoint, strategy_public, strategy_secret, server_public):
    runtime = None
    try:
        config = _strategy_config(endpoint, "execution-strategy-curve-v1")
        peer = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY)
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda _ref: _SCOPE,
            peer_resolver=lambda _service: peer,
            transport_factory=ZmqTransportFactory(
                {
                    "execution-strategy-curve-v1": ClientCurveKeys(
                        server_public, strategy_public, strategy_secret
                    )
                }
            ),
        )
        startup = runtime.start()
        assert startup.degraded_count == 0
        operations = {binding.mount: binding.operations for binding in runtime.bindings}
        control.send(("ready", runtime.run_id, operations))
        while True:
            command = control.recv()
            if command == ("close",):
                runtime.close()
                control.send(("closed", runtime.state))
                return
            if command[0] == "submit":
                reply = runtime.call(
                    "broker.orders",
                    "orders.submit",
                    _submit_arguments(command[1]),
                    scope_ref=_ORDER_SCOPE_REF,
                )
                control.send(("submitted", reply["code"], _plain(dict(reply["result"]["record"]))))
                continue
            if command[0] == "cancel":
                reply = runtime.call(
                    "broker.orders",
                    "orders.cancel",
                    _cancel_arguments(),
                    scope_ref=_ORDER_SCOPE_REF,
                )
                control.send(("cancelled", reply["code"], _plain(dict(reply["result"]["record"]))))
                continue
            if command[0] == "query":
                reply = runtime.call(
                    "broker.order_query",
                    "orders.query",
                    {"intent_id": command[1]},
                    scope_ref=_QUERY_SCOPE_REF,
                )
                control.send(("queried", reply["code"], _plain(dict(reply["result"]))))
                continue
            if command[0] == "forged":
                try:
                    runtime.call(
                        "broker.orders",
                        "orders.submit",
                        _submit_arguments("intent-forged"),
                        scope_ref=_QUERY_SCOPE_REF,
                    )
                except ServiceRuntimeError as error:
                    control.send(("forged", getattr(error, "code", type(error).__name__)))
                else:
                    control.send(("forged", "unexpectedly_accepted"))
                continue
            raise AssertionError("unknown local execution strategy command")
    except BaseException as exc:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def test_spawned_strategy_submits_cancels_and_recovers_durable_order_facts(tmp_path) -> None:
    from tests.unit.test_service_account_integration import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _client_command,
        _close_normally,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }
    service_raw = _service_config(endpoint)
    registry_path = str(tmp_path / "execution-registry.sqlite3")
    store_path = str(tmp_path / "execution.sqlite3")
    host_record = None
    strategy_record = None
    records = []
    forced = []
    failures = []
    try:
        host_record = _start_process(
            context,
            _host_process,
            (service_raw, registry_path, keys, store_path),
            "execution-owner-host-r1",
        )
        records.append(host_record)
        host_ready = _receive(host_record["pipe"])
        assert host_ready[0] == "ready", host_ready
        assert host_ready[1] == endpoint

        strategy_record = _start_process(
            context,
            _strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            "execution-strategy-r1",
        )
        records.append(strategy_record)
        strategy_ready = _receive(strategy_record["pipe"])
        assert strategy_ready[0] == "ready"
        assert strategy_ready[2] == {
            "broker.orders": frozenset({"orders.submit", "orders.cancel"}),
            "broker.order_query": frozenset({"orders.query"}),
        }

        submitted = _client_command(strategy_record, "submit", "intent-1")
        assert submitted[1] == "OK"
        assert submitted[2]["state"] == "ACKED"
        assert submitted[2]["provider_order_id"] == "provider-order-1"
        assert submitted[2]["dispatch_attempts"] == 1

        duplicate = _client_command(strategy_record, "submit", "intent-1")
        assert duplicate[2] == submitted[2]

        queried = _client_command(strategy_record, "query", "intent-1")
        assert queried[1] == "OK"
        assert queried[2]["record"]["state"] == "ACKED"

        counts = _client_command(host_record, "counts")
        assert counts[1] == ("intent-1",)
        assert counts[3] == ("intent-1",)
        assert counts[4] == (("permit-intent-1", "intent-1"),)

        forged = _client_command(strategy_record, "forged")
        assert forged[1] != "unexpectedly_accepted"
        assert _client_command(host_record, "counts")[1] == ("intent-1",)

        denied = _client_command(host_record, "deny_next")
        assert denied == ("armed",)
        refused = _client_command(strategy_record, "submit", "intent-2")
        assert refused[1] == "OK"
        assert refused[2]["state"] == "REJECTED"
        assert _client_command(host_record, "counts")[1] == ("intent-1",)

        assert _client_command(host_record, "fail_next_submit") == ("armed",)
        unknown = _client_command(strategy_record, "submit", "intent-3")
        assert unknown[1] == "OK"
        assert unknown[2]["state"] == "UNKNOWN"
        repeat = _client_command(strategy_record, "submit", "intent-3")
        assert repeat[2]["state"] == "UNKNOWN"
        after_unknown = _client_command(host_record, "counts")
        assert after_unknown[1] == ("intent-1", "intent-3")

        cancelled = _client_command(strategy_record, "cancel")
        assert cancelled[1] == "OK"
        assert cancelled[2]["state"] == "ACKED"
        assert cancelled[2]["target_intent_id"] == "intent-1"
        cancel_repeat = _client_command(strategy_record, "cancel")
        assert cancel_repeat[2] == cancelled[2]
        final_counts = _client_command(host_record, "counts")
        assert final_counts[2] == ("cancel-1",)

        _close_normally(strategy_record, expected_tag="closed")
        _close_normally(host_record, expected_tag="closed")

        # A new service process over the same durable store must serve the same
        # order facts without a provider dispatch.
        host_record = _start_process(
            context,
            _host_process,
            (service_raw, registry_path, keys, store_path),
            "execution-owner-host-r2",
        )
        records.append(host_record)
        assert _receive(host_record["pipe"])[0] == "ready"
        strategy_record = _start_process(
            context,
            _strategy_process,
            (endpoint, strategy_public, strategy_secret, server_public),
            "execution-strategy-r2",
        )
        records.append(strategy_record)
        assert _receive(strategy_record["pipe"])[0] == "ready"

        recovered = _client_command(strategy_record, "query", "intent-1")
        assert recovered[2]["record"]["state"] == "ACKED"
        recovered_unknown = _client_command(strategy_record, "query", "intent-3")
        assert recovered_unknown[2]["record"]["state"] == "UNKNOWN"
        assert _client_command(host_record, "counts")[1] == ()

        _close_normally(strategy_record, expected_tag="closed")
        _close_normally(host_record, expected_tag="closed")
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
