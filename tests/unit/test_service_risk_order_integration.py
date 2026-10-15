"""Risk admission enforced by the spawned account-owner order service."""

from __future__ import annotations

import inspect
import multiprocessing
import sqlite3
from contextlib import suppress
from decimal import Decimal
from pathlib import Path
from typing import Any

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import ExecutionScope, SqliteExecutionStore
from bt_api_execution.facade import ManagedExecutionFacade
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)

from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.unit import test_service_order_entry_integration as order_entry


_ACCOUNT_SCOPE = AccountScope("PAPER", "account-a", "simulation")
_ACCOUNT_B = "account-b"
_ACCOUNT_SCOPE_B = AccountScope("PAPER", _ACCOUNT_B, "simulation")
_SCOPE_B = WireScope(
    order_entry._DOMAIN,
    account_ref=_ACCOUNT_B,
    strategy_ref=order_entry._STRATEGY,
)
_EXECUTION_SCOPE_B = ExecutionScope(
    "PAPER", "simulation", _ACCOUNT_B, order_entry._STRATEGY, order_entry._TRADING_DAY
)
_RISK_POLICY = RiskPolicy(
    "service-order-risk-v1",
    Decimal("150000"),
    10,
    permit_ttl_seconds=30.0,
    notional_unit="USD",
)
_METADATA_TIME_NS = 1_700_000_000_000_000_000
_RISK_CLOCK_SECONDS = 1_700_000_000.0
_ORDER_SCOPE_REF_B = "strategy-orders-b-v1"


def _strategy_config_with_account_b(endpoint: str, security_ref: str):
    raw = order_entry._strategy_config(endpoint, security_ref).to_public_dict()
    raw["clients"]["execution"]["settings"]["bindings"].append(
        {
            "mount": "broker.orders",
            "service_id": order_entry._SERVICE_ID,
            "instance_id": order_entry._SERVICE_INSTANCE,
            "scope_ref": _ORDER_SCOPE_REF_B,
            "transport_ref": "execution-connect-v1",
            "account_ref": _ACCOUNT_B,
        }
    )
    return parse_client_config(raw)


def _assert_imported_sdk_sources() -> None:
    repository = Path(__file__).resolve().parents[2]
    expected = (
        (
            DurableRiskAdmissionAdapter,
            repository / "bt_api/bt_api_risk/src/bt_api_risk/order_admission.py",
        ),
        (
            DurableRiskGate,
            repository / "bt_api/bt_api_risk/src/bt_api_risk/core/admission.py",
        ),
        (
            InstrumentRiskAdmissionMapper,
            repository / "bt_api/bt_api_risk/src/bt_api_risk/core/instrument.py",
        ),
        (
            SqliteExecutionStore,
            repository / "bt_api/bt_api_execution/src/bt_api_execution/store.py",
        ),
        (
            ManagedExecutionFacade,
            repository / "bt_api/bt_api_execution/src/bt_api_execution/facade.py",
        ),
    )
    for symbol, source_path in expected:
        assert Path(inspect.getsourcefile(symbol)).resolve() == source_path.resolve()


def _sqlite_rows(path: Path, statement: str) -> tuple[tuple[Any, ...], ...]:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA query_only = ON")
        return tuple(connection.execute(statement).fetchall())
    finally:
        connection.close()


def _metadata() -> InstrumentRiskMetadata:
    return InstrumentRiskMetadata(
        instrument="BTC-USDT",
        metadata_version="metadata-v1",
        as_of_ns=_METADATA_TIME_NS,
        expires_at_ns=_METADATA_TIME_NS + 60_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("500000"),
        taker_fee_bps=Decimal("0"),
        fixed_fee=Decimal("0"),
        max_slippage_bps=Decimal("0"),
        quantity_unit="BTC",
        valuation_unit="USD",
    )


def _order_arguments(intent_id: str, quantity: str, *, digest: str | None = None) -> dict[str, Any]:
    metadata = _metadata()
    arguments = order_entry._submit_arguments(intent_id)
    arguments["quantity"] = quantity
    arguments["tags"] = {
        INSTRUMENT_METADATA_DIGEST_TAG: digest if digest is not None else metadata.digest
    }
    return arguments


