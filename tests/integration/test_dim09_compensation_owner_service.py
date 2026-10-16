"""DIM09 coordinator recovery through the authenticated account-owner bridge.

The only synthetic boundary is the local provider feed. The coordinator uses
its normal group scheduler and routes compensation through a CURVE-authenticated
Risk ServiceHost. A dropped submit reply exercises the persisted query-only
recovery path before a trusted CLOSE receipt can close the group.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import socket
import sys
import threading
import time
import traceback
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import zmq

import bt_api_execution.group_store as execution_group_store
import bt_api_base.plugins.loader as plugin_loader
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    GroupCompensationBudgetPolicy,
    GroupOrderState,
    payload_sha256,
)
from bt_api_risk import (
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    ExposureAccountingMode,
    InstrumentRiskAdmissionMapper,
    RiskPolicy,
)
from bt_api_py import service_risk_order_entry as risk_entry
from bt_api_py import service_execution_group as group_execution
from bt_api_py.service_components import BorrowedServiceClient, ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_execution_group import GroupCoordinatorService
from bt_api_py.service_execution_group_compensation_source import (
    GroupCompensationAuthorizationSourceV1,
)
import bt_api_py.service_execution_group_client as group_client_source
import bt_api_py.service_execution_group_compensation_source as compensation_source
import bt_api_py.service_group_compensation_contracts as compensation_contracts
from bt_api_py.service_group_compensation_contracts import (
    GroupCompensationCloseAuthorization,
)
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    TrustedGroupWritePolicy,
)
from bt_api_py.service_risk_order_entry import RiskBtApiProviderContract
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.integration import _dim09_three_leg_fixtures as dim09


_RISK_SERVICE_ID = "dim09-compensation-risk-service"
_RISK_INSTANCE = "dim09-compensation-risk-owner"
_RISK_PRINCIPAL = "dim09-compensation-risk-principal"
_COMP_SCOPE_REF = "dim09-compensation-owner-scope-v1"
_SERVER_SECURITY_REF = "dim09-compensation-server-curve-v1"
_CLIENT_SECURITY_REF = "dim09-compensation-client-curve-v1"
_LISTENER_REF = "dim09-compensation-listener-v1"
_CLIENT_TRANSPORT_REF = "dim09-compensation-connect-v1"
_COMP_BUDGET_POLICY = "dim09-compensation-budget-v1"
_GROUP_ID = "dim09-compensation-owner-close-01"


def _assert_dim09_fixed_source_imports() -> None:
    sdk_root = Path(__file__).resolve().parents[2]
    snapshot_root = sdk_root.parent
    module_pins = {
        "bt_api_py.service_execution_group_client": (
            group_client_source,
            sdk_root / "bt_api_py" / "service_execution_group_client.py",
            "051782f9b5b35bb98273b98490f489e937cafbdb124a08d6393bc64bf684d1eb",
        ),
        "bt_api_py.service_execution_group_compensation_source": (
            compensation_source,
            sdk_root / "bt_api_py" / "service_execution_group_compensation_source.py",
            "ba007e90d6cddc9b6471635f600e527bda5c06c13f18f862ed7c69cce4328b21",
        ),
        "bt_api_py.service_group_compensation_contracts": (
            compensation_contracts,
            sdk_root / "bt_api_py" / "service_group_compensation_contracts.py",
            "f526b571f057321e37d0b052f15eb2757e430134051bffd893b2ff139972ddff",
        ),
        "bt_api_execution.group_store": (
            execution_group_store,
            snapshot_root / "execution" / "bt_api_execution" / "group_store.py",
            "fc586144d6f73b0d6af7a39270213fa12fc883b76f846363d5861017fb168593",
        ),
    }
    imported_modules: dict[str, dict[str, str]] = {}
    for name, (module, expected_path, expected_sha256) in module_pins.items():
        actual_path = Path(module.__file__).resolve()
        actual_sha256 = hashlib.sha256(actual_path.read_bytes()).hexdigest()
        assert actual_path == expected_path.resolve(), (name, actual_path, expected_path)
        assert actual_sha256 == expected_sha256, (name, actual_sha256, expected_sha256)
        imported_modules[name] = {"path": str(actual_path), "sha256": actual_sha256}

    source_type = compensation_source.GroupCompensationAuthorizationSourceV1
    parser = source_type._parse_compensation_events
    assert getattr(parser, "__self__", None) is source_type
    method_pins = {
        "verify_accepted_group_projection": (
            group_client_source.verify_accepted_group_projection,
            "2f405cdccbedbfc0498bd6c25b2b2e6b4285926c06e2e81eed24da6dc71fd199",
        ),
        "resolve_close": (
            source_type.resolve_close,
            "c4e2d7c1643492e64a9efa010ffa5508aca27bf473204eb450243d9faad5514a",
        ),
        "_parse_compensation_events": (
            parser,
            "c0c59e7ad8200e9e1cbdea7f6ea2d4f4b23c784ce9495f695c07bdc15163b9b5",
        ),
        "_validate_close_receipt_payload": (
            source_type._validate_close_receipt_payload,
            "adf29b1d594b259bc6dbaf972a7daa5d025d0385e75c1acb7d1c0947e1e1b7c8",
        ),
        "GroupCompensationResolvedActionRef.__post_init__": (
            compensation_contracts.GroupCompensationResolvedActionRef.__post_init__,
            "870d74f8ac80545e77c9e27786608d783d2b768e9189e5b413daef937b7a407d",
        ),
        "_fraction_to_decimal_contract": (
            compensation_contracts._fraction_to_decimal_contract,
            "40f0e407914c4b4af3debc2964dda23b27950c16f0bb84869e500301cf6fe2ee",
        ),
        "_proven_group_compensation_close": (
            execution_group_store._proven_group_compensation_close,
            "f8a09b642f6137e65acc0662fa24ef9c11f7053d45517b12333fa4f8fadb35ef",
        ),
    }
    imported_methods: dict[str, str] = {}
    for name, (method, expected_sha256) in method_pins.items():
        source = inspect.getsource(method)
        actual_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
        assert actual_sha256 == expected_sha256, (name, actual_sha256, expected_sha256)
        imported_methods[name] = actual_sha256
    print(
        "DIM09_FIXED_SOURCE_IMPORT_PINS "
        + json.dumps(
            {"modules": imported_modules, "methods": imported_methods},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _risk_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _RISK_SERVICE_ID,
        "scope_ref": _COMP_SCOPE_REF,
        "transport_ref": _LISTENER_REF,
        "registration_policy_ref": "dim09-compensation-registration-v1",
        "service_requirements_ref": "dim09-compensation-requirements-v1",
        "components": {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _RISK_INSTANCE,
                    "bind_ports": ["service.order_entry", "service.order_query"],
                    "options": {
                        "role": "account_owner",
                        "account_policy_ref": "dim09-compensation-risk-policy-v1",
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
                    "security_profile_ref": _SERVER_SECURITY_REF,
                    "options": {"high_water_mark": 32, "max_message_bytes": 1_000_000},
                }
            },
        },
    }


def _coordinator_runtime_config(endpoint: str) -> dict[str, Any]:
    options = {
        "request_timeout_ms": 2500,
        "high_water_mark": 32,
        "max_message_bytes": 1_000_000,
    }
    client_options = {"request_timeout_ms": 2500, "heartbeat_interval_seconds": 5}
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "dim09-compensation-coordinator-runtime",
        "scope_ref": "dim09-compensation-coordinator-runtime-scope-v1",
        "transport_ref": "dim09-compensation-coordinator-runtime-listener-v1",
        "registration_policy_ref": "dim09-compensation-coordinator-runtime-registration-v1",
        "service_requirements_ref": "dim09-compensation-coordinator-runtime-requirements-v1",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "dim09-compensation-unused-monitor",
                    "bind_ports": ["service.monitor_query"],
                    "options": {},
                },
            }
        },
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": [
                        {
                            "mount": "upstream.group_compensation_orders",
                            "service_id": _RISK_SERVICE_ID,
                            "instance_id": _RISK_INSTANCE,
                            "scope_ref": _COMP_SCOPE_REF,
                            "account_ref": dim09._ACCOUNT,
                            "transport_ref": _CLIENT_TRANSPORT_REF,
                        },
                        {
                            "mount": "upstream.group_compensation_order_query",
                            "service_id": _RISK_SERVICE_ID,
                            "instance_id": _RISK_INSTANCE,
                            "scope_ref": _COMP_SCOPE_REF,
                            "account_ref": dim09._ACCOUNT,
                            "transport_ref": _CLIENT_TRANSPORT_REF,
                        },
                    ],
                    "options": client_options,
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "dim09-compensation-coordinator-runtime-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": "tcp://127.0.0.1:9"},
                    "security_profile_ref": _CLIENT_SECURITY_REF,
                    "options": dict(options),
                },
                _CLIENT_TRANSPORT_REF: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_connect": endpoint},
                    "security_profile_ref": _CLIENT_SECURITY_REF,
                    "options": dict(options),
                },
            },
        },
    }


class _HostThread:
    """Run all Host lifecycle operations on one owner thread."""

    def __init__(self, host: ServiceHost) -> None:
        self.host = host
        self.stop_requested = threading.Event()
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.error: BaseException | None = None
        self.start_succeeded = False
        self.thread = threading.Thread(target=self._run, name="dim09-risk-host")

    def _run(self) -> None:
        try:
            self.host.start()
            self.start_succeeded = True
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
        assert self.ready.wait(10), "Risk ServiceHost did not become ready"
        assert self.error is None, (
            self.error,
            self.host.summary.get("start_failure"),
            self.host.summary.get("component_states"),
            getattr(self, "start_exceptions", None),
        )
        assert self.host.state == "started", self.host.summary

    def close(self) -> None:
        self.stop_requested.set()
        assert self.finished.wait(10), "Risk ServiceHost did not stop normally"
        self.thread.join(1)
        assert not self.thread.is_alive()
        if not self.start_succeeded:
            # start() already reported the preserved first failure.
            return
        assert self.error is None, self.error
        assert self.host.state == "closed", self.host.summary


class _CoordinatorRouter:
    """Route existing group operations locally and compensation over CURVE."""

    def __init__(self, loopback, runtime: ServiceClientRuntime) -> None:
        self.loopback = loopback
        self.runtime = runtime
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.responses: list[tuple[str, str, object]] = []
        self.dropped_submit_replies = 0

    @property
    def state(self) -> str:
        return self.runtime.state

    @property
    def healthy(self) -> bool:
        return self.runtime.healthy

    def call(self, mount: str, operation: str, arguments, **kwargs):
        payload = dict(arguments)
        self.calls.append((mount, operation, payload))
        if mount in {
            "upstream.account_reservations",
            "upstream.orders",
            "upstream.order_query",
        }:
            return self.loopback.call(mount, operation, payload, **kwargs)
        if mount not in {
            "upstream.group_compensation_orders",
            "upstream.group_compensation_order_query",
        }:
            raise AssertionError(("unexpected route", mount, operation))
        try:
            result = self.runtime.call(
                mount,
                operation,
                payload,
                account_ref=dim09._ACCOUNT,
                portfolio_ref=dim09._PORTFOLIO,
                scope_ref=_COMP_SCOPE_REF,
                max_reply_message_bytes=900_000,
            )
        except Exception as error:
            self.responses.append(
                (
                    mount,
                    operation,
                    {
                        "exception_type": type(error).__qualname__,
                        "exception_message": str(error),
                        "traceback": "".join(
                            traceback.format_exception(error)
                        )[-16000:],
                    },
                )
            )
            raise
        self.responses.append((mount, operation, result))
        if (
            mount == "upstream.group_compensation_orders"
            and operation == "orders.submit_group_compensation_close"
            and self.dropped_submit_replies == 0
        ):
            self.dropped_submit_replies += 1
            raise ConnectionError("synthetic coordinator reply loss after Risk commit")
        return result


def test_dim09_owner_compensation_recovers_lost_submit_and_closes_only_from_receipt(
    tmp_path, monkeypatch
) -> None:
    _assert_dim09_fixed_source_imports()
    preparation_errors: list[dict[str, Any]] = []
    accepted_projection_differences: list[tuple[str, ...]] = []
    accepted_projection_facts: list[dict[str, Any]] = []
    source_metadata_facts: list[dict[str, Any]] = []
    authorization_payload_facts: list[dict[str, Any]] = []
    plugin_entry_calls = 0

    def _empty_synthetic_provider_entry_points(*args, **kwargs):
        nonlocal plugin_entry_calls
        caller = sys._getframe(1)
        assert not args and not kwargs
        assert caller.f_globals.get("__name__") == "bt_api_base.plugins.loader"
        assert caller.f_code.co_name == "_discover_entry_points"
        assert caller.f_locals.get("group") == "bt_api.plugins"
        plugin_entry_calls += 1
        return ()

    def _record_synthetic_exception(phase: str, error: BaseException) -> None:
        code = getattr(error, "code", None)
        preparation_errors.append(
            {
                "phase": phase,
                "module": type(error).__module__,
                "type": type(error).__qualname__,
                "code": code if type(code) is str else None,
                "message": str(error),
                "traceback": "".join(traceback.format_exception(error))[-16000:],
            }
        )

    with monkeypatch.context() as plugin_patch:
        plugin_patch.setattr(
            plugin_loader.importlib_metadata,
            "entry_points",
            _empty_synthetic_provider_entry_points,
        )
        cluster = dim09.Dim09ThreeLegCluster(
            tmp_path,
            monkeypatch,
            initial_states=("filled", "rejected", "filled"),
            supports_close=False,
        )
    assert plugin_entry_calls == 1, plugin_entry_calls
    risk_gate: DurableRiskGate | None = None
    host_loop: _HostThread | None = None
    runtime: ServiceClientRuntime | None = None
    try:
        # The real mapped order path is retained. This test-local provider seam
        # widens only its synthetic leaf to accept the trusted CLOSE/GTC action.
        feed_type = type(cluster.provider)
        original_make_order = feed_type.make_order
        synthetic_make_order = dim09._V2SyntheticBtApiFeed.make_order
        close_requests: list[tuple[str, str, str, dict[str, Any]]] = []

        def _close_capable_make_order(
            self,
            symbol,
            volume,
            price,
            order_type,
            *,
            client_order_id,
            **kwargs,
        ):
            if kwargs.get("offset") == "close":
                if (
                    symbol != dim09._V2_INSTRUMENT
                    or Decimal(str(volume)) != Decimal("1")
                    or Decimal(str(price)) != Decimal("100")
                    or order_type != "sell-limit"
                    or kwargs
                    != {
                        "offset": "close",
                        "post_only": False,
                        "reduce_only": True,
                        "time_in_force": "GTC",
                    }
                ):
                    raise AssertionError(
                        (symbol, volume, price, order_type, client_order_id, kwargs)
                    )
                close_requests.append(
                    (client_order_id, symbol, order_type, dict(kwargs))
                )
                return synthetic_make_order(
                    self,
                    symbol,
                    volume,
                    price,
                    order_type,
                    client_order_id=client_order_id,
                    **kwargs,
                )
            return original_make_order(
                self,
                symbol,
                volume,
                price,
                order_type,
                client_order_id=client_order_id,
                **kwargs,
            )

        monkeypatch.setattr(feed_type, "make_order", _close_capable_make_order)

        comp_policy = GroupCompensationBudgetPolicy(
            policy_id=_COMP_BUDGET_POLICY,
            policy_version=1,
            cap_amount=Decimal("1000"),
            currency="USD",
        )
        original_route = cluster.coordinator_target.accounts[cluster.account_ref]
        comp_route = replace(
            original_route,
            compensation_order_scope_ref=_COMP_SCOPE_REF,
            compensation_query_scope_ref=_COMP_SCOPE_REF,
        )
        target = replace(
            cluster.coordinator_target,
            accounts={cluster.account_ref: comp_route},
            compensation_budget_policy=comp_policy,
        )

        # Give the coordinator only the exact SERVICE write route and query
        # routes. The Risk Host policy and the source both bind to the fixture's
        # persistent group/run stores and the original coordinator identity.
        remote_identity = cluster.service_identity
        comp_write_policy = TrustedGroupWritePolicy(
            _RISK_INSTANCE,
            remote_identity.principal_ref,
            remote_identity,
            _COMP_SCOPE_REF,
            cluster.owner_scope,
            dim09._RUN,
            operation="orders.submit_group_compensation_close",
        )
        server_public, server_secret = zmq.curve_keypair()
        client_public, client_secret = zmq.curve_keypair()
        endpoint = _endpoint()
        raw_config = _risk_config(endpoint)
        config = parse_service_config(raw_config)
        service_identity = WireIdentity(
            WireRole.SERVICE, _RISK_PRINCIPAL, _RISK_SERVICE_ID
        )
        owner_scope = cluster.owner_scope
        proof_target = cluster.proof_source.targets[owner_scope]
        journal = proof_target.risk_order_journal
        risk_gate = DurableRiskGate(
            tmp_path / "dim09-compensation-risk.sqlite3",
            RiskPolicy(
                "dim09-compensation-risk-policy-v1",
                Decimal("100000"),
                max_increase_count=10,
                permit_ttl_seconds=240,
                require_strategy_allocation=False,
                notional_unit="USD",
                exposure_accounting_mode=ExposureAccountingMode.RESERVATIONS_ONLY,
            ),
            execution_journal_authority=journal,
        )
        risk_gate.set_strategy_allocation(
            cluster.risk_scope,
            dim09._STRATEGY,
            "dim09-compensation-allocation-v1",
            max_notional=Decimal("100000"),
            notional_unit="USD",
        )
        risk_gate.set_closable_position(
            cluster.risk_scope,
            dim09._V2_INSTRUMENT,
            dim09._STRATEGY,
            "dim09-compensation-close-position-v1",
            closable_quantity=Decimal("1"),
            quantity_unit=cluster.metadata.quantity_unit,
        )
        admission_mapper = InstrumentRiskAdmissionMapper(
            cluster.risk_scope,
            proof_target.risk_target.registry,
            clock_ns=time.time_ns,
            allocation_reader=lambda scope: risk_gate.get_strategy_allocation(
                cluster.risk_scope, scope.strategy_id
            ),
            # RiskOrderEntryService maps its private _AdmissionOrder from the
            # trusted Risk target, so this close-only mapper must bind the
            # provider account ID. The separate group-proof mapper continues
            # to bind the external WireScope alias ``account-a``.
            wire_account_ref=cluster.risk_scope.account_id,
        )
        admission = DurableRiskAdmissionAdapter(risk_gate, admission_mapper)
        source = GroupCompensationAuthorizationSourceV1(
            cluster.accepted_store,
            cluster.origin_run_bindings,
            cluster.coordinator_store,
            cluster.execution_store,
            cluster.proof_source,
            cluster.authority.guard,
            coordinator_principal_ref=remote_identity.principal_ref,
        )

        def _capture_boundary(name: str, callback):
            def _wrapped(*args, **kwargs):
                try:
                    return callback(*args, **kwargs)
                except Exception as error:
                    _record_synthetic_exception(name, error)
                    raise

            return _wrapped

        # The production handler intentionally returns a generic uncertainty
        # for unexpected exceptions. This fixture uses only public synthetic
        # state, so retain the bounded local exception chain here without
        # changing the production error projection.
        monkeypatch.setattr(
            source,
            "resolve_action",
            _capture_boundary("source.resolve_action", source.resolve_action),
        )
        monkeypatch.setattr(
            source,
            "assert_authorization",
            _capture_boundary(
                "source.assert_authorization", source.assert_authorization
            ),
        )
        original_auth_from_payload = GroupCompensationCloseAuthorization.from_payload

        def _capture_auth_from_payload(cls, value):
            if isinstance(value, Mapping):
                expected = set(cls.__dataclass_fields__) | {"schema"}
                authorization_payload_facts.append(
                    {
                        "mapping_type": type(value).__qualname__,
                        "missing_keys": tuple(sorted(expected - set(value))),
                        "extra_keys": tuple(sorted(set(value) - expected)),
                        "schema_type": type(value.get("schema")).__qualname__,
                        "schema_matches": value.get("schema")
                        == "group-compensation-close-auth-v4",
                        "chain_vector_type": type(
                            value.get("source_chain_deficit_vector_base_units")
                        ).__qualname__,
                        "filled_vector_type": type(
                            value.get("source_filled_position_exposure_vector_base_units")
                        ).__qualname__,
                        "prior_actions_type": type(
                            value.get("prior_resolved_actions")
                        ).__qualname__,
                        "chain_coordinate_types": tuple(
                            tuple(type(part).__qualname__ for part in item)
                            if isinstance(item, list)
                            else type(item).__qualname__
                            for item in (
                                value.get("source_chain_deficit_vector_base_units")
                                if isinstance(
                                    value.get("source_chain_deficit_vector_base_units"),
                                    list,
                                )
                                else ()
                            )
                        ),
                        "filled_coordinate_types": tuple(
                            tuple(type(part).__qualname__ for part in item)
                            if isinstance(item, list)
                            else type(item).__qualname__
                            for item in (
                                value.get("source_filled_position_exposure_vector_base_units")
                                if isinstance(
                                    value.get("source_filled_position_exposure_vector_base_units"),
                                    list,
                                )
                                else ()
                            )
                        ),
                        "status_payload_type": type(
                            value.get("source_terminal_status")
                        ).__qualname__,
                        "status_payload_keys": tuple(
                            sorted(value["source_terminal_status"])
                        )
                        if isinstance(value.get("source_terminal_status"), Mapping)
                        else (),
                        "close_position_version_type": type(
                            value.get("close_position_version")
                        ).__qualname__,
                    }
                )
            else:
                authorization_payload_facts.append(
                    {"mapping_type": type(value).__qualname__}
                )
            try:
                return original_auth_from_payload(value)
            except Exception as error:
                _record_synthetic_exception("authorization.from_payload", error)
                raise

        monkeypatch.setattr(
            GroupCompensationCloseAuthorization,
            "from_payload",
            classmethod(_capture_auth_from_payload),
        )

        original_assert_accepted_projection = source._assert_accepted_projection

        def _capture_accepted_projection(accepted, snapshot):
            try:
                return original_assert_accepted_projection(accepted, snapshot)
            except Exception as error:
                _record_synthetic_exception("source.accepted_projection", error)
                labels: list[str] = []
                request = snapshot.request
                for label, matched in (
                    ("group_id", accepted.group_id == request.group_id),
                    ("account_ref", accepted.account_ref == source.execution_scope.account_ref),
                    ("portfolio_ref", accepted.portfolio_ref == request.portfolio_ref),
                    ("strategy_id", accepted.strategy_id == request.strategy_id),
                    ("origin_run_id", accepted.origin_run_id != ""),
                    (
                        "member_leg_order",
                        tuple(leg_id for leg_id, _ in accepted.members)
                        == tuple(leg.leg_id for leg in request.legs),
                    ),
                    (
                        "member_intents",
                        tuple(intent for _, intent in accepted.members)
                        == tuple(leg.maximum_intent for leg in request.legs),
                    ),
                    (
                        "member_intent_map",
                        dict(accepted.members)
                        == {leg.leg_id: leg.maximum_intent for leg in request.legs},
                    ),
                ):
                    if not matched:
                        labels.append(label)
                if accepted.reservation is None:
                    labels.append("accepted_reservation_missing")
                elif len(snapshot.reservations) != 1:
                    labels.append("group_reservation_count")
                else:
                    accepted_reservation = accepted.reservation
                    current_reservation = snapshot.reservations[0]
                    for field in (
                        "group_id",
                        "account_ref",
                        "scope_fingerprint",
                        "phase",
                        "group_fingerprint",
                        "policy_fingerprint",
                        "deadline_ns",
                        "legs",
                    ):
                        if getattr(accepted_reservation, field) != getattr(
                            current_reservation, field
                        ):
                            labels.append(f"reservation.{field}")
                    labels.append(
                        "reservation_legs_match_by_id="
                        + str(
                            {leg.leg_id: leg for leg in accepted_reservation.legs}
                            == {leg.leg_id: leg for leg in current_reservation.legs}
                        ).lower()
                    )
                    accepted_projection_facts.append(
                        {
                            "accepted_phase": accepted_reservation.phase.value,
                            "snapshot_phase": current_reservation.phase.value,
                            "accepted_legs": tuple(
                                (
                                    leg.leg_id,
                                    leg.state,
                                    leg.claimed,
                                    leg.resolved,
                                )
                                for leg in accepted_reservation.legs
                            ),
                            "snapshot_legs": tuple(
                                (
                                    leg.leg_id,
                                    leg.state,
                                    leg.claimed,
                                    leg.resolved,
                                )
                                for leg in current_reservation.legs
                            ),
                        }
                    )
                accepted_projection_differences.append(tuple(labels))
                raise

        monkeypatch.setattr(
            source,
            "_assert_accepted_projection",
            _capture_accepted_projection,
        )

        original_facts_from_verified_source = source._facts_from_verified_source

        def _capture_source_metadata(*args, **kwargs):
            try:
                return original_facts_from_verified_source(*args, **kwargs)
            except Exception as error:
                _record_synthetic_exception(
                    "source.facts_from_verified_source", error
                )
                source_set = kwargs.get("source")
                selected_leg_id = kwargs.get("selected_leg_id")
                selected = (
                    None
                    if not isinstance(source_set, Mapping)
                    else source_set.get(selected_leg_id)
                )
                if selected is not None:
                    plan, intent = selected[0], selected[1]
                    registry = source._proof_target.risk_target.registry
                    metadata = registry.get(intent.instrument)
                    contract = source._proof_target.provider_contract
                    policy = kwargs["snapshot"].request.compensation_budget_policy
                    source_metadata_facts.append(
                        {
                            "instrument": intent.instrument,
                            "intent_metadata_version": intent.metadata_version,
                            "registry_missing": metadata is None,
                            "registry_metadata_version": None
                            if metadata is None
                            else metadata.metadata_version,
                            "registry_quantity_unit": None
                            if metadata is None
                            else metadata.quantity_unit,
                            "registry_multiplier": None
                            if metadata is None
                            else str(metadata.contract_multiplier),
                            "registry_quote_currency": None
                            if metadata is None
                            else metadata.quote_currency,
                            "registry_valuation_unit": None
                            if metadata is None
                            else metadata.valuation_unit,
                            "plan_multiplier": str(plan.base_units_per_order_unit),
                            "policy_currency": None if policy is None else policy.currency,
                            "provider_quote_currency": contract.quote_currency(
                                intent.instrument
                            ),
                        }
                    )
                raise

        monkeypatch.setattr(
            source,
            "_facts_from_verified_source",
            _capture_source_metadata,
        )
        monkeypatch.setattr(
            risk_entry.RiskOrderEntryService,
            "_verify_group_compensation_prior_actions",
            _capture_boundary(
                "risk.verify_prior_actions",
                risk_entry.RiskOrderEntryService._verify_group_compensation_prior_actions,
            ),
        )
        monkeypatch.setattr(
            risk_entry,
            "_assert_group_compensation_source_exposure",
            _capture_boundary(
                "risk.assert_source_exposure",
                risk_entry._assert_group_compensation_source_exposure,
            ),
        )
        monkeypatch.setattr(
            risk_entry,
            "_assert_group_compensation_authorization",
            _capture_boundary(
                "risk.assert_authorization",
                risk_entry._assert_group_compensation_authorization,
            ),
        )
        monkeypatch.setattr(
            risk_entry,
            "_build_group_compensation_authorization",
            _capture_boundary(
                "risk.build_authorization",
                risk_entry._build_group_compensation_authorization,
            ),
        )
        assert source.ready is True
        assert source.composition == (
            cluster.accepted_store,
            cluster.origin_run_bindings,
            cluster.coordinator_store,
            cluster.execution_store,
            cluster.proof_source,
            cluster.authority.guard,
        )
        # GroupProofTarget stays on the accepted OPEN-only contract. The Risk
        # CLOSE service gets its own exact close-capable provider contract.
        provider_contract = replace(
            proof_target.provider_contract,
            supported_time_in_force=("GTC", "IOC"),
            supported_position_effects=("OPEN", "CLOSE"),
        )

        def _risk_resources(component, instance_id, options, scope):
            assert (component, instance_id) == ("risk", _RISK_INSTANCE)
            assert options["account_policy_ref"] == risk_gate.policy.policy_id
            return ComponentResources.borrowed(
                {
                    "bt_api": proof_target.bt_api,
                    "targets": {owner_scope: proof_target.risk_target},
                    "admission": {owner_scope: admission},
                    "journal": journal,
                    "gate": risk_gate,
                    "provider_contracts": {
                        owner_scope: provider_contract
                    },
                    "group_compensation_authorization_source": source,
                },
                ready=lambda: source.ready is True,
            )

        entry_grant = ResourceGrant(
            _RISK_INSTANCE,
            "service.order_entry",
            _COMP_SCOPE_REF,
            owner_scope,
            frozenset({"orders.submit_group_compensation_close"}),
        )
        query_grant = ResourceGrant(
            _RISK_INSTANCE,
            "service.order_query",
            _COMP_SCOPE_REF,
            owner_scope,
            frozenset(
                {
                    "orders.prepare_group_compensation_close",
                    "orders.query_group_compensation_close",
                }
            ),
        )
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=service_identity,
                scope=WireScope(dim09._DOMAIN),
                scope_ref=_COMP_SCOPE_REF,
                scope_version="dim09-compensation-scope-v1",
                security_ref=_SERVER_SECURITY_REF,
                security_version="curve-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version="dim09-compensation-registration-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version="dim09-compensation-requirements-v1",
                curve_public_key=server_public,
                curve_secret_key=server_secret,
                principals={client_public: remote_identity.principal_ref},
                grants={
                    remote_identity.principal_ref: (
                        PrincipalGrant(remote_identity, (entry_grant, query_grant)),
                    )
                },
                group_write_policies=(comp_write_policy,),
            ),
            tmp_path / "dim09-compensation-registry.sqlite3",
            _risk_resources,
        )
        start_exceptions: list[dict[str, Any]] = []
        original_start_error_code = ServiceHost._start_error_code

        def _record_start_exception(exc: BaseException) -> str:
            _record_synthetic_exception("service_host.start", exc)
            start_exceptions.append(preparation_errors[-1])
            return original_start_error_code(exc)

        monkeypatch.setattr(
            ServiceHost, "_start_error_code", staticmethod(_record_start_exception)
        )
        host_loop = _HostThread(host)
        host_loop.start_exceptions = start_exceptions
        host_loop.start()

        runtime_config = parse_service_config(_coordinator_runtime_config(endpoint))
        runtime = ServiceClientRuntime(
            runtime_config,
            identity=remote_identity,
            scope_resolver=lambda _scope_ref: owner_scope,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {
                    _CLIENT_SECURITY_REF: ClientCurveKeys(
                        server_public,
                        client_public,
                        client_secret,
                    )
                }
            ),
            run_id=dim09._RUN,
            requested_operations={
                "upstream.group_compensation_orders": frozenset(
                    {"orders.submit_group_compensation_close"}
                ),
                "upstream.group_compensation_order_query": frozenset(
                    {
                        "orders.prepare_group_compensation_close",
                        "orders.query_group_compensation_close",
                    }
                ),
            },
        )
        startup = runtime.start()
        assert startup.degraded_count == 0, startup
        assert {binding.mount for binding in runtime.bindings} == {
            "upstream.group_compensation_orders",
            "upstream.group_compensation_order_query",
        }

        router = _CoordinatorRouter(cluster.loopback, runtime)
        old_coordinator = cluster.coordinator_service
        old_coordinator.close()
        cluster._extra_closers = [
            closer
            for closer in cluster._extra_closers
            if getattr(closer, "__self__", None) is not old_coordinator
        ]
        new_coordinator = GroupCoordinatorService(
            cluster.coordinator_store,
            {cluster.strategy_template: target},
            cluster.origin_run_bindings,
            BorrowedServiceClient(router),
            remote_identity,
            instance_id=dim09._COORDINATOR_INSTANCE,
            group_admission=cluster.group_admission,
        )
        cluster._extra_closers.append(new_coordinator.close)
        cluster.coordinator_service = new_coordinator
        cluster.coordinator_target = target
        cluster.borrowed_upstream = BorrowedServiceClient(router)
        new_coordinator.maintain_registrations((cluster.strategy_registration,))
        assert new_coordinator.ready is True

        owner_compensation_ticks: list[dict[str, Any]] = []
        pending_compensation_advances: list[dict[str, Any]] = []
        original_maintain_compensation = new_coordinator._maintain_group_compensation
        original_advance_compensation = new_coordinator._advance_pending_compensation

        def _track_compensation_tick(snapshot):
            try:
                before_events = cluster.coordinator_store.events(
                    dim09._PORTFOLIO, _GROUP_ID
                )
                before_actions = group_execution._owner_compensation_actions(before_events)
                before_phases = tuple(
                    (item["authorization"].action_id, item["phase"].value)
                    for item in before_actions
                )
            except Exception as exc:
                before_phases = ((type(exc).__module__, type(exc).__qualname__),)
            result = original_maintain_compensation(snapshot)
            if len(owner_compensation_ticks) < 48:
                owner_compensation_ticks.append(
                    {
                        "state": snapshot.state.value,
                        "before_phases": before_phases,
                        "event_count_after": len(
                            cluster.coordinator_store.events(
                                dim09._PORTFOLIO, _GROUP_ID
                            )
                        ),
                        "state_after": cluster.current_snapshot(_GROUP_ID).state.value,
                    }
                )
            return result

        def _track_pending_advance(snapshot, order_port, action):
            if len(pending_compensation_advances) < 16:
                pending_compensation_advances.append(
                    {
                        "state": snapshot.state.value,
                        "phase": action["phase"].value,
                        "action_id": action["authorization"].action_id,
                    }
                )
            return original_advance_compensation(snapshot, order_port, action)

        monkeypatch.setattr(
            new_coordinator,
            "_maintain_group_compensation",
            _track_compensation_tick,
        )
        monkeypatch.setattr(
            new_coordinator,
            "_advance_pending_compensation",
            _track_pending_advance,
        )

        # A and B create a genuine source fill/residual. C is deliberately not
        # submitted; after the original deadline the coordinator plans a CLOSE
        # against A's filled position. Only the exact local ledger permits it.
        deadline_ns = time.time_ns() + 2_000_000_000
        submitted = cluster.submit(
            cluster.v3_body(_GROUP_ID, deadline_ns=deadline_ns)
        )
        assert submitted["ok"] is True, submitted
        accepted_snapshot = cluster.current_snapshot(_GROUP_ID)
        assert accepted_snapshot.state is GroupOrderState.RECOVERY_REQUIRED
        accepted_binding = cluster.origin_run_bindings.get(
            dim09._PORTFOLIO, _GROUP_ID
        )
        assert accepted_binding is not None
        assert accepted_binding.request_fingerprint == accepted_binding.request.fingerprint
        assert (
            accepted_binding.accepted_request_fingerprint
            == accepted_snapshot.request.fingerprint
        )
        assert new_coordinator._recovery_binding_matches(
            accepted_binding, accepted_snapshot, cluster.strategy_template, target
        ) is True

        quote = accepted_snapshot.request.budget_quote
        assert quote is not None and accepted_snapshot.budget_reservation is not None
        first_quote_leg = quote.legs[0]
        changed_assessment = (
            "0" if first_quote_leg.assessment_fingerprint[0] != "0" else "1"
        ) + first_quote_leg.assessment_fingerprint[1:]
        changed_quote_leg = replace(
            first_quote_leg, assessment_fingerprint=changed_assessment
        )
        changed_quote = replace(
            quote, legs=(changed_quote_leg, *quote.legs[1:])
        )
        changed_request = replace(
            accepted_snapshot.request, budget_quote=changed_quote
        )
        changed_snapshot = replace(
            accepted_snapshot,
            request=changed_request,
            budget_reservation=replace(
                accepted_snapshot.budget_reservation,
                quote_fingerprint=changed_quote.fingerprint,
            ),
        )
        assert (
            changed_snapshot.request.unquoted_fingerprint
            == accepted_snapshot.request.unquoted_fingerprint
        )
        assert changed_snapshot.request.fingerprint != accepted_snapshot.request.fingerprint
        assert new_coordinator._recovery_binding_matches(
            accepted_binding, changed_snapshot, cluster.strategy_template, target
        ) is False

        events: tuple[Any, ...] = ()
        final_snapshot = None
        owner_tick_limit = 64
        owner_deadline = time.monotonic() + 12
        owner_tick_count = 0
        while owner_tick_count < owner_tick_limit and time.monotonic() < owner_deadline:
            owner_tick_count += 1
            new_coordinator.maintain_registrations((cluster.strategy_registration,))
            # Read status only over the same authenticated public query route
            # used by the strategy client; the owner tick is the normal
            # registration-maintenance entry point.
            final_snapshot = cluster.current_snapshot(_GROUP_ID)
            events = cluster.coordinator_store.events(dim09._PORTFOLIO, _GROUP_ID)
            if final_snapshot.state is GroupOrderState.CLOSED:
                break
            time.sleep(0.15)

        assert final_snapshot is not None
        recovery_binding = cluster.origin_run_bindings.get(
            dim09._PORTFOLIO, _GROUP_ID
        )
        recovery_scope = recovery_binding.strategy_scope
        recovery_request = recovery_binding.request
        identity_facts = recovery_binding.strategy_identity
        recovery_checks = {
            "target_scope": new_coordinator._target_scope_for(recovery_scope)
            == cluster.strategy_template,
            "call_scope": group_execution._coordinator_call_scope(recovery_scope),
            "snapshot_fingerprint": final_snapshot.request.fingerprint
            == recovery_binding.accepted_request_fingerprint,
            "binding_fingerprint": recovery_request.fingerprint
            == recovery_binding.request_fingerprint,
            "snapshot_group": final_snapshot.request.group_id == recovery_request.group_id,
            "snapshot_principal": final_snapshot.request.coordinator_principal_ref
            == new_coordinator._identity.principal_ref,
            "binding_principal": recovery_binding.coordinator_principal_ref
            == new_coordinator._identity.principal_ref,
            "instance": recovery_binding.instance_id == new_coordinator._instance_id,
            "scope_ref": recovery_binding.scope_ref == target.scope_ref,
            "origin_run": recovery_binding.origin_run_id == recovery_scope.run_id,
            "run_present": recovery_scope.run_id is not None,
            "portfolio": recovery_scope.portfolio_ref == recovery_request.portfolio_ref,
            "strategy": recovery_scope.strategy_ref == recovery_request.strategy_id,
            "accounts": tuple(recovery_scope.account_refs)
            == recovery_request.account_refs,
            "target_accounts": all(
                account_ref in target.accounts for account_ref in recovery_request.account_refs
            ),
            "leg_accounts": all(
                leg.account_ref in target.accounts for leg in recovery_request.legs
            ),
            "budget_policy": target.portfolio_budget_policy is not None,
            "budget_quote_builder": target.budget_quote_builder is not None,
            "identity_role": identity_facts.get("role")
            == WireRole.STRATEGY_CLIENT.value,
            "identity_principal": identity_facts.get("principal_ref")
            == recovery_binding.strategy_principal_ref,
            "identity_actor": identity_facts.get("actor_ref")
            == recovery_request.strategy_id,
            "identity_delegate": identity_facts.get("delegate_ref") is None
            or (
                type(identity_facts.get("delegate_ref")) is str
                and group_execution._REFERENCE.fullmatch(
                    identity_facts.get("delegate_ref")
                )
                is not None
            ),
        }
        closure_diagnostic = {
            "state": final_snapshot.state.value,
            "residual": tuple(str(value) for value in final_snapshot.residual_vector),
            "leg_statuses": tuple(
                (
                    leg.leg_id,
                    None if leg.child_status is None else leg.child_status.state.value,
                    None if leg.child_status is None else str(leg.child_status.filled_quantity),
                    leg.phase.value,
                )
                for leg in final_snapshot.legs
            ),
            "events": tuple(event.event_type for event in events),
            "operations": tuple((mount, operation) for mount, operation, _ in router.calls),
            "risk_responses": tuple(
                (
                    operation,
                    result.get("ok") if isinstance(result, Mapping) else None,
                    result.get("code") if isinstance(result, Mapping) else type(result).__qualname__,
                    result.get("result", {}).get("phase")
                    if isinstance(result, Mapping)
                    and isinstance(result.get("result"), Mapping)
                    else None,
                )
                for _mount, operation, result in router.responses
            ),
            "recovery_binding_failed": tuple(
                key for key, matched in recovery_checks.items() if not matched
            ),
            "source_ready": group_execution._group_source_is_compensation_ready(final_snapshot),
            "deadline_elapsed": time.time_ns() > final_snapshot.request.deadline_ns,
            "preparation_errors": tuple(preparation_errors),
            "accepted_projection_differences": tuple(
                accepted_projection_differences
            ),
            "accepted_projection_facts": tuple(accepted_projection_facts),
            "source_metadata_facts": tuple(source_metadata_facts),
            "authorization_payload_facts": tuple(authorization_payload_facts),
            "owner_compensation_ticks": tuple(owner_compensation_ticks[-6:]),
            "owner_compensation_tick_count": len(owner_compensation_ticks),
            "pending_compensation_advances": tuple(pending_compensation_advances),
        }
        if final_snapshot.state is not GroupOrderState.CLOSED:
            print(
                "DIM09_RECOVERY_DIAGNOSTIC="
                + json.dumps(closure_diagnostic, separators=(",", ":"), sort_keys=True)
            )
        assert final_snapshot.state is GroupOrderState.CLOSED, closure_diagnostic
        event_kinds = tuple(event.event_type for event in events)
        for expected in (
            "GROUP_COMPENSATION_PLAN_RECORDED",
            "GROUP_COMPENSATION_SUBMIT_STARTED",
            "GROUP_COMPENSATION_STATUS_OBSERVED",
            "GROUP_COMPENSATION_RESOLVED",
            "GROUP_COMPENSATION_CLOSED",
        ):
            assert expected in event_kinds, event_kinds
        plan_event_index = next(
            index
            for index, event in enumerate(events)
            if event.event_type == "GROUP_COMPENSATION_PLAN_RECORDED"
        )
        assert all(
            event.event_type != "GROUP_RECOVERY_REQUIRED"
            for event in events[plan_event_index + 1 :]
        ), event_kinds
        plan_payload = events[plan_event_index].payload
        authorization_payload = plan_payload["authorization"]
        resolved_event = next(
            event for event in events if event.event_type == "GROUP_COMPENSATION_RESOLVED"
        )
        close_receipt = resolved_event.payload["close_receipt"]
        assert authorization_payload["intent_id"].startswith("gclose-")
        assert authorization_payload["risk_intent_id"].startswith("risk-order:")
        assert len(authorization_payload["risk_intent_id"]) == len("risk-order:") + 64
        assert authorization_payload["risk_intent_id"] != authorization_payload["intent_id"]
        assert (
            close_receipt["claim"]["intent_id"]
            == authorization_payload["risk_intent_id"]
        )
        observed_status = next(
            event.payload["status"]
            for event in events
            if event.event_type == "GROUP_COMPENSATION_STATUS_OBSERVED"
        )
        assert observed_status["intent_id"] == authorization_payload["intent_id"]

        # The historical business ID must not be accepted in place of the
        # Risk-owned claim ID, even if an attacker recomputes the outer receipt
        # digest. This exercises the replay projection against public
        # synthetic event data without changing trusted SQLite state.
        tampered_events = list(events)
        resolved_index = events.index(resolved_event)
        tampered_resolution = dict(resolved_event.payload)
        tampered_receipt = dict(close_receipt)
        tampered_claim = dict(tampered_receipt["claim"])
        tampered_claim["intent_id"] = authorization_payload["intent_id"]
        tampered_receipt["claim"] = tampered_claim
        tampered_receipt.pop("receipt_sha256", None)
        tampered_receipt["receipt_sha256"] = payload_sha256(tampered_receipt)
        tampered_resolution["close_receipt"] = tampered_receipt
        tampered_events[resolved_index] = replace(
            resolved_event, payload=tampered_resolution
        )
        with pytest.raises(ValueError, match="unverified group compensation close receipt"):
            group_execution._owner_compensation_actions(tuple(tampered_events))
        prepare_calls = tuple(
            item for item in router.calls if item[1] == "orders.prepare_group_compensation_close"
        )
        submit_calls = tuple(
            item for item in router.calls if item[1] == "orders.submit_group_compensation_close"
        )
        query_calls = tuple(
            item for item in router.calls if item[1] == "orders.query_group_compensation_close"
        )
        assert len(prepare_calls) == 1, prepare_calls
        assert len(submit_calls) == 1, submit_calls
        assert len(query_calls) >= 1, query_calls
        assert router.dropped_submit_replies == 1
        assert close_requests and len(close_requests) == 1, close_requests
        # Chain deficit is historical hedge context; it remains (0, 1, 1)
        # even after the actual filled-position exposure has been closed.
        assert final_snapshot.residual_vector == (
            Decimal("0"),
            Decimal("1"),
            Decimal("1"),
        )
        closed_event = next(
            event for event in events if event.event_type == "GROUP_COMPENSATION_CLOSED"
        )
        closure_proof = closed_event.payload
        ordered_leg_ids = tuple(leg.leg_id for leg in final_snapshot.request.legs)
        assert closure_proof["chain_deficit_vector_base_units"] == [
            [leg_id, format(final_snapshot.residual_vector[index], "f")]
            for index, leg_id in enumerate(ordered_leg_ids)
        ]
        assert closure_proof["signed_uncompensated_filled_exposure_vector_base_units"] == [
            [leg_id, "0"] for leg_id in ordered_leg_ids
        ]
        assert closure_proof["resolved_action_receipt_sha256s"] == [
            close_receipt["receipt_sha256"]
        ]
        assert closure_proof["active_dispatch_latches"] == 0
        assert closure_proof["pending_budget_obligation_amount"] == "0"
        assert final_snapshot.budget_reservation is not None
        assert final_snapshot.budget_reservation.state.value == "RELEASED"
        assert all(
            reservation.phase.value == "RELEASED"
            for reservation in final_snapshot.reservations
        )
        assert all(leg.child_status is None or leg.child_status.state.value != "UNKNOWN" for leg in final_snapshot.legs)

        # Three provider writes exist in total: two opening legs and one CLOSE.
        provider_writes, counters = cluster.provider.snapshot()
        assert len(provider_writes) == 3, (provider_writes, counters)
        assert set(counters) == {"fill_queries", "order_queries"}, counters
        assert close_requests[0][0] in provider_writes, provider_writes
        assert close_requests[0][3] == {
            "offset": "close",
            "post_only": False,
            "reduce_only": True,
            "time_in_force": "GTC",
        }
    finally:
        if runtime is not None:
            runtime.close()
            assert runtime.cleanup_failures == 0
            assert runtime.unregister_uncertainties == 0
            assert runtime.state == "closed"
        if host_loop is not None:
            host_loop.close()
        if risk_gate is not None:
            risk_gate.close()
        cluster.close()
