"""Process entry of the independent Risk service (iteration 64, 64.09): ``bt-risk-service``.

One process per account (D64-15). Start-up order, every step failing closed with
a finite ``RISK_SERVICE_*`` code:

1. load the service configuration (``bt_api_risk.service.v1``);
2. open the Risk SQLite file (``RiskOrderJournal``; pre-trade ledger and
   delegate journal share it) and hold the account writer fence for the
   process lifetime; refuse when another process holds it;
3. build the engine and bootstrap the policy file (first version, or a pure
   tightening of the effective one; a loosening must be published by an
   operator with a reason, D64-12);
4. start the Gateway runtimes and check that the Gateway granted what this
   configuration needs (observer, control, and market/account snapshot when
   configured, freeze 6.3); delegate grants are per (strategy, live run) and can
   only be checked once that run exists, on first use;
5. ``engine.recover()``, replay the observation stream, start the ServiceHost;
6. loop: ``serve_once``, observation poll, market/account refresh,
   ``engine.maintain()``; SIGTERM/SIGINT stop the loop and close everything.

Commands: ``run --config <file>``, ``validate-policy <file>`` (offline, no
account) and ``status --config <file>`` (local ledger summary, no Gateway).
Output never contains keys, account numbers or file paths.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from bt_api_base.market_protocol import MarketSubscriptionKey
from bt_api_base.risk_service_protocol import (
    RISK_ADMIN_OPERATIONS,
    RISK_ADMIN_PORT,
    RISK_ORDER_OPERATIONS,
    RISK_SERVICE_PORT,
)
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.risk_service_config import (
    RiskServiceConfig,
    RiskServiceConfigError,
    ensure_private_directory,
    load_risk_service_config,
    resolve_path,
    restrict_file,
)

POLICY_LABELS = {
    "scope_version": "risk-scope-v1",
    "security_ref": "risk-curve",
    "security_version": "risk-curve-v1",
    "registration_policy_ref": "risk-order-policy-v1",
    "registration_policy_version": "risk-order-policy-v1",
    "service_requirements_ref": "risk-order-requirements-v1",
    "service_requirements_version": "risk-order-requirements-v1",
}
OBSERVER_OPERATIONS = frozenset(
    {"account.events.read_after", "account.events.progress", "account.events.ack"}
)
DELEGATE_OPERATIONS = frozenset(
    {
        "direct.delegated_reserve",
        "direct.delegated_dispatch",
        "direct.delegate_actor_status",
        "direct.delegate_actor_cancel",
        "direct.delegate_actor_cancel_status",
        "direct.delegate_actor_release",
        "direct.delegate_actor_release_status",
    }
)
_GATEWAY_SECURITY = "gateway-curve"
_LOG = logging.getLogger(__name__)


class RiskServiceStartError(RuntimeError):
    """Finite refusal to start or keep running; carries no value."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RiskServiceStartError(code)


# ----------------------------------------------------------------------------- fence probe


def probe_writer_fence(journal: Any, scope: Any) -> None:
    """Refuse at once when another process holds this account's writer fence.

    ``RiskOrderJournal.writer_fence`` waits for the OS lock; a service must not
    wait for a second instance. The probe takes and drops the same lock
    without blocking; a race between probe and acquisition is still serialized
    by the journal's own lock (the loser then waits rather than writing).
    """
    lock_path = getattr(journal, "_lock_path", None)
    if not callable(lock_path):
        _fail("RISK_SERVICE_FENCE_PROBE_UNAVAILABLE")
    path = Path(lock_path(scope))
    try:
        handle = open(path, "a+b")  # noqa: SIM115 - closed below
    except OSError:
        _fail("RISK_SERVICE_STORAGE_UNAVAILABLE")
    try:
        if os.name == "nt":  # pragma: no cover - not exercised on POSIX CI
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                _fail("RISK_SERVICE_FENCE_HELD")
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                _fail("RISK_SERVICE_FENCE_HELD")
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
    restrict_file(path)


# ----------------------------------------------------------------------------- configs


def service_config_document(config: RiskServiceConfig) -> dict:
    """The Risk ServiceHost manifest (no keys; keys go to the trusted authority)."""
    return {
        "service_config_schema_version": 1,
        "process_role": "service",
        "service_id": config.service_id,
        "scope_ref": config.scope_ref,
        "transport_ref": "risk-server",
        "registration_policy_ref": POLICY_LABELS["registration_policy_ref"],
        "service_requirements_ref": POLICY_LABELS["service_requirements_ref"],
        "components": {
            "risk": {
                "config_schema_version": 2,
                "settings": {
                    "instance_id": config.instance_id,
                    "bind_ports": [RISK_SERVICE_PORT, RISK_ADMIN_PORT],
                    "options": {"role": "order_service"},
                },
            }
        },
        "communication": {
            "config_schema_version": 1,
            "profiles": {
                "risk-server": {
                    "backend": "zmq",
                    "config_schema_version": 1,
                    "endpoints": {"rpc_bind": config.rpc_bind},
                    "security_profile_ref": POLICY_LABELS["security_ref"],
                }
            },
        },
    }


