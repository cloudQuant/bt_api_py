"""Two-account, real-process CURVE integration for shared account facts."""

from __future__ import annotations

import multiprocessing
import socket
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import zmq
from bt_api_base.account_protocol import (
    AccountBalance,
    AccountKey,
    AccountObservation,
    AccountPosition,
    AccountResourceKey,
    AccountValuation,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway import GatewayCommandKind, GatewayEventJournal, GatewayPrincipal
from bt_api_gateway.account_session_worker import GatewayAccountSessionWorker
from bt_api_gateway.service_component import GatewayServiceAccountScope
from bt_api_gateway.session_guard import LocalAccountSessionGuard

from bt_api_py.service_account import ServiceAccountClient
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

_DOMAIN_REF = "paper:account-integration"
_STRATEGY_ONE = "strategy-one"
_STRATEGY_TWO = "strategy-two"
_PRINCIPAL_ONE = "principal-one"
_PRINCIPAL_TWO = "principal-two"
_SERVICE_PRINCIPAL = "account-service-principal"
_ACCOUNT_REFS = ("account-a", "account-b")
_ACCOUNT_KEYS = {
    "account-a": AccountKey("synthetic", "loopback", "paper", "owner-a"),
    "account-b": AccountKey("synthetic", "loopback", "paper", "owner-b"),
}
_CROSS_EXCHANGE_ACCOUNT_KEYS = {
    "account-a": AccountKey("synthetic", "exchange-alpha", "paper", "owner-a"),
    "account-b": AccountKey("synthetic", "exchange-beta", "paper", "owner-b"),
}
_SHARED_INSTRUMENT_ID = "instrument-shared"
_SHARED_NATIVE_SYMBOL = "SYNTH-SHARED"
_RESOURCE = AccountResourceKey("spot", "cash")
_REQUIRED_COVERAGE = (_RESOURCE,)
_REQUESTED_OPERATIONS = frozenset({"account.snapshot"})
_REQUEST_TIMEOUT_MS = 1_000
_HEARTBEAT_SECONDS = 5
_MAX_MESSAGE_BYTES = 65_536


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


def _account_service_info(account_ref: str, endpoint: str, security_ref: str) -> dict[str, Any]:
    service_id = f"synthetic-{account_ref}-service"
    instance_id = f"gateway-{account_ref}"
    scope_ref = f"scope-{account_ref}-v1"
    return {
        "account_ref": account_ref,
        "endpoint": endpoint,
        "security_ref": security_ref,
        "service_id": service_id,
        "instance_id": instance_id,
        "scope_ref": scope_ref,
        "server_transport_ref": f"{account_ref}-listener-v1",
        "client_transport_ref": f"{account_ref}-client-v1",
        "principal_ref": _SERVICE_PRINCIPAL,
        "peer": WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, service_id),
    }


def _service_config(service: Mapping[str, Any]) -> dict[str, Any]:
    account_ref = service["account_ref"]
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": service["service_id"],
        "scope_ref": service["scope_ref"],
        "transport_ref": service["server_transport_ref"],
        "registration_policy_ref": f"policy-{account_ref}-v1",
        "service_requirements_ref": f"requirements-{account_ref}-v1",
        "components": {
            "gateway": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": service["instance_id"],
                    "bind_ports": ["service.account_read", "service.provider_session"],
                    "options": {
                        "role": "account_session_worker",
                        "account_session_ref": f"session-{account_ref}-v1",
                    },
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                service["server_transport_ref"]: {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": service["endpoint"]},
                    "security_profile_ref": service["security_ref"],
                    "options": {
                        "high_water_mark": 32,
                        "max_message_bytes": _MAX_MESSAGE_BYTES,
                    },
                }
            },
        },
    }


def _client_config(
    strategy_ref: str,
    services: dict[str, dict[str, Any]],
    account_refs: tuple[str, ...],
) -> dict[str, Any]:
    bindings = []
    profiles = {}
    for account_ref in account_refs:
        service = services[account_ref]
        bindings.append(
            {
                "mount": "store.account",
                "service_id": service["service_id"],
                "instance_id": service["instance_id"],
                "scope_ref": service["scope_ref"],
                "account_ref": account_ref,
                "transport_ref": service["client_transport_ref"],
            }
        )
        profiles[service["client_transport_ref"]] = {
            "backend": "zmq",
            "config_schema_version": 1,
            "endpoints": {"rpc_connect": service["endpoint"]},
            "security_profile_ref": service["security_ref"],
            "options": {
                "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                "high_water_mark": 32,
                "max_message_bytes": _MAX_MESSAGE_BYTES,
            },
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
                        "request_timeout_ms": _REQUEST_TIMEOUT_MS,
                        "heartbeat_interval_seconds": _HEARTBEAT_SECONDS,
                    },
                },
            }
        },
        "communication": {"config_schema_version": 1, "profiles": profiles},
    }


