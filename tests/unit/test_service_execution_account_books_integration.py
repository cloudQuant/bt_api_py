"""EXE03 account books through the trusted owner and real CURVE service path."""

from __future__ import annotations

import multiprocessing
import socket
import time
from contextlib import suppress
from decimal import Decimal
from typing import Any

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import ExecutionScope

from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_DOMAIN = "paper:execution-account-books-integration"
_SERVICE_ID = "execution-account-books-service"
_SERVICE_PRINCIPAL = "execution-account-books-service-principal"
_INSTANCE = "execution-account-books-owner"
_SERVICE_SCOPE_REF = "execution-account-books-service-root-v1"
_SERVER_SECURITY_REF = "execution-account-books-server-curve-v1"
_CLIENT_SECURITY_REF = "execution-account-books-client-curve-v1"
_ACCOUNT = "account-books-account"
_ACCOUNT_KEY = ExecutionScope("PAPER", "simulation", _ACCOUNT, "account-reader").account_key
_STRATEGY_A = "strategy-a"
_STRATEGY_B = "strategy-b"
_MANUAL_BOOK = "manual-book-a"
_STRATEGY_A_PRINCIPAL = "execution-account-books-strategy-a"
_STRATEGY_B_PRINCIPAL = "execution-account-books-strategy-b"
_ACCOUNT_READER_PRINCIPAL = "execution-account-books-account-reader"
_MANUAL_READER_PRINCIPAL = "execution-account-books-manual-reader"
_STRATEGY_A_SCOPE_REF = "account-books-strategy-a-v1"
_STRATEGY_B_SCOPE_REF = "account-books-strategy-b-v1"
_ACCOUNT_SCOPE_REF = "account-books-account-reader-v1"
_MANUAL_SCOPE_REF = "account-books-manual-reader-v1"
_INSTRUMENT = "PAPER:SIM:BTC-USDT"
_PROVIDER_INSTRUMENT = "BTCUSDT"
_ORDER_ENTRY_PORT = "service.order_entry"
_ORDER_QUERY_PORT = "service.order_query"
_QUERY_OPERATION = "orders.query"
_BOOKS_OPERATION = "execution.account_books.query"
_MAX_MESSAGE_BYTES = 65_536