def _gateway_client_config(config: RiskServiceConfig, mount: str, *, market: bool = False):
    """A SERVICE-role client manifest for one Gateway mount of the Risk principal."""
    from bt_api_py.service_config import parse_service_config

    raw = service_config_document(config)
    gateway = config.gateway
    binding = {
        "mount": mount,
        "service_id": gateway.service_id,
        "instance_id": gateway.instance_id,
        "scope_ref": gateway.scope_ref,
        "transport_ref": "gateway-client",
    }
    if not market:
        binding["account_ref"] = config.account_scope
    endpoints = {"rpc_connect": gateway.rpc_endpoint}
    if market:
        endpoints["market_connect"] = gateway.market_endpoint
    raw["clients"] = {"gateway": {"config_schema_version": 1, "settings": {"bindings": [binding]}}}
    raw["communication"]["profiles"]["gateway-client"] = {
        "backend": "zmq",
        "config_schema_version": 1,
        "endpoints": endpoints,
        "security_profile_ref": _GATEWAY_SECURITY,
    }
    return parse_service_config(raw)


def _control_client_config(config: RiskServiceConfig, mounts: Sequence[str]):
    from bt_api_py.service_config import parse_client_config

    gateway = config.gateway
    bindings = [
        {
            "mount": mount,
            "service_id": gateway.service_id,
            "instance_id": gateway.instance_id,
            "scope_ref": gateway.scope_ref,
            "account_ref": config.account_scope,
            "transport_ref": "gateway",
        }
        for mount in mounts
    ]
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "application_client",
            "registration": {"application_ref": gateway.control.actor_ref},
            "clients": {"gateway": {"config_schema_version": 1, "settings": {"bindings": bindings}}},
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "gateway": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": gateway.rpc_endpoint},
                        "security_profile_ref": _GATEWAY_SECURITY,
                    }
                },
            },
        }
    )


def build_authority(config: RiskServiceConfig):
    """Operator-installed principal table -> ServiceHost authority (never from requests)."""
    from bt_api_py.service_host import TrustedServiceAuthority
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant

    domain, account = config.execution_domain_ref, config.account_scope
    grants: dict[str, tuple] = {}
    keys: dict[bytes, str] = {}
    for principal in config.principals.values():
        keys[principal.public_key] = principal.principal_ref
        if principal.role == "operator":
            identity = WireIdentity(
                WireRole.APPLICATION_CLIENT, principal.principal_ref, principal.actor_ref
            )
            resources = (
                ResourceGrant(
                    config.instance_id,
                    RISK_ADMIN_PORT,
                    config.scope_ref,
                    WireScope(domain, account_ref=account),
                    frozenset(principal.operations & RISK_ADMIN_OPERATIONS),
                ),
            )
        else:
            role = WireRole.STRATEGY_CLIENT if principal.role == "strategy" else WireRole.SERVICE
            identity = WireIdentity(role, principal.principal_ref, principal.actor_ref)
            resources = tuple(
                ResourceGrant(
                    config.instance_id,
                    RISK_SERVICE_PORT,
                    config.scope_ref,
                    WireScope(domain, account_ref=account, strategy_ref=strategy),
                    frozenset(principal.operations & RISK_ORDER_OPERATIONS),
                )
                for strategy in sorted(principal.strategy_refs)
            )
        grants[principal.principal_ref] = (PrincipalGrant(identity, resources),)
    return TrustedServiceAuthority(
        identity=WireIdentity(WireRole.SERVICE, config.service_principal_ref, config.service_id),
        scope=WireScope(domain),
        scope_ref=config.scope_ref,
        scope_version=POLICY_LABELS["scope_version"],
        security_ref=POLICY_LABELS["security_ref"],
        security_version=POLICY_LABELS["security_version"],
        registration_policy_ref=POLICY_LABELS["registration_policy_ref"],
        registration_policy_version=POLICY_LABELS["registration_policy_version"],
        service_requirements_ref=POLICY_LABELS["service_requirements_ref"],
        service_requirements_version=POLICY_LABELS["service_requirements_version"],
        curve_public_key=config.server_public_key,
        curve_secret_key=config.server_secret_key,
        principals=keys,
        grants=grants,
    )


# ----------------------------------------------------------------------------- policy