def _risk_order_host_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    execution_store_path: str,
    risk_store_path: str,
    keys: dict[str, bytes],
) -> None:
    config = parse_service_config(raw_config)
    provider = order_entry._SyntheticProvider()
    store = None
    gate = None
    host = None
    try:
        authority = TrustedServiceAuthority(
            identity=WireIdentity(
                WireRole.SERVICE,
                order_entry._SERVICE_PRINCIPAL,
                order_entry._SERVICE_ID,
            ),
            scope=WireScope(order_entry._DOMAIN),
            scope_ref=config.scope_ref,
            scope_version="execution-service-scope-v1",
            security_ref=order_entry._SERVER_SECURITY_REF,
            security_version="curve-key-v1",
            registration_policy_ref=config.registration_policy_ref,
            registration_policy_version="execution-policy-v1",
            service_requirements_ref=config.service_requirements_ref,
            service_requirements_version="execution-requirements-v1",
            curve_public_key=keys["server_public"],
            curve_secret_key=keys["server_secret"],
            principals={keys["strategy_public"]: order_entry._PRINCIPAL},
            grants={
                order_entry._PRINCIPAL: (
                    PrincipalGrant(
                        WireIdentity(
                            WireRole.STRATEGY_CLIENT,
                            order_entry._PRINCIPAL,
                            order_entry._STRATEGY,
                        ),
                        (
                            ResourceGrant(
                                order_entry._SERVICE_INSTANCE,
                                "service.order_entry",
                                order_entry._ORDER_SCOPE_REF,
                                order_entry._SCOPE,
                                frozenset({"orders.submit", "orders.cancel"}),
                            ),
                            ResourceGrant(
                                order_entry._SERVICE_INSTANCE,
                                "service.order_query",
                                order_entry._QUERY_SCOPE_REF,
                                order_entry._SCOPE,
                                frozenset({"orders.query"}),
                            ),
                            ResourceGrant(
                                order_entry._SERVICE_INSTANCE,
                                "service.order_entry",
                                _ORDER_SCOPE_REF_B,
                                _SCOPE_B,
                                frozenset({"orders.submit", "orders.cancel"}),
                            ),
                        ),
                    ),
                )
            },
        )

        store = SqliteExecutionStore(execution_store_path)
        gate = DurableRiskGate(
            risk_store_path,
            _RISK_POLICY,
            clock=lambda: _RISK_CLOCK_SECONDS,
        )
        instrument_metadata = _metadata()
        mapper = InstrumentRiskAdmissionMapper(
            _ACCOUNT_SCOPE,
            InstrumentRiskRegistry((instrument_metadata,)),
            clock_ns=lambda: _METADATA_TIME_NS + 1,
        )
        order_admission = DurableRiskAdmissionAdapter(gate, mapper)

        def resource_provider(component, _instance_id, _options, _scope):
            if component != "execution":
                raise AssertionError("unexpected service component")
            return ComponentResources.borrowed(
                {
                    "store": store,
                    "targets": {
                        order_entry._SCOPE: order_entry._EXECUTION_SCOPE,
                        _SCOPE_B: _EXECUTION_SCOPE_B,
                    },
                    "dispatch": provider,
                    "admission": {"order_gate": order_admission, "cancel_gate": None},
                },
                ready=lambda: True,
            )

        host = ServiceHost(config, authority, registry_path, resource_provider)
        host.start()
        control.send(("ready", host.endpoint))
        while True:
            host.serve_once(timeout_ms=25)
            if not control.poll():
                continue
            command = control.recv()
            if command == ("risk_state",):
                control.send(
                    (
                        "risk_state",
                        tuple(provider.submitted),
                        gate.snapshot(_ACCOUNT_SCOPE),
                    )
                )
                continue
            if command == ("risk_state_account_b",):
                control.send(
                    (
                        "risk_state_account_b",
                        tuple(provider.submitted),
                        gate.snapshot(_ACCOUNT_SCOPE_B),
                    )
                )
                continue
            if len(command) == 2 and command[0] == "dispatch_claim":
                claim = gate.dispatch_claim_binding(command[1])
                control.send(
                    (
                        "dispatch_claim",
                        claim.intent_id,
                        claim.permit_id,
                        claim.scope.provider,
                        claim.scope.account_id,
                        claim.scope.environment,
                        claim.cause_id,
                    )
                )
                continue
            if command == ("close",):
                host.close()
                gate.close()
                store.close()
                gate = None
                store = None
                control.send(("closed", host.state))
                return
            raise AssertionError("unknown risk order host command")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        if gate is not None:
            with suppress(BaseException):
                gate.close()
        if store is not None:
            with suppress(BaseException):
                store.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _host_command(record: dict[str, Any], *command):
    pipe = record["pipe"]
    pipe.send(command)
    assert pipe.poll(10), "spawned service did not answer its local observation command"
    result = pipe.recv()
    assert result[0] != "error", result
    return result