_WIRE_A = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY_A)
_WIRE_B = WireScope(_DOMAIN, account_ref=_ACCOUNT, strategy_ref=_STRATEGY_B)
_WIRE_MANUAL = WireScope(_DOMAIN, account_ref=_ACCOUNT, manual_book_ref=_MANUAL_BOOK)
_WIRE_ACCOUNT = WireScope(_DOMAIN, account_ref=_ACCOUNT)
_WIRE_FORGED_MANUAL = WireScope(_DOMAIN, account_ref=_ACCOUNT, manual_book_ref="manual-book-forged")
_IDENTITY_A = WireIdentity(WireRole.STRATEGY_CLIENT, _STRATEGY_A_PRINCIPAL, _STRATEGY_A)
_IDENTITY_B = WireIdentity(WireRole.STRATEGY_CLIENT, _STRATEGY_B_PRINCIPAL, _STRATEGY_B)
_IDENTITY_ACCOUNT = WireIdentity(
    WireRole.APPLICATION_CLIENT, _ACCOUNT_READER_PRINCIPAL, "account-reader-app"
)
_IDENTITY_MANUAL = WireIdentity(
    WireRole.APPLICATION_CLIENT, _MANUAL_READER_PRINCIPAL, "manual-reader-app"
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
        "transport_ref": "execution-account-books-listener-v1",
        "registration_policy_ref": "execution-account-books-registration-v1",
        "service_requirements_ref": "execution-account-books-requirements-v1",
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _INSTANCE,
                    "bind_ports": [_ORDER_ENTRY_PORT, _ORDER_QUERY_PORT],
                    "options": {"role": "account_owner"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "execution-account-books-listener-v1": {
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


def _client_config(
    endpoint: str,
    *,
    role: str,
    registration_field: str,
    registration_ref: str,
    mount: str,
    scope_ref: str,
    account_ref: str,
    additional_mounts: tuple[str, ...] = (),
) -> dict[str, Any]:
    bindings = [mount, *additional_mounts]
    return {
        "client_config_schema_version": 1,
        "process_role": role,
        "registration": {registration_field: registration_ref},
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": selected_mount,
                            "service_id": _SERVICE_ID,
                            "instance_id": _INSTANCE,
                            "scope_ref": scope_ref,
                            "transport_ref": "execution-account-books-connect-v1",
                            "account_ref": account_ref,
                        }
                        for selected_mount in bindings
                    ],
                    "options": {"request_timeout_ms": 2_000},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "execution-account-books-connect-v1": {
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


def _authority(config, keys: dict[str, bytes]) -> TrustedServiceAuthority:
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    read_operations = frozenset({_QUERY_OPERATION, _BOOKS_OPERATION})
    a_resources = (
        ResourceGrant(
            _INSTANCE,
            _ORDER_ENTRY_PORT,
            _STRATEGY_A_SCOPE_REF,
            _WIRE_A,
            frozenset({"orders.submit", "orders.cancel"}),
        ),
        ResourceGrant(
            _INSTANCE,
            _ORDER_QUERY_PORT,
            _STRATEGY_A_SCOPE_REF,
            _WIRE_A,
            read_operations,
        ),
    )
    b_resources = (
        ResourceGrant(
            _INSTANCE,
            _ORDER_ENTRY_PORT,
            _STRATEGY_B_SCOPE_REF,
            _WIRE_B,
            frozenset({"orders.submit", "orders.cancel"}),
        ),
        ResourceGrant(
            _INSTANCE,
            _ORDER_QUERY_PORT,
            _STRATEGY_B_SCOPE_REF,
            _WIRE_B,
            read_operations,
        ),
    )
    account_resource = ResourceGrant(
        _INSTANCE, _ORDER_QUERY_PORT, _ACCOUNT_SCOPE_REF, _WIRE_ACCOUNT, read_operations
    )
    manual_resource = ResourceGrant(
        _INSTANCE, _ORDER_QUERY_PORT, _MANUAL_SCOPE_REF, _WIRE_MANUAL, read_operations
    )
    return TrustedServiceAuthority(
        identity=service_identity,
        scope=WireScope(_DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="execution-account-books-service-scope-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="curve-key-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="execution-account-books-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="execution-account-books-requirements-v1",
        curve_public_key=keys["server_public"],
        curve_secret_key=keys["server_secret"],
        principals={
            keys["strategy_a_public"]: _STRATEGY_A_PRINCIPAL,
            keys["strategy_b_public"]: _STRATEGY_B_PRINCIPAL,
            keys["account_reader_public"]: _ACCOUNT_READER_PRINCIPAL,
            keys["manual_reader_public"]: _MANUAL_READER_PRINCIPAL,
        },
        grants={
            _STRATEGY_A_PRINCIPAL: (PrincipalGrant(_IDENTITY_A, a_resources),),
            _STRATEGY_B_PRINCIPAL: (PrincipalGrant(_IDENTITY_B, b_resources),),
            _ACCOUNT_READER_PRINCIPAL: (PrincipalGrant(_IDENTITY_ACCOUNT, (account_resource,)),),
            _MANUAL_READER_PRINCIPAL: (PrincipalGrant(_IDENTITY_MANUAL, (manual_resource,)),),
        },
    )


class _SyntheticAuthorizedProvider:
    """Return real partial-fill observations from the exact authorized facts."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def submit_authorized(self, intent, authorization):
        from bt_api_execution import ExecutionState, ProviderObservation

        parameters = authorization.parameters
        if (
            parameters.intent_id != intent.intent_id
            or parameters.strategy_id != intent.scope.strategy_id
        ):
            raise AssertionError("provider received unbound account-book intent facts")
        self.calls.append(parameters.to_payload())
        if parameters.strategy_id == _STRATEGY_A:
            filled, average, commission = Decimal("2"), Decimal("100"), Decimal("0.12")
        elif parameters.strategy_id == _STRATEGY_B:
            filled, average, commission = Decimal("1"), Decimal("101"), Decimal("0.08")
        else:
            raise AssertionError("unexpected provider strategy")
        if filled >= parameters.quantity:
            raise AssertionError("test provider must return a partial fill")
        return ProviderObservation(
            intent.intent_id,
            ExecutionState.PARTIALLY_FILLED,
            provider_order_id=f"synthetic-{parameters.strategy_id}-{intent.intent_id}",
            filled_quantity=filled,
            average_price=average,
            cumulative_commission=commission,
            cumulative_commission_currency="USDT",
        )

    def cancel(self, intent):
        from bt_api_execution import CancelObservation

        return CancelObservation.accepted(
            intent.cancel_id,
            intent.target_intent_id,
            intent.provider_order_id,
        )


class _SyntheticAcceptedDispatch:
    """Minimal provider boundary used by the deterministic cut-race test."""

    def __init__(self) -> None:
        self.calls = 0

    def submit(self, intent):
        from bt_api_execution import ProviderObservation

        self.calls += 1
        return ProviderObservation.accepted(intent.intent_id, "synthetic-race-order")

    def cancel(self, intent):
        from bt_api_execution import CancelObservation

        return CancelObservation.accepted(
            intent.cancel_id,
            intent.target_intent_id,
            intent.provider_order_id,
        )


class _TrustedAccountBooksSource:
    """Trusted owner resource with a complete directory and snapshot cut."""

    def __init__(self, store, targets) -> None:
        self._store = store
        self._targets = dict(targets)
        self._baseline_as_of_ns = time.time_ns()
        self.read_count = 0
        self.last_cut: tuple[str, int] | None = None
        self.large_next = False
        self.after_snapshot_read = None
        self._account_identity = _IDENTITY_ACCOUNT
        self._manual_identity = _IDENTITY_MANUAL

    def authorize_account_view(self, *, principal_ref, identity, scope, account_key) -> bool:
        return (
            principal_ref == _ACCOUNT_READER_PRINCIPAL
            and identity == self._account_identity
            and scope == _WIRE_ACCOUNT
            and account_key == _ACCOUNT_KEY
        )

    def authorize_manual_book_view(
        self, *, principal_ref, identity, scope, account_key, manual_book_ref
    ) -> bool:
        return (
            principal_ref == _MANUAL_READER_PRINCIPAL
            and identity == self._manual_identity
            and scope == _WIRE_MANUAL
            and account_key == _ACCOUNT_KEY
            and manual_book_ref == _MANUAL_BOOK
        )

    def read_inputs(self, *, account_key, journal_generation, execution_cursor):
        from bt_api_execution import (
            AllocationOwnerKind,
            CurrencyAmount,
            ExecutionAccountSnapshot,
            ExecutionAllocationBaseline,
            ExecutionOwnerScope,
            ExecutionScopeDirectory,
            ExternalActivitySnapshot,
            ExternalFillFact,
            ExternalOrderFact,
            OwnerPosition,
            PositionFact,
            Side,
        )

        from bt_api_py.service_execution_account_books import ExecutionAccountBookReadInputsV1

        if account_key != _ACCOUNT_KEY:
            raise AssertionError("account-book source received another account")
        self.read_count += 1
        self.last_cut = (journal_generation, execution_cursor)
        minimum_as_of_ns = self._baseline_as_of_ns + (803 if self.large_next else 3)
        as_of_ns = max(time.time_ns(), minimum_as_of_ns)
        directory_revision = "account-books-owner-directory-r1"
        provider_generation = "synthetic-provider-generation-1"
        directory = ExecutionScopeDirectory(
            account_key=_ACCOUNT_KEY,
            source="trusted-account-owner-registry",
            source_ref="complete-owner-directory-r1",
            revision=directory_revision,
            covers_from_ns=self._baseline_as_of_ns,
            as_of_ns=as_of_ns,
            scopes=tuple(
                ExecutionOwnerScope(
                    execution_scope,
                    (
                        AllocationOwnerKind.STRATEGY
                        if wire_scope.strategy_ref is not None
                        else AllocationOwnerKind.MANUAL_BOOK
                    ),
                    (
                        execution_scope.strategy_id
                        if wire_scope.strategy_ref is not None
                        else wire_scope.manual_book_ref
                    ),
                    wire_scope,
                )
                for wire_scope, execution_scope in self._targets.items()
            ),
        )
        baseline_snapshot = ExecutionAccountSnapshot(
            account_key=_ACCOUNT_KEY,
            source="synthetic-provider-net-position-snapshot",
            source_ref="provider-baseline-r1",
            generation=provider_generation,
            journal_generation=journal_generation,
            as_of_ns=self._baseline_as_of_ns,
            execution_cursor=0,
            position_mode="NET",
            positions=(PositionFact(_INSTRUMENT, Decimal("0.5")),),
            cumulative_fees=(CurrencyAmount("USDT", Decimal("0.10")),),
            fee_coverage_start_ns=self._baseline_as_of_ns,
        )
        allocation_baseline = ExecutionAllocationBaseline(
            account_key=_ACCOUNT_KEY,
            source="trusted-virtual-position-ledger",
            source_ref="virtual-baseline-r1",
            revision="virtual-baseline-revision-1",
            scope_directory_revision=directory_revision,
            provider_generation=provider_generation,
            journal_generation=journal_generation,
            as_of_ns=self._baseline_as_of_ns,
            execution_cursor=0,
            positions=(
                OwnerPosition(
                    AllocationOwnerKind.STRATEGY,
                    _STRATEGY_A,
                    _INSTRUMENT,
                    Decimal("1"),
                ),
                OwnerPosition(
                    AllocationOwnerKind.STRATEGY,
                    _STRATEGY_B,
                    _INSTRUMENT,
                    Decimal("-0.5"),
                ),
                OwnerPosition(
                    AllocationOwnerKind.MANUAL_BOOK,
                    _MANUAL_BOOK,
                    _INSTRUMENT,
                    Decimal("0.25"),
                ),
            ),
        )
        extra_fills = []
        extra_orders = []
        if self.large_next:
            self.large_next = False
            for index in range(800):
                fill_ref = f"large-fill-{index:04d}"
                order_ref = f"large-order-{index:04d}"
                observed_at = self._baseline_as_of_ns + index + 3
                extra_orders.append(
                    ExternalOrderFact(
                        order_ref,
                        _INSTRUMENT,
                        Side.BUY,
                        Decimal("0.001"),
                        Decimal("0.001"),
                        "FILLED",
                        observed_at_ns=observed_at,
                        owner_kind=AllocationOwnerKind.MANUAL_BOOK,
                        owner_ref=_MANUAL_BOOK,
                    )
                )
                extra_fills.append(
                    ExternalFillFact(
                        fill_ref,
                        _INSTRUMENT,
                        Side.BUY,
                        Decimal("0.001"),
                        Decimal("1"),
                        occurred_at_ns=observed_at,
                        fee=Decimal("0.001"),
                        fee_currency="USDT",
                        provider_order_ref=order_ref,
                    )
                )
        external_activity = ExternalActivitySnapshot(
            account_key=_ACCOUNT_KEY,
            source="synthetic-provider-complete-account-activity",
            source_ref="activity-window-r1",
            generation=provider_generation,
            coverage_start_ns=self._baseline_as_of_ns,
            coverage_end_ns=as_of_ns,
            fills=(
                ExternalFillFact(
                    "provider-fill-manual-1",
                    _INSTRUMENT,
                    Side.BUY,
                    Decimal("0.5"),
                    Decimal("102"),
                    occurred_at_ns=self._baseline_as_of_ns + 1,
                    fee=Decimal("0.02"),
                    fee_currency="USDT",
                    provider_order_ref="provider-order-manual-1",
                ),
                ExternalFillFact(
                    "provider-fill-unallocated-1",
                    _INSTRUMENT,
                    Side.SELL,
                    Decimal("0.25"),
                    Decimal("99"),
                    occurred_at_ns=self._baseline_as_of_ns + 2,
                    fee=Decimal("0.01"),
                    fee_currency="USDT",
                    provider_order_ref="provider-order-unallocated-1",
                ),
                *extra_fills,
            ),
            orders=(
                ExternalOrderFact(
                    "provider-order-manual-1",
                    _INSTRUMENT,
                    Side.BUY,
                    Decimal("0.5"),
                    Decimal("0.5"),
                    "FILLED",
                    observed_at_ns=self._baseline_as_of_ns + 1,
                    owner_kind=AllocationOwnerKind.MANUAL_BOOK,
                    owner_ref=_MANUAL_BOOK,
                ),
                ExternalOrderFact(
                    "provider-order-unallocated-1",
                    _INSTRUMENT,
                    Side.SELL,
                    Decimal("0.25"),
                    Decimal("0.25"),
                    "FILLED",
                    observed_at_ns=self._baseline_as_of_ns + 2,
                ),
                *extra_orders,
            ),
        )
        current_snapshot = ExecutionAccountSnapshot(
            account_key=_ACCOUNT_KEY,
            source=baseline_snapshot.source,
            source_ref="provider-current-r1",
            generation=provider_generation,
            journal_generation=journal_generation,
            as_of_ns=as_of_ns,
            execution_cursor=execution_cursor,
            position_mode="NET",
            positions=(PositionFact(_INSTRUMENT, Decimal("1.75")),),
            cumulative_fees=(CurrencyAmount("USDT", Decimal("0.36")),),
            fee_coverage_start_ns=self._baseline_as_of_ns,
        )
        inputs = ExecutionAccountBookReadInputsV1(
            directory,
            baseline_snapshot,
            current_snapshot,
            allocation_baseline,
            external_activity,
        )
        callback = self.after_snapshot_read
        self.after_snapshot_read = None
        if callback is not None:
            callback()
        return inputs


def _targets():
    return {
        _WIRE_A: ExecutionScope("PAPER", "simulation", _ACCOUNT, _STRATEGY_A, "20261015"),
        _WIRE_B: ExecutionScope("PAPER", "simulation", _ACCOUNT, _STRATEGY_B, "20261015"),
        _WIRE_MANUAL: ExecutionScope(
            "PAPER", "simulation", _ACCOUNT, "manual-execution", "20261015"
        ),
    }


def _host_process(control, raw_config, registry_path: str, store_path: str, keys):
    from bt_api_execution import (
        ExecutionAdmissionValidatorRegistry,
        ExecutionBasicAdmissionGate,
        ExecutionProviderValidatorV1,
        OrderType,
        PositionEffect,
        Side,
        SqliteExecutionStore,
        TimeInForce,
    )

    from bt_api_py.service_components import ComponentResources

    config = parse_service_config(raw_config)
    store = SqliteExecutionStore(store_path)
    targets = _targets()
    provider = _SyntheticAuthorizedProvider()
    source = _TrustedAccountBooksSource(store, targets)
    validator = ExecutionProviderValidatorV1(
        provider_id="PAPER",
        validator_id="exe03-account-books-synthetic-provider",
        version="v1",
        dispatch_port=provider,
        instrument_map={_INSTRUMENT: _PROVIDER_INSTRUMENT},
        quantity_steps={_INSTRUMENT: Decimal("0.1")},
        max_quantities={_INSTRUMENT: Decimal("100")},
        price_ticks={_INSTRUMENT: Decimal("0.01")},
        supported_order_types=(OrderType.LIMIT,),
        supported_time_in_force=(TimeInForce.GTC,),
        supported_actions=(
            (Side.BUY, PositionEffect.OPEN),
            (Side.SELL, PositionEffect.OPEN),
        ),
        supports_reduce_only=False,
    )
    gate = ExecutionBasicAdmissionGate(
        store,
        ExecutionAdmissionValidatorRegistry((validator,)),
    )

    def resource_provider(component, instance_id, options, scope):
        if component != "execution" or instance_id != _INSTANCE:
            raise AssertionError("unexpected service resource request")
        return ComponentResources.borrowed(
            {
                "store": store,
                "targets": targets,
                "dispatch": provider,
                "admission": {"basic_admission_gate": gate},
                "account_books": source,
            },
            ready=lambda: True,
        )

    host = None
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
            control.send(("startup_failed", error.code, host.state))
            store.close()
            return
        control.send(("ready", host.endpoint, host.state))
        while True:
            if control.poll(0.01):
                command = control.recv()
                if command[0] == "metrics":
                    control.send(
                        (
                            "metrics",
                            tuple(dict(row) for row in provider.calls),
                            source.read_count,
                            source.last_cut,
                        )
                    )
                    continue
                if command[0] == "oversize":
                    source.large_next = True
                    control.send(("oversize_ready",))
                    continue
                if command[0] == "close":
                    host.close()
                    store.close()
                    control.send(("closed", host.state))
                    return
                raise AssertionError("unknown account-book host command")
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


def _runtime(
    endpoint: str,
    *,
    identity: WireIdentity,
    client_public: bytes,
    client_secret: bytes,
    server_public: bytes,
    role: str,
    registration_field: str,
    registration_ref: str,
    mount: str,
    scope_ref: str,
    scope: WireScope,
) -> ServiceClientRuntime:
    config = parse_client_config(
        _client_config(
            endpoint,
            role=role,
            registration_field=registration_field,
            registration_ref=registration_ref,
            mount=mount,
            scope_ref=scope_ref,
            account_ref=_ACCOUNT,
            additional_mounts=(("broker.order_query",) if mount == "broker.orders" else ()),
        )
    )
    peer = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    return ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda _ref: scope,
        peer_resolver=lambda _service_id: peer,
        transport_factory=ZmqTransportFactory(
            {
                _CLIENT_SECURITY_REF: ClientCurveKeys(
                    server_public,
                    client_public,
                    client_secret,
                )
            }
        ),
        requested_operations=(
            {
                "broker.orders": frozenset({"orders.submit", "orders.cancel"}),
                "broker.order_query": frozenset({_QUERY_OPERATION, _BOOKS_OPERATION}),
            }
            if mount == "broker.orders"
            else {mount: frozenset({_QUERY_OPERATION, _BOOKS_OPERATION})}
        ),
    )


def _order_arguments(intent_id: str, *, quantity: str, side: str, price: str) -> dict[str, Any]:
    return {
        "intent_id": intent_id,
        "signal_id": f"signal-{intent_id}",
        "instrument": _INSTRUMENT,
        "side": side,
        "position_effect": "OPEN",
        "order_type": "LIMIT",
        "quantity": quantity,
        "price": price,
        "time_in_force": "GTC",
        "reduce_only": False,
        "metadata_version": "metadata-account-books-v1",
        "deadline_ns": None,
        "tags": {},
    }


def _book(result: dict[str, Any], owner_kind: str, owner_ref: str) -> dict[str, Any]:
    return next(
        item
        for item in result["books"]
        if item["owner_kind"] == owner_kind and item["owner_ref"] == owner_ref
    )


def _position(book: dict[str, Any]) -> str:
    return next(
        item["net_quantity"]
        for item in book["current_positions"]
        if item["instrument"] == _INSTRUMENT
    )


def _close_client(runtime: ServiceClientRuntime | None) -> None:
    if runtime is not None and runtime.state not in {"closed", "failed"}:
        runtime.close()


@pytest.mark.unit
def test_exe03_account_books_use_authorized_curved_views_and_exact_reconciliation_cut(
    tmp_path,
) -> None:
    from tests.unit.test_service_account_integration import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _close_normally,
        _receive,
        _start_process,
    )

    context = multiprocessing.get_context("spawn")
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_a_public, strategy_a_secret = zmq.curve_keypair()
    strategy_b_public, strategy_b_secret = zmq.curve_keypair()
    account_public, account_secret = zmq.curve_keypair()
    manual_public, manual_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_a_public": strategy_a_public,
        "strategy_b_public": strategy_b_public,
        "account_reader_public": account_public,
        "manual_reader_public": manual_public,
    }
    host_record = None
    records = []
    runtimes: list[ServiceClientRuntime] = []
    forced: list[str] = []
    failures: list[str] = []
    try:
        host_record = _start_process(
            context,
            _host_process,
            (
                _service_config(endpoint),
                str(tmp_path / "exe03-account-books-registry.sqlite3"),
                str(tmp_path / "exe03-account-books-store.sqlite3"),
                keys,
            ),
            "exe03-account-books-service-host",
        )
        records.append(host_record)
        ready = _receive(host_record["pipe"])
        assert ready == ("ready", endpoint, "started")

        strategy_a = _runtime(
            endpoint,
            identity=_IDENTITY_A,
            client_public=strategy_a_public,
            client_secret=strategy_a_secret,
            server_public=server_public,
            role="strategy_client",
            registration_field="strategy_ref",
            registration_ref=_STRATEGY_A,
            mount="broker.orders",
            scope_ref=_STRATEGY_A_SCOPE_REF,
            scope=_WIRE_A,
        )
        strategy_b = _runtime(
            endpoint,
            identity=_IDENTITY_B,
            client_public=strategy_b_public,
            client_secret=strategy_b_secret,
            server_public=server_public,
            role="strategy_client",
            registration_field="strategy_ref",
            registration_ref=_STRATEGY_B,
            mount="broker.orders",
            scope_ref=_STRATEGY_B_SCOPE_REF,
            scope=_WIRE_B,
        )
        account_reader = _runtime(
            endpoint,
            identity=_IDENTITY_ACCOUNT,
            client_public=account_public,
            client_secret=account_secret,
            server_public=server_public,
            role="application_client",
            registration_field="application_ref",
            registration_ref="account-reader-app",
            mount="application.order_query",
            scope_ref=_ACCOUNT_SCOPE_REF,
            scope=_WIRE_ACCOUNT,
        )
        manual_reader = _runtime(
            endpoint,
            identity=_IDENTITY_MANUAL,
            client_public=manual_public,
            client_secret=manual_secret,
            server_public=server_public,
            role="application_client",
            registration_field="application_ref",
            registration_ref="manual-reader-app",
            mount="application.order_query",
            scope_ref=_MANUAL_SCOPE_REF,
            scope=_WIRE_MANUAL,
        )
        runtimes.extend((strategy_a, strategy_b, account_reader, manual_reader))
        for runtime in runtimes:
            assert runtime.start().degraded_count == 0

        submitted_a = strategy_a.call(
            "broker.orders",
            "orders.submit",
            _order_arguments("shared-intent", quantity="5", side="BUY", price="100"),
            scope_ref=_STRATEGY_A_SCOPE_REF,
        )
        submitted_b = strategy_b.call(
            "broker.orders",
            "orders.submit",
            _order_arguments("shared-intent", quantity="3", side="SELL", price="101"),
            scope_ref=_STRATEGY_B_SCOPE_REF,
        )
        assert submitted_a["ok"] is True and submitted_a["code"] == "OK"
        assert submitted_b["ok"] is True and submitted_b["code"] == "OK"
        assert submitted_a["result"]["record"]["state"] == "PARTIALLY_FILLED"
        assert submitted_b["result"]["record"]["state"] == "PARTIALLY_FILLED"
        assert submitted_a["result"]["record"]["filled_quantity"] == "2"
        assert submitted_b["result"]["record"]["filled_quantity"] == "1"

        strategy_a_view = strategy_a.call(
            "broker.order_query", _BOOKS_OPERATION, {}, scope_ref=_STRATEGY_A_SCOPE_REF
        )
        assert strategy_a_view["ok"] is True and strategy_a_view["code"] == "OK", strategy_a_view
        strategy_a_result = strategy_a_view["result"]
        assert strategy_a_result["view"] == "OWNER_BOOK"
        assert [(item["owner_kind"], item["owner_ref"]) for item in strategy_a_result["books"]] == [
            ("STRATEGY", _STRATEGY_A)
        ]
        assert len(strategy_a_result["managed_orders"]) == 1
        assert [
            (item["owner_kind"], item["owner_ref"]) for item in strategy_a_result["managed_orders"]
        ] == [("STRATEGY", _STRATEGY_A)]
        assert strategy_a_result["managed_orders"][0]["intent_id"] == "shared-intent"
        assert [
            (item["owner_kind"], item["owner_ref"]) for item in strategy_a_result["managed_fills"]
        ] == [("STRATEGY", _STRATEGY_A)]
        assert strategy_a_result["external_orders"] == ()
        assert strategy_a_result["external_fills"] == ()
        assert "provider_position_change" not in strategy_a_result
        assert "position_residual" not in strategy_a_result
        assert "fee_residual" not in strategy_a_result

        strategy_b_view = strategy_b.call(
            "broker.order_query", _BOOKS_OPERATION, {}, scope_ref=_STRATEGY_B_SCOPE_REF
        )
        assert strategy_b_view["ok"] is True and strategy_b_view["code"] == "OK"
        strategy_b_result = strategy_b_view["result"]
        assert [(item["owner_kind"], item["owner_ref"]) for item in strategy_b_result["books"]] == [
            ("STRATEGY", _STRATEGY_B)
        ]
        assert len(strategy_b_result["managed_orders"]) == 1
        assert [
            (item["owner_kind"], item["owner_ref"]) for item in strategy_b_result["managed_orders"]
        ] == [("STRATEGY", _STRATEGY_B)]
        assert strategy_b_result["managed_orders"][0]["intent_id"] == "shared-intent"
        assert [
            (item["owner_kind"], item["owner_ref"]) for item in strategy_b_result["managed_fills"]
        ] == [("STRATEGY", _STRATEGY_B)]
        assert strategy_b_result["external_orders"] == ()
        assert strategy_b_result["external_fills"] == ()

        manual_view = manual_reader.call(
            "application.order_query", _BOOKS_OPERATION, {}, scope_ref=_MANUAL_SCOPE_REF
        )
        assert manual_view["ok"] is True and manual_view["code"] == "OK"
        manual_result = manual_view["result"]
        assert manual_result["view"] == "OWNER_BOOK"
        assert [(item["owner_kind"], item["owner_ref"]) for item in manual_result["books"]] == [
            ("MANUAL_BOOK", _MANUAL_BOOK)
        ]
        assert [item["provider_order_ref"] for item in manual_result["external_orders"]] == [
            "provider-order-manual-1"
        ]
        assert [item["fill_ref"] for item in manual_result["external_fills"]] == [
            "provider-fill-manual-1"
        ]
        assert "position_residual" not in manual_result

        account_view = account_reader.call(
            "application.order_query", _BOOKS_OPERATION, {}, scope_ref=_ACCOUNT_SCOPE_REF
        )
        assert account_view["ok"] is True and account_view["code"] == "OK"
        account_result = account_view["result"]
        assert account_result["view"] == "ACCOUNT"
        assert account_result["coverage"]["complete"] is True
        assert account_result["coverage"]["scope_directory_revision"] == (
            "account-books-owner-directory-r1"
        )
        assert len(account_result["managed_orders"]) == 2
        assert {
            (item["owner_kind"], item["owner_ref"], item["intent_id"])
            for item in account_result["managed_orders"]
        } == {
            ("STRATEGY", _STRATEGY_A, "shared-intent"),
            ("STRATEGY", _STRATEGY_B, "shared-intent"),
        }
        assert len(account_result["managed_fills"]) == 2
        assert {
            (item["owner_kind"], item["owner_ref"], item["intent_id"], item["fee_delta"])
            for item in account_result["managed_fills"]
        } == {
            ("STRATEGY", _STRATEGY_A, "shared-intent", "0.12"),
            ("STRATEGY", _STRATEGY_B, "shared-intent", "0.08"),
        }
        assert _position(_book(account_result, "STRATEGY", _STRATEGY_A)) == "3"
        assert _position(_book(account_result, "STRATEGY", _STRATEGY_B)) == "-1.5"
        assert _position(_book(account_result, "MANUAL_BOOK", _MANUAL_BOOK)) == "0.75"
        assert _position(_book(account_result, "UNALLOCATED", "UNALLOCATED")) == "-0.50"
        assert [item["net_quantity"] for item in account_result["provider_position_change"]] == [
            "1.25"
        ]
        assert [item["net_quantity"] for item in account_result["position_residual"]] == []
        assert account_result["fee_residual"] == ({"currency": "USDT", "amount": "0.03"},)
        assert _book(account_result, "UNALLOCATED", "UNALLOCATED")["fees_since_baseline"] == (
            {"currency": "USDT", "amount": "0.04"},
        )
        assert [item["fill_ref"] for item in account_result["external_fills"]] == [
            "provider-fill-manual-1",
            "provider-fill-unallocated-1",
        ]

        host_record["pipe"].send(("metrics",))
        before_invalid = _receive(host_record["pipe"])
        assert before_invalid[0] == "metrics"
        reads_before_invalid = before_invalid[2]
        invalid_arguments = account_reader.call(
            "application.order_query",
            _BOOKS_OPERATION,
            {"complete": True, "manual_book_ref": _MANUAL_BOOK},
            scope_ref=_ACCOUNT_SCOPE_REF,
        )
        assert invalid_arguments == {"ok": False, "code": "INVALID_REQUEST", "result": {}}
        host_record["pipe"].send(("metrics",))
        after_invalid = _receive(host_record["pipe"])
        assert after_invalid[0] == "metrics"
        assert after_invalid[2] == reads_before_invalid

        forged = _runtime(
            endpoint,
            identity=_IDENTITY_ACCOUNT,
            client_public=account_public,
            client_secret=account_secret,
            server_public=server_public,
            role="application_client",
            registration_field="application_ref",
            registration_ref="account-reader-app",
            mount="application.order_query",
            scope_ref=_ACCOUNT_SCOPE_REF,
            scope=_WIRE_FORGED_MANUAL,
        )
        try:
            with pytest.raises(ServiceRuntimeError) as denied:
                forged.start()
            assert denied.value.code == "SCOPE_DENIED"
        finally:
            forged.close()
        host_record["pipe"].send(("metrics",))
        after_forgery = _receive(host_record["pipe"])
        assert after_forgery[0] == "metrics"
        assert after_forgery[2] == reads_before_invalid

        host_record["pipe"].send(("oversize",))
        assert _receive(host_record["pipe"]) == ("oversize_ready",)
        too_large = account_reader.call(
            "application.order_query", _BOOKS_OPERATION, {}, scope_ref=_ACCOUNT_SCOPE_REF
        )
        assert too_large == {"ok": False, "code": "REPLY_TOO_LARGE", "result": {}}

        host_record["pipe"].send(("metrics",))
        metrics = _receive(host_record["pipe"])
        assert metrics[0] == "metrics"
        provider_calls, read_count, last_cut = metrics[1:]
        assert len(provider_calls) == 2
        assert {row["intent_id"] for row in provider_calls} == {"shared-intent"}
        assert {row["strategy_id"] for row in provider_calls} == {_STRATEGY_A, _STRATEGY_B}
        assert {row["provider_instrument"] for row in provider_calls} == {_PROVIDER_INSTRUMENT}
        assert read_count == 5
        assert type(last_cut) is tuple and last_cut[1] > 0

        for runtime in reversed(runtimes):
            runtime.close()
        runtimes.clear()
        _close_normally(host_record, expected_tag="closed")
    finally:
        for runtime in reversed(runtimes):
            _close_client(runtime)
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)


@pytest.mark.unit
def test_exe03_account_book_operation_is_not_advertised_without_source(tmp_path) -> None:
    from bt_api_execution import SqliteExecutionStore

    from bt_api_py.service_execution_account_books import ExecutionOrderEntryService

    store = SqliteExecutionStore(str(tmp_path / "execution-without-account-books.sqlite3"))
    service = ExecutionOrderEntryService(
        store,
        _targets(),
        _SyntheticAcceptedDispatch(),
        allow_unprotected=True,
        writer_id="exe03-no-books-source-test",
    )
    try:
        assert service.supported_operations == frozenset(
            {"orders.submit", "orders.cancel", "orders.query"}
        )
        response = service.handle(
            _BOOKS_OPERATION,
            principal_ref=_ACCOUNT_READER_PRINCIPAL,
            identity=_IDENTITY_ACCOUNT,
            scope=_WIRE_ACCOUNT,
            arguments={},
        )
        assert response["ok"] is False
        assert response["code"] == "UNKNOWN_OPERATION"
    finally:
        store.close()


@pytest.mark.unit
def test_exe03_account_book_refuses_a_snapshot_if_execution_advances_after_its_cut(
    tmp_path,
) -> None:
    from bt_api_execution import SqliteExecutionStore
    from bt_api_execution.service_order_entry import ExecutionOrderEntryService

    from bt_api_py.service_execution_account_books import ExecutionAccountBookReadAdapter

    store = SqliteExecutionStore(str(tmp_path / "execution-cut-race.sqlite3"))
    targets = _targets()
    dispatch = _SyntheticAcceptedDispatch()
    writer = ExecutionOrderEntryService(
        store,
        targets,
        dispatch,
        allow_unprotected=True,
        writer_id="exe03-cut-race-test",
    )
    source = _TrustedAccountBooksSource(store, targets)

    def append_managed_order_after_snapshot() -> None:
        result = writer.handle(
            "orders.submit",
            principal_ref=_STRATEGY_A_PRINCIPAL,
            identity=_IDENTITY_A,
            scope=_WIRE_A,
            arguments=_order_arguments("cut-race-intent", quantity="1", side="BUY", price="100"),
        )
        assert result["ok"] is True and result["code"] == "OK", result
        assert dispatch.calls == 1, result

    source.after_snapshot_read = append_managed_order_after_snapshot
    adapter = ExecutionAccountBookReadAdapter(store, targets, source)
    try:
        response = adapter.handle(
            _BOOKS_OPERATION,
            principal_ref=_ACCOUNT_READER_PRINCIPAL,
            identity=_IDENTITY_ACCOUNT,
            scope=_WIRE_ACCOUNT,
            arguments={},
        )
        assert response == {"ok": False, "code": "UNAVAILABLE", "result": {}}
        assert source.read_count == 1
        assert dispatch.calls == 1
    finally:
        store.close()
