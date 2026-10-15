"""One hundred spawned strategies share one public market source and ten accounts."""

from __future__ import annotations

import json
import multiprocessing
import os
import socket
import sqlite3
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
import pytest
import zmq
from bt_api_base.account_protocol import (
    AccountBalance,
    AccountKey,
    AccountObservation,
    AccountResourceKey,
    AccountValuation,
)
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
from bt_api_gateway.market_hub import GatewayMarketHub
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.service_account import ServiceAccountClient
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_config import parse_client_config, parse_service_config
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_market import ServiceMarketClient, ZmqMarketTransportFactory
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ServiceRuntimeError,
    ZmqTransportFactory,
)

_DOMAIN_REF = "paper:gw02-hundred-strategy"
_MARKET_SERVICE_ID = "gw02-market-service"
_MARKET_INSTANCE_ID = "gw02-market-hub"
_MARKET_SCOPE_REF = "gw02-market-scope-v1"
_MARKET_SECURITY_REF = "gw02-market-curve-v1"
_MARKET_OPERATIONS = frozenset(
    {
        "market.subscribe",
        "market.unsubscribe",
        "market.snapshot",
        "market.head",
        "market.read_after",
    }
)
_ACCOUNT_OPERATIONS = frozenset({"account.snapshot"})
_ACCOUNT_SERVICE_PRINCIPAL = "gw02-account-service-principal"
_ACCOUNT_SECURITY_PREFIX = "gw02-account-curve-"
_ACCOUNT_LISTENER_PREFIX = "gw02-account-listener-"
_ACCOUNT_CLIENT_PREFIX = "gw02-account-client-"
_ACCOUNT_SCOPE_PREFIX = "gw02-account-scope-"
_ACCOUNT_SERVICE_PREFIX = "gw02-account-service-"
_ACCOUNT_INSTANCE_PREFIX = "gw02-account-gateway-"
_ACCOUNT_REF_PREFIX = "gw02-account-"
_STRATEGY_REF_PREFIX = "gw02-strategy-"
_PRINCIPAL_REF_PREFIX = "gw02-principal-"
_TRANSPORT_TIMEOUT_MS = 30_000
_HEARTBEAT_SECONDS = 30
_ACCOUNT_COUNT = 10
_STRATEGIES_PER_ACCOUNT = 10
_STRATEGY_COUNT = _ACCOUNT_COUNT * _STRATEGIES_PER_ACCOUNT
_SERVER_COUNT = _ACCOUNT_COUNT + 1
_PILOT_RSS_BYTES = 54 * 1024 * 1024
_PROJECTED_RSS_BYTES_PER_PROCESS = 72 * 1024 * 1024
_MINIMUM_START_FREE_BYTES = 10 * 1024**3
_MINIMUM_BARRIER_FREE_BYTES = 2 * 1024**3
_RESOURCE = AccountResourceKey("spot", "cash")
_ACCOUNT_REFS = tuple(f"{_ACCOUNT_REF_PREFIX}{index:02d}" for index in range(_ACCOUNT_COUNT))
_ACCOUNT_KEYS = tuple(
    AccountKey("synthetic", "loopback", "paper", f"gw02-owner-{index:02d}")
    for index in range(_ACCOUNT_COUNT)
)
_MARKET_KEY = MarketSubscriptionKey(
    source_ref="gw02-synthetic-source",
    entitlement_ref="gw02-public-entitlement",
    instrument=MarketInstrumentKey(
        provider="synthetic",
        venue="loopback",
        environment="paper",
        product_type="spot",
        instrument_id="gw02-shared-instrument",
        native_symbol="GW02-USD",
    ),
    channel="trades",
)


def _free_endpoints(count: int) -> tuple[str, ...]:
    probes: list[socket.socket] = []
    try:
        endpoints = []
        for _ in range(count):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.bind(("127.0.0.1", 0))
            probes.append(probe)
            endpoints.append(f"tcp://127.0.0.1:{probe.getsockname()[1]}")
        return tuple(endpoints)
    finally:
        for probe in probes:
            probe.close()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _account_observation(account_index: int, sequence: int) -> AccountObservation:
    account_key = _ACCOUNT_KEYS[account_index]
    observed_at = _utc_now()
    total = f"{100 + account_index}.00"
    available = f"{80 + account_index}.00"
    locked = "20.00"
    return AccountObservation(
        account_key=account_key,
        observation_id=f"{_ACCOUNT_REFS[account_index]}-observation-{sequence}",
        coverage=(_RESOURCE,),
        balances=(
            AccountBalance(
                resource=_RESOURCE,
                currency="USD",
                total=total,
                available=available,
                locked=locked,
                observed_at_utc=observed_at,
            ),
        ),
        positions=(),
        valuation=AccountValuation(
            currency="USD",
            available_cash=available,
            equity=total,
            observed_at_utc=observed_at,
        ),
        started_at_utc=observed_at,
        completed_at_utc=observed_at,
        consistency="provider_atomic",
        balances_complete=True,
        positions_complete=True,
    )


class _CountedSyntheticMarketProvider:
    """One counted upstream handle per exact provider subscription key."""

    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0
        self.generation: str | None = None
        self.hub: GatewayMarketHub | None = None

    def subscribe(self, key: MarketSubscriptionKey, *, generation: str) -> object:
        if key != _MARKET_KEY or not generation or self.generation is not None:
            raise AssertionError("market hub requested an incompatible synthetic source handle")
        self.starts += 1
        self.generation = generation
        return ("gw02-market-handle", self.starts, key.stream_id, generation)

    def unsubscribe(self, handle: object) -> bool:
        if (
            not isinstance(handle, tuple)
            or len(handle) != 4
            or handle[0] != "gw02-market-handle"
            or handle[3] != self.generation
        ):
            raise AssertionError("market provider was asked to release an unknown handle")
        self.stops += 1
        self.generation = None
        return True

    def publish(self, marker: str) -> int:
        if self.hub is None or self.generation is None:
            raise AssertionError("synthetic public market source is not active")
        event = self.hub.publish(
            _MARKET_KEY,
            {"marker": marker, "source": "gw02-counted-synthetic-provider"},
            _utc_now(),
            generation=self.generation,
        )
        return event.header.sequence


class _CountedSyntheticAccountProvider:
    """One read-only synthetic provider session for one exact account key."""

    def __init__(self, account_index: int) -> None:
        self.account_index = account_index
        self.connect_calls = 0
        self.read_calls = 0
        self.close_calls = 0
        self.generation: str | None = None

    def connect(self, *, account_key: AccountKey, generation: str, timeout: float) -> None:
        if (
            account_key != _ACCOUNT_KEYS[self.account_index]
            or not generation
            or timeout <= 0
            or self.generation is not None
        ):
            raise AssertionError("account provider received an invalid session contract")
        self.connect_calls += 1
        self.generation = generation

    def read_account(self, *, generation: str, timeout: float) -> AccountObservation:
        if generation != self.generation or timeout <= 0:
            raise AssertionError("account read used the wrong synthetic session generation")
        self.read_calls += 1
        return _account_observation(self.account_index, self.read_calls)

    def close(self) -> bool:
        self.close_calls += 1
        self.generation = None
        return True


def _market_service_config(rpc_bind: str, market_bind: str) -> dict[str, Any]:
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": _MARKET_SERVICE_ID,
        "scope_ref": _MARKET_SCOPE_REF,
        "transport_ref": "gw02-market-listener-v1",
        "registration_policy_ref": "gw02-market-policy-v1",
        "service_requirements_ref": "gw02-market-requirements-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": _MARKET_INSTANCE_ID,
                    "bind_ports": ["service.market_source"],
                    "options": {"role": "market_hub"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "gw02-market-listener-v1": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": rpc_bind, "market_bind": market_bind},
                    "security_profile_ref": _MARKET_SECURITY_REF,
                    "options": {"high_water_mark": 128, "max_message_bytes": 262_144},
                }
            },
        },
    }


