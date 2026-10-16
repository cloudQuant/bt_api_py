"""Paper BUY through real local Execution/Risk/Monitor ServiceHosts.

The provider leaf is a deterministic synthetic quote source. Execution,
Risk, CURVE registration, the owner-thread journal relay, and Monitor's
durable ingress/outbox are the real selected package components.
"""

from __future__ import annotations

import multiprocessing
import time
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.service_components import ComponentResources, TrustedRiskPolicyBinding
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import (
    ServiceHost,
    TrustedServiceAuthority,
    TrustedServiceClients,
)
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.unit._spawned_process_helpers import (
    _assert_no_fallback_cleanup,
    _bounded_reap_spawned_process,
    _close_normally,
    _receive,
    _start_process,
)
from tests.unit.test_service_execution_paper_host_integration import (
    _ACCOUNT_REF,
    _DOMAIN_REF,
    _ORDER_SCOPE_REF,
    _SERVICE_ID,
    _SERVICE_INSTANCE,
    _SERVICE_PRINCIPAL,
    _STRATEGY_REF,
    _WIRE_SCOPE,
    _endpoint,
    _paper_domain,
    _plain_host_components,
    _SyntheticQuoteSource,
)

_MONITOR_ID = "paper-risk-monitor-service"
_MONITOR_INSTANCE = "paper-risk-monitor-ingress"
_MONITOR_PRINCIPAL = "paper-risk-monitor-principal"
_MONITOR_SCOPE_REF = "paper-risk-monitor-scope-v1"
_MONITOR_SECURITY_REF = "paper-risk-monitor-server-curve-v1"
_MONITOR_LISTENER_REF = "paper-risk-monitor-listener-v1"
_MONITOR_CONNECT_REF = "paper-risk-monitor-connect-v1"
_OWNER_CONNECT_REF = "paper-risk-owner-monitor-client-v1"
_OWNER_LISTENER_REF = "paper-risk-owner-listener-v1"
_APPLICATION_REF = "paper-risk-application-v1"
_APPLICATION_PRINCIPAL = "paper-risk-application-principal"
_RISK_INSTANCE = "paper-risk-admission-owner"
_RISK_POLICY_REF = "paper-risk-policy-v1"
_RISK_INSTRUMENT = "BTC-USDT"
_RISK_INTENT_ID = "paper-risk-buy-once"
_RISK_CLOSE_INTENT_ID = "paper-risk-close-once"
_MAX_MESSAGE_BYTES = 65_536
_INGRESS_SCOPE = _WIRE_SCOPE
_EVENT_TYPES = ("execution.lifecycle", "execution.cancellation")


class _SharedSyntheticQuoteSource:
    """Synthetic read-only feed with a cursor that survives owner restarts."""

    def __init__(self, cursor_counter) -> None:
        self._cursor_counter = cursor_counter
        self.calls: list[str] = []

    @property
    def cursor(self) -> int:
        with self._cursor_counter.get_lock():
            return int(self._cursor_counter.value)

    def read_quote(self, instrument: str):
        from bt_api_execution import PaperQuoteV1

        self.calls.append(instrument)
        if instrument != _RISK_INSTRUMENT:
            return None
        with self._cursor_counter.get_lock():
            self._cursor_counter.value += 1
            cursor = int(self._cursor_counter.value)
        return PaperQuoteV1(
            source_ref="paper-host-synthetic-quotes-v1",
            generation="paper-host-quote-generation-v1",
            cursor=cursor,
            instrument=instrument,
            metadata_version="paper-instrument-v1",
            quote_currency="USDT",
            bid=Decimal("10.00"),
            ask=Decimal("10.02"),
            bid_quantity=Decimal("5"),
            ask_quantity=Decimal("5"),
            as_of_ns=time.time_ns(),
        )


def _owner_service_config(
    endpoint: str,
    monitor_endpoint: str,
    *,
    owner_instance_id: str = _SERVICE_INSTANCE,
) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _SERVICE_ID,
        "scope_ref": "paper-risk-owner-root-v1",
        "transport_ref": _OWNER_LISTENER_REF,
        "registration_policy_ref": "paper-risk-owner-registration-v1",
        "service_requirements_ref": "paper-risk-owner-requirements-v1",
        "components": {
            "execution": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": owner_instance_id,
                    "bind_ports": ["service.order_entry"],
                    "options": {
                        "role": "account_owner",
                        "paper_composition": "execution_risk_monitor",
                    },
                },
            },
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _RISK_INSTANCE,
                    "bind_ports": ["service.order_entry"],
                    "options": {
                        "role": "account_owner",
                        "account_policy_ref": _RISK_POLICY_REF,
                    },
                },
            },
        },
        "clients": {
            "monitor": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "upstream.telemetry",
                            "service_id": _MONITOR_ID,
                            "instance_id": _MONITOR_INSTANCE,
                            "scope_ref": _MONITOR_SCOPE_REF,
                            "transport_ref": _MONITOR_CONNECT_REF,
                        }
                    ],
                    "options": {
                        "request_timeout_ms": 2_000,
                        "heartbeat_interval_seconds": 5,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _OWNER_LISTENER_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": "paper-risk-owner-server-curve-v1",
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                },
                _MONITOR_CONNECT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": monitor_endpoint},
                    "security_profile_ref": _OWNER_CONNECT_REF,
                    "options": {
                        "request_timeout_ms": 2_000,
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                },
            },
        },
    }


