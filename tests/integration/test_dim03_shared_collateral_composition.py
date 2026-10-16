"""Trusted shared-collateral composition through a real local ServiceHost.

The provider leaves are deterministic in-memory feeds. Risk and order-journal
state use fresh SQLite files, and the normal authenticated CURVE service path
is exercised without exchange credentials or provider network access.
"""

from __future__ import annotations

import hashlib
import socket
import sqlite3
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import zmq
from bt_api_base.account_protocol import AccountResourceKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_risk import (
    AccountScope,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    IntentAction,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskDeniedError,
    RiskIntent,
    RiskPolicy,
)

from bt_api_py.bt_api import BtApi
from bt_api_py.service_components import (
    ComponentResources,
    TrustedSharedCollateralDomain,
)
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_execution import ServiceExecutionClient, ServiceExecutionError
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_risk_order_entry import (
    RiskBtApiProviderContract,
    RiskOrderEntryService,
    RiskOrderTarget,
)
from bt_api_py.service_risk_order_journal import (
    RiskOrderJournal,
    canonical_json,
    sha256_text,
)
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)


_DOMAIN = "paper:dim03-shared-collateral"
_SERVICE_ID = "dim03-risk-service"
_SERVICE_PRINCIPAL = "dim03-risk-service-principal"
_SERVICE_INSTANCE = "dim03-risk-owner"
_SERVICE_SCOPE_REF = "dim03-service-root-v1"
_SERVER_SECURITY_REF = "dim03-server-curve-v1"
_CLIENT_SECURITY_REF = "dim03-client-curve-v1"
_LISTENER_TRANSPORT_REF = "dim03-listener-v1"
_CONNECT_TRANSPORT_REF = "dim03-connect-v1"
_PRINCIPAL = "dim03-strategy-principal"
_STRATEGY = "dim03-strategy"
_SCOPE_REF_A = "dim03-scope-account-a-v1"
_SCOPE_REF_B = "dim03-scope-account-b-v1"
_INSTRUMENT = "BTC-USD"
_POLICY_ID = "dim03-shared-policy-v1"
_DOMAIN_ID = "dim03-unified-margin-v1"
_DOMAIN_REVISION = "dim03-unified-margin-r1"
_REQUEST_TIMEOUT_MS = 2_000
_MAX_MESSAGE_BYTES = 65_536

_WIRE_SCOPE_A = WireScope(
    _DOMAIN,
    account_ref="account-a",
    strategy_ref=_STRATEGY,
    portfolio_ref="portfolio-a",
)
_WIRE_SCOPE_B = WireScope(
    _DOMAIN,
    account_ref="account-b",
    strategy_ref=_STRATEGY,
    portfolio_ref="portfolio-b",
)
_WIRE_SCOPES = {
    _SCOPE_REF_A: _WIRE_SCOPE_A,
    _SCOPE_REF_B: _WIRE_SCOPE_B,
}
_ACCOUNT_SCOPE_A = AccountScope("fixture", "provider-account-a", "simulation")
_ACCOUNT_SCOPE_B = AccountScope("fixture", "provider-account-b", "simulation-perpetual")
_WIRE_SCOPE_C = WireScope(
    _DOMAIN,
    account_ref="account-c",
    strategy_ref=_STRATEGY,
    portfolio_ref="portfolio-c",
)
_ACCOUNT_SCOPE_C = AccountScope("fixture", "provider-account-c", "simulation-futures")