def _resource_grant(
    service: Mapping[str, Any], account_ref: str, strategy_ref: str
) -> ResourceGrant:
    return ResourceGrant(
        service["instance_id"],
        "service.account_read",
        service["scope_ref"],
        WireScope(
            _DOMAIN_REF,
            account_ref=account_ref,
            strategy_ref=strategy_ref,
        ),
        _REQUESTED_OPERATIONS,
    )


def _account_instrument(account_ref: str, account_key: AccountKey) -> MarketInstrumentKey:
    """Default fixture instrument on the account's own synthetic venue."""

    return MarketInstrumentKey(
        provider=account_key.provider,
        venue=account_key.venue,
        environment=account_key.environment,
        product_type=_RESOURCE.product_type,
        instrument_id=f"instrument-{account_ref}",
        native_symbol=f"SYNTH-{account_ref.upper()}",
    )


def _shared_instrument(account_key: AccountKey) -> MarketInstrumentKey:
    """Variant instrument whose identity is deliberately shared by both accounts."""

    return MarketInstrumentKey(
        provider=account_key.provider,
        venue=account_key.venue,
        environment=account_key.environment,
        product_type=_RESOURCE.product_type,
        instrument_id=_SHARED_INSTRUMENT_ID,
        native_symbol=_SHARED_NATIVE_SYMBOL,
    )


class _SyntheticAccountProvider:
    """Counted local provider fixture with only connect, full-read, and close."""

    def __init__(
        self,
        account_ref: str,
        account_key: AccountKey,
        instrument: MarketInstrumentKey,
    ) -> None:
        self.account_ref = account_ref
        self.account_key = account_key
        self.instrument = instrument
        self.connect_calls = 0
        self.read_calls = 0
        self.close_calls = 0
        self.generation: str | None = None

    def connect(self, *, account_key: AccountKey, generation: str, timeout: float) -> None:
        if account_key != self.account_key or not generation or timeout <= 0:
            raise AssertionError("provider received invalid synthetic session metadata")
        if self.generation is not None:
            raise AssertionError("a second provider session was opened")
        self.connect_calls += 1
        self.generation = generation

    def read_account(self, *, generation: str, timeout: float) -> AccountObservation:
        if generation != self.generation or timeout <= 0:
            raise AssertionError("provider read did not use its active synthetic generation")
        self.read_calls += 1
        return _observation(self.account_ref, self.read_calls, self.account_key, self.instrument)

    def close(self) -> bool:
        self.close_calls += 1
        self.generation = None
        return True


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _observation(
    account_ref: str,
    sequence: int,
    account_key: AccountKey,
    instrument: MarketInstrumentKey,
) -> AccountObservation:
    observed_at = _utc_now()
    if account_ref == "account-a":
        total, available, locked = (
            ("100.00", "80.00", "20.00") if sequence == 1 else ("101.25", "81.25", "20.00")
        )
    else:
        total, available, locked = "700.00", "650.00", "50.00"
    balances = (
        AccountBalance(
            resource=_RESOURCE,
            currency="USD",
            total=total,
            available=available,
            locked=locked,
            observed_at_utc=observed_at,
        ),
    )
    positions: tuple[AccountPosition, ...] = ()
    if account_ref == "account-b" or sequence == 1:
        positions = (
            AccountPosition(
                resource=_RESOURCE,
                instrument=instrument,
                side="long",
                quantity="2.500" if account_ref == "account-a" else "7",
                qty_unit="unit",
                average_price="10.25" if account_ref == "account-a" else "4.50",
                price_currency="USD",
                observed_at_utc=observed_at,
            ),
        )
    valuation = AccountValuation(
        currency="USD",
        available_cash=available,
        equity=total,
        observed_at_utc=observed_at,
    )
    return AccountObservation(
        account_key=account_key,
        observation_id=f"{account_ref}-observation-{sequence}",
        coverage=_REQUIRED_COVERAGE,
        balances=balances,
        positions=positions,
        valuation=valuation,
        started_at_utc=observed_at,
        completed_at_utc=observed_at,
        consistency="provider_atomic",
        balances_complete=True,
        positions_complete=True,
    )


def _host_status(host, worker, provider) -> dict[str, Any]:
    snapshot = worker.latest_snapshot
    return {
        "host_state": host.state,
        "worker_state": worker.state,
        "worker_ready": worker.ready,
        "generation": worker.session_generation,
        "snapshot_id": None if snapshot is None else snapshot.snapshot_id,
        "cursor": None if snapshot is None else snapshot.cursor,
        "connect_calls": provider.connect_calls,
        "read_calls": provider.read_calls,
        "close_calls": provider.close_calls,
    }