def read_policy_files(policy_file: Path) -> tuple[str, str | None]:
    """Policy text and the trusted exchange-rules text the policy names (if any)."""
    from bt_api_risk.pretrade.policy import load_document

    try:
        text = policy_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _fail("RISK_SERVICE_POLICY_UNAVAILABLE")
    try:
        document = load_document(text)
    except Exception:
        _fail("RISK_SERVICE_POLICY_INVALID")
    rules_text = None
    named = document.get("exchange_rules") if isinstance(document, Mapping) else None
    if isinstance(named, Mapping) and named.get("file") is not None:
        try:
            rules_path = resolve_path(named["file"], policy_file.resolve().parent)
            rules_text = rules_path.read_text(encoding="utf-8")
        except (RiskServiceConfigError, OSError, UnicodeDecodeError):
            _fail("RISK_SERVICE_EXCHANGE_RULES_UNAVAILABLE")
    return text, rules_text


def bootstrap_policy(engine: Any, ledger: Any, config: RiskServiceConfig, registry: Any) -> str:
    """Publish the policy file at start when the ledger has none, or when it only tightens."""
    from bt_api_risk.pretrade import CallerIdentity, RiskAdminError, RiskPolicyError
    from bt_api_risk.pretrade.policy import classify_change, validate_policy_document

    text, rules_text = read_policy_files(config.policy_file)
    try:
        policy = validate_policy_document(
            text, exchange_rules_text=rules_text, rule_allowlist=registry.allowlist
        )
    except RiskPolicyError:
        _fail("RISK_SERVICE_POLICY_INVALID")
    if policy.route != config.route or policy.account_scope != config.account_scope:
        _fail("RISK_SERVICE_POLICY_ACCOUNT_MISMATCH")
    summary = engine.status_summary()
    if summary["policy_digest"] == policy.digest:
        return "unchanged"
    with ledger.transaction() as txn:
        row = txn.latest_policy()
    if row is not None:
        if summary["policy_version"] is None:
            _fail("RISK_SERVICE_POLICY_STORE_UNREADABLE")
        old = validate_policy_document(
            row["document_json"],
            exchange_rules_text=row["exchange_rules_text"],
            rule_allowlist=registry.allowlist,
        )
        if classify_change(old, policy)["loosened"]:
            # D64-12: loosening needs an authorized operator, a reason and no latch.
            _fail("RISK_SERVICE_POLICY_FILE_LOOSENS")
    operator = CallerIdentity(
        principal_ref="risk-host:policy-file",
        actor_ref=config.service_id,
        role="operator",
        strategy_refs=frozenset(),
        run_id=None,
        operations=frozenset({"risk.policy.publish"}),
    )
    try:
        engine.publish_policy(
            operator,
            text,
            summary["policy_version"] or 0,
            # The first version has no predecessor to compare with, so it counts as a
            # loosening; the operator's file is the decision. Later files only tighten.
            "first policy version from the service policy file" if row is None else None,
            exchange_rules_text=rules_text,
        )
    except RiskAdminError:
        _fail("RISK_SERVICE_POLICY_PUBLISH_REFUSED")
    return "bootstrapped" if row is None else "tightened"


# ----------------------------------------------------------------------------- Gateway composition


class _DelegateHandle:
    def __init__(self, runtime: Any, client: Any, host: Any) -> None:
        self.runtime, self.client, self.host = runtime, client, host

    def close(self) -> None:
        self.runtime.close(timeout_seconds=5.0)


class _MarketOwnerExtension:
    """Owner-thread market clients of the Risk principal on ``upstream.market``."""

    def __init__(self, settings: Any, *, stream_scope: WireScope, security_profiles: Mapping) -> None:
        self._settings, self._scope, self._profiles = settings, stream_scope, security_profiles
        self._market: Any = None

    def start(self, runtime: Any) -> None:
        from bt_api_py.gateway_strategy_market import GatewayMarketOwner

        self._market = GatewayMarketOwner(
            self._settings,
            stream_scope=self._scope,
            security_profiles=self._profiles,
            mount="upstream.market",
        )
        self._market.start(runtime)

    def invoke(self, operation: str, arguments: Mapping[str, Any]) -> Any:
        from bt_api_py.service_runtime_thread import OwnerOperationError

        if self._market is None or not operation.startswith("market."):
            raise OwnerOperationError("OWNER_OPERATION_UNAVAILABLE")
        return self._market.invoke(operation.removeprefix("market."), arguments)

    @property
    def cleanup_uncertainties(self) -> int:
        return 0 if self._market is None else self._market.cleanup_uncertainties

    def close(self) -> None:
        if self._market is not None:
            self._market.close()


class _AccountOwnerExtension:
    def __init__(self, settings: Any, *, account_ref: str, scope_ref: str) -> None:
        self._settings, self._account, self._scope_ref = settings, account_ref, scope_ref
        self._owner: Any = None

    def start(self, runtime: Any) -> None:
        from bt_api_py.gateway_strategy_account import GatewayAccountOwner

        self._owner = GatewayAccountOwner(
            self._settings,
            account_ref=self._account,
            scope_ref=self._scope_ref,
            mount="application.account",
        )
        self._owner.start(runtime)

    def invoke(self, operation: str, arguments: Mapping[str, Any]) -> Any:
        from bt_api_py.service_runtime_thread import OwnerOperationError

        if self._owner is None or not operation.startswith("account."):
            raise OwnerOperationError("OWNER_OPERATION_UNAVAILABLE")
        return self._owner.invoke(operation.removeprefix("account."), dict(arguments))

    @property
    def cleanup_uncertainties(self) -> int:
        return 0

    def close(self) -> None:
        if self._owner is not None:
            self._owner.close()