class _SyntheticFeed:
    """Normal BtApi provider edge with no external I/O."""

    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        self.writes: list[str] = []
        self.orders: dict[str, dict[str, Any]] = {}

    def make_order(
        self,
        symbol: str,
        volume: float,
        _price: float,
        order_type: str,
        *,
        client_order_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        row = {
            "account_id": self.account_id,
            "client_order_id": client_order_id,
            "filled": 0,
            "order_id": "provider-" + client_order_id,
            "quantity": volume,
            "side": order_type.split("-", 1)[0],
            "status": "open",
            "symbol": symbol,
        }
        self.writes.append(client_order_id)
        self.orders[client_order_id] = row
        return {
            "account_id": self.account_id,
            "client_order_id": client_order_id,
            "complete": True,
            "schema_version": "dim03-order-v1",
            "watermark": len(self.writes),
            "data": [dict(row)],
        }

    def query_order(
        self,
        _symbol: str,
        order_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        row = self.orders.get(order_id)
        return {
            "account_id": self.account_id,
            "client_order_id": order_id,
            "complete": True,
            "schema_version": "dim03-order-v1",
            "watermark": len(self.orders),
            "data": [] if row is None else [dict(row)],
        }

    def get_deals(
        self,
        _symbol: str | None,
        *,
        client_order_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "client_order_id": client_order_id,
            "complete": True,
            "schema_version": "dim03-fills-v1",
            "watermark": "fills-v1",
            "data": [],
        }


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
            "scope_ref": _SERVICE_SCOPE_REF,
            "transport_ref": _LISTENER_TRANSPORT_REF,
            "registration_policy_ref": "dim03-registration-v1",
            "service_requirements_ref": "dim03-requirements-v1",
            "components": {
                "risk": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": _SERVICE_INSTANCE,
                        "bind_ports": ["service.order_entry"],
                        "options": {
                            "role": "account_owner",
                            "account_policy_ref": _POLICY_ID,
                        },
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    _LISTENER_TRANSPORT_REF: {
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
    )


def _client_config(endpoint: str):
    bindings = []
    for scope_ref, wire_scope in _WIRE_SCOPES.items():
        bindings.append(
            {
                "mount": "broker.orders",
                "service_id": _SERVICE_ID,
                "instance_id": _SERVICE_INSTANCE,
                "scope_ref": scope_ref,
                "transport_ref": _CONNECT_TRANSPORT_REF,
                "account_ref": wire_scope.account_ref,
            }
        )
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": _STRATEGY},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": bindings,
                        "options": {"request_timeout_ms": _REQUEST_TIMEOUT_MS},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    _CONNECT_TRANSPORT_REF: {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": endpoint},
                        "security_profile_ref": _CLIENT_SECURITY_REF,
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


def _metadata() -> InstrumentRiskMetadata:
    now_ns = time.time_ns()
    return InstrumentRiskMetadata(
        instrument=_INSTRUMENT,
        metadata_version="dim03-instrument-v1",
        as_of_ns=now_ns - 1_000_000,
        expires_at_ns=now_ns + 3_600_000_000_000,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("1000"),
        quantity_unit="BTC",
        valuation_unit="USD",
    )


def _provider_contract(exchange_name: str) -> RiskBtApiProviderContract:
    return RiskBtApiProviderContract(
        exchange_name=exchange_name,
        profile_id="dim03-provider-profile-v1",
        order_schema="dim03-order-v1",
        fill_schema="dim03-fills-v1",
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
            ("rejected", "rejected"),
        ),
        supported_order_types=("LIMIT",),
        supported_time_in_force=("GTC",),
        supported_position_effects=("OPEN",),
    )


@dataclass
class _Stack:
    host: ServiceHost
    gate: DurableRiskGate
    journal: RiskOrderJournal
    auxiliary_gates: tuple[DurableRiskGate, ...]
    bt_api: BtApi
    feeds: Mapping[WireScope, _SyntheticFeed]
    risk_paths: tuple[Path, ...]
    registry_path: Path

    def close_resources(self) -> None:
        self.bt_api.close()
        self.gate.close()
        for gate in self.auxiliary_gates:
            gate.close()


def _stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    shared_domain: bool,
    incomplete_account_b: bool = False,
    split_account_b_admission: bool = False,
    missing_owner: bool = False,
    unlisted_same_pool_target: bool = False,
    undeclared_shared_pool: bool = False,
    isolated_resource_maps: bool = False,
) -> tuple[_Stack, dict[str, bytes]]:
    endpoint = _endpoint()
    config = _service_config(endpoint)
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    keys = {
        "server_public": server_public,
        "server_secret": server_secret,
        "client_public": client_public,
        "client_secret": client_secret,
    }
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    strategy_identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY)
    resource_grants = tuple(
        ResourceGrant(
            _SERVICE_INSTANCE,
            "service.order_entry",
            scope_ref,
            wire_scope,
            frozenset({"orders.submit"}),
        )
        for scope_ref, wire_scope in _WIRE_SCOPES.items()
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=WireScope(_DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="dim03-scope-v1",
        security_ref=_SERVER_SECURITY_REF,
        security_version="dim03-curve-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="dim03-registration-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="dim03-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={client_public: _PRINCIPAL},
        grants={_PRINCIPAL: (PrincipalGrant(strategy_identity, resource_grants),)},
    )

    journal = RiskOrderJournal(
        tmp_path / "risk-orders.sqlite3", writer_id="dim03-risk-order-owner"
    )
    policy = RiskPolicy(
        _POLICY_ID,
        Decimal("30"),
        max_increase_count=10,
        notional_unit="USD",
    )
    gate = DurableRiskGate(
        tmp_path / "risk-ledger.sqlite3",
        policy,
        execution_journal_authority=journal,
    )
    risk_paths = (tmp_path / "risk-ledger.sqlite3",)
    other_gate = None
    if split_account_b_admission:
        other_gate_path = tmp_path / "risk-ledger-other.sqlite3"
        other_gate = DurableRiskGate(
            other_gate_path,
            policy,
            execution_journal_authority=journal,
        )
        risk_paths += (other_gate_path,)

    registry = InstrumentRiskRegistry((_metadata(),))
    resource_a = AccountResourceKey("spot", "unified-usd")
    resource_b = AccountResourceKey("perpetual", "unified-usd")
    if isolated_resource_maps:
        resource_a = AccountResourceKey("spot", "isolated-account-a")
        resource_b = AccountResourceKey("perpetual", "isolated-account-b")
        gate.set_margin_pool_limit(
            _ACCOUNT_SCOPE_A,
            resource_a.product_type,
            resource_a.margin_pool,
            "dim03-isolated-limit-a-v1",
            max_notional=Decimal("30"),
        )
        gate.set_margin_pool_limit(
            _ACCOUNT_SCOPE_B,
            resource_b.product_type,
            resource_b.margin_pool,
            "dim03-isolated-limit-b-v1",
            max_notional=Decimal("30"),
        )
    resources_enabled = shared_domain or undeclared_shared_pool or isolated_resource_maps
    resources_by_scope: dict[WireScope, dict[str, AccountResourceKey]] = {
        _WIRE_SCOPE_A: {_INSTRUMENT: resource_a} if resources_enabled else {},
        _WIRE_SCOPE_B: (
            {}
            if incomplete_account_b
            else ({_INSTRUMENT: resource_b} if resources_enabled else {})
        ),
    }
    if unlisted_same_pool_target:
        resources_by_scope[_WIRE_SCOPE_C] = {
            _INSTRUMENT: AccountResourceKey("futures", "unified-usd")
        }
    feeds = {
        _WIRE_SCOPE_A: _SyntheticFeed(_ACCOUNT_SCOPE_A.account_id),
        _WIRE_SCOPE_B: _SyntheticFeed(_ACCOUNT_SCOPE_B.account_id),
    }
    if unlisted_same_pool_target:
        feeds[_WIRE_SCOPE_C] = _SyntheticFeed(_ACCOUNT_SCOPE_C.account_id)
    monkeypatch.setattr("bt_api_py.bt_api._ensure_plugins_loaded", lambda: None)
    bt_api = BtApi(debug=False)
    targets: dict[WireScope, RiskOrderTarget] = {}
    admissions: dict[WireScope, DurableRiskAdmissionAdapter] = {}
    contracts: dict[WireScope, RiskBtApiProviderContract] = {}
    account_values = (
        (
            _WIRE_SCOPE_A,
            _ACCOUNT_SCOPE_A,
            "FIXTURE___SPOT",
        ),
        (
            _WIRE_SCOPE_B,
            _ACCOUNT_SCOPE_B,
            "FIXTURE___PERPETUAL",
        ),
    )
    account_values_with_unlisted = list(account_values)
    if unlisted_same_pool_target:
        account_values_with_unlisted.append(
            (_WIRE_SCOPE_C, _ACCOUNT_SCOPE_C, "FIXTURE___FUTURES")
        )
    for wire_scope, account_scope, exchange_name in account_values_with_unlisted:
        feed = feeds[wire_scope]
        bt_api.exchange_feeds[exchange_name] = feed
        target = RiskOrderTarget(
            account_scope=account_scope,
            strategy_id=_STRATEGY,
            exchange_name=exchange_name,
            provider_account_id=account_scope.account_id,
            registry=registry,
            resource_by_instrument=resources_by_scope[wire_scope],
        )
        mapper = InstrumentRiskAdmissionMapper(
            account_scope,
            registry,
            clock_ns=time.time_ns,
            resource_by_instrument=resources_by_scope[wire_scope],
        )
        admission_gate = other_gate if wire_scope is _WIRE_SCOPE_B and other_gate else gate
        targets[wire_scope] = target
        admissions[wire_scope] = DurableRiskAdmissionAdapter(admission_gate, mapper)
        contracts[wire_scope] = _provider_contract(exchange_name)

    declarations: tuple[TrustedSharedCollateralDomain, ...] = ()
    if shared_domain:
        declarations = (
            TrustedSharedCollateralDomain(
                domain_id=_DOMAIN_ID,
                revision=_DOMAIN_REVISION,
                member_scopes=(_ACCOUNT_SCOPE_A, _ACCOUNT_SCOPE_B),
                member_targets=(
                    (_WIRE_SCOPE_A, targets[_WIRE_SCOPE_A]),
                    (_WIRE_SCOPE_B, targets[_WIRE_SCOPE_B]),
                ),
                max_notional=Decimal("30"),
                owner_instance_id=None if missing_owner else _SERVICE_INSTANCE,
                owner_gate=gate,
            ),
        )

    def resource_provider(component, _instance_id, _options, _scope):
        if component != "risk":
            raise AssertionError("unexpected component selected by DIM03 service")
        return ComponentResources.borrowed(
            {
                "bt_api": bt_api,
                "targets": targets,
                "admission": admissions,
                "journal": journal,
                "gate": gate,
                "provider_contracts": contracts,
            },
            ready=lambda: True,
            shared_collateral_domains=declarations,
        )

    host = ServiceHost(
        config,
        authority,
        tmp_path / "service-registry.sqlite3",
        resource_provider,
    )
    return (
        _Stack(
            host=host,
            gate=gate,
            journal=journal,
            auxiliary_gates=() if other_gate is None else (other_gate,),
            bt_api=bt_api,
            feeds=feeds,
            risk_paths=risk_paths,
            registry_path=tmp_path / "service-registry.sqlite3",
        ),
        keys,
    )


class _ServingHost:
    def __init__(self, host: ServiceHost, *, trace_hook=None) -> None:
        self.host = host
        self._trace_hook = trace_hook
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._finished = threading.Event()
        self._failure: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="dim03-service-host", daemon=True)

    def _run(self) -> None:
        original_trace = sys.gettrace()
        if self._trace_hook is not None:
            sys.settrace(self._trace_hook)
        try:
            self.host.start()
            self._ready.set()
            while not self._stop.is_set():
                self.host.serve_once(timeout_ms=20)
            self.host.close()
        except Exception as error:
            self._failure = error
            self._ready.set()
        finally:
            if self._trace_hook is not None:
                sys.settrace(original_trace)
            self._finished.set()

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(timeout=10), "ServiceHost startup did not finish"
        if self._failure is not None:
            raise AssertionError("ServiceHost thread failed") from self._failure
        assert self.host.state == "started"
        assert self.host.endpoint is not None

    def close(self) -> None:
        self._stop.set()
        assert self._finished.wait(timeout=10), "ServiceHost thread did not close"
        self._thread.join(timeout=1)
        assert not self._thread.is_alive()
        if self._failure is not None:
            raise AssertionError("ServiceHost thread failed") from self._failure


def _trace_projection(value: object | None, fields: tuple[str, ...]) -> object:
    if value is None or type(value) in (str, int, bool, float):
        return value
    if hasattr(value, "key"):
        return {"type": type(value).__qualname__, "key": value.key}
    if hasattr(value, "value") and type(value.value) is str:
        return value.value
    return {
        "type": type(value).__qualname__,
        **{name: getattr(value, name) for name in fields if hasattr(value, name)},
    }


def _trace_reconciliation_failure(gate: DurableRiskGate, events: list[dict[str, object]]):
    """Capture the exact synthetic reconciliation exception and gate outcome."""

    targets = {
        RiskOrderEntryService._reconcile_claimed_order.__code__: "reconcile",
        DurableRiskGate.resolve_dispatch_freeze.__code__: "risk_resolution",
    }
    fields = (
        "scope", "permit_id", "intent_id", "intent_hash", "cause_id", "claim_digest",
        "evidence_class", "dispatch_attempt_count", "journal_revision",
        "journal_record_sha256", "reconciliation_evidence_sha256", "writer_fence_sha256",
        "provider_order_id", "accepted_request_sha256", "exposure_reservation_id",
        "exposure_reservation_sha256", "filled_quantity", "trade_count", "state",
        "latest_revision", "latest_record_sha256", "event_type", "revision", "record_sha256",
    )

    def trace(frame, event, argument):
        target = targets.get(frame.f_code)
        if target is None:
            return None
        if target == "risk_resolution" and event == "line":
            return trace
        local = frame.f_locals
        row: dict[str, object] = {"target": target, "event": event, "line": frame.f_lineno}
        row["gate_identity_match"] = (
            getattr(local.get("self"), "_gate", None) is gate
            if target == "reconcile"
            else local.get("self") is gate
        )
        if target == "reconcile" and "allow_resolution" in local:
            row["allow_resolution_is_true"] = local["allow_resolution"] is True
            row["allow_resolution_type"] = type(local["allow_resolution"]).__qualname__
        if event != "line":
            for name in (
                "allow_resolution", "command", "claim", "proof_command", "proof",
                "exposure_id", "exposure_sha", "reconciliation_sha", "order_event",
                "fill_event", "current",
            ):
                if name not in local:
                    continue
                row[name] = _trace_projection(local[name], fields)
        elif "allow_resolution" in local:
            row["allow_resolution"] = local["allow_resolution"]
            row["allow_resolution_is_true"] = local["allow_resolution"] is True
            row["allow_resolution_type"] = type(local["allow_resolution"]).__qualname__
        if event == "exception":
            error_type, error, error_tb = argument
            row.update(
                exception_type=f"{error_type.__module__}.{error_type.__qualname__}",
                exception_message=str(error),
                traceback="".join(traceback.format_exception(error_type, error, error_tb)),
            )
        elif event == "return":
            row["return"] = _trace_projection(argument, fields)
        events.append(row)
        return trace

    return trace


def _client_runtime(endpoint: str, keys: dict[str, bytes]) -> ServiceClientRuntime:
    config = _client_config(endpoint)
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, _STRATEGY)
    service_identity = WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, _SERVICE_ID)
    runtime = ServiceClientRuntime(
        config,
        identity=identity,
        scope_resolver=lambda scope_ref: _WIRE_SCOPES[scope_ref],
        peer_resolver=lambda _service_id: service_identity,
        transport_factory=ZmqTransportFactory(
            {
                _CLIENT_SECURITY_REF: ClientCurveKeys(
                    keys["server_public"],
                    keys["client_public"],
                    keys["client_secret"],
                )
            }
        ),
    )
    return runtime


