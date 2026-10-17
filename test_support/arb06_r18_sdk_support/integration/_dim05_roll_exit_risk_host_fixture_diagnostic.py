"""Reusable real Risk ServiceHost fixture for DIM05 group roll/exit tests.

The group and origin proof must already be persisted by the caller.  This
fixture only supplies the Risk owner Host and synthetic provider leaf; it does
not fabricate a Risk reply or seed group authority.
"""

from __future__ import annotations

import inspect
import os
import json
import queue
import re
import socket
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import SqliteOrderGroupStore, risk_account_scope_fingerprint
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskRegistry,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_execution_group_client import GroupOriginRunBindingStore
from bt_api_py.service_execution_group_roll_exit_source import (
    GroupRollExitAuthorizationSourceV1,
)
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import (
    PrincipalGrant,
    ResourceGrant,
    TrustedGroupWritePolicy,
)
from bt_api_py.service_risk_order_entry import RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal

from tests.integration import test_arb03_dim05_group_partial_fill_service as dim05
from tests.unit._service_group_provider_fixtures import _V2SyntheticBtApiFeed


_RISK_SERVICE_ID = "dim05-roll-exit-risk-service"
_RISK_INSTANCE = "dim05-roll-exit-risk-owner"
_RISK_PRINCIPAL = "dim05-roll-exit-risk-principal"
_ENTRY_SCOPE_REF = "dim05-roll-exit-entry-v1"
_QUERY_SCOPE_REF = "dim05-roll-exit-query-v1"
_SERVER_SECURITY_REF = "dim05-roll-exit-server-curve-v1"
_CLIENT_SECURITY_REF = "dim05-roll-exit-client-curve-v1"
_LISTENER_REF = "dim05-roll-exit-listener-v1"
_CLIENT_TRANSPORT_REF = "dim05-roll-exit-connect-v1"
_SUBMIT = "orders.submit_group_roll_exit"
_PREPARE = "orders.prepare_group_roll_exit"
_QUERY = "orders.query_group_roll_exit"


def _endpoint() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def _component(instance_id: str, ports: tuple[str, ...], role: str) -> dict[str, Any]:
    return {
        "config_schema_version": 2,
        "settings": {
            "instance_id": instance_id,
            "bind_ports": list(ports),
            "options": {"role": role, "account_policy_ref": dim05._risk_policy().policy_id},
        },
    }