def _granted(snapshot: Any, mount: str, operations: frozenset[str]) -> bool:
    return (
        snapshot.state == "running"
        and snapshot.healthy is True
        and any(
            b.active is True
            and b.mount == mount
            and type(b.operations) is frozenset
            and operations <= b.operations
            for b in snapshot.bindings
        )
    )


class RiskServiceHost:
    """Owns storage, engine, Gateway runtimes and the Risk ServiceHost on one thread."""

    def __init__(
        self,
        config: RiskServiceConfig,
        *,
        now_ns: Callable[[], int] = time.time_ns,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(config) is not RiskServiceConfig:
            raise TypeError("RISK_SERVICE_CONFIG_REQUIRED")
        self.config = config
        self._now_ns, self._monotonic = now_ns, monotonic
        self._stack = ExitStack()
        self._started = False
        self.engine: Any = None
        self.ledger: Any = None
        self.service_host: Any = None
        self.order_port: Any = None
        self.control: Any = None
        self.facts: Any = None
        self.pump: Any = None
        self.market: Any = None
        self.account: Any = None
        self.financial: Any = None
        self.component: Any = None
        self._runtimes: list[Any] = []
        self._next: dict[str, float] = {}
        self.stop_event = threading.Event()

    # ------------------------------------------------------------------ start

    def open_storage(self) -> None:
        from bt_api_risk.core.admission import AccountScope
        from bt_api_risk.gateway_delegate_journal import RiskGatewayDelegateJournal
        from bt_api_risk.pretrade import PolicyStore, PretradeLedger, RuleRegistry
        from bt_api_risk.risk_order_journal import RiskOrderJournal

        config = self.config
        ensure_private_directory(config.storage_directory)
        try:
            journal = RiskOrderJournal(config.risk_database, writer_id="risk-service")
        except Exception:
            _fail("RISK_SERVICE_STORAGE_UNAVAILABLE")
        restrict_file(config.risk_database)
        scope = AccountScope(config.provider, config.account_scope, config.environment)
        probe_writer_fence(journal, scope)
        fence = self._stack.enter_context(journal.writer_fence(scope))
        try:
            self.ledger = PretradeLedger(journal, fence)
            self.delegate_storage = RiskGatewayDelegateJournal(journal, fence)
        except Exception:
            _fail("RISK_SERVICE_STORAGE_SCHEMA_INVALID")
        self.policy_store = PolicyStore(self.ledger)
        try:
            self.rules = RuleRegistry(dict(config.trusted_rules))
        except Exception:
            _fail("RISK_SERVICE_RULES_INVALID")

    def _curve(self, identity: Any) -> Any:
        from bt_api_py.service_runtime import ClientCurveKeys

        return ClientCurveKeys(
            self.config.gateway.server_public_key, identity.public_key, identity.secret_key
        )

    def _peer(self) -> WireIdentity:
        gateway = self.config.gateway
        return WireIdentity(WireRole.SERVICE, gateway.principal_ref, gateway.service_id)

    def _service_runtime(self, mount: str, scope: WireScope, operations: frozenset[str], *, run_id: str | None = None, extension: Callable[[], Any] | None = None, market: bool = False) -> Any:
        from bt_api_py.service_runtime import ServiceClientRuntime, ZmqTransportFactory
        from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

        delegate = self.config.gateway.delegate
        client_config = _gateway_client_config(self.config, mount, market=market)
        identity = WireIdentity(WireRole.SERVICE, delegate.principal_ref, delegate.actor_ref)
        factory = ZmqTransportFactory({_GATEWAY_SECURITY: self._curve(delegate)})
        peer = self._peer()
        requested = {mount: operations}
        return ThreadedServiceClientRuntime(
            lambda: ServiceClientRuntime(
                client_config,
                identity=identity,
                scope_resolver=lambda _ref: scope,
                peer_resolver=lambda _service: peer,
                transport_factory=factory,
                run_id=run_id,
                requested_operations=requested,
            ),
            owner_extension_factory=extension,
        )

    def _connect_delegate(self, route: Any) -> _DelegateHandle:
        from bt_api_py.gateway_delegate_risk_host import GatewayRiskDelegateHost
        from bt_api_py.gateway_delegate_service_client import GatewayDelegateServiceClient

        runtime = self._service_runtime(
            "upstream.direct_write", route.scope, DELEGATE_OPERATIONS, run_id=route.scope.run_id
        )
        try:
            snapshot = runtime.start(timeout_seconds=self.config.call_timeout_seconds)
            if not _granted(snapshot, "upstream.direct_write", DELEGATE_OPERATIONS):
                raise RiskServiceStartError("RISK_SERVICE_GATEWAY_DELEGATE_UNAVAILABLE")
            host = GatewayRiskDelegateHost(self.delegate_storage, route=route)
            client = GatewayDelegateServiceClient(runtime, route=route, host=host)
        except BaseException:
            try:
                runtime.close(timeout_seconds=5.0)
            except Exception:
                _LOG.warning("Risk delegate runtime cleanup uncertain after a refused start")
            raise
        return _DelegateHandle(runtime, client, host)

    def build_engine(self) -> None:
        from bt_api_risk.pretrade import PretradeEngine

        from bt_api_py.risk_service_gateway import (
            DelegateOrderPort,
            GatewayControlAdapter,
            RiskFactsCache,
        )

        config, gateway = self.config, self.config.gateway
        max_age = config.account_snapshot_max_age_seconds
        financial_age = config.strategy_financial_max_age_seconds
        self.facts = RiskFactsCache(
            config.instruments,
            now_ns=self._now_ns,
            account_max_age_ns=None if max_age is None else int(max_age * 1_000_000_000),
            financial_max_age_ns=None
            if financial_age is None
            else int(financial_age * 1_000_000_000),
        )
        identity = WireIdentity(
            WireRole.SERVICE, gateway.delegate.principal_ref, gateway.delegate.actor_ref
        )

        def instrument_for(venue: object, native: object) -> Any:
            binding = config.instrument_for(venue, native)
            return None if binding is None else binding.key

        self.instrument_for = instrument_for
        self.order_port = DelegateOrderPort(
            account_scope=config.account_scope,
            execution_domain_ref=config.execution_domain_ref,
            identity=identity,
            service_id=gateway.service_id,
            instance_id=gateway.instance_id,
            scope_ref=gateway.scope_ref,
            grant_revisions=gateway.grant_revisions,
            instrument_for=instrument_for,
            connect=self._connect_delegate,
            timeout_seconds=config.call_timeout_seconds,
            retry_seconds=config.maintain_seconds,
            monotonic=self._monotonic,
        )
        self.control_runtime = self._control_runtime()
        self.control = GatewayControlAdapter(
            self.control_runtime,
            mount="application.direct_write",
            service_id=gateway.service_id,
            instance_id=gateway.instance_id,
            scope_ref=gateway.scope_ref,
            account_ref=config.account_scope,
            timeout_seconds=config.call_timeout_seconds,
        )
        try:
            self.engine = PretradeEngine(
                self.ledger,
                self.policy_store,
                self.rules,
                self.facts,
                self.order_port,
                self.control,
                account_scope=config.account_scope,
                route=config.route,
                now_ns=self._now_ns,
                reservation_ttl_seconds=config.reservation_ttl_seconds,
                actor_principal=gateway.delegate.principal_ref,
            )
        except Exception:
            _fail("RISK_SERVICE_ENGINE_INVALID")

    def _application_runtime(self, requested: Mapping[str, frozenset[str]], extension: Any = None) -> Any:
        from bt_api_py.service_runtime import ServiceClientRuntime, ZmqTransportFactory
        from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime

        config, gateway = self.config, self.config.gateway
        client_config = _control_client_config(config, list(requested))
        identity = WireIdentity(
            WireRole.APPLICATION_CLIENT, gateway.control.principal_ref, gateway.control.actor_ref
        )
        scope = WireScope(config.execution_domain_ref, account_ref=config.account_scope)
        factory = ZmqTransportFactory({_GATEWAY_SECURITY: self._curve(gateway.control)})
        peer = self._peer()
        frozen = dict(requested)
        return ThreadedServiceClientRuntime(
            lambda: ServiceClientRuntime(
                client_config,
                identity=identity,
                scope_resolver=lambda _ref: scope,
                peer_resolver=lambda _service: peer,
                transport_factory=factory,
                requested_operations=frozen,
            ),
            owner_extension_factory=extension,
        )

    def _control_runtime(self) -> Any:
        from bt_api_py.risk_service_gateway import CONTROL_OPERATIONS as CONTROL_OPERATIONS_FOR_RISK

        config, gateway = self.config, self.config.gateway
        mounts = ["application.direct_write"]
        requested = {"application.direct_write": CONTROL_OPERATIONS_FOR_RISK}
        extension = None
        if gateway.account_snapshot is not None:
            from bt_api_base.account_protocol import AccountKey, AccountResourceKey

            from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings

            snap = gateway.account_snapshot
            settings = GatewayAccountClientSettings(
                expected_account=AccountKey(
                    config.provider, snap.venue, config.environment, snap.account_id
                ),
                required_coverage=tuple(AccountResourceKey(a, b) for a, b in snap.resources),
                requested_operations=frozenset({"account.snapshot.typed"}),
            )
            mounts.append("application.account")
            requested["application.account"] = frozenset({"account.snapshot.typed"})

            def extension():
                return _AccountOwnerExtension(
                    settings, account_ref=config.account_scope, scope_ref=gateway.scope_ref
                )

        return self._application_runtime(requested, extension)

    def start_gateway(self) -> None:
        """Start the account-wide Gateway runtimes and check the grants this config needs."""
        from bt_api_py.gateway_account_observer_client import (
            GatewayAccountObserverClient,
            GatewayAccountObserverRoute,
        )
        from bt_api_py.risk_service_gateway import CONTROL_OPERATIONS as CONTROL_OPERATIONS_FOR_RISK
        from bt_api_py.risk_service_gateway import ObservationPump, payload_reader

        config, gateway = self.config, self.config.gateway
        timeout = config.call_timeout_seconds
        account_scope = WireScope(config.execution_domain_ref, account_ref=config.account_scope)
        observer_runtime = self._service_runtime(
            "upstream.account", account_scope, OBSERVER_OPERATIONS
        )
        self._runtimes.append(observer_runtime)
        try:
            snapshot = observer_runtime.start(timeout_seconds=timeout)
        except Exception:
            _fail("RISK_SERVICE_GATEWAY_OBSERVER_UNAVAILABLE")
        if not _granted(snapshot, "upstream.account", OBSERVER_OPERATIONS):
            _fail("RISK_SERVICE_GATEWAY_OBSERVER_UNAVAILABLE")
        delegate = gateway.delegate
        observer = GatewayAccountObserverClient(
            observer_runtime,
            route=GatewayAccountObserverRoute(
                "upstream.account",
                gateway.service_id,
                gateway.instance_id,
                gateway.scope_ref,
                account_scope,
                WireIdentity(WireRole.SERVICE, delegate.principal_ref, delegate.actor_ref),
            ),
        )
        self._runtimes.append(self.control_runtime)
        try:
            snapshot = self.control_runtime.start(timeout_seconds=timeout)
        except Exception:
            _fail("RISK_SERVICE_GATEWAY_CONTROL_UNAVAILABLE")
        if not _granted(snapshot, "application.direct_write", CONTROL_OPERATIONS_FOR_RISK):
            _fail("RISK_SERVICE_GATEWAY_CONTROL_UNAVAILABLE")
        if gateway.account_snapshot is not None:
            from bt_api_py.gateway_strategy_account import GatewayStrategyAccountClient
            from bt_api_py.risk_service_gateway import RiskAccountSnapshotSource

            if not _granted(snapshot, "application.account", frozenset({"account.snapshot.typed"})):
                _fail("RISK_SERVICE_GATEWAY_ACCOUNT_UNAVAILABLE")
            self.account = RiskAccountSnapshotSource(
                GatewayStrategyAccountClient(self.control_runtime),
                self.facts,
                instrument_ids={
                    (b.key.venue, b.key.native_symbol): b.instrument_id
                    for b in config.instruments.values()
                },
                timeout_seconds=timeout,
            )
        if config.strategy_financial_seconds is not None:
            self._start_strategy_financial()
        if gateway.market is not None:
            self._start_market()
        self.pump = ObservationPump(
            self.engine,
            observer,
            self.facts,
            risk_principal=delegate.principal_ref,
            intent_payload=payload_reader(self.delegate_storage),
            timeout_seconds=timeout,
        )

    def _start_strategy_financial(self) -> None:
        from bt_api_py.risk_service_gateway import (
            STRATEGY_FINANCIAL_OPERATION,
            StrategyFinancialSource,
        )

        config, gateway = self.config, self.config.gateway
        operations = frozenset({STRATEGY_FINANCIAL_OPERATION})
        runtime = self._application_runtime({"application.account": operations})
        self._runtimes.append(runtime)
        try:
            snapshot = runtime.start(timeout_seconds=config.call_timeout_seconds)
        except Exception:
            _fail("RISK_SERVICE_GATEWAY_STRATEGY_FINANCIAL_UNAVAILABLE")
        if not _granted(snapshot, "application.account", operations):
            _fail("RISK_SERVICE_GATEWAY_STRATEGY_FINANCIAL_UNAVAILABLE")
        self.financial = StrategyFinancialSource(
            runtime,
            self.facts,
            mount="application.account",
            service_id=gateway.service_id,
            instance_id=gateway.instance_id,
            scope_ref=gateway.scope_ref,
            account_ref=config.account_scope,
            timeout_seconds=config.call_timeout_seconds,
        )

    def _start_market(self) -> None:
        from bt_api_py.gateway_strategy_market import (
            GatewayMarketClientSettings,
            GatewayMarketSubscriptionClient,
            GatewayMarketSubscriptionSettings,
        )
        from bt_api_py.risk_service_gateway import RiskMarketFeed

        config, market = self.config, self.config.gateway.market
        keys = {}
        for index, instrument_id in enumerate(market.instrument_ids):
            key = MarketSubscriptionKey(
                market.source_ref,
                market.entitlement_ref,
                config.instruments[instrument_id].key,
                market.channel,
            )
            keys["m" + str(index)] = (key, instrument_id)
        settings = GatewayMarketClientSettings(
            {name: GatewayMarketSubscriptionSettings(key) for name, (key, _) in keys.items()}
        )
        profiles = {_GATEWAY_SECURITY: self._curve(config.gateway.delegate)}
        stream_scope = WireScope(config.execution_domain_ref)
        runtime = self._service_runtime(
            "upstream.market",
            WireScope(config.execution_domain_ref, account_ref=config.account_scope),
            settings.requested_operations,
            market=True,
            extension=lambda: _MarketOwnerExtension(
                settings, stream_scope=stream_scope, security_profiles=profiles
            ),
        )
        self._runtimes.append(runtime)
        try:
            snapshot = runtime.start(timeout_seconds=config.call_timeout_seconds)
        except Exception:
            _fail("RISK_SERVICE_GATEWAY_MARKET_UNAVAILABLE")
        if not _granted(snapshot, "upstream.market", settings.requested_operations):
            _fail("RISK_SERVICE_GATEWAY_MARKET_UNAVAILABLE")
        self.market = RiskMarketFeed(
            {
                name: (GatewayMarketSubscriptionClient(runtime, name), key, instrument_id)
                for name, (key, instrument_id) in keys.items()
            },
            self.facts,
            timeout_seconds=config.call_timeout_seconds,
        )
        try:
            self.market.start()
        except Exception:
            _fail("RISK_SERVICE_GATEWAY_MARKET_UNAVAILABLE")

    def ready(self) -> bool:
        try:
            _ = self.ledger.fence  # raises when the writer fence is no longer ours
        except Exception:
            return False
        return self._started and self.pump is not None and self.pump.verified_epoch is not None

    def start_service_host(self) -> None:
        from bt_api_py.risk_service_component import RiskOrderService
        from bt_api_py.service_components import ComponentResources
        from bt_api_py.service_config import parse_service_config
        from bt_api_py.service_host import ServiceHost, ServiceHostError

        config = self.config
        self.component = RiskOrderService(
            self.engine,
            self.ledger,
            account_scope=config.account_scope,
            route=config.route,
            execution_domain_ref=config.execution_domain_ref,
            principals=config.principals,
            instrument_for=self.instrument_for,
            ready=self.ready,
            note_run=self.order_port.note_run,
            cancel_status=self.control.cancel_scope_status,
        )
        component = self.component
        try:
            self.service_host = ServiceHost(
                parse_service_config(service_config_document(config)),
                build_authority(config),
                config.registry_database,
                lambda *_: ComponentResources.borrowed({"service": component}, ready=lambda: True),
            )
            self.service_host.start()
        except ServiceHostError as error:
            _fail("RISK_SERVICE_HOST_" + error.code)
        except Exception:
            _fail("RISK_SERVICE_HOST_START_FAILED")
        restrict_file(config.registry_database)

    def start(self) -> RiskServiceHost:
        """Run start-up steps 2-5; any refusal closes what was opened and raises."""
        try:
            self.open_storage()
            self.build_engine()
            bootstrap_policy(self.engine, self.ledger, self.config, self.rules)
            self.start_gateway()
            self.engine.recover()
            self.pump.poll()
            if self.market is not None:
                self.market.poll()
            if self.account is not None:
                self.account.refresh()
            if self.financial is not None:
                self.financial.refresh()
            self._started = True
            self.start_service_host()
        except BaseException:
            self.close()
            raise
        now = self._monotonic()
        self._next = {
            "observation": now,
            "maintain": now,
            "market": now,
            "account": now,
            "financial": now,
        }
        return self

    # ------------------------------------------------------------------ loop

    def run_once(self) -> None:
        config = self.config
        self.service_host.serve_once(config.loop_timeout_ms)
        try:
            _ = self.ledger.fence
        except Exception:
            _fail("RISK_SERVICE_FENCE_LOST")
        now = self._monotonic()
        if now >= self._next["observation"]:
            self.pump.poll()
            self._next["observation"] = now + config.observation_poll_seconds
        if self.market is not None and now >= self._next["market"]:
            self.market.poll()
            self._next["market"] = now + config.market_poll_seconds  # type: ignore[operator]
        if self.account is not None and now >= self._next["account"]:
            self.account.refresh()
            self._next["account"] = now + config.account_snapshot_seconds  # type: ignore[operator]
        if self.financial is not None and now >= self._next["financial"]:
            self.financial.refresh()
            self._next["financial"] = now + config.strategy_financial_seconds  # type: ignore[operator]
        if now >= self._next["maintain"]:
            self.engine.maintain()
            self._next["maintain"] = now + config.maintain_seconds

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.run_once()

    def close(self) -> None:
        if self.service_host is not None and self.service_host.state == "started":
            try:
                self.service_host.close()
            except Exception:
                _LOG.warning("Risk ServiceHost close failed; cleanup uncertain")
        if self.order_port is not None:
            self.order_port.close()
        for runtime in reversed(self._runtimes):
            try:
                runtime.close(timeout_seconds=5.0)
            except Exception:
                _LOG.warning("Risk Gateway runtime close failed; cleanup uncertain")
        self._runtimes.clear()
        self._started = False
        self._stack.close()  # releases the writer fence last


# ----------------------------------------------------------------------------- CLI


def _print(value: Mapping) -> None:
    sys.stdout.write(json.dumps(value, sort_keys=True, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _command_run(args: argparse.Namespace) -> int:
    try:
        config = load_risk_service_config(args.config)
    except RiskServiceConfigError as error:
        sys.stderr.write("RISK_SERVICE_START_REFUSED " + error.code + "\n")
        return 2
    host = RiskServiceHost(config)

    def stop(_signum, _frame):
        host.stop_event.set()

    previous = {
        sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)
    }
    try:
        try:
            host.start()
        except RiskServiceStartError as error:
            sys.stderr.write("RISK_SERVICE_START_REFUSED " + error.code + "\n")
            return 2
        if args.ready_file:
            ready = Path(args.ready_file)
            ready.write_text(json.dumps({"pid": os.getpid(), "state": "serving"}), encoding="utf-8")
            restrict_file(ready)
        try:
            host.run()
        except RiskServiceStartError as error:
            sys.stderr.write("RISK_SERVICE_STOPPED " + error.code + "\n")
            return 3
        return 0
    finally:
        host.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _command_validate_policy(args: argparse.Namespace) -> int:
    from bt_api_risk.pretrade import RiskPolicyError
    from bt_api_risk.pretrade.policy import validate_policy_document

    allowlist: Mapping[str, int] = {}
    if args.config:
        try:
            allowlist = load_risk_service_config(args.config).trusted_rules
        except RiskServiceConfigError as error:
            _print({"valid": False, "code": error.code})
            return 2
    try:
        text, rules_text = read_policy_files(Path(args.policy))
        policy = validate_policy_document(
            text, exchange_rules_text=rules_text, rule_allowlist=dict(allowlist)
        )
    except RiskServiceStartError as error:
        _print({"valid": False, "code": error.code})
        return 1
    except RiskPolicyError as error:
        _print({"valid": False, "code": error.code})
        return 1
    _print({"valid": True, "policy_id": policy.policy_id, "digest": policy.digest})
    return 0


class _Inert:
    """Ports for an offline ledger read: every Gateway call is unavailable."""

    def __getattr__(self, _name):
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("RISK_SERVICE_OFFLINE")

        return unavailable

    def trading_day(self):
        return None


def _command_status(args: argparse.Namespace) -> int:
    try:
        config = load_risk_service_config(args.config)
    except RiskServiceConfigError as error:
        _print({"code": error.code})
        return 2
    if not config.risk_database.is_file():
        _print({"code": "RISK_SERVICE_STORAGE_MISSING"})
        return 2
    host = RiskServiceHost(config)
    try:
        host.open_storage()
        from bt_api_risk.pretrade import PretradeEngine

        engine = PretradeEngine(
            host.ledger,
            host.policy_store,
            host.rules,
            _Inert(),
            _Inert(),
            _Inert(),
            account_scope=config.account_scope,
            route=config.route,
            now_ns=time.time_ns,
            reservation_ttl_seconds=config.reservation_ttl_seconds,
            actor_principal=config.gateway.delegate.principal_ref,
        )
        from bt_api_py.risk_service_component import json_safe

        _print({"running": False, "summary": json_safe(engine.status_summary())})
        return 0
    except RiskServiceStartError as error:
        _print({"running": error.code == "RISK_SERVICE_FENCE_HELD", "code": error.code})
        return 3 if error.code == "RISK_SERVICE_FENCE_HELD" else 2
    finally:
        host.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bt-risk-service")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run the Risk service for one account")
    run.add_argument("--config", required=True)
    run.add_argument("--ready-file", default=None)
    validate = commands.add_parser("validate-policy", help="validate a policy file offline")
    validate.add_argument("policy")
    validate.add_argument("--config", default=None)
    status = commands.add_parser("status", help="local ledger summary (no Gateway)")
    status.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        return _command_run(args)
    if args.command == "validate-policy":
        return _command_validate_policy(args)
    return _command_status(args)


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())


__all__ = [
    "DELEGATE_OPERATIONS",
    "OBSERVER_OPERATIONS",
    "RiskServiceHost",
    "RiskServiceStartError",
    "bootstrap_policy",
    "build_authority",
    "main",
    "probe_writer_fence",
    "read_policy_files",
    "service_config_document",
]
