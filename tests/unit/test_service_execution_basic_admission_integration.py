"""Execution-only admission through a spawned CURVE ServiceHost and client."""

from __future__ import annotations

import importlib.abc
import json
import multiprocessing
import socket
import sqlite3
import sys
import time
from contextlib import suppress
from typing import Any

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_DOMAIN = "paper:execution-basic-admission-integration"
_SERVICE_ID = "execution-basic-service"
_SERVICE_PRINCIPAL = "execution-basic-service-principal"
_SERVICE_INSTANCE = "execution-basic-owner"
_SERVICE_SCOPE_REF = "execution-basic-service-root-v1"
_SERVER_SECURITY_REF = "execution-basic-server-curve-v1"
_CLIENT_SECURITY_REF = "execution-basic-client-curve-v1"
_ACCOUNT = "execution-account-a"
_WRONG_ACCOUNT = "execution-account-b"
_STRATEGY = "execution-strategy-a"
_STRATEGY_PRINCIPAL = "execution-strategy-a-principal"
_ORDER_SCOPE_REF = "execution-orders-a-v1"
_WRONG_ACCOUNT_SCOPE_REF = "execution-orders-b-v1"
_PROVIDER_WRITER_ID = "execution-order-entry"
_RIVAL_WRITER_ID = "execution-exe04-rival-owner"
_PERMIT_TTL_NS = 5_000_000_000
_LEASE_TTL_NS = 10_000_000_000
_MAX_MESSAGE_BYTES = 65_536

_WIRE_SCOPE = WireScope(
    _DOMAIN,
    account_ref=_ACCOUNT,
    strategy_ref=_STRATEGY,
)
_WRONG_ACCOUNT_WIRE_SCOPE = WireScope(
    _DOMAIN,
    account_ref=_WRONG_ACCOUNT,
    strategy_ref=_STRATEGY,
)
_STRATEGY_IDENTITY = WireIdentity(
    WireRole.STRATEGY_CLIENT,
    _STRATEGY_PRINCIPAL,
    _STRATEGY,
)


class _ForbiddenRiskOrGatewayImport(importlib.abc.MetaPathFinder):
    """Reject imports of optional Risk or Gateway packages in spawned roles."""

    _roots = frozenset({"bt_api_risk", "bt_api_gateway"})

    def find_spec(self, fullname: str, path=None, target=None):
        root = fullname.partition(".")[0]
        if root in self._roots:
            raise AssertionError(f"forbidden optional-package import: {root}")
        return None


def _install_execution_only_import_guard() -> None:
    sys.meta_path.insert(0, _ForbiddenRiskOrGatewayImport())


def _assert_no_risk_or_gateway_loaded() -> None:
    assert not any(
        name == root or name.startswith(f"{root}.")
        for root in _ForbiddenRiskOrGatewayImport._roots
        for name in sys.modules
    )


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
        "transport_ref": "execution-basic-listener-v1",
        "registration_policy_ref": "execution-basic-registration-v1",
        "service_requirements_ref": "execution-basic-requirements-v1",
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
                "execution-basic-listener-v1": {
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


def _client_config(endpoint: str, *, account_ref: str, scope_ref: str):
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
                                "scope_ref": scope_ref,
                                "transport_ref": "execution-basic-connect-v1",
                                "account_ref": account_ref,
                            }
                        ],
                        "options": {"request_timeout_ms": 2_000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "execution-basic-connect-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": _CLIENT_SECURITY_REF,
                        "options": {
                            "request_timeout_ms": 2_000,
                            "high_water_mark": 32,
                            "max_message_bytes": _MAX_MESSAGE_BYTES,
                        },
                    }
                },
            },
        }
    )


def _scope_for_account(account_ref: str) -> WireScope:
    return WireScope(
        _DOMAIN,
        account_ref=account_ref,
        strategy_ref=_STRATEGY,
    )