def _account_service_info(
    account_index: int, endpoint: str, server_public: bytes
) -> dict[str, Any]:
    account_ref = _ACCOUNT_REFS[account_index]
    return {
        "account_index": account_index,
        "account_ref": account_ref,
        "endpoint": endpoint,
        "server_public": server_public,
        "service_id": f"{_ACCOUNT_SERVICE_PREFIX}{account_index:02d}",
        "instance_id": f"{_ACCOUNT_INSTANCE_PREFIX}{account_index:02d}",
        "scope_ref": f"{_ACCOUNT_SCOPE_PREFIX}{account_index:02d}-v1",
        "security_ref": f"{_ACCOUNT_SECURITY_PREFIX}{account_index:02d}v1",
        "peer": WireIdentity(
            WireRole.SERVICE,
            _ACCOUNT_SERVICE_PRINCIPAL,
            f"{_ACCOUNT_SERVICE_PREFIX}{account_index:02d}",
        ),
    }


def _account_service_config(service: dict[str, Any]) -> dict[str, Any]:
    account_index = service["account_index"]
    listener_ref = f"{_ACCOUNT_LISTENER_PREFIX}{account_index:02d}v1"
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service["service_id"],
        "scope_ref": service["scope_ref"],
        "transport_ref": listener_ref,
        "registration_policy_ref": f"gw02-account-policy-{account_index:02d}-v1",
        "service_requirements_ref": f"gw02-account-requirements-{account_index:02d}-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": service["instance_id"],
                    "bind_ports": ["service.account_read", "service.provider_session"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": f"gw02-session-{account_index:02d}-v1",
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                listener_ref: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": service["endpoint"]},
                    "security_profile_ref": service["security_ref"],
                    "options": {"high_water_mark": 64, "max_message_bytes": 262_144},
                }
            },
        },
    }


def _client_config(
    strategy_ref: str,
    account_service: dict[str, Any],
    market_rpc: str,
    market_pub: str,
    *,
    cross_account_service: dict[str, Any] | None,
) -> dict[str, Any]:
    account_ref = account_service["account_ref"]
    bindings: list[dict[str, Any]] = [
        {
            "mount": "store.market",
            "service_id": _MARKET_SERVICE_ID,
            "instance_id": _MARKET_INSTANCE_ID,
            "scope_ref": _MARKET_SCOPE_REF,
            "transport_ref": "gw02-market-client-v1",
        },
        {
            "mount": "store.account",
            "service_id": account_service["service_id"],
            "instance_id": account_service["instance_id"],
            "scope_ref": account_service["scope_ref"],
            "account_ref": account_ref,
            "transport_ref": f"gw02-account-client-{account_service['account_index']:02d}-v1",
        },
    ]
    profiles: dict[str, Any] = {
        "gw02-market-client-v1": {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": market_rpc, "market_connect": market_pub},
            "security_profile_ref": _MARKET_SECURITY_REF,
            "options": {"request_timeout_ms": _TRANSPORT_TIMEOUT_MS},
        },
        bindings[1]["transport_ref"]: {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": account_service["endpoint"]},
            "security_profile_ref": account_service["security_ref"],
            "options": {"request_timeout_ms": _TRANSPORT_TIMEOUT_MS},
        },
    }
    if cross_account_service is not None:
        foreign_ref = cross_account_service["account_ref"]
        transport_ref = f"gw02-account-client-{cross_account_service['account_index']:02d}-probe-v1"
        bindings.append(
            {
                "mount": "store.account",
                "service_id": cross_account_service["service_id"],
                "instance_id": cross_account_service["instance_id"],
                "scope_ref": cross_account_service["scope_ref"],
                "account_ref": foreign_ref,
                "transport_ref": transport_ref,
                "required": False,
            }
        )
        profiles[transport_ref] = {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": cross_account_service["endpoint"]},
            "security_profile_ref": cross_account_service["security_ref"],
            "options": {"request_timeout_ms": _TRANSPORT_TIMEOUT_MS},
        }
    return {
        "client_config_schema_version": 1,
        "process_role": "strategy_client",
        "registration": {"strategy_ref": strategy_ref},
        "clients": {
            "gateway": {
                "config_schema_version": 1,
                "settings": {
                    "bindings": bindings,
                    "options": {
                        "request_timeout_ms": _TRANSPORT_TIMEOUT_MS,
                        "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                    },
                },
            }
        },
        "communication": {"config_schema_version": 1, "profiles": profiles},
    }


def _market_host_process(control, raw_config, registry_path, server_keys, audience) -> None:
    config = parse_service_config(raw_config)
    service_identity = WireIdentity(WireRole.SERVICE, "gw02-market-service", _MARKET_SERVICE_ID)
    service_scope = WireScope(_DOMAIN_REF)
    principals = {
        public_key: principal_ref for public_key, principal_ref, _strategy, _account in audience
    }
    grants = {}
    for _public_key, principal_ref, strategy_ref, account_ref in audience:
        grant = ResourceGrant(
            _MARKET_INSTANCE_ID,
            "service.market_source",
            _MARKET_SCOPE_REF,
            WireScope(
                _DOMAIN_REF,
                account_ref=account_ref,
                strategy_ref=strategy_ref,
            ),
            _MARKET_OPERATIONS,
        )
        grants[principal_ref] = (
            PrincipalGrant(
                WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref),
                (grant,),
            ),
        )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=service_scope,
        scope_ref=config.scope_ref,
        scope_version="gw02-market-scope-v1",
        security_ref=_MARKET_SECURITY_REF,
        security_version="gw02-market-curve-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="gw02-market-policy-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="gw02-market-requirements-v1",
        curve_public_key=server_keys[0],
        curve_secret_key=server_keys[1],
        principals=principals,
        grants=grants,
    )
    provider = _CountedSyntheticMarketProvider()
    hub = GatewayMarketHub(
        provider,
        key_universe=frozenset({_MARKET_KEY}),
        principal_allowed_keys={
            principal_ref: frozenset({_MARKET_KEY})
            for _public_key, principal_ref, _strategy, _account in audience
        },
        service_identity=service_identity,
        service_scope=service_scope,
    )
    provider.hub = hub

    def resources(component, _instance_id, _options, _scope):
        if component != "gateway":
            raise AssertionError("GW02 only selects the trusted market gateway")
        return ComponentResources.owned({"hub": hub}, ready=lambda: hub.ready, close=hub.close)

    host = ServiceHost(config, authority, registry_path, resources)
    try:
        host.start()
        control.send(("ready", os.getpid(), host.endpoint, host.market_endpoint))
        while True:
            host.serve_once(10)
            if not control.poll():
                continue
            command = control.recv()
            action = command[0]
            if action == "status":
                control.send(("status", dict(hub.readiness), provider.starts, provider.stops))
            elif action == "publish":
                sequence = provider.publish(command[1])
                control.send(("published", command[1], sequence))
            elif action == "close":
                host.close()
                control.send(("closed", os.getpid(), host.state, provider.starts, provider.stops))
                return
            else:
                raise AssertionError("unknown synthetic market host command")
    except BaseException as exc:
        if host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _account_host_status(host, worker, provider) -> dict[str, Any]:
    snapshot = worker.latest_snapshot
    return {
        "service_state": host.state,
        "worker_state": worker.state,
        "worker_ready": worker.ready,
        "account_key": worker.account_key.to_dict(),
        "session_generation": worker.session_generation,
        "snapshot_id": None if snapshot is None else snapshot.snapshot_id,
        "observation_id": (
            None if snapshot is None else snapshot.payload["observation"]["observation_id"]
        ),
        "provider_connect_calls": provider.connect_calls,
        "provider_read_calls": provider.read_calls,
        "provider_close_calls": provider.close_calls,
    }