def _execution_clients(runtime: ServiceClientRuntime):
    return (
        ServiceExecutionClient(runtime, "broker.orders", scope_ref=_SCOPE_REF_A),
        ServiceExecutionClient(runtime, "broker.orders", scope_ref=_SCOPE_REF_B),
    )


def _submit(client: ServiceExecutionClient, intent_id: str):
    return client.submit(
        intent_id=intent_id,
        signal_id="signal-" + intent_id,
        instrument=_INSTRUMENT,
        side="BUY",
        position_effect="OPEN",
        order_type="LIMIT",
        quantity="1",
        price="20",
        metadata_version="dim03-instrument-v1",
    )


def _assert_persisted_provider_binding(
    stack: _Stack,
    *,
    account_scope: AccountScope,
    feed: _SyntheticFeed,
    intent_id: str,
    exchange_name: str,
) -> None:
    """Bind the caller intent, stored request, provider write, and journal proof."""

    command = stack.journal.get_command(account_scope, _PRINCIPAL, intent_id)
    assert command is not None
    assert command.scope == account_scope
    assert command.principal_ref == _PRINCIPAL
    assert command.intent_id == intent_id
    assert command.canonical_payload["intent_id"] == intent_id
    assert command.exchange_name == exchange_name
    assert command.provider_account_id == account_scope.account_id
    assert command.payload_sha256 == sha256_text(
        canonical_json(dict(command.canonical_payload))
    )

    client_order_id = command.client_order_id
    assert client_order_id.startswith("risk-")
    assert client_order_id != intent_id
    assert command.order_request["client_order_id"] == client_order_id
    assert command.order_request["idempotency_key"] == client_order_id
    assert command.order_request["account_id"] == account_scope.account_id
    assert command.order_request["symbol"] == _INSTRUMENT
    assert command.order_request_sha256 == sha256_text(
        canonical_json(
            {
                "exchange_name": exchange_name,
                "operation": "BtApi.make_order(normalized=True)",
                "request": dict(command.order_request),
            }
        )
    )
    assert feed.writes == [client_order_id]

    provider_order = feed.orders[client_order_id]
    assert provider_order["account_id"] == account_scope.account_id
    assert provider_order["client_order_id"] == client_order_id
    provider_order_id = provider_order["order_id"]
    assert provider_order_id == "provider-" + client_order_id

    ack_event = stack.journal.get_event(
        account_scope, _PRINCIPAL, intent_id, command.latest_revision
    )
    assert ack_event is not None
    assert ack_event.scope == account_scope
    assert ack_event.principal_ref == _PRINCIPAL
    assert ack_event.intent_id == intent_id
    assert command.state == ack_event.event_type == "ACKED_TRACKED"
    assert ack_event.revision == command.latest_revision
    assert ack_event.record_sha256 == command.latest_record_sha256
    assert dict(command.latest_event) == dict(ack_event.payload)
    assert ack_event.payload["public_state"] == "ACKED_TRACKED"
    assert ack_event.payload["risk_intent_id"] == command.risk_intent_id
    assert ack_event.payload["provider_order_id"] == provider_order_id
    assert ack_event.payload["dispatch_attempt_count"] == 1
    assert ack_event.payload["filled_quantity"] == 0
    assert ack_event.payload["trade_count"] == 0

    # RiskOrderJournal exposes validated point reads, not an event-list method.
    command_events = []
    for revision in range(1, ack_event.revision + 1):
        event = stack.journal.get_event(account_scope, _PRINCIPAL, intent_id, revision)
        if event is not None:
            command_events.append(event)
    proof_events = [
        event for event in command_events if event.event_type == "PROOF_READY"
    ]
    assert len(proof_events) == 1
    proof_event = proof_events[0]
    assert proof_event.scope == account_scope
    assert proof_event.principal_ref == _PRINCIPAL
    assert proof_event.intent_id == intent_id
    assert proof_event.revision < ack_event.revision
    assert proof_event.event_type == "PROOF_READY"
    assert (
        proof_event.payload["exposure_reference_id"]
        == ack_event.payload["exposure_reference_id"]
    )
    assert (
        proof_event.payload["exposure_reference_sha256"]
        == ack_event.payload["exposure_reference_sha256"]
    )
    assert proof_event.payload["risk_intent_id"] == command.risk_intent_id
    assert proof_event.payload["accepted_request_sha256"] == command.order_request_sha256
    assert proof_event.payload["provider_order_id"] == provider_order_id
    assert (
        proof_event.payload["reconciliation_evidence_sha256"]
        == ack_event.payload["reconciliation_evidence_sha256"]
    )

    reference = proof_event.payload["exposure_reference"]
    assert sha256_text(canonical_json(dict(reference))) == proof_event.payload[
        "exposure_reference_sha256"
    ]
    assert reference["scope_key"] == account_scope.key
    assert reference["intent_id"] == command.risk_intent_id
    assert reference["accepted_request_sha256"] == command.order_request_sha256
    assert reference["provider_order_id"] == provider_order_id
    order_revision = reference["order_observation_revision"]
    assert type(order_revision) is int and order_revision < proof_event.revision
    order_event = stack.journal.get_event(
        account_scope, _PRINCIPAL, intent_id, order_revision
    )
    assert order_event is not None
    assert order_event.scope == account_scope
    assert order_event.principal_ref == _PRINCIPAL
    assert order_event.intent_id == intent_id
    assert order_event.event_type == "ORDER_OBSERVATION"
    assert order_event.record_sha256 == reference["order_observation_record_sha256"]
    assert order_event.payload["account_id"] == account_scope.account_id
    assert order_event.payload["client_order_id"] == client_order_id
    assert order_event.payload["provider_order_id"] == provider_order_id
    assert order_event.payload["record"]["account_id"] == account_scope.account_id
    assert order_event.payload["record"]["client_order_id"] == client_order_id
    assert order_event.payload["record"]["order_id"] == provider_order_id
    assert order_event.payload["instrument"] == _INSTRUMENT