def test_spawned_service_enforces_real_risk_admission_before_order_dispatch(tmp_path) -> None:
    from tests.unit.test_service_account_integration import (
        _assert_no_fallback_cleanup,
        _bounded_reap_spawned_process,
        _close_normally,
        _receive,
        _start_process,
    )

    _assert_imported_sdk_sources()
    context = multiprocessing.get_context("spawn")
    endpoint = order_entry._endpoint()
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "strategy_public": strategy_public,
    }
    host_record = None
    runtime = None
    forced = []
    failures = []
    try:
        host_record = _start_process(
            context,
            _risk_order_host_process,
            (
                order_entry._service_config(endpoint),
                str(tmp_path / "risk-order-registry.sqlite3"),
                str(tmp_path / "risk-order-execution.sqlite3"),
                str(tmp_path / "risk-order-admission.sqlite3"),
                keys,
            ),
            "risk-order-account-owner-host",
        )
        startup = _receive(host_record["pipe"])
        assert startup[0] == "ready", startup
        assert startup[1] == endpoint

        peer = WireIdentity(
            WireRole.SERVICE,
            order_entry._SERVICE_PRINCIPAL,
            order_entry._SERVICE_ID,
        )
        identity = WireIdentity(
            WireRole.STRATEGY_CLIENT,
            order_entry._PRINCIPAL,
            order_entry._STRATEGY,
        )
        config = _strategy_config_with_account_b(endpoint, "execution-strategy-curve-v1")
        assert set(config.clients) == {"execution"}
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda scope_ref: {
                order_entry._ORDER_SCOPE_REF: order_entry._SCOPE,
                order_entry._QUERY_SCOPE_REF: order_entry._SCOPE,
                _ORDER_SCOPE_REF_B: _SCOPE_B,
            }[scope_ref],
            peer_resolver=lambda _service: peer,
            transport_factory=ZmqTransportFactory(
                {
                    "execution-strategy-curve-v1": ClientCurveKeys(
                        server_public, strategy_public, strategy_secret
                    )
                }
            ),
            requested_operations={
                "broker.orders": frozenset({"orders.submit", "orders.cancel"}),
                "broker.order_query": frozenset({"orders.query"}),
            },
        )
        receipt = runtime.start()
        assert receipt.degraded_count == 0
        assert {
            (binding.mount, binding.scope.account_ref, binding.scope_ref)
            for binding in receipt.bindings
        } == {
            ("broker.orders", order_entry._ACCOUNT, order_entry._ORDER_SCOPE_REF),
            ("broker.orders", _ACCOUNT_B, _ORDER_SCOPE_REF_B),
            ("broker.order_query", order_entry._ACCOUNT, order_entry._QUERY_SCOPE_REF),
        }

        account_mismatch = runtime.call(
            "broker.orders",
            "orders.submit",
            _order_arguments("risk-bad-account", "1"),
            account_ref=_ACCOUNT_B,
            scope_ref=_ORDER_SCOPE_REF_B,
        )
        assert account_mismatch["code"] == "OK"
        account_mismatch_record = account_mismatch["result"]["record"]
        assert account_mismatch_record["state"] == "REJECTED"
        assert account_mismatch_record["unknown_reason"] == "admission_denied"
        assert account_mismatch_record["dispatch_attempts"] == 0
        assert account_mismatch_record["permit_reference"] is None
        assert account_mismatch_record["provider_order_id"] is None
        after_account_mismatch = _host_command(host_record, "risk_state_account_b")
        assert after_account_mismatch[1] == ()
        assert after_account_mismatch[2]["increase_count"] == 0
        assert after_account_mismatch[2]["increase_notional"] == Decimal("0")

        metadata_mismatch = runtime.call(
            "broker.orders",
            "orders.submit",
            _order_arguments("risk-bad-metadata", "1", digest="0" * 64),
            scope_ref=order_entry._ORDER_SCOPE_REF,
        )
        assert metadata_mismatch["code"] == "OK"
        metadata_mismatch_record = metadata_mismatch["result"]["record"]
        assert metadata_mismatch_record["state"] == "REJECTED"
        assert metadata_mismatch_record["unknown_reason"] == "admission_denied"
        assert metadata_mismatch_record["dispatch_attempts"] == 0
        assert metadata_mismatch_record["permit_reference"] is None
        assert metadata_mismatch_record["provider_order_id"] is None
        after_metadata = _host_command(host_record, "risk_state")
        assert after_metadata[1] == ()
        assert after_metadata[2]["increase_count"] == 0
        assert after_metadata[2]["increase_notional"] == Decimal("0")

        over_budget = runtime.call(
            "broker.orders",
            "orders.submit",
            _order_arguments("risk-over-budget", "4"),
            scope_ref=order_entry._ORDER_SCOPE_REF,
        )
        assert over_budget["code"] == "OK"
        over_budget_record = over_budget["result"]["record"]
        assert over_budget_record["state"] == "REJECTED"
        assert over_budget_record["unknown_reason"] == "admission_denied"
        assert over_budget_record["dispatch_attempts"] == 0
        assert over_budget_record["permit_reference"] is None
        assert over_budget_record["provider_order_id"] is None
        after_denial = _host_command(host_record, "risk_state")
        assert after_denial[1] == ()
        assert after_denial[2]["increase_count"] == 0
        assert after_denial[2]["increase_notional"] == Decimal("0")

        valid_arguments = _order_arguments("risk-approved", "2")
        accepted = runtime.call(
            "broker.orders",
            "orders.submit",
            valid_arguments,
            scope_ref=order_entry._ORDER_SCOPE_REF,
        )
        assert accepted["code"] == "OK"
        accepted_record = accepted["result"]["record"]
        assert accepted_record["state"] == "ACKED"
        assert accepted_record["dispatch_attempts"] == 1
        assert accepted_record["provider_order_id"] == "provider-order-1"
        assert accepted_record["permit_reference"]
        dispatch_claim = _host_command(
            host_record, "dispatch_claim", accepted_record["permit_reference"]
        )
        assert dispatch_claim == (
            "dispatch_claim",
            "risk-approved",
            accepted_record["permit_reference"],
            "PAPER",
            order_entry._ACCOUNT,
            "simulation",
            "dispatch-inflight:risk-approved",
        )

        duplicate = runtime.call(
            "broker.orders",
            "orders.submit",
            valid_arguments,
            scope_ref=order_entry._ORDER_SCOPE_REF,
        )
        assert duplicate["code"] == "OK"
        assert duplicate["result"]["record"] == accepted_record

        after_acceptance = _host_command(host_record, "risk_state")
        assert after_acceptance[1] == ("risk-approved",)
        assert after_acceptance[2]["increase_count"] == 1
        assert after_acceptance[2]["increase_notional"] == Decimal("100000")
        assert after_acceptance[2]["active_freeze_reasons"] == [
            "dispatch-inflight:risk-approved"
        ]

        runtime.close()
        assert runtime.cleanup_failures == 0
        runtime = None
        _close_normally(host_record, expected_tag="closed")
        risk_store_path = tmp_path / "risk-order-admission.sqlite3"
        reservations = _sqlite_rows(
            risk_store_path,
            "SELECT intent_id, permit_id FROM risk_reservations ORDER BY intent_id",
        )
        assert tuple(row[0] for row in reservations) == ("risk-approved",)
        assert reservations[0][1] == accepted_record["permit_reference"]
        claims = _sqlite_rows(
            risk_store_path,
            "SELECT permit_id FROM risk_dispatch_claims ORDER BY permit_id",
        )
        assert claims == ((accepted_record["permit_reference"],),)
    finally:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        if host_record is not None:
            _bounded_reap_spawned_process(host_record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)