def _account_host_process(
    control,
    account_index,
    raw_config,
    registry_path,
    journal_path,
    guard_directory,
    server_keys,
    audience,
) -> None:
    account_ref = _ACCOUNT_REFS[account_index]
    provider = _CountedSyntheticAccountProvider(account_index)
    host = None
    worker = None
    try:
        config = parse_service_config(raw_config)
        instance_id = f"{_ACCOUNT_INSTANCE_PREFIX}{account_index:02d}"
        journal_scope = f"gw02-journal-{account_index:02d}"
        state_scope = f"gw02-state-{account_index:02d}"
        authorized_strategies = frozenset(
            strategy_ref for _public, _principal, strategy_ref, authorized in audience if authorized
        )
        account_scope = GatewayServiceAccountScope(
            account_ref=account_ref,
            execution_domain_ref=_DOMAIN_REF,
            journal_account_scope=journal_scope,
            account_read_strategy_scope=state_scope,
            shared_account_facts_scope=state_scope,
            shared_fact_strategy_refs=authorized_strategies,
        )
        gateway_principals = {
            principal_ref: GatewayPrincipal(
                principal_id=principal_ref,
                account_scopes=frozenset({journal_scope}),
                strategy_scopes=frozenset({state_scope}),
                allowed_kinds=frozenset({GatewayCommandKind.READ}),
            )
            for _public, principal_ref, _strategy, authorized in audience
            if authorized
        }
        holder: dict[str, GatewayAccountSessionWorker] = {}

        def resources(component, _selected_instance, options, _scope):
            if component != "gateway" or options.get("role") != "account_session_worker":
                raise AssertionError("GW02 only selects one account session worker")
            journal = GatewayEventJournal(journal_path)
            guard = LocalAccountSessionGuard(
                Path(guard_directory),
                _ACCOUNT_KEYS[account_index].account_key_id,
                owner_ref=f"gw02-host-{account_index:02d}",
            )
            session_worker = GatewayAccountSessionWorker(
                provider,
                journal,
                account_key=_ACCOUNT_KEYS[account_index],
                journal_account_scope=journal_scope,
                state_scope=state_scope,
                guard=guard,
                required_coverage=(_RESOURCE,),
                refresh_interval_seconds=3_600,
                max_staleness_seconds=7_200,
                request_timeout_seconds=5,
            )
            holder["worker"] = session_worker
            return ComponentResources.owned(
                {
                    "journal": journal,
                    "accounts": {account_ref: account_scope},
                    "principals": gateway_principals,
                },
                ready=lambda: True,
                close=session_worker.close,
                internal_ports={"service.provider_session": session_worker},
            )

        grants = {}
        for _public, principal_ref, strategy_ref, authorized in audience:
            if authorized:
                grant = ResourceGrant(
                    instance_id,
                    "service.account_read",
                    config.scope_ref,
                    WireScope(
                        _DOMAIN_REF,
                        account_ref=account_ref,
                        strategy_ref=strategy_ref,
                    ),
                    _ACCOUNT_OPERATIONS,
                )
                grants[principal_ref] = (
                    PrincipalGrant(
                        WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref),
                        (grant,),
                    ),
                )
            else:
                # The probe credential is recognized, but has no grant in this
                # account. ServiceRegistry must reject the cross-account bind.
                grants[principal_ref] = ()
        authority = TrustedServiceAuthority(
            identity=WireIdentity(
                WireRole.SERVICE,
                _ACCOUNT_SERVICE_PRINCIPAL,
                config.service_id,
            ),
            scope=WireScope(_DOMAIN_REF),
            scope_ref=config.scope_ref,
            scope_version=f"gw02-account-scope-{account_index:02d}-v1",
            security_ref=f"{_ACCOUNT_SECURITY_PREFIX}{account_index:02d}v1",
            security_version=f"gw02-account-curve-{account_index:02d}-v1",
            registration_policy_ref=config.registration_policy_ref,
            registration_policy_version=f"gw02-account-policy-{account_index:02d}-v1",
            service_requirements_ref=config.service_requirements_ref,
            service_requirements_version=(f"gw02-account-requirements-{account_index:02d}-v1"),
            curve_public_key=server_keys[0],
            curve_secret_key=server_keys[1],
            principals={public: principal for public, principal, _strategy, _ok in audience},
            grants=grants,
        )
        host = ServiceHost(config, authority, registry_path, resources)
        host.start()
        worker = holder["worker"]
        control.send(
            (
                "ready",
                account_index,
                os.getpid(),
                host.endpoint,
                _account_host_status(host, worker, provider),
            )
        )
        while True:
            host.serve_once(10)
            if not control.poll():
                continue
            command = control.recv()
            if command[0] == "status":
                control.send(
                    ("status", account_index, _account_host_status(host, worker, provider))
                )
            elif command[0] == "close":
                host.close()
                control.send(
                    (
                        "closed",
                        account_index,
                        os.getpid(),
                        host.state,
                        _account_host_status(host, worker, provider),
                    )
                )
                return
            else:
                raise AssertionError("unknown account host command")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", account_index, getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _to_plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_to_plain(item) for item in value)
    if hasattr(value, "items"):
        return {key: _to_plain(item) for key, item in value.items()}
    return value


def _event_tuple(event) -> tuple[Any, ...]:
    return (
        event.header.stream_id,
        event.header.stream_epoch,
        event.header.sequence,
        _to_plain(event.payload["key"]),
        _to_plain(event.payload["data"]),
    )