def _risk_host_config(endpoint: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _RISK_SERVICE_ID,
        "scope_ref": "dim05-roll-exit-risk-root-v1",
        "transport_ref": _LISTENER_REF,
        "registration_policy_ref": "dim05-roll-exit-registration-v1",
        "service_requirements_ref": "dim05-roll-exit-requirements-v1",
        "components": {
            "risk": _component(
                _RISK_INSTANCE,
                ("service.order_entry", "service.order_query"),
                "account_owner",
            )
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


def _client_service_config(endpoint: str, account_ref: str) -> dict[str, Any]:
    options = {"request_timeout_ms": 2500, "high_water_mark": 32, "max_message_bytes": 1_000_000}
    bindings = [
        {
            "mount": "upstream.group_roll_exit_orders",
            "service_id": _RISK_SERVICE_ID,
            "instance_id": _RISK_INSTANCE,
            "scope_ref": _ENTRY_SCOPE_REF,
            "account_ref": account_ref,
            "portfolio_ref": dim05._PORTFOLIO,
            "transport_ref": _CLIENT_TRANSPORT_REF,
        },
        {
            "mount": "upstream.group_roll_exit_order_query",
            "service_id": _RISK_SERVICE_ID,
            "instance_id": _RISK_INSTANCE,
            "scope_ref": _QUERY_SCOPE_REF,
            "account_ref": account_ref,
            "portfolio_ref": dim05._PORTFOLIO,
            "transport_ref": _CLIENT_TRANSPORT_REF,
        },
    ]
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": "dim05-roll-exit-coordinator-runtime",
        "scope_ref": "dim05-roll-exit-coordinator-runtime-scope-v1",
        "transport_ref": "dim05-roll-exit-runtime-listener-v1",
        "registration_policy_ref": "dim05-roll-exit-runtime-registration-v1",
        "service_requirements_ref": "dim05-roll-exit-runtime-requirements-v1",
        "components": {
            "monitor": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": "dim05-roll-exit-unused-monitor",
                    "bind_ports": ["service.monitor_query"],
                    "options": {},
                },
            }
        },
        "clients": {
            "execution": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": bindings,
                    "options": {"request_timeout_ms": 2500, "heartbeat_interval_seconds": 5},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "dim05-roll-exit-runtime-listener-v1": {
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


class _FilledCloseFeed(_V2SyntheticBtApiFeed):
    """A fixed synthetic provider leaf; Risk still creates and submits the order."""

    def __init__(self, ledger_path: str, provider_account: str) -> None:
        super().__init__(ledger_path, ("open", "open", "filled"), provider_account)
        self.calls: list[tuple[str, Decimal, Decimal, str, str]] = []

    def make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        *,
        client_order_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        quantity = Decimal(str(volume))
        price_value = Decimal(str(price))
        if kwargs != {
            "offset": "close",
            "post_only": False,
            "reduce_only": True,
            "time_in_force": "GTC",
        }:
            raise AssertionError("Risk close must reach the synthetic provider as CLOSE/reduce-only/GTC")
        self.calls.append((symbol, quantity, price_value, order_type, client_order_id))
        return super().make_order(
            symbol,
            volume,
            price,
            order_type,
            client_order_id=client_order_id,
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class Dim05RiskHostCase:
    endpoint: str
    risk_identity: WireIdentity
    coordinator_identity: WireIdentity
    service_scope: WireScope
    owner_scope: WireScope
    account_scope: AccountScope
    account_ref: str
    provider_account_id: str
    venue: str
    environment: str
    coordinator_principal_ref: str
    coordinator_service_id: str
    coordinator_instance_id: str
    run_id: str
    entry_scope_ref: str
    query_scope_ref: str
    server_public_key: bytes
    server_secret_key: bytes
    client_public_key: bytes
    client_secret_key: bytes
    risk_service_config: Mapping[str, Any]
    risk_client_service_config: Mapping[str, Any]
    entry_client_binding: Mapping[str, Any]
    query_client_binding: Mapping[str, Any]
    account_paths: Mapping[str, str]
    coordinator_paths: Mapping[str, str]
    metadata_reference_ns: int
    group_id: str
    source_leg_id: str
    source_instrument: str
    source_strategy_ref: str
    peer_strategy_ref: str
    risk_scope_fingerprint: str
    source_filled_quantity: Decimal
    peer_quantity: Decimal
    _owner_call: Callable[[Callable[[], Any]], Any]
    _available_reader: Callable[[str], Decimal]
    _provider_reader: Callable[[], tuple[tuple[str, Decimal, Decimal, str, str], ...]]

    def available(self, strategy_ref: str) -> Decimal:
        if strategy_ref not in {self.source_strategy_ref, self.peer_strategy_ref}:
            raise KeyError("unknown strategy in DIM05 synthetic fixture")
        return self._owner_call(lambda: self._available_reader(strategy_ref))

    def provider_calls(self) -> tuple[tuple[str, Decimal, Decimal, str, str], ...]:
        return self._owner_call(self._provider_reader)


_HOST_START_ERROR_DESCRIPTOR = vars(ServiceHost).get("_start_error_code")
if type(_HOST_START_ERROR_DESCRIPTOR) is not staticmethod:
    raise RuntimeError("ServiceHost._start_error_code must remain a staticmethod")
_ORIGINAL_START_ERROR_CODE = _HOST_START_ERROR_DESCRIPTOR.__func__
_ORIGINAL_START_ERROR_SIGNATURE = inspect.signature(_ORIGINAL_START_ERROR_CODE)
if tuple(_ORIGINAL_START_ERROR_SIGNATURE.parameters) != ("exc",):
    raise RuntimeError("ServiceHost._start_error_code signature changed")
_SERVICE_HOST_SOURCE = Path(sys.modules[ServiceHost.__module__].__file__).resolve()
_FIXTURE_SOURCE_ROOT = Path(__file__).resolve().parent
_PUBLIC_SOURCE_PACKAGE_NAMES = (
    "bt_api_py",
    "bt_api_base",
    "bt_api_execution",
    "bt_api_gateway",
    "bt_api_monitor",
    "bt_api_risk",
    "bt_api_transport_zmq",
)
_SAFE_START_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,95}")


def _fixed_public_source_roots() -> tuple[tuple[str, Path], ...]:
    roots: list[tuple[str, Path]] = []
    for package_name in _PUBLIC_SOURCE_PACKAGE_NAMES:
        package = sys.modules.get(package_name)
        source = getattr(package, "__file__", None) if package is not None else None
        if type(source) is str and source:
            try:
                roots.append((package_name, Path(source).resolve().parent))
            except (OSError, RuntimeError):
                continue
    roots.append(("fixture", _FIXTURE_SOURCE_ROOT))
    return tuple(roots)


def _public_exception_projection(error: BaseException) -> dict[str, Any]:
    """Project only type and bounded frames under fixed SDK/fixture source roots."""

    error_type = type(error)
    module_name = getattr(error_type, "__module__", "")
    qualified_name = getattr(error_type, "__qualname__", error_type.__name__)
    type_name = f"{module_name}.{qualified_name}" if module_name else qualified_name
    frames: list[dict[str, Any]] = []
    frame_count = 0
    current = error.__traceback__
    roots = _fixed_public_source_roots()
    while current is not None:
        code = current.tb_frame.f_code
        try:
            filename = Path(code.co_filename).resolve()
        except (OSError, RuntimeError):
            filename = None
        if filename is not None:
            for label, source_root in roots:
                try:
                    relative = filename.relative_to(source_root)
                except ValueError:
                    continue
                frames.append(
                    {
                        "source": label,
                        "file": relative.as_posix(),
                        "line": int(current.tb_lineno),
                        "function": code.co_name,
                    }
                )
                frame_count += 1
                if len(frames) > 24:
                    del frames[0]
                break
        current = current.tb_next
    return {"exception_type": type_name, "frame_count": frame_count, "frames": frames}


def _diagnostic_service_host_type(observations: list[dict[str, Any]]) -> type[ServiceHost]:
    """Subclass Host only in this fixture and preserve its exact static API."""

    def start_error_code(exc: BaseException) -> str:
        code = _ORIGINAL_START_ERROR_CODE(exc)
        try:
            row = _public_exception_projection(exc)
            if type(code) is str and _SAFE_START_CODE.fullmatch(code):
                row["start_error_code"] = code
            observations.append(row)
        except BaseException:
            # Diagnostics must never change ServiceHost's original error code.
            pass
        return code

    if inspect.signature(start_error_code) != _ORIGINAL_START_ERROR_SIGNATURE:
        raise RuntimeError("diagnostic _start_error_code signature differs from ServiceHost")
    descriptor = staticmethod(start_error_code)
    if type(descriptor) is not staticmethod:
        raise RuntimeError("diagnostic _start_error_code descriptor changed")
    return type("_DiagnosticServiceHost", (ServiceHost,), {"_start_error_code": descriptor})


def _component_summary_projection(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    components = summary.get("components")
    if not isinstance(components, (tuple, list)):
        return rows
    for component in components:
        if not isinstance(component, Mapping):
            continue
        capabilities = component.get("capabilities", ())
        projected_capabilities: list[dict[str, Any]] = []
        if isinstance(capabilities, (tuple, list)):
            for capability in capabilities:
                if isinstance(capability, Mapping):
                    projected_capabilities.append(
                        {
                            "port": capability.get("port"),
                            "operations": list(capability.get("operations", ())),
                        }
                    )
        rows.append(
            {
                "component": component.get("component"),
                "instance_id": component.get("instance_id"),
                "declared_ports": list(component.get("declared_ports", ())),
                "capabilities": projected_capabilities,
                "internal_ports": list(component.get("internal_ports", ())),
            }
        )
    return rows


class _OwnerLoop:
    """Run Host, store access and fixture observations on one owner thread."""

    def __init__(
        self,
        host: ServiceHost,
        cleanup: list[Callable[[], None]],
        *,
        diagnostic_receipt_path: Path,
    ) -> None:
        self.host = host
        self.cleanup = cleanup
        self.diagnostic_receipt_path = diagnostic_receipt_path
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.commands: queue.Queue[tuple[Callable[[], Any], threading.Event, list[Any]]] = queue.Queue()
        self.error: BaseException | None = None
        self.cleanup_errors: list[BaseException] = []
        self.thread = threading.Thread(target=self._run, name="dim05-roll-exit-risk-fixture")
        self._cleanup_callbacks_run = False
        self._joined = False

    def _run_cleanup_callbacks(self) -> None:
        if self._cleanup_callbacks_run:
            return
        self._cleanup_callbacks_run = True
        for callback in reversed(self.cleanup):
            try:
                callback()
            except BaseException as error:
                self.cleanup_errors.append(error)

    def _run(self) -> None:
        try:
            self.host.start()
            self.ready.set()
            while not self.stop.is_set():
                try:
                    callback, done, result = self.commands.get_nowait()
                except queue.Empty:
                    self.host.serve_once(timeout_ms=15)
                    continue
                try:
                    result.append((True, callback()))
                except BaseException as error:
                    result.append((False, error))
                finally:
                    done.set()
        except BaseException as error:
            self.error = error
        finally:
            self.ready.set()
            if self.host.state != "closed":
                try:
                    self.host.close()
                except BaseException as error:
                    self.cleanup_errors.append(error)
            self._run_cleanup_callbacks()
            self.finished.set()

    def start(self) -> None:
        self.thread.start()
        if not self.ready.wait(10):
            raise TimeoutError("DIM05 Risk Host fixture startup timed out")
        if self.error is not None:
            add_note = getattr(self.error, "add_note", None)
            if callable(add_note):
                add_note(f"fixture diagnostic receipt: {self.diagnostic_receipt_path}")
            raise self.error
        if self.host.state != "started":
            raise RuntimeError("DIM05 Risk Host fixture did not reach started state")

    def call(self, callback: Callable[[], Any]) -> Any:
        if self.error is not None or self.stop.is_set():
            raise RuntimeError("DIM05 Risk Host fixture is not serving")
        done = threading.Event()
        result: list[Any] = []
        self.commands.put((callback, done, result))
        assert done.wait(5), "DIM05 Risk Host owner-thread observation timed out"
        succeeded, value = result[0]
        if not succeeded:
            raise value
        return value

    def close(self) -> dict[str, Any]:
        self.stop.set()
        thread_started = self.thread.ident is not None
        if thread_started:
            finished = self.finished.wait(10)
            self.thread.join(1)
            self._joined = not self.thread.is_alive()
        else:
            finished = True
            if self.host.state != "closed":
                try:
                    self.host.close()
                except BaseException as error:
                    self.cleanup_errors.append(error)
            self._run_cleanup_callbacks()
            self.finished.set()
            self._joined = True
        return {
            "thread_started": thread_started,
            "finished": bool(finished and self.finished.is_set()),
            "joined": self._joined,
            "thread_alive": self.thread.is_alive(),
            "host_state": self.host.state,
        }


@contextmanager
def dim05_roll_exit_risk_host(
    tmp_path: Path,
    *,
    account_paths: Mapping[str, Mapping[str, str]],
    coordinator_paths: Mapping[str, str],
    metadata_reference_ns: int,
    group_id: str,
    source_leg_id: str,
    peer_quantity: Decimal = Decimal("0.75"),
) -> Iterator[Dim05RiskHostCase]:
    """Start the real registered Risk Host for an already-persisted DIM05 group.

    The caller owns the group setup and its SQLite paths.  The only simulated
    edge is the final provider response, fixed to a full fill for a valid CLOSE.
    """

    assert type(metadata_reference_ns) is int and metadata_reference_ns > 0
    assert type(group_id) is str and group_id
    assert type(source_leg_id) is str and source_leg_id
    assert type(peer_quantity) is Decimal and peer_quantity.is_finite() and peer_quantity > 0
    assert "groups" in coordinator_paths and "run_bindings" in coordinator_paths

    group_store = SqliteOrderGroupStore(coordinator_paths["groups"])
    try:
        snapshot = group_store.get(dim05._PORTFOLIO, group_id)
    finally:
        group_store.close()
    assert snapshot is not None, "caller must persist the accepted group before starting Risk Host"
    request = snapshot.request
    source_progress = next((row for row in snapshot.legs if row.leg_id == source_leg_id), None)
    source_plan = next((row for row in request.legs if row.leg_id == source_leg_id), None)
    assert source_progress is not None and source_plan is not None
    assert source_plan.maximum_intent.position_effect.value == "OPEN"
    assert source_progress.child_status is not None
    source_quantity = source_progress.child_status.filled_quantity
    assert type(source_quantity) is Decimal and source_quantity > 0
    account_ref = source_plan.account_ref
    assert set(account_paths) == {account_ref}, "this fixture currently supports one owner account"
    instrument = source_plan.maximum_intent.instrument
    instruments = tuple(dict.fromkeys(leg.maximum_intent.instrument for leg in request.legs))
    assert all(leg.account_ref == account_ref for leg in request.legs)
    assert type(request.strategy_id) is str and request.strategy_id == dim05._STRATEGY

    provider_account = f"provider-{account_ref}"
    endpoint = _endpoint()
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    owner_scope = dim05._owner_scope(account_ref)
    peer_strategy = "strategy-dim05-peer"
    if peer_strategy == request.strategy_id:
        peer_strategy = "strategy-dim05-peer-other"
    peer_scope = replace(owner_scope, strategy_ref=peer_strategy)
    coordinator_identity = WireIdentity(
        WireRole.SERVICE, dim05._COORDINATOR_PRINCIPAL, dim05._COORDINATOR_SERVICE_ID
    )
    risk_identity = WireIdentity(WireRole.SERVICE, _RISK_PRINCIPAL, _RISK_SERVICE_ID)

    risk_gate_path = str(tmp_path / "dim05-roll-exit-risk-gate.sqlite3")
    risk_journal_path = str(tmp_path / "dim05-roll-exit-risk-journal.sqlite3")
    risk_store_path = str(tmp_path / "dim05-roll-exit-risk-registry.sqlite3")
    state: dict[str, Any] = {}
    cleanup: list[Callable[[], None]] = []
    provider_failure: list[dict[str, Any]] = []
    start_error_observations: list[dict[str, Any]] = []
    resource_cleanup_errors: list[dict[str, Any]] = []
    resource_cleanup_invocations = [0]
    old_mapper = None
    mapper_original_captured = False
    mapper_installed = False
    from bt_api_py._venue_mappers import MAPPERS

    def close_resources() -> None:
        nonlocal mapper_installed
        resource_cleanup_invocations[0] += 1
        for key in ("bt_api", "risk_gate", "origin_store", "group_store", "graph"):
            resource = state.get(key)
            if resource is None:
                continue
            close = getattr(resource, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except BaseException as error:
                resource_cleanup_errors.append(_public_exception_projection(error))
        if mapper_installed:
            try:
                if old_mapper is None:
                    MAPPERS.pop("FIXTURE___SPOT", None)
                else:
                    MAPPERS["FIXTURE___SPOT"] = old_mapper
            except BaseException as error:
                resource_cleanup_errors.append(_public_exception_projection(error))
            mapper_installed = False

    # Register before the builder starts so every successfully assigned resource
    # is covered if a later constructor or Host startup step fails.
    cleanup.append(close_resources)

    def resource_provider_inner(component, instance_id, options, _scope):
        nonlocal old_mapper, mapper_original_captured, mapper_installed
        assert (component, instance_id, options["role"]) == ("risk", _RISK_INSTANCE, "account_owner")
        if not mapper_original_captured:
            old_mapper = MAPPERS.get("FIXTURE___SPOT")
            mapper_original_captured = True
        # The accepted account graph builder also assigns this synthetic mapper;
        # mark before entering it so the early cleanup callback can restore the
        # exact pre-fixture binding even if a later builder step raises.
        mapper_installed = True
        graph = dim05._new_account_resource_graph(
            account_ref,
            account_paths[account_ref],
            ("open", "open", "filled"),
            metadata_reference_ns,
            instruments,
            (),
        )
        state["graph"] = graph
        risk_scope = graph.risk_scope
        risk_scope_fingerprint = risk_account_scope_fingerprint(risk_scope.key)
        local_group_store = SqliteOrderGroupStore(coordinator_paths["groups"])
        state["group_store"] = local_group_store
        origin_store = GroupOriginRunBindingStore(coordinator_paths["run_bindings"])
        state["origin_store"] = origin_store
        graph.authority.guard.bind_reconciliation_source(graph.bundle.proof_source)
        source = GroupRollExitAuthorizationSourceV1(
            graph.accepted_store,
            origin_store,
            local_group_store,
            graph.execution_store,
            graph.bundle.proof_source,
            graph.authority.guard,
            coordinator_principal_ref=dim05._COORDINATOR_PRINCIPAL,
        )
        assert source.ready is True
        feed = _FilledCloseFeed(account_paths[account_ref]["provider"], provider_account)
        state["feed"] = feed
        bt_api = BtApi(debug=False)
        state["bt_api"] = bt_api
        MAPPERS["FIXTURE___SPOT"] = MAPPERS["BINANCE___SPOT"]
        bt_api.exchange_feeds["FIXTURE___SPOT"] = feed
        journal = RiskOrderJournal(risk_journal_path, writer_id="dim05-roll-exit-risk")
        state["risk_journal"] = journal
        gate = DurableRiskGate(
            risk_gate_path,
            dim05._risk_policy(),
            execution_journal_authority=journal,
            gross_exposure_authority=journal,
        )
        state["risk_gate"] = gate
        proof_source = graph.bundle.proof_source
        registry = proof_source.targets[graph.owner_scope].risk_target.registry
        targets: dict[WireScope, RiskOrderTarget] = {}
        admissions: dict[WireScope, DurableRiskAdmissionAdapter] = {}
        contracts = {}
        strategy_scopes = ((request.strategy_id, owner_scope), (peer_strategy, peer_scope))
        for strategy_id, wire_scope in strategy_scopes:
            gate.set_strategy_allocation(
                risk_scope,
                strategy_id,
                f"dim05-roll-exit-allocation-{strategy_id}",
                max_notional=Decimal("100000"),
                notional_unit="USD",
            )
            targets[wire_scope] = RiskOrderTarget(
                account_scope=risk_scope,
                strategy_id=strategy_id,
                exchange_name="FIXTURE___SPOT",
                provider_account_id=provider_account,
                registry=registry,
            )
            mapper = InstrumentRiskAdmissionMapper(
                risk_scope,
                registry,
                clock_ns=time.time_ns,
                allocation_reader=lambda routed_scope: gate.get_strategy_allocation(
                    risk_scope, routed_scope.strategy_id
                ),
                wire_account_ref=account_ref,
            )
            admissions[wire_scope] = DurableRiskAdmissionAdapter(gate, mapper)
            contracts[wire_scope] = replace(
                proof_source.targets[graph.owner_scope].provider_contract,
                supported_time_in_force=("GTC", "IOC"),
                supported_position_effects=("OPEN", "CLOSE"),
            )
        gate.set_closable_position(
            risk_scope,
            instrument,
            request.strategy_id,
            "dim05-roll-exit-source-position-v1",
            closable_quantity=source_quantity,
            quantity_unit="base",
        )
        gate.set_closable_position(
            risk_scope,
            instrument,
            peer_strategy,
            "dim05-roll-exit-peer-position-v1",
            closable_quantity=peer_quantity,
            quantity_unit="base",
        )
        policy = TrustedGroupWritePolicy(
            _RISK_INSTANCE,
            dim05._COORDINATOR_PRINCIPAL,
            coordinator_identity,
            _ENTRY_SCOPE_REF,
            owner_scope,
            dim05._RUN,
            operation=_SUBMIT,
        )
        state.update(
            {
                "graph": graph,
                "group_store": local_group_store,
                "origin_store": origin_store,
                "source": source,
                "risk_journal": journal,
                "risk_gate": gate,
                "feed": feed,
                "bt_api": bt_api,
            }
        )

        return ComponentResources.borrowed(
            {
                "bt_api": bt_api,
                "targets": targets,
                "admission": admissions,
                "journal": journal,
                "gate": gate,
                "provider_contracts": contracts,
                "group_roll_exit_authorization_source": source,
            },
            ready=lambda: source.ready is True,
        )

    def resource_provider(component, instance_id, options, scope):
        try:
            return resource_provider_inner(component, instance_id, options, scope)
        except BaseException as error:
            provider_failure.append(_public_exception_projection(error))
            raise

    entry_grant = ResourceGrant(
        _RISK_INSTANCE,
        "service.order_entry",
        _ENTRY_SCOPE_REF,
        owner_scope,
        frozenset({_SUBMIT}),
    )
    query_grant = ResourceGrant(
        _RISK_INSTANCE,
        "service.order_query",
        _QUERY_SCOPE_REF,
        owner_scope,
        frozenset({"orders.prepare_group_roll_exit", "orders.query_group_roll_exit"}),
    )
    config = parse_service_config(_risk_host_config(endpoint))
    diagnostic_host_type = _diagnostic_service_host_type(start_error_observations)
    host = diagnostic_host_type(
        config,
        TrustedServiceAuthority(
            identity=risk_identity,
            scope=WireScope(dim05._DOMAIN),
            scope_ref=config.scope_ref,
            scope_version="dim05-roll-exit-service-scope-v1",
            security_ref=_SERVER_SECURITY_REF,
            security_version="dim05-roll-exit-curve-v1",
            registration_policy_ref=config.registration_policy_ref,
            registration_policy_version="dim05-roll-exit-registration-v1",
            service_requirements_ref=config.service_requirements_ref,
            service_requirements_version="dim05-roll-exit-requirements-v1",
            curve_public_key=server_public,
            curve_secret_key=server_secret,
            principals={client_public: dim05._COORDINATOR_PRINCIPAL},
            grants={
                dim05._COORDINATOR_PRINCIPAL: (
                    PrincipalGrant(coordinator_identity, (entry_grant, query_grant)),
                )
            },
            group_write_policies=(
                TrustedGroupWritePolicy(
                    _RISK_INSTANCE,
                    dim05._COORDINATOR_PRINCIPAL,
                    coordinator_identity,
                    _ENTRY_SCOPE_REF,
                    owner_scope,
                    dim05._RUN,
                    operation=_SUBMIT,
                ),
            ),
        ),
        risk_store_path,
        resource_provider,
    )
    receipt_text = os.environ.get("ARB06_STARTUP_DIAGNOSTIC_RECEIPT")
    diagnostic_receipt_path = (
        Path(receipt_text).resolve()
        if type(receipt_text) is str and receipt_text.strip()
        else tmp_path / "dim05-risk-host-start-diagnostic.json"
    )
    if diagnostic_receipt_path.exists():
        raise AssertionError("fresh fixture diagnostic receipt path already exists")
    loop = _OwnerLoop(host, cleanup, diagnostic_receipt_path=diagnostic_receipt_path)
    primary_error: BaseException | None = None
    try:
        client_config = _client_service_config(endpoint, account_ref)
        binding_rows = client_config["clients"]["execution"]["settings"]["bindings"]
        entry_binding = next(row for row in binding_rows if row["mount"] == "upstream.group_roll_exit_orders")
        query_binding = next(row for row in binding_rows if row["mount"] == "upstream.group_roll_exit_order_query")
        loop.start()
        if provider_failure:
            raise AssertionError(("resource_provider", provider_failure[-1]))
        risk_scope = state["graph"].risk_scope
        risk_scope_fingerprint = risk_account_scope_fingerprint(risk_scope.key)
        case = Dim05RiskHostCase(
            endpoint=endpoint,
            risk_identity=risk_identity,
            coordinator_identity=coordinator_identity,
            service_scope=WireScope(dim05._DOMAIN),
            owner_scope=owner_scope,
            account_scope=risk_scope,
            account_ref=account_ref,
            provider_account_id=provider_account,
            venue="fixture",
            environment="simulation",
            coordinator_principal_ref=dim05._COORDINATOR_PRINCIPAL,
            coordinator_service_id=dim05._COORDINATOR_SERVICE_ID,
            coordinator_instance_id=dim05._COORDINATOR_INSTANCE_ID,
            run_id=dim05._RUN,
            entry_scope_ref=_ENTRY_SCOPE_REF,
            query_scope_ref=_QUERY_SCOPE_REF,
            server_public_key=server_public,
            server_secret_key=server_secret,
            client_public_key=client_public,
            client_secret_key=client_secret,
            risk_service_config=_risk_host_config(endpoint),
            risk_client_service_config=client_config,
            entry_client_binding=dict(entry_binding),
            query_client_binding=dict(query_binding),
            account_paths=dict(account_paths[account_ref]),
            coordinator_paths=dict(coordinator_paths),
            metadata_reference_ns=metadata_reference_ns,
            group_id=group_id,
            source_leg_id=source_leg_id,
            source_instrument=instrument,
            source_strategy_ref=request.strategy_id,
            peer_strategy_ref=peer_strategy,
            risk_scope_fingerprint=risk_scope_fingerprint,
            source_filled_quantity=source_quantity,
            peer_quantity=peer_quantity,
            _owner_call=loop.call,
            _available_reader=lambda strategy_ref: state["risk_gate"].get_closable_position(
                risk_scope,
                instrument,
                strategy_ref,
                position_side="NET",
                position_bucket="ALL",
            ).available_quantity,
            _provider_reader=lambda: tuple(state["feed"].calls),
        )
        yield case
    except BaseException as error:
        primary_error = error
        raise
    finally:
        loop_status = loop.close()
        summary = host.summary
        host_state = host.state
        start_failure = summary.get("start_failure")
        if type(start_failure) is not str or _SAFE_START_CODE.fullmatch(start_failure) is None:
            start_failure = None
        summary_cleanup = summary.get("cleanup_failures", ())
        safe_summary_cleanup = [
            item for item in summary_cleanup
            if type(item) is str and _SAFE_START_CODE.fullmatch(item) is not None
        ]
        receipt = {
            "schema": "arb06-risk-host-fixture-start-cleanup-v1",
            "host": {
                "state": host_state,
                "start_failure": start_failure,
                "components": _component_summary_projection(summary),
                "cleanup_failures": safe_summary_cleanup,
            },
            "owner_loop": loop_status,
            "owner_error": (
                _public_exception_projection(loop.error) if loop.error is not None else None
            ),
            "start_error_observations": list(start_error_observations),
            "resource_provider_failures": list(provider_failure),
            "resource_cleanup_invocations": resource_cleanup_invocations[0],
            "resource_cleanup_errors": list(resource_cleanup_errors),
            "owner_cleanup_errors": [
                _public_exception_projection(error) for error in loop.cleanup_errors
            ],
        }
        receipt["cleanup_verified"] = bool(
            loop_status["finished"]
            and loop_status["joined"]
            and not loop_status["thread_alive"]
            and host_state == "closed"
            and resource_cleanup_invocations[0] == 1
            and not resource_cleanup_errors
            and not loop.cleanup_errors
            and not safe_summary_cleanup
        )
        receipt_error: BaseException | None = None
        try:
            diagnostic_receipt_path.parent.mkdir(parents=True, exist_ok=True)
            diagnostic_receipt_path.write_text(
                json.dumps(receipt, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
        except BaseException as error:
            receipt_error = error
        if primary_error is not None:
            add_note = getattr(primary_error, "add_note", None)
            if callable(add_note):
                add_note(f"fixture diagnostic receipt: {diagnostic_receipt_path}")
                if receipt_error is not None:
                    add_note(f"fixture receipt write error type: {type(receipt_error).__name__}")
                elif not receipt["cleanup_verified"]:
                    add_note("fixture cleanup verification failed; see the finite receipt")
        elif receipt_error is not None:
            raise AssertionError(
                ("fixture diagnostic receipt write failed", type(receipt_error).__name__)
            ) from receipt_error
        elif not receipt["cleanup_verified"]:
            raise AssertionError(("fixture cleanup verification failed", receipt))