class _AdmissionClock:
    """Deterministic expiry and real store-fence transition for one request."""

    def __init__(self, store: object, store_path: str, execution_scope: object | None) -> None:
        self._store = store
        self._store_path = store_path
        self._execution_scope = execution_scope
        self.mode: str | None = None
        self.calls = 0
        self.base_ns = 0
        self.rival_fencing_token: int | None = None
        self.injection_error: str | None = None

    def arm(self, mode: str) -> None:
        if mode not in {"expire", "stale_owner"}:
            raise ValueError("unsupported test clock mode")
        self.mode = mode
        self.calls = 0
        self.base_ns = time.time_ns()
        self.rival_fencing_token = None
        self.injection_error = None

    def __call__(self) -> int:
        if self.mode is None:
            return time.time_ns()
        self.calls += 1
        if self.mode == "expire" and self.calls >= 2:
            # The first clock call signs the permit at base_ns + 1, so return
            # its exact expiry boundary (issued_at + TTL) at final claim.
            return self.base_ns + _PERMIT_TTL_NS + 1
        if self.mode == "stale_owner" and self.calls == 3:
            try:
                with sqlite3.connect(self._store_path, timeout=5.0) as connection:
                    row = connection.execute(
                        "SELECT owner_id, fencing_token FROM execution_writer_leases "
                        "WHERE scope_key = ?",
                        (self._execution_scope.account_key,),
                    ).fetchone()
                if row is None:
                    raise AssertionError("the service did not hold its execution writer lease")
                owner_id, old_token = row
                released = self._store.release_lease(
                    self._execution_scope,
                    owner_id,
                    fencing_token=old_token,
                )
                if released is not True:
                    raise AssertionError("the exact old writer generation was not released")
                rival = self._store.acquire_or_renew_lease(
                    self._execution_scope,
                    _RIVAL_WRITER_ID,
                    ttl_ns=_LEASE_TTL_NS,
                )
                if rival.fencing_token != old_token + 1:
                    raise AssertionError("writer fence did not advance by one generation")
                self.rival_fencing_token = rival.fencing_token
            except Exception as error:
                self.injection_error = type(error).__name__
        return self.base_ns + self.calls


class _SqliteAuthorizedProvider:
    """Synthetic provider whose durable call is built from authorized parameters."""

    def __init__(self, journal_path: str) -> None:
        self._journal_path = journal_path
        with sqlite3.connect(self._journal_path, timeout=5.0) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS authorized_provider_calls (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent_id TEXT NOT NULL,
                    account_ref TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    provider_instrument TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    price TEXT,
                    owner_id TEXT NOT NULL,
                    fencing_token INTEGER NOT NULL,
                    parameters_json TEXT NOT NULL
                )
                """
            )

    def submit_authorized(self, intent: object, authorization: object):
        parameters = authorization.parameters
        if parameters.intent_id != intent.intent_id:
            raise AssertionError("provider received authorization for another intent")
        payload = parameters.to_payload()
        with sqlite3.connect(self._journal_path, timeout=5.0) as connection:
            connection.execute(
                """
                INSERT INTO authorized_provider_calls(
                    intent_id, account_ref, strategy_id, provider_instrument,
                    side, quantity, price, owner_id, fencing_token, parameters_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parameters.intent_id,
                    parameters.account_ref,
                    parameters.strategy_id,
                    parameters.provider_instrument,
                    parameters.side.value,
                    format(parameters.quantity, "f"),
                    None if parameters.price is None else format(parameters.price, "f"),
                    authorization.owner_id,
                    authorization.fencing_token,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                ),
            )
        from bt_api_execution import ProviderObservation

        return ProviderObservation.accepted(
            parameters.intent_id,
            f"synthetic-provider-{parameters.intent_id}",
        )

    def cancel(self, intent: object):
        from bt_api_execution import CancelObservation

        return CancelObservation.accepted(
            intent.cancel_id,
            intent.target_intent_id,
            intent.provider_order_id,
        )

    def rows(self) -> tuple[tuple[Any, ...], ...]:
        with sqlite3.connect(self._journal_path, timeout=5.0) as connection:
            return tuple(
                connection.execute(
                    "SELECT intent_id, account_ref, strategy_id, provider_instrument, "
                    "side, quantity, price, owner_id, fencing_token, parameters_json "
                    "FROM authorized_provider_calls ORDER BY sequence"
                ).fetchall()
            )