def _strategy_process(
    control,
    strategy_index,
    raw_config,
    strategy_ref,
    principal_ref,
    account_index,
    service_records,
    security_material,
    start_event,
    abort_event,
) -> None:
    account_ref = _ACCOUNT_REFS[account_index]
    runtime = None
    market = None
    account_client = None
    pending_events: list[tuple[Any, ...]] = []
    try:
        config = parse_client_config(raw_config)
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)
        scopes = {}
        for service_id, record in service_records.items():
            scope_account = (
                account_ref if service_id == _MARKET_SERVICE_ID else record["account_ref"]
            )
            scopes[record["scope_ref"]] = WireScope(
                _DOMAIN_REF,
                account_ref=scope_account,
                strategy_ref=strategy_ref,
            )
        peers = {record["service_id"]: record["peer"] for record in service_records.values()}
        curve_keys = {
            security_ref: ClientCurveKeys(*material)
            for security_ref, material in security_material.items()
        }
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda scope_ref: scopes[scope_ref],
            peer_resolver=lambda service_id: peers[service_id],
            transport_factory=ZmqTransportFactory(curve_keys),
            requested_operations={
                "store.market": _MARKET_OPERATIONS,
                "store.account": _ACCOUNT_OPERATIONS,
            },
        )
        market_security = curve_keys[_MARKET_SECURITY_REF]
        market = ServiceMarketClient(
            runtime,
            "store.market",
            _MARKET_KEY,
            subscriber_factory=ZmqMarketTransportFactory({_MARKET_SECURITY_REF: market_security}),
            stream_scope=WireScope(_DOMAIN_REF),
            head_check_interval_seconds=30,
        )
        account_service = service_records[account_ref]
        account_client = ServiceAccountClient(
            runtime,
            "store.account",
            account_ref=account_ref,
            expected_account=_ACCOUNT_KEYS[account_index],
            required_coverage=(_RESOURCE,),
            expected_scope_ref=account_service["scope_ref"],
        )
        control.send(("loaded", strategy_index, os.getpid()))
        deadline = time.monotonic() + 120
        while not start_event.wait(0.05):
            if abort_event.is_set():
                control.send(("aborted", strategy_index, os.getpid()))
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("GW02 shared strategy start barrier expired")
        if abort_event.is_set():
            control.send(("aborted", strategy_index, os.getpid()))
            return

        started_ns = time.perf_counter_ns()
        summary = runtime.start()
        bootstrap = market.start()
        account_view = account_client.refresh()
        bindings = runtime.bindings
        market_bindings = tuple(binding for binding in bindings if binding.mount == "store.market")
        account_bindings = tuple(
            binding
            for binding in bindings
            if binding.mount == "store.account" and binding.scope.account_ref == account_ref
        )
        if len(market_bindings) != 1 or len(account_bindings) != 1:
            raise AssertionError("strategy did not obtain exactly its market and account bindings")
        control.send(
            (
                "ready",
                {
                    "strategy_index": strategy_index,
                    "pid": os.getpid(),
                    "strategy_ref": strategy_ref,
                    "principal_ref": principal_ref,
                    "account_ref": account_ref,
                    "started_ns": started_ns,
                    "market_registration_id": market_bindings[0].registration_id,
                    "account_registration_id": account_bindings[0].registration_id,
                    "active_binding_count": len(bindings),
                    "degraded_count": summary.degraded_count,
                    "degradation_codes": summary.degradation_codes,
                    "market_bootstrap": {
                        "snapshot_kind": bootstrap.snapshot_kind,
                        "snapshot_complete": bootstrap.snapshot_complete,
                        "full_baseline_available": bootstrap.full_baseline_available,
                    },
                    "account_view": {
                        "account_key": account_view.observation.account_key.to_dict(),
                        "observation_id": account_view.observation.observation_id,
                        "snapshot_id": account_view.snapshot_id,
                        "cursor": account_view.cursor,
                        "session_generation": account_view.session_generation,
                        "balance_total": account_view.observation.balances[0].total,
                        "healthy": account_client.healthy,
                    },
                },
            )
        )

        def pump_market(timeout_ms: int) -> None:
            event = market.poll(timeout_ms=timeout_ms)
            if event is not None:
                pending_events.append(_event_tuple(event))

        while True:
            delay = runtime.next_maintenance_delay()
            if delay is not None and delay <= 0:
                runtime.maintain()
            pump_market(0)
            if not control.poll(0.002):
                continue
            command = control.recv()
            action = command[0]
            if action == "collect":
                marker, timeout_seconds = command[1], command[2]
                deadline = time.monotonic() + timeout_seconds
                while time.monotonic() < deadline and not any(
                    event[4].get("marker") == marker for event in pending_events
                ):
                    pump_market(10)
                selected = tuple(
                    event for event in pending_events if event[4].get("marker") == marker
                )
                pending_events = [
                    event for event in pending_events if event[4].get("marker") != marker
                ]
                control.send(("events", strategy_index, os.getpid(), marker, selected))
            elif action == "drain":
                pending_events.clear()
                control.send(("drained", strategy_index, os.getpid()))
            elif action == "read-own-account":
                view = account_client.refresh()
                control.send(
                    (
                        "account-view",
                        strategy_index,
                        view.observation.account_key.to_dict(),
                        view.observation.observation_id,
                        view.snapshot_id,
                        account_client.healthy,
                    )
                )
            elif action == "cross-account":
                foreign_index = command[1]
                foreign_ref = _ACCOUNT_REFS[foreign_index]
                foreign_scope = service_records[foreign_ref]["scope_ref"]
                try:
                    runtime.call(
                        "store.account",
                        "account.snapshot",
                        {"limit": 1},
                        account_ref=foreign_ref,
                        scope_ref=foreign_scope,
                    )
                except ServiceRuntimeError as exc:
                    denial = exc.code
                else:
                    denial = "ALLOWED"
                view = account_client.refresh()
                control.send(
                    (
                        "cross-account-result",
                        strategy_index,
                        denial,
                        view.observation.account_key.to_dict(),
                        view.observation.observation_id,
                        runtime.healthy,
                    )
                )
            elif action == "close":
                market.close()
                account_client.close()
                runtime.close()
                control.send(
                    (
                        "closed",
                        strategy_index,
                        os.getpid(),
                        runtime.state,
                        runtime.cleanup_failures,
                        runtime.unregister_uncertainties,
                    )
                )
                return
            else:
                raise AssertionError("unknown strategy process command")
    except BaseException as exc:
        if market is not None:
            with suppress(BaseException):
                market.close()
        if account_client is not None:
            with suppress(BaseException):
                account_client.close()
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(
                ("error", strategy_index, os.getpid(), getattr(exc, "code", type(exc).__name__))
            )
        raise
    finally:
        control.close()


def _start_process(context, target, args, name: str) -> dict[str, Any]:
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=target, args=(child, *args), name=name)
    try:
        process.start()
    except BaseException:
        parent.close()
        child.close()
        process.close()
        raise
    child.close()
    return {"process": process, "pipe": parent, "name": name, "closed": False}


def _await_messages(records, expected_tag: str, timeout: float, label: str) -> dict[int, tuple]:
    pending = set(records)
    received: dict[int, tuple] = {}
    deadline = time.monotonic() + timeout
    while pending and time.monotonic() < deadline:
        made_progress = False
        for index in tuple(pending):
            record = records[index]
            if not record["pipe"].poll():
                continue
            message = record["pipe"].recv()
            if not isinstance(message, tuple) or not message or message[0] != expected_tag:
                raise AssertionError(f"{label} {index} returned unexpected message: {message!r}")
            received[index] = message
            pending.remove(index)
            made_progress = True
        if not made_progress:
            time.sleep(0.005)
    if pending:
        state = {index: records[index]["process"].exitcode for index in sorted(pending)}
        raise AssertionError(f"{label} timed out; pending={sorted(pending)}, exits={state}")
    return received


def _send_to_all(records, *command) -> None:
    for index in sorted(records):
        record = records[index]
        if not record["process"].is_alive():
            raise AssertionError(f"{record['name']} exited before command {command[0]}")
        record["pipe"].send(command)


def _receive_one(record, timeout: float, expected_tag: str) -> tuple:
    if not record["pipe"].poll(timeout):
        raise AssertionError(f"{record['name']} missed its {expected_tag} response bound")
    message = record["pipe"].recv()
    if not isinstance(message, tuple) or not message or message[0] != expected_tag:
        raise AssertionError(f"{record['name']} returned unexpected message: {message!r}")
    return message


def _finish_process(record, *, expected_tag: str, expected_index: int | None = None) -> tuple:
    message = _receive_one(record, 45.0, expected_tag)
    if expected_index is not None and message[1] != expected_index:
        raise AssertionError(f"{record['name']} replied for process {message[1]}")
    process = record["process"]
    process.join(30)
    if process.is_alive():
        raise AssertionError(f"{record['name']} did not exit after normal close")
    if process.exitcode != 0:
        raise AssertionError(f"{record['name']} exited with code {process.exitcode}")
    record["exit_code"] = process.exitcode
    record["normal_exit"] = True
    record["closed"] = True
    record["pipe"].close()
    process.close()
    return message