def test_shared_domain_caps_two_accounts_across_products_on_one_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack, keys = _stack(tmp_path, monkeypatch, shared_domain=True)
    trace_events: list[dict[str, object]] = []
    serving = _ServingHost(
        stack.host,
        trace_hook=_trace_reconciliation_failure(stack.gate, trace_events),
    )
    runtime = None
    try:
        serving.start()
        runtime = _client_runtime(stack.host.endpoint, keys)  # type: ignore[arg-type]
        summary = runtime.start()
        assert summary.degraded_count == 0
        client_a, client_b = _execution_clients(runtime)

        first = _submit(client_a, "dim03-shared-first")
        assert first.state == "ACKED_TRACKED", (
            f"first state={first.state}; reconciliation diagnostics={trace_events!r}"
        )
        assert not any(
            row.get("event") == "exception" for row in trace_events
        ), trace_events
        _assert_persisted_provider_binding(
            stack,
            account_scope=_ACCOUNT_SCOPE_A,
            feed=stack.feeds[_WIRE_SCOPE_A],
            intent_id="dim03-shared-first",
            exchange_name="FIXTURE___SPOT",
        )

        with pytest.raises(ServiceExecutionError) as denied:
            _submit(client_b, "dim03-shared-over-cap")
        assert denied.value.code == "ADMISSION_DENIED"
        assert stack.feeds[_WIRE_SCOPE_B].writes == []
        assert stack.gate.snapshot(_ACCOUNT_SCOPE_A)["increase_notional"] == Decimal("20")
        assert stack.gate.snapshot(_ACCOUNT_SCOPE_B)["increase_notional"] == Decimal("0")
    finally:
        try:
            if runtime is not None:
                runtime.close()
        finally:
            try:
                serving.close()
            finally:
                stack.close_resources()