def _resource_provider(
    store: object,
    provider: _SqliteAuthorizedProvider,
    clock: _AdmissionClock,
    *,
    missing_gate: bool,
):
    from decimal import Decimal

    from bt_api_execution import (
        ExecutionAdmissionValidatorRegistry,
        ExecutionBasicAdmissionGate,
        ExecutionProviderValidatorV1,
        ExecutionScope,
        OrderType,
        PositionEffect,
        Side,
        TimeInForce,
    )

    execution_scope = ExecutionScope(
        "PAPER",
        "simulation",
        _ACCOUNT,
        _STRATEGY,
        "20261015",
    )
    if clock._execution_scope is None:
        clock._execution_scope = execution_scope
    if missing_gate:
        admission = {"allow_unprotected": False}
    else:
        validator = ExecutionProviderValidatorV1(
            provider_id="PAPER",
            validator_id="execution-only-paper-order-policy",
            version="v1",
            dispatch_port=provider,
            instrument_map={"BTC-USDT": "BTCUSDT"},
            quantity_steps={"BTC-USDT": Decimal("1")},
            max_quantities={"BTC-USDT": Decimal("20")},
            price_ticks={"BTC-USDT": Decimal("0.5")},
            supported_order_types=(OrderType.LIMIT,),
            supported_time_in_force=(TimeInForce.GTC,),
            supported_actions=((Side.BUY, PositionEffect.OPEN),),
            supports_reduce_only=False,
        )
        gate = ExecutionBasicAdmissionGate(
            store,
            ExecutionAdmissionValidatorRegistry((validator,)),
            permit_ttl_ns=_PERMIT_TTL_NS,
            clock_ns=clock,
        )
        admission = {"basic_admission_gate": gate}
    return ComponentResources.borrowed(
        {
            "store": store,
            "targets": {
                _WIRE_SCOPE: execution_scope,
                # The second trusted grant intentionally resolves to the first
                # account. BasicAdmission must reject that inconsistent map.
                _WRONG_ACCOUNT_WIRE_SCOPE: execution_scope,
            },
            "dispatch": provider,
            "admission": admission,
        },
        ready=lambda: True,
    )