def _service_host_process(
    control,
    account_ref: str,
    account_key: AccountKey,
    instrument: MarketInstrumentKey,
    raw_config: dict[str, Any],
    registry_path: str,
    database_path: str,
    guard_directory: str,
    server_keys: tuple[bytes, bytes],
    client_audience: tuple[tuple[bytes, str, str, bool], ...],
) -> None:
    """Spawn target: one ServiceHost and one account-owned synthetic session."""

    host = None
    worker = None
    provider = _SyntheticAccountProvider(account_ref, account_key, instrument)
    try:
        config = parse_service_config(raw_config)
        service = {
            "account_ref": account_ref,
            "service_id": config.service_id,
            "instance_id": "gateway-" + account_ref,
            "scope_ref": config.scope_ref,
        }
        authorized_strategies = frozenset(
            strategy_ref
            for _public, _principal, strategy_ref, authorized in client_audience
            if authorized
        )
        journal_scope = f"journal:{account_ref}"
        state_scope = f"state:{account_ref}"
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
            for _public, principal_ref, _strategy, authorized in client_audience
            if authorized
        }
        holder: dict[str, GatewayAccountSessionWorker] = {}

        def resources(component, _instance_id, options, _scope):
            if component != "gateway" or options.get("role") != "account_session_worker":
                raise AssertionError("only the selected account session gateway is expected")
            journal = GatewayEventJournal(database_path)
            session_guard = LocalAccountSessionGuard(
                Path(guard_directory),
                account_key.account_key_id,
                owner_ref=f"host-{account_ref}",
            )
            session_worker = GatewayAccountSessionWorker(
                provider,
                journal,
                account_key=account_key,
                journal_account_scope=journal_scope,
                state_scope=state_scope,
                guard=session_guard,
                required_coverage=_REQUIRED_COVERAGE,
                refresh_interval_seconds=60,
                max_staleness_seconds=120,
                request_timeout_seconds=2,
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

        identities = {
            principal_ref: WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)
            for _public, principal_ref, strategy_ref, _authorized in client_audience
        }
        grants = {}
        for _public, principal_ref, strategy_ref, authorized in client_audience:
            grants[principal_ref] = (
                (
                    PrincipalGrant(
                        identities[principal_ref],
                        (_resource_grant(service, account_ref, strategy_ref),),
                    ),
                )
                if authorized
                else ()
            )
        service_scope = WireScope(_DOMAIN_REF)
        host = ServiceHost(
            config,
            TrustedServiceAuthority(
                identity=WireIdentity(WireRole.SERVICE, _SERVICE_PRINCIPAL, config.service_id),
                scope=service_scope,
                scope_ref=config.scope_ref,
                scope_version=f"scope-{account_ref}-v1",
                security_ref=f"curve-{account_ref}-v1",
                security_version="curve-v1",
                registration_policy_ref=config.registration_policy_ref,
                registration_policy_version=f"policy-{account_ref}-v1",
                service_requirements_ref=config.service_requirements_ref,
                service_requirements_version=f"requirements-{account_ref}-v1",
                curve_public_key=server_keys[0],
                curve_secret_key=server_keys[1],
                principals={
                    public: principal for public, principal, _strategy, _ok in client_audience
                },
                grants=grants,
            ),
            registry_path,
            resources,
        )
        host.start()
        worker = holder["worker"]
        control.send(("ready", account_ref, host.endpoint, _host_status(host, worker, provider)))
        while True:
            host.serve_once(10)
            if not control.poll():
                continue
            command = control.recv()
            action = command[0]
            if action == "status":
                control.send(("status", account_ref, _host_status(host, worker, provider)))
            elif action == "refresh":
                snapshot = worker.refresh()
                control.send(
                    (
                        "refreshed",
                        account_ref,
                        snapshot.snapshot_id,
                        snapshot.cursor,
                        _host_status(host, worker, provider),
                    )
                )
            elif action == "close":
                host.close()
                control.send(("closed", account_ref, _host_status(host, worker, provider)))
                return
            else:
                raise AssertionError("unknown account host control request")
    except BaseException as exc:
        if host is not None and host.state not in {"closed", "failed"}:
            with suppress(BaseException):
                host.close()
        with suppress(Exception):
            control.send(("error", account_ref, getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _view_payload(view, facade, *, same_as_previous: bool) -> dict[str, Any]:
    return {
        "account_key": view.observation.account_key.to_dict(),
        "observation": view.observation.to_dict(),
        "cursor": view.cursor,
        "snapshot_id": view.snapshot_id,
        "session_generation": view.session_generation,
        "fresh": facade.healthy,
        "same_as_previous": same_as_previous,
    }


def _strategy_client_process(
    control,
    principal_ref: str,
    strategy_ref: str,
    raw_config: dict[str, Any],
    client_keys: dict[str, tuple[bytes, bytes, bytes]],
    services: dict[str, dict[str, Any]],
    account_refs: tuple[str, ...],
    account_keys: Mapping[str, AccountKey],
) -> None:
    """Spawn target: one strategy runtime with account-specific read facades."""

    runtime = None
    facades: dict[str, ServiceAccountClient] = {}
    latest_views: dict[str, object] = {}
    try:
        config = parse_client_config(raw_config)
        identity = WireIdentity(WireRole.STRATEGY_CLIENT, principal_ref, strategy_ref)
        scopes = {
            services[account_ref]["scope_ref"]: WireScope(
                _DOMAIN_REF,
                account_ref=account_ref,
            )
            for account_ref in account_refs
        }
        peers = {
            services[account_ref]["service_id"]: services[account_ref]["peer"]
            for account_ref in account_refs
        }
        security_profiles = {
            services[account_ref]["security_ref"]: ClientCurveKeys(*client_keys[account_ref])
            for account_ref in account_refs
        }
        runtime = ServiceClientRuntime(
            config,
            identity=identity,
            scope_resolver=lambda scope_ref: scopes[scope_ref],
            peer_resolver=lambda service_id: peers[service_id],
            transport_factory=ZmqTransportFactory(security_profiles),
            requested_operations={"store.account": _REQUESTED_OPERATIONS},
        )
        runtime.start()
        for account_ref in account_refs:
            facades[account_ref] = ServiceAccountClient(
                runtime,
                "store.account",
                account_ref=account_ref,
                expected_account=account_keys[account_ref],
                required_coverage=_REQUIRED_COVERAGE,
            )
        registered = tuple(
            sorted(
                (binding.scope.account_ref, binding.registration_id) for binding in runtime.bindings
            )
        )
        control.send(("ready", strategy_ref, registered, runtime.healthy, runtime.run_id))

        while True:
            delay = runtime.next_maintenance_delay()
            if delay is not None and delay <= 0:
                runtime.maintain()
            if not control.poll(0.05):
                continue
            command = control.recv()
            action = command[0]
            if action == "read":
                account_ref = command[1]
                facade = facades[account_ref]
                view = facade.refresh()
                same = latest_views.get(account_ref) is view
                latest_views[account_ref] = view
                control.send(
                    ("view", account_ref, _view_payload(view, facade, same_as_previous=same))
                )
            elif action == "cache":
                account_ref = command[1]
                facade = facades[account_ref]
                view = facade.snapshot()
                same = latest_views.get(account_ref) is view
                latest_views[account_ref] = view
                control.send(
                    ("view", account_ref, _view_payload(view, facade, same_as_previous=same))
                )
            elif action == "route-b":
                try:
                    runtime.call(
                        "store.account",
                        "account.snapshot",
                        {"limit": 1},
                        account_ref="account-b",
                        scope_ref=services["account-b"]["scope_ref"],
                    )
                except ServiceRuntimeError as exc:
                    control.send(("route-denied", exc.code, runtime.healthy))
                else:
                    control.send(("route-allowed",))
            elif action == "route-ambiguous":
                try:
                    runtime.call("store.account", "account.snapshot", {"limit": 1})
                except ServiceRuntimeError as exc:
                    control.send(("route-ambiguous-denied", exc.code, runtime.healthy))
                else:
                    control.send(("route-ambiguous-accepted",))
            elif action == "close":
                for facade in facades.values():
                    facade.close()
                runtime.close()
                control.send(
                    (
                        "closed",
                        runtime.state,
                        runtime.cleanup_failures,
                        runtime.unregister_uncertainties,
                        runtime.healthy,
                    )
                )
                return
            else:
                raise AssertionError("unknown strategy client control request")
    except BaseException as exc:
        if runtime is not None:
            with suppress(BaseException):
                runtime.close()
        with suppress(Exception):
            control.send(("error", strategy_ref, getattr(exc, "code", type(exc).__name__)))
        raise
    finally:
        control.close()


def _start_process(context, target, args, label: str) -> dict[str, Any]:
    parent, child = context.Pipe(duplex=True)
    process = context.Process(target=target, args=(child, *args), name=label)
    record = {"process": process, "pipe": parent, "label": label, "closed": False}
    try:
        process.start()
    except BaseException:
        parent.close()
        child.close()
        process.close()
        record["closed"] = True
        raise
    child.close()
    return record


def _receive(pipe, timeout: float = 15.0):
    assert pipe.poll(timeout), "spawned account integration process missed its response bound"
    return pipe.recv()


def _host_command(record: dict[str, Any], *command):
    record["pipe"].send(command)
    result = _receive(record["pipe"])
    assert result[0] != "error", result
    return result


def _client_command(record: dict[str, Any], *command):
    process = record["process"]
    assert process.is_alive(), f"{record['label']} exited before its command"
    record["pipe"].send(command)
    result = _receive(record["pipe"])
    assert result[0] != "error", result
    return result


def _close_normally(record: dict[str, Any], *, expected_tag: str) -> tuple[Any, ...]:
    process = record["process"]
    assert process.is_alive(), f"{record['label']} exited before normal close"
    record["pipe"].send(("close",))
    result = _receive(record["pipe"], timeout=15.0)
    assert result[0] == expected_tag, result
    process.join(15)
    assert not process.is_alive(), f"{record['label']} did not exit normally within its bound"
    assert process.exitcode == 0, f"{record['label']} exited with code {process.exitcode}"
    record["pipe"].close()
    process.close()
    record["closed"] = True
    return result


def _bounded_reap_spawned_process(record: dict[str, Any], forced, failures) -> None:
    """Try protocol close and join before fallback, for this test's process only."""

    if record["closed"]:
        return
    process, pipe = record["process"], record["pipe"]

    def alive() -> bool:
        try:
            return process.is_alive()
        except Exception as exc:
            failures.append(f"{record['label']}:state:{type(exc).__name__}")
            return False

    if alive():
        try:
            pipe.send(("close",))
        except Exception as exc:
            failures.append(f"{record['label']}:normal_close:{type(exc).__name__}")
    try:
        process.join(5)
    except Exception as exc:
        failures.append(f"{record['label']}:normal_join:{type(exc).__name__}")
    if alive():
        forced.append(record["label"])
        try:
            process.terminate()
        except Exception as exc:
            failures.append(f"{record['label']}:terminate:{type(exc).__name__}")
        try:
            process.join(5)
        except Exception as exc:
            failures.append(f"{record['label']}:terminate_join:{type(exc).__name__}")
    if alive():
        try:
            process.kill()
        except Exception as exc:
            failures.append(f"{record['label']}:kill:{type(exc).__name__}")
        try:
            process.join(5)
        except Exception as exc:
            failures.append(f"{record['label']}:kill_join:{type(exc).__name__}")
    still_alive = alive()
    try:
        pipe.close()
    except Exception as exc:
        failures.append(f"{record['label']}:pipe_close:{type(exc).__name__}")
    if still_alive:
        failures.append(f"{record['label']}:still_alive_after_kill")
        return
    try:
        process.close()
    except Exception as exc:
        failures.append(f"{record['label']}:process_close:{type(exc).__name__}")
    record["closed"] = True


def _assert_no_fallback_cleanup(forced, failures) -> None:
    if forced or failures:
        raise AssertionError(
            f"spawned account integration needed fallback cleanup: "
            f"forced={tuple(forced)}, failures={tuple(failures)}"
        )


def _direct_runtime(
    raw_config: dict[str, Any],
    identity: WireIdentity,
    service: dict[str, Any],
    client_curve: tuple[bytes, bytes],
) -> ServiceClientRuntime:
    parsed = parse_client_config(raw_config)
    scope = WireScope(_DOMAIN_REF, account_ref=service["account_ref"])
    keys = ClientCurveKeys(service["server_key"], client_curve[0], client_curve[1])
    return ServiceClientRuntime(
        parsed,
        identity=identity,
        scope_resolver=lambda _scope_ref: scope,
        peer_resolver=lambda _service_id: service["peer"],
        transport_factory=ZmqTransportFactory({service["security_ref"]: keys}),
        requested_operations={"store.account": _REQUESTED_OPERATIONS},
    )


def _assert_registration_denied(runtime: ServiceClientRuntime, expected_code: str) -> None:
    try:
        runtime.start()
    except ServiceRuntimeError as exc:
        assert exc.code == expected_code, exc.code
    else:
        raise AssertionError("an unauthorized strategy principal registered successfully")
    finally:
        runtime.close()


def _summary(view: dict[str, Any]) -> tuple[Any, ...]:
    observation = view["observation"]
    return (
        view["snapshot_id"],
        view["cursor"],
        view["session_generation"],
        observation,
    )


def _exercise_shared_account_variant(
    tmp_path,
    *,
    account_keys: Mapping[str, AccountKey],
    instruments: Mapping[str, MarketInstrumentKey],
    expect_cross_exchange: bool,
    expect_shared_instrument: bool,
) -> None:
    """Shared GW01 scenario body: two real account hosts and two strategies."""

    context = multiprocessing.get_context("spawn")
    endpoints = _free_endpoints(2)
    server_keys = {ref: zmq.curve_keypair() for ref in _ACCOUNT_REFS}
    client_keys = {ref: zmq.curve_keypair() for ref in ("client-one", "client-two")}
    account_services = {
        ref: _account_service_info(ref, endpoints[index], f"curve-{ref}-v1")
        for index, ref in enumerate(_ACCOUNT_REFS)
    }
    for ref in _ACCOUNT_REFS:
        account_services[ref]["server_key"] = server_keys[ref][0]
    host_audience = (
        (client_keys["client-one"][0], _PRINCIPAL_ONE, _STRATEGY_ONE, True),
        (client_keys["client-two"][0], _PRINCIPAL_TWO, _STRATEGY_TWO, True),
    )
    host_b_audience = (
        (client_keys["client-one"][0], _PRINCIPAL_ONE, _STRATEGY_ONE, True),
        (client_keys["client-two"][0], _PRINCIPAL_TWO, _STRATEGY_TWO, False),
    )
    service_raw = {ref: _service_config(service) for ref, service in account_services.items()}
    client_raw = {
        "client-one": _client_config(_STRATEGY_ONE, account_services, _ACCOUNT_REFS),
        "client-two": _client_config(_STRATEGY_TWO, account_services, ("account-a",)),
    }
    records: list[dict[str, Any]] = []
    forced_cleanup: list[str] = []
    cleanup_failures: list[str] = []
    host_records: dict[str, dict[str, Any]] = {}
    client_records: dict[str, dict[str, Any]] = {}
    try:
        (tmp_path / "session-guards").mkdir()
        for index, account_ref in enumerate(_ACCOUNT_REFS):
            audience = host_audience if account_ref == "account-a" else host_b_audience
            record = _start_process(
                context,
                _service_host_process,
                (
                    account_ref,
                    account_keys[account_ref],
                    instruments[account_ref],
                    service_raw[account_ref],
                    str(tmp_path / f"{account_ref}-registry.sqlite"),
                    str(tmp_path / f"{account_ref}-journal.sqlite"),
                    str(tmp_path / "session-guards"),
                    server_keys[account_ref],
                    audience,
                ),
                f"account-host-{account_ref}",
            )
            records.append(record)
            ready = _receive(record["pipe"], timeout=15.0)
            assert ready[0] == "ready" and ready[1] == account_ref, ready
            assert ready[2] == endpoints[index], ready
            assert ready[3]["host_state"] == "started" and ready[3]["worker_ready"] is True
            assert ready[3]["connect_calls"] == 1 and ready[3]["read_calls"] == 1
            assert ready[3]["close_calls"] == 0
            host_records[account_ref] = record

        for key_name, principal_ref, strategy_ref, account_refs in (
            ("client-one", _PRINCIPAL_ONE, _STRATEGY_ONE, _ACCOUNT_REFS),
            ("client-two", _PRINCIPAL_TWO, _STRATEGY_TWO, ("account-a",)),
        ):
            record = _start_process(
                context,
                _strategy_client_process,
                (
                    principal_ref,
                    strategy_ref,
                    client_raw[key_name],
                    {
                        "account-a": (
                            account_services["account-a"]["server_key"],
                            client_keys[key_name][0],
                            client_keys[key_name][1],
                        ),
                        **(
                            {
                                "account-b": (
                                    account_services["account-b"]["server_key"],
                                    client_keys[key_name][0],
                                    client_keys[key_name][1],
                                )
                            }
                            if "account-b" in account_refs
                            else {}
                        ),
                    },
                    account_services,
                    account_refs,
                    {ref: account_keys[ref] for ref in account_refs},
                ),
                f"strategy-client-{key_name}",
            )
            records.append(record)
            ready = _receive(record["pipe"], timeout=15.0)
            assert ready[0] == "ready" and ready[1] == strategy_ref, ready
            assert ready[3] is True and ready[4], ready
            registered_accounts = {account for account, _registration in ready[2]}
            assert registered_accounts == set(account_refs), ready
            assert len({registration for _account, registration in ready[2]}) == len(account_refs)
            client_records[key_name] = record

        first_one_a = _client_command(client_records["client-one"], "read", "account-a")
        first_one_b = _client_command(client_records["client-one"], "read", "account-b")
        first_two_a = _client_command(client_records["client-two"], "read", "account-a")
        assert first_one_a[0] == first_one_b[0] == first_two_a[0] == "view"
        one_a, one_b, two_a = first_one_a[2], first_one_b[2], first_two_a[2]
        assert one_a["fresh"] and one_b["fresh"] and two_a["fresh"]
        assert _summary(one_a) == _summary(two_a)
        assert one_a["account_key"] == two_a["account_key"] == account_keys["account-a"].to_dict()
        assert one_b["account_key"] == account_keys["account-b"].to_dict()
        assert one_a["account_key"] != one_b["account_key"]
        assert one_a["session_generation"] != one_b["session_generation"]
        assert one_a["observation"]["balances"][0]["total"] == "100.00"
        assert one_b["observation"]["balances"][0]["total"] == "700.00"
        assert one_a["observation"]["positions"][0]["quantity"] == "2.500"
        assert one_b["observation"]["positions"][0]["quantity"] == "7"
        assert type(one_a["observation"]["balances"][0]["total"]) is str
        assert first_one_a[2]["same_as_previous"] is False

        # Fixture-dimension checks: both providers stay synthetic; the accounts
        # either live on different venues or share one venue and one instrument
        # identity, while account facts stay keyed by the full account key only.
        assert (
            account_keys["account-a"].provider == account_keys["account-b"].provider == "synthetic"
        )
        first_instrument_a = one_a["observation"]["positions"][0]["instrument"]
        first_instrument_b = one_b["observation"]["positions"][0]["instrument"]
        if expect_cross_exchange:
            assert account_keys["account-a"].venue != account_keys["account-b"].venue
            assert one_a["account_key"]["venue"] != one_b["account_key"]["venue"]
        else:
            assert account_keys["account-a"].venue == account_keys["account-b"].venue
        if expect_shared_instrument:
            assert first_instrument_a == first_instrument_b
        else:
            assert first_instrument_a != first_instrument_b

        route_denied = _client_command(client_records["client-two"], "route-b")
        assert route_denied == ("route-denied", "AMBIGUOUS_OR_MISSING_ROUTE", True)

        # Strategy one owns two same-mount bindings; an unqualified call cannot
        # pick a route and is rejected before any wire or provider activity.
        ambiguous = _client_command(client_records["client-one"], "route-ambiguous")
        assert ambiguous == ("route-ambiguous-denied", "AMBIGUOUS_OR_MISSING_ROUTE", True)

        denied_b_config = _client_config(_STRATEGY_TWO, account_services, ("account-b",))
        denied_b_runtime = _direct_runtime(
            denied_b_config,
            WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL_TWO, _STRATEGY_TWO),
            {
                **account_services["account-b"],
                "server_key": account_services["account-b"]["server_key"],
            },
            client_keys["client-two"],
        )
        _assert_registration_denied(denied_b_runtime, "DENIED")

        unknown_public, unknown_secret = zmq.curve_keypair()
        unknown_config = _client_config("strategy-unknown", account_services, ("account-a",))
        unknown_runtime = _direct_runtime(
            unknown_config,
            WireIdentity(WireRole.STRATEGY_CLIENT, "principal-unknown", "strategy-unknown"),
            {
                **account_services["account-a"],
                "server_key": account_services["account-a"]["server_key"],
            },
            (unknown_public, unknown_secret),
        )
        try:
            try:
                unknown_runtime.start()
            except ServiceRuntimeError as exc:
                # IMMEDIATE prevents enqueueing before the rejected CURVE
                # handshake completes; the SDK preserves the not-sent result.
                assert exc.code == "REQUEST_NOT_SENT", exc.code
            else:
                raise AssertionError("a CURVE key absent from the trusted principal map connected")
        finally:
            unknown_runtime.close()

        status_a = _host_command(host_records["account-a"], "status")
        status_b = _host_command(host_records["account-b"], "status")
        assert status_a[2]["connect_calls"] == status_a[2]["read_calls"] == 1
        assert status_b[2]["connect_calls"] == status_b[2]["read_calls"] == 1
        assert status_a[2]["close_calls"] == status_b[2]["close_calls"] == 0

        # Upstream sharing unchanged: two readers on account-a (strategies one
        # and two) and one reader on account-b still leave exactly one active
        # provider session for each account key; every rejected call above
        # added no provider read.
        active_upstreams = {
            account_keys[account_ref]: status[2]["connect_calls"] - status[2]["close_calls"]
            for account_ref, status in (("account-a", status_a), ("account-b", status_b))
        }
        assert len(active_upstreams) == len(_ACCOUNT_REFS), active_upstreams
        assert all(count == 1 for count in active_upstreams.values()), active_upstreams

        refreshed = _host_command(host_records["account-a"], "refresh")
        assert refreshed[0] == "refreshed" and refreshed[1] == "account-a", refreshed
        assert refreshed[4]["worker_ready"] is True
        assert refreshed[4]["connect_calls"] == 1
        assert refreshed[4]["read_calls"] == 2
        assert refreshed[4]["close_calls"] == 0
        assert refreshed[2] != one_a["snapshot_id"] and refreshed[3] > one_a["cursor"]

        cached_old = _client_command(client_records["client-one"], "cache", "account-a")[2]
        cached_b = _client_command(client_records["client-one"], "cache", "account-b")[2]
        assert cached_old["snapshot_id"] == one_a["snapshot_id"]
        assert cached_old["cursor"] == one_a["cursor"]
        assert cached_old["same_as_previous"] is True and cached_old["fresh"] is True
        assert cached_b["snapshot_id"] == one_b["snapshot_id"]

        new_one_a = _client_command(client_records["client-one"], "read", "account-a")[2]
        new_two_a = _client_command(client_records["client-two"], "read", "account-a")[2]
        assert _summary(new_one_a) == _summary(new_two_a)
        assert new_one_a["snapshot_id"] == refreshed[2]
        assert new_one_a["cursor"] == refreshed[3]
        assert new_one_a["session_generation"] == one_a["session_generation"]
        assert new_one_a["observation"]["observation_id"] == "account-a-observation-2"
        assert new_one_a["observation"]["balances"][0]["total"] == "101.25"
        assert new_one_a["observation"]["positions"] == []
        assert new_one_a["observation"]["balances_complete"] is True
        assert new_one_a["observation"]["positions_complete"] is True
        assert new_one_a["cursor"] > one_a["cursor"]
        assert new_one_a["snapshot_id"] != one_a["snapshot_id"]

        closed_two = _close_normally(client_records["client-two"], expected_tag="closed")
        assert closed_two[1] == "closed" and closed_two[2] == 0 and closed_two[3] == 0
        still_shared = _client_command(client_records["client-one"], "read", "account-a")[2]
        assert still_shared["snapshot_id"] == new_one_a["snapshot_id"]
        assert still_shared["observation"]["balances"][0]["total"] == "101.25"
        after_s2 = _host_command(host_records["account-a"], "status")[2]
        assert after_s2["worker_ready"] is True
        assert after_s2["connect_calls"] == 1 and after_s2["read_calls"] == 2
        assert after_s2["close_calls"] == 0

        closed_one = _close_normally(client_records["client-one"], expected_tag="closed")
        assert closed_one[1] == "closed" and closed_one[2] == 0 and closed_one[3] == 0
        for account_ref in _ACCOUNT_REFS:
            status = _host_command(host_records[account_ref], "status")[2]
            assert status["worker_ready"] is True
            assert status["connect_calls"] == 1
            assert status["read_calls"] == (2 if account_ref == "account-a" else 1)
            assert status["close_calls"] == 0

        for account_ref in _ACCOUNT_REFS:
            closed_host = _close_normally(host_records[account_ref], expected_tag="closed")
            host_status = closed_host[2]
            assert host_status["host_state"] == "closed"
            assert host_status["worker_state"] == "closed"
            assert host_status["worker_ready"] is False
            assert host_status["connect_calls"] == 1
            assert host_status["read_calls"] == (2 if account_ref == "account-a" else 1)
            assert host_status["close_calls"] == 1
            assert host_status["connect_calls"] - host_status["close_calls"] == 0
    finally:
        for record in reversed(records):
            _bounded_reap_spawned_process(record, forced_cleanup, cleanup_failures)
        _assert_no_fallback_cleanup(forced_cleanup, cleanup_failures)


def test_two_account_hosts_share_full_readonly_snapshots_without_strategy_owned_sessions(tmp_path):
    """Two real account hosts serve shared full replacements over CURVE RPC."""

    _exercise_shared_account_variant(
        tmp_path,
        account_keys=_ACCOUNT_KEYS,
        instruments={ref: _account_instrument(ref, _ACCOUNT_KEYS[ref]) for ref in _ACCOUNT_REFS},
        expect_cross_exchange=False,
        expect_shared_instrument=False,
    )


def test_cross_exchange_accounts_share_readonly_snapshots_without_strategy_owned_sessions(tmp_path):
    """Variant (a): the two accounts live on different synthetic exchanges."""

    _exercise_shared_account_variant(
        tmp_path,
        account_keys=_CROSS_EXCHANGE_ACCOUNT_KEYS,
        instruments={
            ref: _account_instrument(ref, _CROSS_EXCHANGE_ACCOUNT_KEYS[ref])
            for ref in _ACCOUNT_REFS
        },
        expect_cross_exchange=True,
        expect_shared_instrument=False,
    )


def test_same_venue_same_symbol_accounts_share_readonly_snapshots_without_strategy_owned_sessions(
    tmp_path,
):
    """Variant (b): two accounts on one venue sharing one instrument identity."""

    _exercise_shared_account_variant(
        tmp_path,
        account_keys=_ACCOUNT_KEYS,
        instruments={ref: _shared_instrument(_ACCOUNT_KEYS[ref]) for ref in _ACCOUNT_REFS},
        expect_cross_exchange=False,
        expect_shared_instrument=True,
    )