def test_undeclared_isolated_accounts_keep_independent_legacy_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack, keys = _stack(
        tmp_path, monkeypatch, shared_domain=False, isolated_resource_maps=True
    )
    trace_events: list[dict[str, object]] = []
    serving = _ServingHost(
        stack.host,
        trace_hook=_trace_reconciliation_failure(stack.gate, trace_events),
    )
    runtime = None
    try:
        serving.start()
        runtime = _client_runtime(stack.host.endpoint, keys)  # type: ignore[arg-type]
        summary = runtime.start()
        assert summary.degraded_count == 0
        client_a, client_b = _execution_clients(runtime)

        first = _submit(client_a, "dim03-isolated-account-a")
        second = _submit(client_b, "dim03-isolated-account-b")
        assert first.state == second.state == "ACKED_TRACKED", (
            f"first={first.state!r}; second={second.state!r}; "
            f"reconciliation diagnostics={trace_events!r}"
        )
        assert not any(row.get("event") == "exception" for row in trace_events), trace_events
        _assert_persisted_provider_binding(
            stack,
            account_scope=_ACCOUNT_SCOPE_A,
            feed=stack.feeds[_WIRE_SCOPE_A],
            intent_id="dim03-isolated-account-a",
            exchange_name="FIXTURE___SPOT",
        )
        _assert_persisted_provider_binding(
            stack,
            account_scope=_ACCOUNT_SCOPE_B,
            feed=stack.feeds[_WIRE_SCOPE_B],
            intent_id="dim03-isolated-account-b",
            exchange_name="FIXTURE___PERPETUAL",
        )
        assert stack.gate.snapshot(_ACCOUNT_SCOPE_A)["increase_notional"] == Decimal("20")
        assert stack.gate.snapshot(_ACCOUNT_SCOPE_B)["increase_notional"] == Decimal("20")
    finally:
        try:
            if runtime is not None:
                runtime.close()
        finally:
            try:
                serving.close()
            finally:
                stack.close_resources()