def _authority(config, keys: dict[str, bytes]) -> TrustedServiceAuthority:
    identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    resources = (
        ResourceGrant(
            _SERVICE_INSTANCE,
            "service.order_entry",
            _ORDER_SCOPE_REF,
            _WIRE_SCOPE,
            frozenset({"orders.submit", "orders.cancel"}),
        ),
        ResourceGrant(
            _SERVICE_INSTANCE,
            "service.order_entry",
            _WRONG_ACCOUNT_SCOPE_REF,
            _WRONG_ACCOUNT_WIRE_SCOPE,
            frozenset({"orders.submit", "orders.cancel"}),
        ),
    )
    return TrustedServiceAuthority(
        identity=identity,
        scope=WireScope(_DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="execution-basic-service-scope-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="execution-basic-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="execution-basic-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals={keys["strategy_public"]: _STRATEGY_PRINCIPAL},
        grants={_STRATEGY_PRINCIPAL: (PrincipalGrant(_STRATEGY_IDENTITY, resources),)},
    )


def _host_process(
    control,
    raw_config,
    registry_path: str,
    store_path: str,
    provider_path: str,
    keys: dict[str, bytes],
    *,
    missing_gate: bool = False,
):
    _install_execution_only_import_guard()
    from bt_api_execution import SqliteExecutionStore

    config = parse_service_config(raw_config)
    store = SqliteExecutionStore(store_path)
    provider = _SqliteAuthorizedProvider(provider_path)
    execution_scope = None
    clock = _AdmissionClock(store, store_path, execution_scope)
    host = None

    def resource_provider(component, _instance_id, _options, _scope):
        if component != "execution":
            raise AssertionError("unexpected service component")
        resources = _resource_provider(
            store,
            provider,
            clock,
            missing_gate=missing_gate,
        )
        clock._execution_scope = next(iter(resources.values["targets"].values()))
        return resources

    try:
        host = ServiceHost(
            config,
            _authority(config, keys),
            registry_path,
            resource_provider,
        )
        try:
            host.start()
        except ServiceHostError as error:
            _assert_no_risk_or_gateway_loaded()
            control.send(("startup_failed", error.code, host.state))
            store.close()
            return
        _assert_no_risk_or_gateway_loaded()
        control.send(("ready", host.endpoint, host.state))
        while True:
            if control.poll():
                command = control.recv()
                if command[0] == "close":
                    host.close()
                    store.close()
                    control.send(("closed", host.state, provider.rows()))
                    return
                if command[0] == "arm":
                    clock.arm(command[1])
                    control.send(("armed", command[1]))
                    continue
                if command[0] == "counts":
                    control.send(
                        (
                            "counts",
                            provider.rows(),
                            clock.calls,
                            clock.rival_fencing_token,
                            clock.injection_error,
                        )
                    )
                    continue
                raise AssertionError("unknown BasicAdmission host command")
            host.serve_once(timeout_ms=20)
    except BaseException as error:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(BaseException):
            store.close()
        with suppress(Exception):
            control.send(("host_error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        control.close()


def _strategy_process(
    control,
    endpoint: str,
    account_ref: str,
    scope_ref: str,
    strategy_public: bytes,
    strategy_secret: bytes,
    server_public: bytes,
):
    _install_execution_only_import_guard()
    from bt_api_py.service_execution import ServiceExecutionClient, ServiceExecutionError

    runtime = None
    try:
        config = _client_config(endpoint, account_ref=account_ref, scope_ref=scope_ref)
        identity = _STRATEGY_IDENTITY
        peer = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
        scope = _scope_for_account(account_ref)
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda _ref: scope,
            peer_resolver=lambda _service: peer,
            transport_factory=ZmqTransportFactory(
                {
                    _CLIENT_SECURITY_REF: ClientCurveKeys(
                        server_public,
                        strategy_public,
                        strategy_secret,
                    )
                }
            ),
        )
        try:
            startup = runtime.start()
        except ServiceRuntimeError as error:
            _assert_no_risk_or_gateway_loaded()
            control.send(("registration_failed", error.code))
            return
        execution = ServiceExecutionClient(runtime, "broker.orders", scope_ref=scope_ref)
        _assert_no_risk_or_gateway_loaded()
        control.send(("ready", runtime.run_id, startup.degraded_count))
        while True:
            command = control.recv()
            if command[0] == "close":
                runtime.close()
                control.send(("closed", runtime.state))
                return
            if command[0] == "submit":
                _, intent_id, side = command
                try:
                    record = execution.submit(
                        intent_id=intent_id,
                        signal_id=f"signal-{intent_id}",
                        instrument="BTC-USDT",
                        side=side,
                        position_effect="OPEN",
                        order_type="LIMIT",
                        quantity="2",
                        price="50000",
                        time_in_force="GTC",
                        reduce_only=False,
                        metadata_version="metadata-v1",
                    )
                except ServiceExecutionError as error:
                    record = error.record
                    control.send(
                        (
                            "submit_error",
                            error.code,
                            None if record is None else record.state,
                            None if record is None else record.dispatch_attempts,
                        )
                    )
                else:
                    control.send(
                        (
                            "submitted",
                            record.state,
                            record.provider_order_id,
                            record.dispatch_attempts,
                        )
                    )
                continue
            raise AssertionError("unknown BasicAdmission client command")
    except BaseException as error:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("client_error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        control.close()


def _missing_gate_host_process(
    control,
    raw_config,
    registry_path: str,
    store_path: str,
    provider_path: str,
    keys: dict[str, bytes],
):
    _host_process(
        control,
        raw_config,
        registry_path,
        store_path,
        provider_path,
        keys,
        missing_gate=True,
    )


def _assert_no_provider_rows(path: str) -> None:
    with sqlite3.connect(path, timeout=5.0) as connection:
        rows = connection.execute("SELECT COUNT(*) FROM authorized_provider_calls").fetchone()
    assert rows == (0,)


def _submit_command(record, intent_id: str, side: str = "BUY"):
    from tests.unit.test_service_account_integration import _client_command

    return _client_command(record, "submit", intent_id, side)


def test_spawned_execution_only_admission_uses_trusted_parameters_and_blocks_invalid_requests(
    tmp_path,
) -> None:
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
    raw_config = _service_config(endpoint)
    registry_path = str(tmp_path / "execution-owner-registry.sqlite3")
    store_path = str(tmp_path / "execution-basic.sqlite3")
    provider_path = str(tmp_path / "synthetic-provider.sqlite3")
    host_record = None
    strategy_record = None
    wrong_account_record = None
    records = []
    forced = []
    failures = []
    try:
        host_record = _start_process(
            context,
            _host_process,
            (raw_config, registry_path, store_path, provider_path, keys),
            "execution-basic-owner-host",
        )
        records.append(host_record)
        host_ready = _receive(host_record["pipe"])
        assert host_ready[0] == "ready", host_ready
        assert host_ready[1] == endpoint

        strategy_record = _start_process(
            context,
            _strategy_process,
            (
                endpoint,
                _ACCOUNT,
                _ORDER_SCOPE_REF,
                strategy_public,
                strategy_secret,
                server_public,
            ),
            "execution-basic-strategy-client",
        )
        records.append(strategy_record)
        strategy_ready = _receive(strategy_record["pipe"])
        assert strategy_ready[0] == "ready", strategy_ready
        assert strategy_ready[2] == 0

        accepted = _submit_command(strategy_record, "exe04-positive-order")
        assert accepted == (
            "submitted",
            "ACKED",
            "synthetic-provider-exe04-positive-order",
            1,
        )

        counts = _client_command(host_record, "counts")
        assert counts[0] == "counts"
        assert len(counts[1]) == 1
        positive = counts[1][0]
        assert positive[:9] == (
            "exe04-positive-order",
            _ACCOUNT,
            _STRATEGY,
            "BTCUSDT",
            "BUY",
            "2",
            "50000",
            _PROVIDER_WRITER_ID,
            1,
        )
        authorized_parameters = json.loads(positive[9])
        expected_parameters = {
            "account_ref": _ACCOUNT,
            "deadline_ns": None,
            "environment": "simulation",
            "intent_id": "exe04-positive-order",
            "metadata_version": "metadata-v1",
            "order_type": "LIMIT",
            "position_effect": "OPEN",
            "price": "50000",
            "provider_id": "PAPER",
            "provider_instrument": "BTCUSDT",
            "quantity": "2",
            "reduce_only": False,
            "side": "BUY",
            "signal_id": "signal-exe04-positive-order",
            "strategy_id": _STRATEGY,
            "tags": {},
            "time_in_force": "GTC",
            "trading_day": "20261015",
        }
        fingerprint = authorized_parameters.pop("intent_fingerprint")
        assert type(fingerprint) is str and len(fingerprint) == 64
        assert authorized_parameters == expected_parameters

        wrong_action = _submit_command(
            strategy_record,
            "exe04-wrong-action-order",
            "SELL",
        )
        assert wrong_action == ("submitted", "BLOCKED", None, 0)
        assert len(_client_command(host_record, "counts")[1]) == 1

        assert _client_command(host_record, "arm", "expire") == ("armed", "expire")
        expired = _submit_command(strategy_record, "exe04-expired-order")
        # The durable DISPATCHING claim precedes the final expiry check. The
        # request is recorded as one blocked attempt, with no provider call.
        assert expired == ("submitted", "BLOCKED", None, 1)
        expired_clock = _client_command(host_record, "counts")
        assert expired_clock[2] == 2
        assert len(expired_clock[1]) == 1

        # Account B has a distinct trusted wire grant, but the deliberately
        # mismatched server target maps it to account A. The BasicAdmission
        # context must reject that confused-deputy mapping before dispatch.
        wrong_account_record = _start_process(
            context,
            _strategy_process,
            (
                endpoint,
                _WRONG_ACCOUNT,
                _WRONG_ACCOUNT_SCOPE_REF,
                strategy_public,
                strategy_secret,
                server_public,
            ),
            "execution-basic-wrong-account-client",
        )
        records.append(wrong_account_record)
        wrong_account_start = _receive(wrong_account_record["pipe"])
        assert wrong_account_start[0] == "ready", wrong_account_start
        wrong_account = _submit_command(
            wrong_account_record,
            "exe04-wrong-account-order",
        )
        assert wrong_account == ("submit_error", "INVALID_REQUEST", None, None)
        _close_normally(wrong_account_record, expected_tag="closed")
        assert len(_client_command(host_record, "counts")[1]) == 1

        assert _client_command(host_record, "arm", "stale_owner") == (
            "armed",
            "stale_owner",
        )
        stale = _submit_command(strategy_record, "exe04-stale-owner-order")
        assert stale == ("submit_error", "OWNER_LEASE_UNAVAILABLE", "DISPATCHING", 1)
        stale_counts = _client_command(host_record, "counts")
        assert stale_counts[3] == 2
        assert stale_counts[4] is None
        assert len(stale_counts[1]) == 1

        _close_normally(strategy_record, expected_tag="closed")
        _close_normally(host_record, expected_tag="closed")

        with sqlite3.connect(provider_path, timeout=5.0) as connection:
            provider_rows = connection.execute(
                "SELECT intent_id, account_ref, strategy_id, provider_instrument, side, "
                "quantity, price, owner_id, fencing_token FROM authorized_provider_calls "
                "ORDER BY sequence"
            ).fetchall()
        assert provider_rows == [positive[:9]]

        from bt_api_execution import ExecutionScope, ExecutionState, SqliteExecutionStore

        store = SqliteExecutionStore(store_path)
        try:
            scope = ExecutionScope("PAPER", "simulation", _ACCOUNT, _STRATEGY, "20261015")
            positive_record = store.get("exe04-positive-order", scope=scope)
            assert positive_record.state is ExecutionState.ACKED
            assert positive_record.payload_sha256 == fingerprint
            assert (
                store.get("exe04-wrong-action-order", scope=scope).state is ExecutionState.BLOCKED
            )
            assert store.get("exe04-expired-order", scope=scope).state is ExecutionState.BLOCKED
            stale_record = store.get("exe04-stale-owner-order", scope=scope)
            assert stale_record.state is ExecutionState.DISPATCHING
            assert stale_record.dispatch_attempts == 1
        finally:
            store.close()
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)


def test_execution_account_owner_startup_fails_closed_without_basic_admission(
    tmp_path,
) -> None:
    from tests.unit.test_service_account_integration import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, _strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }
    raw_config = _service_config(endpoint)
    registry_path = str(tmp_path / "missing-admission-registry.sqlite3")
    store_path = str(tmp_path / "missing-admission-store.sqlite3")
    provider_path = str(tmp_path / "missing-admission-provider.sqlite3")
    record = None
    records = []
    forced = []
    failures = []
    try:
        record = _start_process(
            context,
            _missing_gate_host_process,
            (raw_config, registry_path, store_path, provider_path, keys),
            "execution-basic-missing-gate-host",
        )
        records.append(record)
        result = _receive(record["pipe"])
        assert result[0] == "startup_failed", result
        assert result[1] == "ADMISSION_POLICY_REQUIRED", result
        assert result[2] in {"failed", "closed"}, result
        record["process"].join(15)
        assert not record["process"].is_alive()
        assert record["process"].exitcode == 0
        record["pipe"].close()
        record["process"].close()
        record["closed"] = True
        _assert_no_provider_rows(provider_path)
    finally:
        for item in reversed(records):
            _bounded_reap_spawned_process(item, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