def _bounded_reap(
    record: dict[str, Any], abort_event, forced: list[str], failures: list[str]
) -> None:
    if record["closed"]:
        return
    process, pipe = record["process"], record["pipe"]
    try:
        if process.is_alive() and record.get("kind") == "strategy" and not record.get("ready"):
            abort_event.set()
        elif process.is_alive():
            pipe.send(("close",))
    except Exception as exc:
        failures.append(f"{record['name']}:close-send:{type(exc).__name__}")
    try:
        process.join(15)
    except Exception as exc:
        failures.append(f"{record['name']}:normal-join:{type(exc).__name__}")
    try:
        alive = process.is_alive()
    except Exception as exc:
        alive = False
        failures.append(f"{record['name']}:state:{type(exc).__name__}")
    if alive:
        forced.append(record["name"])
        try:
            process.terminate()
            process.join(10)
        except Exception as exc:
            failures.append(f"{record['name']}:terminate:{type(exc).__name__}")
    try:
        alive = process.is_alive()
    except Exception as exc:
        alive = False
        failures.append(f"{record['name']}:post-terminate-state:{type(exc).__name__}")
    if alive:
        try:
            process.kill()
            process.join(10)
        except Exception as exc:
            failures.append(f"{record['name']}:kill:{type(exc).__name__}")
    try:
        alive = process.is_alive()
    except Exception as exc:
        alive = False
        failures.append(f"{record['name']}:post-kill-state:{type(exc).__name__}")
    try:
        pipe.close()
    except Exception as exc:
        failures.append(f"{record['name']}:pipe-close:{type(exc).__name__}")
    if alive:
        failures.append(f"{record['name']}:still-alive")
        return
    if process.exitcode is not None:
        record["exit_code"] = process.exitcode
        record["normal_exit"] = process.exitcode == 0 and record.get("closed", False)
        try:
            process.close()
            record["closed"] = True
        except Exception as exc:
            failures.append(f"{record['name']}:process-close:{type(exc).__name__}")


def _registry_rows(path: Path) -> tuple[dict[str, Any], ...]:
    with sqlite3.connect(path, timeout=10) as connection:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT principal, identity_json, scope_json, port FROM sessions "
            "ORDER BY principal, port"
        ).fetchall()
    return tuple(
        {
            "principal": row[0],
            "identity": json.loads(row[1]),
            "scope": json.loads(row[2]),
            "port": row[3],
        }
        for row in rows
    )