def test_unmapped_multi_account_targets_reject_host_before_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack, _keys = _stack(tmp_path, monkeypatch, shared_domain=False)
    try:
        with pytest.raises(ServiceHostError) as rejected:
            stack.host.start()

        assert rejected.value.code == "SHARED_COLLATERAL_COMPOSITION_INCOMPLETE"
        assert stack.host.state == "failed"
        assert stack.host._registry is None
        assert stack.host._server is None
        assert stack.host.endpoint is None
        assert not stack.registry_path.exists()
        assert stack.feeds[_WIRE_SCOPE_A].writes == []
        assert stack.feeds[_WIRE_SCOPE_B].writes == []
        for account_scope in (_ACCOUNT_SCOPE_A, _ACCOUNT_SCOPE_B):
            snapshot = stack.gate.snapshot(account_scope)
            assert snapshot["increase_count"] == 0
            assert snapshot["increase_notional"] == Decimal("0")
    finally:
        stack.close_resources()


@pytest.mark.parametrize(
    (
        "incomplete_account_b",
        "split_account_b_admission",
        "missing_owner",
        "unlisted_same_pool_target",
    ),
    (
        (True, False, False, False),
        (False, False, True, False),
        (False, True, False, False),
        (False, False, False, True),
    ),
    ids=(
        "incomplete-resource-map",
        "owner-missing",
        "different-risk-ledger",
        "undeclared-third-account-in-shared-pool",
    ),
)
def test_shared_domain_startup_rejects_incomplete_trusted_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    incomplete_account_b: bool,
    split_account_b_admission: bool,
    missing_owner: bool,
    unlisted_same_pool_target: bool,
) -> None:
    stack, _keys = _stack(
        tmp_path,
        monkeypatch,
        shared_domain=True,
        incomplete_account_b=incomplete_account_b,
        split_account_b_admission=split_account_b_admission,
        missing_owner=missing_owner,
        unlisted_same_pool_target=unlisted_same_pool_target,
    )
    if split_account_b_admission:
        assert stack.gate.ledger_identity_ref != stack.auxiliary_gates[0].ledger_identity_ref

    try:
        with pytest.raises(ServiceHostError) as rejected:
            stack.host.start()

        assert rejected.value.code == "SHARED_COLLATERAL_COMPOSITION_INCOMPLETE"
        assert stack.host.state == "failed"
        assert stack.host._registry is None
        assert stack.host._server is None
        assert stack.host.endpoint is None
        assert not stack.registry_path.exists()
        assert stack.feeds[_WIRE_SCOPE_A].writes == []
        assert stack.feeds[_WIRE_SCOPE_B].writes == []
        if unlisted_same_pool_target:
            assert stack.feeds[_WIRE_SCOPE_C].writes == []
        for risk_path in stack.risk_paths:
            with sqlite3.connect(risk_path) as connection:
                row = connection.execute(
                    "SELECT ready, manifest_complete FROM risk_shared_collateral_domains "
                    "WHERE domain_id = ?",
                    (_DOMAIN_ID,),
                ).fetchone()
            assert row == (0, 0)
        account_scopes = (_ACCOUNT_SCOPE_A, _ACCOUNT_SCOPE_B) + (
            (_ACCOUNT_SCOPE_C,) if unlisted_same_pool_target else ()
        )
        for gate in (stack.gate, *stack.auxiliary_gates):
            for account_scope in account_scopes:
                snapshot = gate.snapshot(account_scope)
                assert snapshot["increase_count"] == 0
                assert snapshot["increase_notional"] == Decimal("0")
        if unlisted_same_pool_target:
            with stack.gate._connection() as connection:
                scopes = {
                    row[0]
                    for row in connection.execute(
                        "SELECT scope_key FROM risk_shared_collateral_domain_scopes "
                        "WHERE domain_id = ?",
                        (_DOMAIN_ID,),
                    )
                }
            assert scopes == {scope.key for scope in account_scopes}
            intent = RiskIntent(
                intent_id="dim03-unlisted-pool-account",
                scope=_ACCOUNT_SCOPE_C,
                action=IntentAction.INCREASE,
                notional=Decimal("1"),
                payload_fingerprint=hashlib.sha256(
                    b"dim03-unlisted-pool-account"
                ).hexdigest(),
                notional_unit="USD",
                resource=AccountResourceKey("futures", "unified-usd"),
            )
            with pytest.raises(RiskDeniedError) as denied:
                stack.gate.reserve(intent)
            assert denied.value.code == "SHARED_COLLATERAL_DOMAIN_UNREADY"
    finally:
        stack.close_resources()


def test_unconfigured_shared_margin_pool_rejects_host_before_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack, _keys = _stack(
        tmp_path,
        monkeypatch,
        shared_domain=False,
        undeclared_shared_pool=True,
    )
    try:
        with pytest.raises(ServiceHostError) as rejected:
            stack.host.start()

        assert rejected.value.code == "SHARED_COLLATERAL_COMPOSITION_INCOMPLETE"
        assert stack.host.state == "failed"
        assert stack.host._registry is None
        assert stack.host._server is None
        assert stack.host.endpoint is None
        assert not stack.registry_path.exists()
        assert stack.feeds[_WIRE_SCOPE_A].writes == []
        assert stack.feeds[_WIRE_SCOPE_B].writes == []
    finally:
        stack.close_resources()