def _monitor_service_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _MONITOR_ID,
        "scope_ref": "paper-risk-monitor-root-v1",
        "transport_ref": _MONITOR_LISTENER_REF,
        "registration_policy_ref": "paper-risk-monitor-registration-v1",
        "service_requirements_ref": "paper-risk-monitor-requirements-v1",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _MONITOR_INSTANCE,
                    "bind_ports": ["service.telemetry_ingress"],
                    "options": {
                        "role": "durable_ingress",
                        "max_events": 128,
                        "max_bytes": 262_144,
                        "max_event_bytes": 16_384,
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                _MONITOR_LISTENER_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": endpoint},
                    "security_profile_ref": _MONITOR_SECURITY_REF,
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _application_client_config(endpoint: str):
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "application_client",
            "registration": {"application_ref": _APPLICATION_REF},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "application.orders",
                                "service_id": _SERVICE_ID,
                                "instance_id": _SERVICE_INSTANCE,
                                "scope_ref": _ORDER_SCOPE_REF,
                                "transport_ref": "paper-risk-application-connect-v1",
                                "account_ref": _ACCOUNT_REF,
                            }
                        ],
                        "options": {"request_timeout_ms": 2_000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "paper-risk-application-connect-v1": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": "paper-risk-application-curve-v1",
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


def _risk_resources(
    paths: dict[str, str],
    *,
    initialize_strategy_allocation: bool = True,
    quote_cursor_counter,
):
    """Create one exact shared Paper/Risk graph from synthetic local leaves."""

    from bt_api_execution import (
        PAPER_ENVIRONMENT,
        PAPER_PROVIDER,
        ExecutionAdmissionValidatorRegistry,
        ExecutionBasicAdmissionGate,
        ExecutionProviderValidatorV1,
        ExecutionScope,
        OrderType,
        PaperMatchingPort,
        PositionEffect,
        Side,
        SqliteExecutionStore,
        TimeInForce,
    )
    from bt_api_execution.paper_risk import PaperRiskAdmissionAdapter
    from bt_api_execution.paper_execution_risk import PaperExecutionCloseAuthority
    from bt_api_execution.paper_risk import PaperRiskCompositionError
    from bt_api_risk import (
        AccountScope,
        DurableRiskAdmissionAdapter,
        DurableRiskGate,
        InstrumentRiskAdmissionMapper,
        InstrumentRiskMetadata,
        InstrumentRiskRegistry,
        RiskPolicy,
    )
    from bt_api_py import bt_api as bt_api_module
    from bt_api_py.service_risk_order_entry import (
        RiskBtApiProviderContract,
        RiskOrderJournal,
        RiskOrderTarget,
    )

    bt_api_module._ensure_plugins_loaded = lambda: None
    bt_api = bt_api_module.BtApi(debug=False)
    store = SqliteExecutionStore(paths["execution"])
    journal = RiskOrderJournal(paths["risk-journal"], writer_id="paper-risk-host-owner")
    policy = RiskPolicy(
        policy_id=_RISK_POLICY_REF,
        max_increase_notional=Decimal("1000"),
        max_increase_count=10,
        permit_ttl_seconds=60.0,
        require_strategy_allocation=True,
        notional_unit="USDT",
    )
    account_scope = AccountScope(PAPER_PROVIDER, _ACCOUNT_REF, PAPER_ENVIRONMENT)
    domain = _paper_domain()
    execution_scope = ExecutionScope(
        PAPER_PROVIDER,
        PAPER_ENVIRONMENT,
        _ACCOUNT_REF,
        _STRATEGY_REF,
        "paper-host-trading-day-v1",
    )
    quote_source = _SharedSyntheticQuoteSource(quote_cursor_counter)
    dispatch = PaperMatchingPort(domain, quote_source)
    risk_metadata = InstrumentRiskMetadata(
        instrument=_RISK_INSTRUMENT,
        metadata_version="paper-instrument-v1",
        as_of_ns=time.time_ns() - 1_000_000,
        expires_at_ns=time.time_ns() + 3_600_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.25"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("1000"),
        taker_fee_bps=Decimal("10"),
        quantity_unit="BTC",
        valuation_unit="USDT",
        quote_currency="USDT",
    )
    instrument_registry = InstrumentRiskRegistry((risk_metadata,))
    close_authority = PaperExecutionCloseAuthority(
        store,
        execution_scope,
        domain,
        dispatch,
        account_scope,
        instrument_registry,
        journal,
    )
    if initialize_strategy_allocation:
        from bt_api_execution.errors import DurableStoreError

        observed_at_ns = time.time_ns()
        incomplete_source = SimpleNamespace(
            intent=SimpleNamespace(
                instrument=_RISK_INSTRUMENT,
                metadata_version=domain.instrument(_RISK_INSTRUMENT).metadata_version,
            ),
            receipt=SimpleNamespace(match_time_ns=observed_at_ns),
        )
        invalid_metadata_cases = (
            (
                replace(
                    risk_metadata,
                    as_of_ns=observed_at_ns - 2_000_000_000,
                    expires_at_ns=observed_at_ns - 1_000_000_000,
                ),
                "Paper instrument metadata is not valid at the exact fill and observation times",
            ),
            (
                replace(
                    risk_metadata,
                    as_of_ns=observed_at_ns + 1_000_000_000,
                    expires_at_ns=observed_at_ns + 2_000_000_000,
                ),
                "Paper instrument metadata is not valid at the exact fill and observation times",
            ),
            (
                replace(risk_metadata, quantity_step=Decimal("0.5")),
                "Paper instrument metadata is not bound to the Risk source",
            ),
            (
                replace(risk_metadata, tick_size=Decimal("0.05")),
                "Paper instrument metadata is not bound to the Risk source",
            ),
        )
        for invalid_metadata, expected_error in invalid_metadata_cases:
            invalid_authority = PaperExecutionCloseAuthority(
                store,
                execution_scope,
                domain,
                dispatch,
                account_scope,
                InstrumentRiskRegistry((invalid_metadata,)),
                journal,
            )
            try:
                invalid_authority._observation(incomplete_source, None, None)
            except DurableStoreError as error:
                assert str(error) == expected_error
            else:
                raise AssertionError("unbound or stale Risk metadata was accepted")
    gate = DurableRiskGate(
        paths["risk"],
        policy,
        execution_journal_authority=journal,
        gross_exposure_authority=close_authority,
    )
    if initialize_strategy_allocation:
        gate.set_strategy_allocation(
            account_scope,
            _STRATEGY_REF,
            "paper-risk-allocation-v1",
            max_notional=Decimal("1000"),
            notional_unit="USDT",
        )
    basic_validator = ExecutionProviderValidatorV1(
        provider_id=PAPER_PROVIDER,
        validator_id="paper-risk-market-policy-v1",
        version="1",
        dispatch_port=dispatch,
        instrument_map={_RISK_INSTRUMENT: _RISK_INSTRUMENT},
        quantity_steps={_RISK_INSTRUMENT: Decimal("0.25")},
        max_quantities={_RISK_INSTRUMENT: Decimal("10")},
        price_ticks={_RISK_INSTRUMENT: Decimal("0.01")},
        supported_order_types=(OrderType.MARKET,),
        supported_time_in_force=(TimeInForce.FOK,),
        supported_actions=(
            (Side.BUY, PositionEffect.OPEN),
            (Side.SELL, PositionEffect.CLOSE),
        ),
        supports_reduce_only=True,
    )
    basic_admission = ExecutionBasicAdmissionGate(
        store,
        ExecutionAdmissionValidatorRegistry((basic_validator,)),
        permit_ttl_ns=60_000_000_000,
    )
    paper_admission = PaperRiskAdmissionAdapter(
        gate,
        domain,
        dispatch,
        close_authority=close_authority,
        execution_store=store,
        execution_scope=execution_scope,
    )
    assert paper_admission.is_bound_to_composition(gate, domain, dispatch)
    assert not close_authority.is_bound_to_composition(
        gate,
        store=store,
        execution_scope=execution_scope,
        owner_domain=domain,
        matching_port=dispatch,
        risk_scope=AccountScope(
            PAPER_PROVIDER, _ACCOUNT_REF + "-other", PAPER_ENVIRONMENT
        ),
        instrument_registry=instrument_registry,
    )
    wrong_gate = DurableRiskGate(
        paths["risk"] + ".wrong-authority",
        policy,
        execution_journal_authority=journal,
    )
    try:
        assert not close_authority.is_bound_to_composition(
            wrong_gate,
            store=store,
            execution_scope=execution_scope,
            owner_domain=domain,
            matching_port=dispatch,
            risk_scope=account_scope,
            instrument_registry=instrument_registry,
        )
    finally:
        wrong_gate.close()

    class _ForgedPaperAuthority:
        def is_bound_to_composition(self, *_args, **_kwargs):
            return True

    try:
        PaperRiskAdmissionAdapter(
            gate,
            domain,
            dispatch,
            close_authority=_ForgedPaperAuthority(),
            execution_store=store,
            execution_scope=execution_scope,
        )
    except PaperRiskCompositionError as error:
        assert error.code == "PAPER_CLOSE_AUTHORITY_MISMATCH"
    else:
        raise AssertionError("noncanonical Paper close authority was accepted")
    mapper = InstrumentRiskAdmissionMapper(
        account_scope,
        instrument_registry,
        allocation_reader=lambda routed_scope: gate.get_strategy_allocation(
            account_scope, routed_scope.strategy_id
        ),
        wire_account_ref=_ACCOUNT_REF,
    )
    risk_target = RiskOrderTarget(
        account_scope=account_scope,
        strategy_id=_STRATEGY_REF,
        exchange_name="paper___spot",
        provider_account_id=_ACCOUNT_REF,
        registry=instrument_registry,
    )
    risk_admission = DurableRiskAdmissionAdapter(gate, mapper)
    provider_contract = RiskBtApiProviderContract(
        exchange_name="paper___spot",
        profile_id="paper-risk-provider-v1",
        order_schema="paper-risk-order-v1",
        fill_schema="paper-risk-fill-v1",
        client_order_id_query=True,
        client_order_id_query_unique=True,
        complete_order_snapshot=True,
        complete_fill_history=True,
        provider_accepts_client_order_id=True,
        provider_status_mapping=(
            ("cancelled", "canceled"),
            ("expired", "expired"),
            ("filled", "completed"),
            ("open", "accepted"),
            ("partial", "partial"),
            ("rejected", "rejected"),
        ),
        supported_order_types=("LIMIT",),
        supported_time_in_force=("GTC",),
        supported_position_effects=("OPEN",),
        normalizer_schema="bt-api-order-source-provenance-v2",
        order_revision_field="order_revision",
        fill_revision_field="fill_revision",
        fill_watermark_field="fill_watermark",
        fee_amount_field="fee_amount",
        fee_currency_field="fee_currency",
        quote_currency_by_instrument=((_RISK_INSTRUMENT, "USDT"),),
    )
    execution_values = {
        "store": store,
        "targets": {_WIRE_SCOPE: execution_scope},
        "dispatch": dispatch,
        "admission": {
            "basic_admission_gate": basic_admission,
            "order_gate": paper_admission,
        },
        "paper_owner_domain": domain,
    }
    risk_values = {
        "bt_api": bt_api,
        "targets": {_WIRE_SCOPE: risk_target},
        "admission": {_WIRE_SCOPE: risk_admission},
        "journal": journal,
        "gate": gate,
        "provider_contracts": {_WIRE_SCOPE: provider_contract},
    }
    return {
        "execution": ComponentResources.borrowed(
            execution_values, ready=lambda: True
        ),
        "risk": ComponentResources.borrowed(
            risk_values,
            ready=lambda: True,
            risk_policy_bindings=(
                TrustedRiskPolicyBinding(_RISK_POLICY_REF, _WIRE_SCOPE, gate.policy),
            ),
        ),
    }, {
        "bt_api": bt_api,
        "store": store,
        "journal": journal,
        "gate": gate,
        "execution_scope": execution_scope,
        "account_scope": account_scope,
        "quote_source": quote_source,
        "close_authority": close_authority,
    }


def _owner_host_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    paths: dict[str, str],
    owner_keys: dict[str, bytes],
    monitor_keys: dict[str, bytes],
    owner_instance_id: str = _SERVICE_INSTANCE,
    initialize_strategy_allocation: bool = True,
    quote_cursor_counter=None,
) -> None:
    resources = None
    host = None
    graph = None
    try:
        config = parse_service_config(raw_config)
        resources, graph = _risk_resources(
            paths,
            initialize_strategy_allocation=initialize_strategy_allocation,
            quote_cursor_counter=quote_cursor_counter,
        )
        monitor_identity = WireIdentity(
            WireRole.SERVICE, _MONITOR_PRINCIPAL, _MONITOR_ID
        )
        owner_identity = WireIdentity(
            WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID
        )

        def resource_provider(component, _instance_id, _options, _scope):
            if component not in resources:
                raise AssertionError("unexpected selected component")
            return resources[component]

        clients = TrustedServiceClients(
            scope_resolver=lambda scope_ref: (
                _WIRE_SCOPE if scope_ref == _MONITOR_SCOPE_REF else _bad_scope()
            ),
            peer_resolver=lambda service_id: (
                monitor_identity if service_id == _MONITOR_ID else _bad_peer()
            ),
            transport_factory=ZmqTransportFactory(
                {
                    _OWNER_CONNECT_REF: ClientCurveKeys(
                        monitor_keys["server_public"],
                        owner_keys["server_public"],
                        owner_keys["server_secret"],
                    )
                }
            ),
            requested_operations={
                "upstream.telemetry": frozenset({"events.append"})
            },
        )
        owner_scope = WireScope(_DOMAIN_REF)
        application_grant = ResourceGrant(
            owner_instance_id,
            "service.order_entry",
            _ORDER_SCOPE_REF,
            _WIRE_SCOPE,
            frozenset({"orders.submit"}),
        )
        monitor_endpoint = raw_config["communication"]["profiles"][
            _MONITOR_CONNECT_REF
        ]["endpoints"]["rpc_connect"]
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=owner_identity,
                scope=owner_scope,
                scope_ref=config.scope_ref,
                scope_version="paper-risk-owner-scope-v1",
                security_ref="paper-risk-owner-server-curve-v1",
                security_version="curve-key-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="paper-risk-owner-policy-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="paper-risk-owner-requirements-v1",
                curve_public_key=owner_keys["server_public"],
                curve_secret_key=owner_keys["server_secret"],
                principals={
                    owner_keys["application_public"]: _APPLICATION_PRINCIPAL
                },
                grants={
                    _APPLICATION_PRINCIPAL: (
                        PrincipalGrant(
                            WireIdentity(
                                WireRole.APPLICATION_CLIENT,
                                _APPLICATION_PRINCIPAL,
                                _APPLICATION_REF,
                            ),
                            (application_grant,),
                        ),
                    )
                },
            ),
            registry_path,
            resource_provider,
            service_directory={
                _SERVICE_ID: config,
                _MONITOR_ID: parse_service_config(
                    _monitor_service_config(monitor_endpoint)
                ),
            },
            trusted_clients=clients,
        )
        host.start()
        control.send(
            (
                "ready",
                host.endpoint,
                host.state,
                _plain_host_components(host),
                dict(host.summary["paper"]["compositions"]),
                host.summary["paper"]["monitor_relays"],
                dict(host.summary["paper"]["monitor_faults"]),
            )
        )
        while True:
            if control.poll(0.01):
                command = control.recv()
                if command == ("snapshot",):
                    scope = graph["execution_scope"]
                    record = graph["store"].get(_RISK_INTENT_ID, scope=scope)
                    receipt = graph["store"].get_paper_match_receipt(
                        _RISK_INTENT_ID, scope=scope
                    )
                    book = graph["store"].read_paper_book(scope)
                    risk_snapshot = graph["gate"].snapshot(graph["account_scope"])
                    close_record = graph["store"].get(
                        _RISK_CLOSE_INTENT_ID, scope=scope
                    )
                    close_receipt = graph["store"].get_paper_match_receipt(
                        _RISK_CLOSE_INTENT_ID, scope=scope
                    )
                    close_resolution = (
                        None
                        if close_record is None or close_record.permit_reference is None
                        else graph["gate"].dispatch_close_resolution_receipt(
                            close_record.permit_reference
                        )
                    )
                    closable = graph["gate"].get_closable_position(
                        graph["account_scope"],
                        _RISK_INSTRUMENT,
                        _STRATEGY_REF,
                        position_side="NET",
                        position_bucket="ALL",
                    )
                    control.send(
                        (
                            "snapshot",
                            None if record is None else record.state.value,
                            None if record is None else record.dispatch_attempts,
                            None if record is None else record.permit_reference,
                            None if receipt is None else receipt.state.value,
                            None if receipt is None else receipt.event_id,
                            book.receipt_count,
                            book.execution_cursor,
                            book.execution_journal_generation,
                            str(book.cash),
                            tuple(
                                (row.instrument, str(row.quantity))
                                for row in book.positions
                            ),
                            graph["quote_source"].calls,
                            risk_snapshot["increase_count"],
                            str(risk_snapshot["increase_notional"]),
                            host.healthy,
                            dict(host.summary["paper"]["monitor_faults"]),
                            str(book.cumulative_fees),
                            str(closable.declared_quantity),
                            str(closable.used_quantity),
                            str(closable.available_quantity),
                            None if close_record is None else close_record.state.value,
                            None if close_receipt is None else close_receipt.state.value,
                            None if close_receipt is None else close_receipt.event_id,
                            None
                            if close_resolution is None
                            else close_resolution.status.value,
                            graph["quote_source"].cursor,
                        )
                    )
                    continue
                if command == ("close",):
                    host.close()
                    control.send(
                        (
                            "closed",
                            host.state,
                            tuple(host.summary["cleanup_failures"]),
                            tuple(graph["quote_source"].calls),
                            host.summary["paper"]["monitor_relays"],
                            dict(host.summary["paper"]["monitor_faults"]),
                        )
                    )
                    break
                raise AssertionError("unknown Paper/Risk/Monitor owner command")
            host.serve_once(timeout_ms=25)
    except BaseException as error:
        if host is not None and host.state == "started":
            try:
                host.close()
            except BaseException:
                pass
        with suppress_exception():
            control.send(("host_error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        if graph is not None:
            _close_graph(graph)
        control.close()


def _bad_scope():
    raise ValueError("unexpected scope reference")


def _bad_peer():
    raise ValueError("unexpected peer identity")


def _require_process_response(record, response, expected_tag: str) -> None:
    if response[0] == "host_error":
        process, pipe = record["process"], record["pipe"]
        process.join(15)
        if not process.is_alive():
            pipe.close()
            process.close()
            record["closed"] = True
    assert response[0] == expected_tag, response


def suppress_exception():
    from contextlib import suppress

    return suppress(Exception)


def _close_graph(graph: dict[str, Any]) -> None:
    failures = []
    for name in ("gate", "store", "bt_api"):
        try:
            graph[name].close()
        except BaseException as error:
            failures.append(type(error).__name__)
    if failures:
        raise AssertionError("trusted paper risk graph cleanup failed")


def _writer_lease(path: str, scope_key: str) -> tuple[str, int, int]:
    import sqlite3

    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT owner_id, fencing_token, expires_at_ns "
            "FROM execution_writer_leases WHERE scope_key = ?",
            (scope_key,),
        ).fetchone()
    assert row is not None
    return str(row[0]), int(row[1]), int(row[2])


def _monitor_host_process(
    control,
    raw_config: dict[str, Any],
    registry_path: str,
    outbox_path: str,
    outbox_scope: str,
    monitor_keys: dict[str, bytes],
    owner_keys: dict[str, bytes],
) -> None:
    from bt_api_monitor.durable import DurableOutbox
    from bt_api_monitor.service_ingress import MonitorIngressTarget

    host = None
    outbox = None
    try:
        config = parse_service_config(raw_config)
        outbox = DurableOutbox(outbox_path)
        provider_calls = []

        def resource_provider(component, _instance_id, _options, _scope):
            provider_calls.append(component)
            if component != "monitor":
                raise AssertionError("unexpected selected Monitor component")
            return ComponentResources.borrowed(
                {
                    "outbox": outbox,
                    "targets": {
                        _INGRESS_SCOPE: MonitorIngressTarget(outbox_scope, _EVENT_TYPES)
                    },
                },
                ready=lambda: True,
            )

        owner_identity = WireIdentity(
            WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID
        )
        owner_grant = ResourceGrant(
            _MONITOR_INSTANCE,
            "service.telemetry_ingress",
            _MONITOR_SCOPE_REF,
            _INGRESS_SCOPE,
            frozenset({"events.append"}),
        )
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=WireIdentity(WireRole.SERVICE, _MONITOR_PRINCIPAL, _MONITOR_ID),
                scope=_INGRESS_SCOPE,
                scope_ref=config.scope_ref,
                scope_version="paper-risk-monitor-scope-v1",
                security_ref=_MONITOR_SECURITY_REF,
                security_version="curve-key-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="paper-risk-monitor-policy-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="paper-risk-monitor-requirements-v1",
                curve_public_key=monitor_keys["server_public"],
                curve_secret_key=monitor_keys["server_secret"],
                principals={owner_keys["server_public"]: _SERVICE_PRINCIPAL},
                grants={
                    _SERVICE_PRINCIPAL: (
                        PrincipalGrant(owner_identity, (owner_grant,)),
                    )
                },
            ),
            registry_path,
            resource_provider,
        )
        host.start()
        control.send(("ready", host.endpoint, host.state, tuple(provider_calls)))
        while True:
            host.serve_once(timeout_ms=25)
            if control.poll(0.01):
                command = control.recv()
                if command == ("close",):
                    host.close()
                    control.send(("closed", host.state, tuple(host.summary["cleanup_failures"])))
                    return
                if command == ("rows",):
                    rows, has_more = outbox.read_page_types(
                        outbox_scope, _EVENT_TYPES, 0, 100
                    )
                    control.send(
                        (
                            "rows",
                            has_more,
                            tuple(
                                (
                                    row.sequence,
                                    row.event.event_id,
                                    row.event.event_type,
                                    row.event.data["producer"]["principal_ref"],
                                    row.event.data["payload"]["schema"],
                                    row.event.data["payload"]["source_scope"]["account_ref"],
                                    row.event.data["payload"]["source_scope"]["strategy_id"],
                                    row.event.data["payload"]["source_event_id"],
                                )
                                for row in rows
                            ),
                        )
                    )
                    continue
                raise AssertionError("unknown Monitor command")
    except BaseException as error:
        if host is not None and host.state == "started":
            try:
                host.close()
            except BaseException:
                pass
        with suppress_exception():
            control.send(("host_error", getattr(error, "code", type(error).__name__)))
        raise
    finally:
        control.close()