def _assert_market_rows(rows, expected_strategy_indices) -> None:
    expected = {
        (
            f"{_PRINCIPAL_REF_PREFIX}{index:03d}",
            f"{_STRATEGY_REF_PREFIX}{index:03d}",
            _ACCOUNT_REFS[index // _STRATEGIES_PER_ACCOUNT],
        )
        for index in expected_strategy_indices
    }
    actual = {
        (
            row["identity"]["principal_ref"],
            row["identity"]["actor_ref"],
            row["scope"]["account_ref"],
        )
        for row in rows
    }
    assert len(rows) == len(expected), (len(rows), len(expected))
    assert actual == expected
    assert all(row["port"] == "service.market_source" for row in rows)


def _assert_account_rows(rows, account_index: int, expected_strategy_indices) -> None:
    expected = {
        (
            f"{_PRINCIPAL_REF_PREFIX}{index:03d}",
            f"{_STRATEGY_REF_PREFIX}{index:03d}",
        )
        for index in expected_strategy_indices
    }
    actual = {(row["identity"]["principal_ref"], row["identity"]["actor_ref"]) for row in rows}
    assert len(rows) == len(expected), (account_index, len(rows), len(expected))
    assert actual == expected
    assert all(row["scope"]["account_ref"] == _ACCOUNT_REFS[account_index] for row in rows)
    assert all(row["port"] == "service.account_read" for row in rows)


def _host_status(record) -> tuple:
    record["pipe"].send(("status",))
    return _receive_one(record, 15.0, "status")


def _close_host(record, *, expected_index: int | None = None) -> tuple:
    record["pipe"].send(("close",))
    return _finish_process(record, expected_tag="closed", expected_index=expected_index)


def _write_observation(path: Path, evidence: dict[str, Any]) -> None:
    path.write_text(json.dumps(evidence, sort_keys=True, indent=2), encoding="utf-8")


def test_one_hundred_spawned_strategies_share_one_market_source_and_ten_account_sessions(
    tmp_path: Path,
):
    """Prove 10×10 real strategy processes on one barrier and isolated CURVE sessions."""

    if os.name != "nt":
        pytest.skip("GW02 acceptance requires Windows spawn and local CURVE endpoints")
    required_available = (
        _STRATEGY_COUNT + _SERVER_COUNT
    ) * _PROJECTED_RSS_BYTES_PER_PROCESS + 2 * 1024**3
    memory_before = psutil.virtual_memory().available
    assert memory_before >= max(_MINIMUM_START_FREE_BYTES, required_available), (
        "insufficient free memory for 111 independent Gateway processes: "
        f"available={memory_before}, required={max(_MINIMUM_START_FREE_BYTES, required_available)}"
    )

    context = multiprocessing.get_context("spawn")
    endpoints = _free_endpoints(_ACCOUNT_COUNT + 2)
    market_server_keys = zmq.curve_keypair()
    client_keys = {index: zmq.curve_keypair() for index in range(_STRATEGY_COUNT)}
    account_server_keys = {index: zmq.curve_keypair() for index in range(_ACCOUNT_COUNT)}
    account_services = {
        index: _account_service_info(index, endpoints[index + 2], account_server_keys[index][0])
        for index in range(_ACCOUNT_COUNT)
    }
    market_identity = WireIdentity(WireRole.SERVICE, "gw02-market-service", _MARKET_SERVICE_ID)
    market_audience = tuple(
        (
            client_keys[index][0],
            f"{_PRINCIPAL_REF_PREFIX}{index:03d}",
            f"{_STRATEGY_REF_PREFIX}{index:03d}",
            _ACCOUNT_REFS[index // _STRATEGIES_PER_ACCOUNT],
        )
        for index in range(_STRATEGY_COUNT)
    )
    account_audiences = {}
    for account_index in range(_ACCOUNT_COUNT):
        owned = tuple(
            (
                client_keys[index][0],
                f"{_PRINCIPAL_REF_PREFIX}{index:03d}",
                f"{_STRATEGY_REF_PREFIX}{index:03d}",
                True,
            )
            for index in range(
                account_index * _STRATEGIES_PER_ACCOUNT,
                (account_index + 1) * _STRATEGIES_PER_ACCOUNT,
            )
        )
        cross_probe = ()
        if account_index == 1:
            # Strategy 000 has a live account-000 binding and tries to register
            # its same CURVE identity against account 001 without a grant.
            cross_probe = (
                (
                    client_keys[0][0],
                    f"{_PRINCIPAL_REF_PREFIX}000",
                    f"{_STRATEGY_REF_PREFIX}000",
                    False,
                ),
            )
        account_audiences[account_index] = owned + cross_probe

    market_registry = tmp_path / "market-registry.sqlite3"
    account_registry_paths = {
        index: tmp_path / f"account-{index:02d}-registry.sqlite3" for index in range(_ACCOUNT_COUNT)
    }
    account_journal_paths = {
        index: tmp_path / f"account-{index:02d}-journal.sqlite3" for index in range(_ACCOUNT_COUNT)
    }
    guard_directory = tmp_path / "account-session-guards"
    guard_directory.mkdir()
    market_raw_config = _market_service_config(endpoints[0], endpoints[1])
    account_raw_configs = {
        index: _account_service_config(account_services[index]) for index in range(_ACCOUNT_COUNT)
    }
    service_records = {
        _ACCOUNT_REFS[index]: account_services[index] for index in range(_ACCOUNT_COUNT)
    }
    market_service = {
        "service_id": _MARKET_SERVICE_ID,
        "instance_id": _MARKET_INSTANCE_ID,
        "scope_ref": _MARKET_SCOPE_REF,
        "peer": market_identity,
        "market_endpoint": endpoints[1],
    }

    market_record = None
    account_hosts: dict[int, dict[str, Any]] = {}
    clients: dict[int, dict[str, Any]] = {}
    start_event = context.Event()
    abort_event = context.Event()
    barrier_published_ns = None
    forced_cleanup: list[str] = []
    cleanup_failures: list[str] = []
    lifecycle: dict[str, Any] = {
        "case": "GW02_100_STRATEGY_10_ACCOUNT_LOCAL_SYNTHETIC",
        "strategy_target_count": _STRATEGY_COUNT,
        "account_target_count": _ACCOUNT_COUNT,
        "distinct_market_key_count": 1,
        "provider_contract": "one acquired handle per exact MarketSubscriptionKey",
        "memory_before_bytes": memory_before,
        "pilot_mean_rss_bytes": _PILOT_RSS_BYTES,
        "projected_process_rss_bytes": _PROJECTED_RSS_BYTES_PER_PROCESS,
        "projected_total_child_rss_bytes": required_available - 2 * 1024**3,
        "loaded_count": 0,
        "ready_count": 0,
        "barrier_published_ns": None,
        "strategies": [],
        "servers": [],
        "forced_cleanup_requests": forced_cleanup,
        "cleanup_failures": cleanup_failures,
        "normal_strategy_exit_count": 0,
        "normal_server_exit_count": 0,
        "cleanup_complete": False,
    }
    observation_path = tmp_path / "gw02-hundred-strategy-observation.json"
    success = False

    try:
        market_record = _start_process(
            context,
            _market_host_process,
            (
                market_raw_config,
                str(market_registry),
                market_server_keys,
                market_audience,
            ),
            "gw02-market-service-host",
        )
        market_record["pid"] = market_record["process"].pid
        market_record["kind"] = "market"
        market_record["ready"] = False
        for account_index in range(_ACCOUNT_COUNT):
            server_keys = account_server_keys[account_index]
            audience = account_audiences[account_index]
            record = _start_process(
                context,
                _account_host_process,
                (
                    account_index,
                    account_raw_configs[account_index],
                    str(account_registry_paths[account_index]),
                    str(account_journal_paths[account_index]),
                    str(guard_directory),
                    server_keys,
                    audience,
                ),
                f"gw02-account-host-{account_index:02d}",
            )
            record.update(
                {
                    "pid": record["process"].pid,
                    "kind": "account",
                    "account_index": account_index,
                    "ready": False,
                    "registry_path": account_registry_paths[account_index],
                }
            )
            account_hosts[account_index] = record

        server_records = {-1: market_record, **account_hosts}
        server_ready = _await_messages(server_records, "ready", 90.0, "service host")
        for account_index, record in account_hosts.items():
            message = server_ready[account_index]
            if message[1] != account_index or message[2] != record["pid"]:
                raise AssertionError(f"account host identity mismatch: {message!r}")
            state = message[4]
            if (
                state["service_state"] != "started"
                or state["worker_ready"] is not True
                or state["account_key"] != _ACCOUNT_KEYS[account_index].to_dict()
                or state["provider_connect_calls"] != 1
                or state["provider_read_calls"] != 1
                or state["provider_close_calls"] != 0
            ):
                raise AssertionError(f"account host did not open one exact session: {message!r}")
            record["ready"] = True
            record["ready_status"] = state
        market_ready = server_ready[-1]
        if market_ready[1] != market_record["pid"] or not market_ready[2] or not market_ready[3]:
            raise AssertionError(f"market host did not bind its local endpoints: {market_ready!r}")
        market_record["ready"] = True
        market_record["rpc_endpoint"] = market_ready[2]
        market_record["market_endpoint"] = market_ready[3]

        for strategy_index in range(_STRATEGY_COUNT):
            account_index = strategy_index // _STRATEGIES_PER_ACCOUNT
            own_service = account_services[account_index]
            cross_service = None
            if strategy_index == 0:
                cross_service = account_services[1]
            raw_client_config = _client_config(
                f"{_STRATEGY_REF_PREFIX}{strategy_index:03d}",
                own_service,
                market_ready[2],
                market_ready[3],
                cross_account_service=cross_service,
            )
            security_material = {
                _MARKET_SECURITY_REF: (
                    market_server_keys[0],
                    client_keys[strategy_index][0],
                    client_keys[strategy_index][1],
                ),
                own_service["security_ref"]: (
                    own_service["server_public"],
                    client_keys[strategy_index][0],
                    client_keys[strategy_index][1],
                ),
            }
            if cross_service is not None:
                security_material[cross_service["security_ref"]] = (
                    cross_service["server_public"],
                    client_keys[strategy_index][0],
                    client_keys[strategy_index][1],
                )
            strategy_record = _start_process(
                context,
                _strategy_process,
                (
                    strategy_index,
                    raw_client_config,
                    f"{_STRATEGY_REF_PREFIX}{strategy_index:03d}",
                    f"{_PRINCIPAL_REF_PREFIX}{strategy_index:03d}",
                    account_index,
                    {**service_records, _MARKET_SERVICE_ID: market_service},
                    security_material,
                    start_event,
                    abort_event,
                ),
                f"gw02-strategy-{strategy_index:03d}",
            )
            strategy_record.update(
                {
                    "pid": strategy_record["process"].pid,
                    "kind": "strategy",
                    "strategy_index": strategy_index,
                    "account_index": account_index,
                    "loaded": False,
                    "ready": False,
                }
            )
            clients[strategy_index] = strategy_record

        loaded = _await_messages(clients, "loaded", 120.0, "strategy process import")
        for strategy_index, record in clients.items():
            message = loaded[strategy_index]
            if (
                message[1] != strategy_index
                or message[2] != record["pid"]
                or not record["process"].is_alive()
            ):
                raise AssertionError(f"strategy worker was not independently alive: {message!r}")
            record["loaded"] = True
            record["loaded_pid"] = message[2]
        if len({record["pid"] for record in clients.values()}) != _STRATEGY_COUNT:
            raise AssertionError("the 100 strategy workers did not have 100 unique process IDs")

        memory_at_barrier = psutil.virtual_memory().available
        lifecycle["memory_at_barrier_bytes"] = memory_at_barrier
        if memory_at_barrier < _MINIMUM_BARRIER_FREE_BYTES:
            raise AssertionError(
                "free memory fell below the GW02 barrier reserve before any strategy admission: "
                f"available={memory_at_barrier}, required={_MINIMUM_BARRIER_FREE_BYTES}"
            )
        lifecycle["loaded_count"] = len(loaded)
        lifecycle["loaded_unique_pid_count"] = len(
            {record["loaded_pid"] for record in clients.values()}
        )
        lifecycle["all_loaded_alive_before_barrier"] = all(
            record["process"].is_alive() for record in clients.values()
        )
        if not lifecycle["all_loaded_alive_before_barrier"]:
            raise AssertionError("not all strategy processes survived until the common barrier")

        barrier_published_ns = time.perf_counter_ns()
        lifecycle["barrier_published_ns"] = barrier_published_ns
        start_event.set()
        ready_messages = _await_messages(clients, "ready", 120.0, "strategy admission")
        strategy_ready = {index: message[1] for index, message in ready_messages.items()}
        lifecycle["ready_count"] = len(strategy_ready)
        for strategy_index, record in clients.items():
            details = strategy_ready[strategy_index]
            if (
                details["strategy_index"] != strategy_index
                or details["pid"] != record["pid"]
                or details["started_ns"] < barrier_published_ns
                or details["account_ref"]
                != _ACCOUNT_REFS[strategy_index // _STRATEGIES_PER_ACCOUNT]
                or details["active_binding_count"] != 2
                or not details["account_view"]["healthy"]
            ):
                raise AssertionError(
                    f"strategy did not complete the shared admission path: {details!r}"
                )
            record["ready"] = True
            record["ready_details"] = details

        start_times = [details["started_ns"] for details in strategy_ready.values()]
        lifecycle["start_spread_ns"] = max(start_times) - min(start_times)
        if lifecycle["start_spread_ns"] > 5_000_000_000:
            raise AssertionError(
                f"strategy start wave exceeded five seconds: {lifecycle['start_spread_ns']}"
            )
        assert (
            len({item["market_registration_id"] for item in strategy_ready.values()})
            == _STRATEGY_COUNT
        )
        assert (
            len({item["account_registration_id"] for item in strategy_ready.values()})
            == _STRATEGY_COUNT
        )

        for account_index in range(_ACCOUNT_COUNT):
            expected_indices = range(
                account_index * _STRATEGIES_PER_ACCOUNT,
                (account_index + 1) * _STRATEGIES_PER_ACCOUNT,
            )
            account_members = [strategy_ready[index] for index in expected_indices]
            assert len(account_members) == _STRATEGIES_PER_ACCOUNT
            account_identity = {
                (
                    item["account_view"]["account_key"]["account_id"],
                    item["account_view"]["observation_id"],
                    item["account_view"]["snapshot_id"],
                    item["account_view"]["session_generation"],
                    item["account_view"]["balance_total"],
                )
                for item in account_members
            }
            assert len(account_identity) == 1, (account_index, account_identity)
            identity = next(iter(account_identity))
            assert identity[0] == _ACCOUNT_KEYS[account_index].account_id
            assert identity[1] == f"{_ACCOUNT_REFS[account_index]}-observation-1"
            assert identity[4] == f"{100 + account_index}.00"

        for strategy_index, details in strategy_ready.items():
            is_probe = strategy_index == 0
            if is_probe:
                assert details["degraded_count"] == 1
                assert details["degradation_codes"] == ("DENIED",)
            else:
                assert details["degraded_count"] == 0
                assert details["degradation_codes"] == ()

        market_status = _host_status(market_record)
        if market_status[0] != "status":
            raise AssertionError(f"unexpected market status: {market_status!r}")
        readiness, market_starts, market_stops = market_status[1:]
        assert readiness["active_registrations"] == _STRATEGY_COUNT
        assert readiness["active_upstreams"] == 1
        assert readiness["pending_releases"] == 0
        assert (market_starts, market_stops) == (1, 0)
        _assert_market_rows(_registry_rows(market_registry), range(_STRATEGY_COUNT))

        account_statuses = {}
        for account_index, record in account_hosts.items():
            status = _host_status(record)
            if status[0] != "status" or status[1] != account_index:
                raise AssertionError(f"unexpected account status: {status!r}")
            account_statuses[account_index] = status[2]
            state = status[2]
            assert state["service_state"] == "started"
            assert state["worker_ready"] is True
            assert state["account_key"] == _ACCOUNT_KEYS[account_index].to_dict()
            assert state["provider_connect_calls"] == 1
            assert state["provider_read_calls"] == 1
            assert state["provider_close_calls"] == 0
            account_indices = range(
                account_index * _STRATEGIES_PER_ACCOUNT,
                (account_index + 1) * _STRATEGIES_PER_ACCOUNT,
            )
            _assert_account_rows(
                _registry_rows(record["registry_path"]), account_index, account_indices
            )
        assert (
            len({status["session_generation"] for status in account_statuses.values()})
            == _ACCOUNT_COUNT
        )
        assert (
            len({status["account_key"]["account_id"] for status in account_statuses.values()})
            == _ACCOUNT_COUNT
        )

        # The same live strategy's extra account-001 registration was denied
        # by account-001's actual CURVE ServiceHost, and it has no local route.
        clients[0]["pipe"].send(("cross-account", 1))
        cross_result = _receive_one(clients[0], 30.0, "cross-account-result")
        assert cross_result[1] == 0
        assert cross_result[2] == "AMBIGUOUS_OR_MISSING_ROUTE"
        assert cross_result[3] == _ACCOUNT_KEYS[0].to_dict()
        assert cross_result[4] == f"{_ACCOUNT_REFS[0]}-observation-1"
        assert cross_result[5] is True
        _assert_account_rows(
            _registry_rows(account_registry_paths[1]),
            1,
            range(_STRATEGIES_PER_ACCOUNT, 2 * _STRATEGIES_PER_ACCOUNT),
        )

        # One provider key and key universe are shared by all accounts because
        # the synthetic provider contract acquires one handle per exact key.
        time.sleep(0.5)
        published = market_record["pipe"]
        published.send(("publish", "gw02-warmup"))
        warmup = _receive_one(market_record, 15.0, "published")
        assert warmup[1] == "gw02-warmup"
        _send_to_all(clients, "drain")
        drained = _await_messages(clients, "drained", 30.0, "market warmup drain")
        assert len(drained) == _STRATEGY_COUNT

        published.send(("publish", "gw02-common-event-1"))
        common_event = _receive_one(market_record, 15.0, "published")
        _send_to_all(clients, "collect", "gw02-common-event-1", 30.0)
        event_messages = _await_messages(clients, "events", 45.0, "common market event")
        event_values = {}
        for strategy_index, message in event_messages.items():
            if message[1] != strategy_index or message[3] != "gw02-common-event-1":
                raise AssertionError(f"market event was routed to the wrong strategy: {message!r}")
            events = message[4]
            assert len(events) == 1, (strategy_index, events)
            event_values[strategy_index] = events[0]
        assert len({(item[0], item[1], item[2]) for item in event_values.values()}) == 1
        assert all(item[2] == common_event[2] for item in event_values.values())
        assert all(item[0] == _MARKET_KEY.stream_id for item in event_values.values())
        assert all(item[3] == _MARKET_KEY.to_dict() for item in event_values.values())
        assert all(
            item[4]
            == {
                "marker": "gw02-common-event-1",
                "source": "gw02-counted-synthetic-provider",
            }
            for item in event_values.values()
        )

        # Closing one client releases only its two registrations and market
        # reference; its nine account peers and all other accounts stay live.
        clients[0]["pipe"].send(("close",))
        close_zero = _finish_process(clients[0], expected_tag="closed", expected_index=0)
        assert close_zero[3] == "closed"
        assert close_zero[4] == 0 and close_zero[5] == 0
        survivors = {index: record for index, record in clients.items() if index != 0}
        assert all(record["process"].is_alive() for record in survivors.values())
        market_after_one = _host_status(market_record)
        assert market_after_one[1]["active_registrations"] == _STRATEGY_COUNT - 1
        assert market_after_one[1]["active_upstreams"] == 1
        assert (market_after_one[2], market_after_one[3]) == (1, 0)
        rows_after_one = _registry_rows(market_registry)
        _assert_market_rows(rows_after_one, range(1, _STRATEGY_COUNT))
        account_zero_after_one = _host_status(account_hosts[0])
        state_zero_after_one = account_zero_after_one[2]
        assert state_zero_after_one["provider_connect_calls"] == 1
        assert state_zero_after_one["provider_read_calls"] == 1
        assert state_zero_after_one["provider_close_calls"] == 0
        _assert_account_rows(
            _registry_rows(account_registry_paths[0]),
            0,
            range(1, _STRATEGIES_PER_ACCOUNT),
        )
        clients[1]["pipe"].send(("read-own-account",))
        survivor_read = _receive_one(clients[1], 20.0, "account-view")
        assert survivor_read[1] == 1
        assert survivor_read[2] == _ACCOUNT_KEYS[0].to_dict()
        assert survivor_read[3] == f"{_ACCOUNT_REFS[0]}-observation-1"
        assert survivor_read[5] is True

        published.send(("publish", "gw02-common-event-2"))
        common_event_after_one = _receive_one(market_record, 15.0, "published")
        _send_to_all(survivors, "collect", "gw02-common-event-2", 30.0)
        survivor_events = _await_messages(survivors, "events", 45.0, "surviving market event")
        assert len(survivor_events) == _STRATEGY_COUNT - 1
        survivor_fingerprints = []
        for strategy_index, message in survivor_events.items():
            assert message[1] == strategy_index
            assert len(message[4]) == 1
            event = message[4][0]
            assert event[2] == common_event_after_one[2]
            assert event[4]["marker"] == "gw02-common-event-2"
            survivor_fingerprints.append((event[0], event[1], event[2]))
        assert len(set(survivor_fingerprints)) == 1

        _send_to_all(survivors, "close")
        close_messages = _await_messages(survivors, "closed", 90.0, "normal strategy close")
        for strategy_index, message in close_messages.items():
            if (
                message[1] != strategy_index
                or message[3] != "closed"
                or message[4] != 0
                or message[5] != 0
            ):
                raise AssertionError(f"strategy cleanup was not normal: {message!r}")
        for record in survivors.values():
            process = record["process"]
            process.join(30)
            assert not process.is_alive()
            assert process.exitcode == 0
            record["exit_code"] = process.exitcode
            record["normal_exit"] = True
            record["closed"] = True
            record["pipe"].close()
            process.close()

        market_final = _host_status(market_record)
        assert market_final[1]["active_registrations"] == 0
        assert market_final[1]["active_upstreams"] == 0
        assert market_final[1]["pending_releases"] == 0
        assert (market_final[2], market_final[3]) == (1, 1)
        assert _registry_rows(market_registry) == ()
        for account_index, record in account_hosts.items():
            status = _host_status(record)
            state = status[2]
            assert state["provider_connect_calls"] == 1
            assert state["provider_read_calls"] == 1
            assert state["provider_close_calls"] == 0
            assert _registry_rows(account_registry_paths[account_index]) == ()

        market_closed = _close_host(market_record)
        assert market_closed[2:] == ("closed", 1, 1)
        market_record["server_exit_code"] = 0
        for account_index, record in account_hosts.items():
            closed = _close_host(record, expected_index=account_index)
            final_state = closed[4]
            assert closed[3] == "closed"
            assert final_state["worker_state"] == "closed"
            assert final_state["provider_connect_calls"] == 1
            assert final_state["provider_read_calls"] == 1
            assert final_state["provider_close_calls"] == 1
            record["server_exit_code"] = 0

        all_child_pids = [
            market_record["pid"],
            *(record["pid"] for record in account_hosts.values()),
            *(record["pid"] for record in clients.values()),
        ]
        assert len(all_child_pids) == _STRATEGY_COUNT + _SERVER_COUNT
        assert len(set(all_child_pids)) == len(all_child_pids)
        assert all(record["exit_code"] == 0 for record in clients.values())
        assert all(record["server_exit_code"] == 0 for record in account_hosts.values())
        assert market_record["server_exit_code"] == 0
        lifecycle["all_child_pid_count"] = len(all_child_pids)
        lifecycle["all_unique_child_pid_count"] = len(set(all_child_pids))
        lifecycle["normal_strategy_exit_count"] = sum(
            record.get("normal_exit") is True for record in clients.values()
        )
        lifecycle["normal_server_exit_count"] = sum(
            record.get("normal_exit") is True for record in [market_record, *account_hosts.values()]
        )
        lifecycle["market_provider_start_stop"] = [market_final[2], market_final[3]]
        lifecycle["account_provider_session_counts"] = {
            _ACCOUNT_REFS[index]: {
                "connect": 1,
                "initial_read": 1,
                "close": 1,
                "generation": account_statuses[index]["session_generation"],
            }
            for index in range(_ACCOUNT_COUNT)
        }
        lifecycle["strategies"] = [
            {
                "strategy_index": index,
                "pid": record["pid"],
                "loaded_pid": record.get("loaded_pid"),
                "account_ref": _ACCOUNT_REFS[record["account_index"]],
                "ready": record.get("ready", False),
                "started_ns": record.get("ready_details", {}).get("started_ns"),
                "normal_close_ack": record.get("normal_exit", False),
                "exit_code": record.get("exit_code"),
            }
            for index, record in clients.items()
        ]
        lifecycle["servers"] = [
            {
                "kind": record["kind"],
                "pid": record["pid"],
                "account_ref": (
                    _ACCOUNT_REFS[record["account_index"]] if record["kind"] == "account" else None
                ),
                "ready": record.get("ready", False),
                "normal_close_ack": record.get("normal_exit", False),
                "exit_code": record.get("exit_code"),
            }
            for record in [market_record, *account_hosts.values()]
        ]
        lifecycle["cleanup_complete"] = (
            lifecycle["loaded_count"] == _STRATEGY_COUNT
            and lifecycle["ready_count"] == _STRATEGY_COUNT
            and lifecycle["normal_strategy_exit_count"] == _STRATEGY_COUNT
            and lifecycle["normal_server_exit_count"] == _SERVER_COUNT
            and not forced_cleanup
            and not cleanup_failures
        )
        success = True
    finally:
        if not start_event.is_set():
            abort_event.set()
        for record in clients.values():
            _bounded_reap(record, abort_event, forced_cleanup, cleanup_failures)
        if market_record is not None:
            _bounded_reap(market_record, abort_event, forced_cleanup, cleanup_failures)
        for record in account_hosts.values():
            _bounded_reap(record, abort_event, forced_cleanup, cleanup_failures)
        if success:
            lifecycle["cleanup_complete"] = (
                lifecycle["normal_strategy_exit_count"] == _STRATEGY_COUNT
                and lifecycle["normal_server_exit_count"] == _SERVER_COUNT
                and not forced_cleanup
                and not cleanup_failures
            )
        lifecycle["forced_cleanup_requests"] = list(forced_cleanup)
        lifecycle["cleanup_failures"] = list(cleanup_failures)
        lifecycle["strategies"] = [
            {
                "strategy_index": index,
                "pid": record["pid"],
                "loaded_pid": record.get("loaded_pid"),
                "account_ref": _ACCOUNT_REFS[record["account_index"]],
                "ready": record.get("ready", False),
                "started_ns": record.get("ready_details", {}).get("started_ns"),
                "normal_close_ack": record.get("normal_exit", False),
                "exit_code": record.get("exit_code"),
            }
            for index, record in clients.items()
        ]
        lifecycle["servers"] = [
            {
                "kind": record["kind"],
                "pid": record["pid"],
                "account_ref": (
                    _ACCOUNT_REFS[record["account_index"]] if record["kind"] == "account" else None
                ),
                "ready": record.get("ready", False),
                "normal_close_ack": record.get("normal_exit", False),
                "exit_code": record.get("exit_code"),
            }
            for record in [
                *([market_record] if market_record is not None else []),
                *account_hosts.values(),
            ]
        ]
        _write_observation(observation_path, lifecycle)

    if success:
        assert lifecycle["cleanup_complete"] is True, lifecycle
        assert len(lifecycle["strategies"]) == _STRATEGY_COUNT
        assert len(lifecycle["servers"]) == _SERVER_COUNT
        assert lifecycle["forced_cleanup_requests"] == []
        assert lifecycle["cleanup_failures"] == []
        assert lifecycle["normal_strategy_exit_count"] == _STRATEGY_COUNT
        assert lifecycle["normal_server_exit_count"] == _SERVER_COUNT
        assert observation_path.is_file()