def _application_runtime(endpoint: str, keys: dict[str, bytes]):
    owner_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    runtime = ServiceClientRuntime(
        _application_client_config(endpoint),
        identity=WireIdentity(
            WireRole.APPLICATION_CLIENT,
            _APPLICATION_PRINCIPAL,
            _APPLICATION_REF,
        ),
        scope_resolver=lambda scope_ref: (
            _WIRE_SCOPE if scope_ref == _ORDER_SCOPE_REF else _bad_scope()
        ),
        peer_resolver=lambda service_id: (
            owner_identity if service_id == _SERVICE_ID else _bad_peer()
        ),
        transport_factory=ZmqTransportFactory(
            {
                "paper-risk-application-curve-v1": ClientCurveKeys(
                    keys["server_public"],
                    keys["application_public"],
                    keys["application_secret"],
                )
            }
        ),
        requested_operations={"application.orders": frozenset({"orders.submit"})},
    )
    runtime.start()
    from bt_api_py.service_execution import ServiceExecutionClient

    return runtime, ServiceExecutionClient(
        runtime, "application.orders", scope_ref=_ORDER_SCOPE_REF
    )


def test_real_curve_paper_risk_monitor_buy_and_restart_do_not_duplicate_events(
    tmp_path,
) -> None:
    from bt_api_execution import ExecutionScope, SqliteExecutionStore
    from bt_api_execution.journal_reader import ExecutionJournalReader
    from bt_api_monitor.durable import DurableOutbox
    from bt_api_py.service_execution_monitor import _outbox_scope

    paths = {
        "execution": str(tmp_path / "paper-execution.sqlite3"),
        "risk": str(tmp_path / "paper-risk.sqlite3"),
        "risk-journal": str(tmp_path / "paper-risk-journal.sqlite3"),
    }
    execution_scope = ExecutionScope(
        "paper",
        "paper",
        _ACCOUNT_REF,
        _STRATEGY_REF,
        "paper-host-trading-day-v1",
    )
    seed_store = SqliteExecutionStore(paths["execution"])
    seed_store.close()
    reader = ExecutionJournalReader(paths["execution"], scope=execution_scope)
    outbox_scope = _outbox_scope(reader.generation, execution_scope.key)
    reader.close()

    context = multiprocessing.get_context("spawn")
    quote_cursor_counter = context.Value("Q", 0)
    monitor_endpoint = _endpoint()
    owner_endpoint = _endpoint()
    def curve_keys():
        server_public, server_secret = zmq.curve_keypair()
        application_public, application_secret = zmq.curve_keypair()
        return {
            "server_public": server_public,
            "server_secret": server_secret,
            "application_public": application_public,
            "application_secret": application_secret,
        }

    monitor_keys = curve_keys()
    owner_keys = curve_keys()
    raw_monitor_config = _monitor_service_config(monitor_endpoint)
    raw_owner_config = _owner_service_config(owner_endpoint, monitor_endpoint)
    monitor_record = None
    owner_records = []
    forced: list[str] = []
    failures: list[str] = []
    strategy_runtime = None
    try:
        monitor_record = _start_process(
            context,
            _monitor_host_process,
            (
                raw_monitor_config,
                str(tmp_path / "paper-monitor-registry.sqlite3"),
                str(tmp_path / "paper-monitor-outbox.sqlite3"),
                outbox_scope,
                monitor_keys,
                owner_keys,
            ),
            "paper-risk-monitor-host",
        )
        monitor_ready = _receive(monitor_record["pipe"])
        _require_process_response(monitor_record, monitor_ready, "ready")
        assert monitor_ready == (
            "ready",
            monitor_endpoint,
            "started",
            ("monitor",),
        )

        owner_record = _start_process(
            context,
            _owner_host_process,
            (
                raw_owner_config,
                str(tmp_path / "paper-owner-registry.sqlite3"),
                paths,
                owner_keys,
                monitor_keys,
                _SERVICE_INSTANCE,
                True,
                quote_cursor_counter,
            ),
            "paper-risk-execution-owner-host",
        )
        owner_records.append(owner_record)
        owner_ready = _receive(owner_record["pipe"])
        _require_process_response(owner_record, owner_ready, "ready")
        assert owner_ready[0] == "ready", owner_ready
        assert owner_ready[1:3] == (owner_endpoint, "started")
        assert owner_ready[4] == {_SERVICE_INSTANCE: "execution_risk_monitor"}
        assert owner_ready[5] == 1
        assert owner_ready[6] == {}
        assert owner_ready[3] == (
            (
                "execution",
                _SERVICE_INSTANCE,
                ("service.order_entry",),
                (
                    (
                        "service.order_entry",
                        ("orders.cancel", "orders.submit"),
                    ),
                ),
            ),
            (
                "risk",
                _RISK_INSTANCE,
                ("service.order_entry",),
                (("service.order_entry", ("orders.submit",)),),
            ),
        )

        first_writer_lease = _writer_lease(
            paths["execution"], execution_scope.account_key
        )
        assert first_writer_lease[1] == 1
        assert first_writer_lease[2] > time.time_ns()

        # A second process using the same trusted component instance and Paper
        # domain cannot enter the account owner's active E-store sidecar fence.
        # It shares the E/Risk databases but has a separate listener and
        # service registry; rejection occurs before recovery or dispatch.
        competing_endpoint = _endpoint()
        competing_config = _owner_service_config(competing_endpoint, monitor_endpoint)
        assert competing_config["components"] == raw_owner_config["components"]
        competing_record = _start_process(
            context,
            _owner_host_process,
            (
                competing_config,
                str(tmp_path / "paper-owner-competing-registry.sqlite3"),
                paths,
                owner_keys,
                monitor_keys,
                _SERVICE_INSTANCE,
                False,
                quote_cursor_counter,
            ),
            "paper-risk-competing-execution-owner-host",
        )
        owner_records.append(competing_record)
        competing_result = _receive(competing_record["pipe"])
        assert competing_result == ("host_error", "ORDER_OWNER_ALREADY_ACTIVE")
        competing_record["process"].join(15)
        assert not competing_record["process"].is_alive()
        assert competing_record["process"].exitcode == 1
        competing_record["pipe"].close()
        competing_record["process"].close()
        competing_record["closed"] = True
        assert _writer_lease(paths["execution"], execution_scope.account_key) == (
            first_writer_lease
        )

        strategy_runtime, execution = _application_runtime(owner_endpoint, owner_keys)
        order = execution.submit(
            intent_id=_RISK_INTENT_ID,
            signal_id="paper-risk-signal-v1",
            instrument=_RISK_INSTRUMENT,
            side="BUY",
            position_effect="OPEN",
            order_type="MARKET",
            quantity="1.25",
            price=None,
            time_in_force="FOK",
            reduce_only=False,
            metadata_version="paper-instrument-v1",
        )
        assert order.state == "FILLED"
        assert order.dispatch_attempts == 1
        assert type(order.permit_reference) is str and order.permit_reference

        owner_record["pipe"].send(("snapshot",))
        first = _receive(owner_record["pipe"])
        _require_process_response(owner_record, first, "snapshot")
        assert first[0] == "snapshot", first
        assert first[1:5] == ("FILLED", 1, order.permit_reference, "FILLED")
        assert first[7] > 0
        assert type(first[8]) is str and first[8]
        assert first[9] == "87.4625"
        assert first[10] == ((_RISK_INSTRUMENT, "1.25"),)
        assert first[11] == [_RISK_INSTRUMENT]
        assert first[12] == 1
        assert first[13] == "12.5375"
        assert first[14] is True
        assert first[15] == {}
        assert first[16:20] == ("0.0125", "1.25", "0", "1.25")
        assert first[20:24] == (None, None, None, None)
        assert first[24] == 1

        monitor_record["pipe"].send(("rows",))
        first_rows = _receive(monitor_record["pipe"])
        _require_process_response(monitor_record, first_rows, "rows")
        assert first_rows[0] == "rows" and first_rows[1] is False
        assert first_rows[2]
        assert len({row[1] for row in first_rows[2]}) == len(first_rows[2])
        assert all(row[2] == "execution.lifecycle" for row in first_rows[2])
        assert all(
            row[3] == _SERVICE_PRINCIPAL
            and row[4] == "sdk.execution_monitor_projection.v2"
            and row[5] == _ACCOUNT_REF
            and row[6] == _STRATEGY_REF
            for row in first_rows[2]
        )

        source_reader = ExecutionJournalReader(paths["execution"], scope=execution_scope)
        try:
            source_events = source_reader.read_page("execution", limit=100).events
            source_ids = {event.event_id for event in source_events}
            monitor_source_ids = {row[7] for row in first_rows[2]}
            assert monitor_source_ids == source_ids
        finally:
            source_reader.close()

        strategy_runtime.close()
        strategy_runtime = None
        owner_closed = _close_normally(owner_record, expected_tag="closed")
        assert owner_closed[1:3] == ("closed", ())
        assert owner_closed[3] == (_RISK_INSTRUMENT,)
        assert owner_closed[4] == 0
        assert owner_closed[5] == {}

        restart_record = _start_process(
            context,
            _owner_host_process,
            (
                raw_owner_config,
                str(tmp_path / "paper-owner-registry.sqlite3"),
                paths,
                owner_keys,
                monitor_keys,
                _SERVICE_INSTANCE,
                True,
                quote_cursor_counter,
            ),
            "paper-risk-execution-owner-restarted",
        )
        owner_records.append(restart_record)
        restarted = _receive(restart_record["pipe"])
        _require_process_response(restart_record, restarted, "ready")
        assert restarted[0] == "ready", restarted
        assert restarted[4] == {_SERVICE_INSTANCE: "execution_risk_monitor"}
        assert restarted[5] == 1 and restarted[6] == {}
        assert restarted[3] == owner_ready[3]

        strategy_runtime, execution = _application_runtime(
            owner_endpoint, owner_keys
        )
        replay = execution.submit(
            intent_id=_RISK_INTENT_ID,
            signal_id="paper-risk-signal-v1",
            instrument=_RISK_INSTRUMENT,
            side="BUY",
            position_effect="OPEN",
            order_type="MARKET",
            quantity="1.25",
            price=None,
            time_in_force="FOK",
            reduce_only=False,
            metadata_version="paper-instrument-v1",
        )
        assert replay == order

        restart_record["pipe"].send(("snapshot",))
        second = _receive(restart_record["pipe"])
        _require_process_response(restart_record, second, "snapshot")
        assert second[0] == "snapshot", second
        assert second[1:5] == ("FILLED", 1, order.permit_reference, "FILLED")
        assert second[7:11] == first[7:11]
        assert second[11] == []
        assert second[12:14] == first[12:14]
        assert second[14:16] == first[14:16]
        assert second[16:24] == first[16:24]
        assert second[14] is True and second[15] == {}
        assert second[24] == first[24]

        monitor_record["pipe"].send(("rows",))
        second_rows = _receive(monitor_record["pipe"])
        _require_process_response(monitor_record, second_rows, "rows")
        assert second_rows == first_rows

        close_order = execution.submit(
            intent_id=_RISK_CLOSE_INTENT_ID,
            signal_id="paper-risk-close-signal-v1",
            instrument=_RISK_INSTRUMENT,
            side="SELL",
            position_effect="CLOSE",
            order_type="MARKET",
            quantity="1.25",
            price=None,
            time_in_force="FOK",
            reduce_only=True,
            metadata_version="paper-instrument-v1",
        )
        assert close_order.state == "FILLED"
        assert close_order.dispatch_attempts == 1
        assert type(close_order.permit_reference) is str and close_order.permit_reference

        restart_record["pipe"].send(("snapshot",))
        after_close = _receive(restart_record["pipe"])
        _require_process_response(restart_record, after_close, "snapshot")
        assert after_close[0] == "snapshot", after_close
        assert after_close[1:5] == ("FILLED", 1, order.permit_reference, "FILLED")
        assert after_close[6] == 2 and after_close[7] > second[7]
        assert after_close[8] == first[8]
        assert Decimal(after_close[9]) == Decimal("99.95")
        assert after_close[10] == ()
        assert after_close[11] == [_RISK_INSTRUMENT]
        assert after_close[12:14] == first[12:14]
        assert after_close[14:16] == (True, {})
        assert Decimal(after_close[16]) == Decimal("0.0250")
        assert tuple(Decimal(value) for value in after_close[17:20]) == (
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
        )
        assert after_close[20] == "FILLED"
        assert after_close[21] == "FILLED"
        assert type(after_close[22]) is str and after_close[22]
        assert after_close[23] == "TERMINAL_CLOSE_RESOLVED"
        assert after_close[24] == second[24] + 1

        monitor_record["pipe"].send(("rows",))
        after_close_rows = _receive(monitor_record["pipe"])
        _require_process_response(monitor_record, after_close_rows, "rows")
        assert after_close_rows[0] == "rows" and after_close_rows[1] is False
        assert len(after_close_rows[2]) > len(second_rows[2])
        assert len({row[1] for row in after_close_rows[2]}) == len(after_close_rows[2])
        assert all(row[2] == "execution.lifecycle" for row in after_close_rows[2])
        source_reader = ExecutionJournalReader(paths["execution"], scope=execution_scope)
        try:
            source_events = source_reader.read_page("execution", limit=100).events
            assert {row[7] for row in after_close_rows[2]} == {
                event.event_id for event in source_events
            }
        finally:
            source_reader.close()

        strategy_runtime.close()
        strategy_runtime = None
        closed_after_close = _close_normally(restart_record, expected_tag="closed")
        assert closed_after_close[1:3] == ("closed", ())
        assert closed_after_close[3] == (_RISK_INSTRUMENT,)
        # monitor_relays reports live relay attachments, which are closed here;
        # delivery itself was proved by the durable Monitor rows above.
        assert closed_after_close[4] == 0
        assert closed_after_close[5] == {}

        final_record = _start_process(
            context,
            _owner_host_process,
            (
                raw_owner_config,
                str(tmp_path / "paper-owner-registry.sqlite3"),
                paths,
                owner_keys,
                monitor_keys,
                _SERVICE_INSTANCE,
                True,
                quote_cursor_counter,
            ),
            "paper-risk-execution-owner-after-close-restarted",
        )
        owner_records.append(final_record)
        final_ready = _receive(final_record["pipe"])
        _require_process_response(final_record, final_ready, "ready")
        assert final_ready[0] == "ready", final_ready
        assert final_ready[4] == {_SERVICE_INSTANCE: "execution_risk_monitor"}
        assert final_ready[5] == 1 and final_ready[6] == {}
        assert final_ready[3] == owner_ready[3]

        strategy_runtime, execution = _application_runtime(owner_endpoint, owner_keys)
        assert execution.submit(
            intent_id=_RISK_INTENT_ID,
            signal_id="paper-risk-signal-v1",
            instrument=_RISK_INSTRUMENT,
            side="BUY",
            position_effect="OPEN",
            order_type="MARKET",
            quantity="1.25",
            price=None,
            time_in_force="FOK",
            reduce_only=False,
            metadata_version="paper-instrument-v1",
        ) == order
        assert execution.submit(
            intent_id=_RISK_CLOSE_INTENT_ID,
            signal_id="paper-risk-close-signal-v1",
            instrument=_RISK_INSTRUMENT,
            side="SELL",
            position_effect="CLOSE",
            order_type="MARKET",
            quantity="1.25",
            price=None,
            time_in_force="FOK",
            reduce_only=True,
            metadata_version="paper-instrument-v1",
        ) == close_order
        final_record["pipe"].send(("snapshot",))
        final_snapshot = _receive(final_record["pipe"])
        _require_process_response(final_record, final_snapshot, "snapshot")
        assert final_snapshot[1:11] == after_close[1:11]
        assert final_snapshot[12:24] == after_close[12:24]
        assert final_snapshot[11] == []
        assert final_snapshot[24] == after_close[24]

        monitor_record["pipe"].send(("rows",))
        final_rows = _receive(monitor_record["pipe"])
        _require_process_response(monitor_record, final_rows, "rows")
        assert final_rows == after_close_rows
        durable_outbox = DurableOutbox(
            str(tmp_path / "paper-monitor-outbox.sqlite3"), read_only=True
        )
        persisted, has_more = durable_outbox.read_page_types(
            outbox_scope, _EVENT_TYPES, 0, 100
        )
        assert has_more is False
        assert tuple(
            (row.sequence, row.event.event_id, row.event.event_type)
            for row in persisted
        ) == tuple((row[0], row[1], row[2]) for row in after_close_rows[2])

        strategy_runtime.close()
        strategy_runtime = None
        final_closed = _close_normally(final_record, expected_tag="closed")
        assert final_closed[1:3] == ("closed", ())
        assert final_closed[4:] == (0, {})
        monitor_closed = _close_normally(monitor_record, expected_tag="closed")
        assert monitor_closed[1:] == ("closed", ())
    finally:
        if strategy_runtime is not None:
            strategy_runtime.close()
        for record in reversed(owner_records):
            if not record["closed"]:
                _bounded_reap_spawned_process(record, forced, failures)
        if monitor_record is not None and not monitor_record["closed"]:
            _bounded_reap_spawned_process(monitor_record, forced, failures)
        _assert_no_fallback_cleanup(forced, failures)

