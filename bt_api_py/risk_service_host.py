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
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
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
from bt_api_py.service_runtime_cleanup import runtime_cleanup_confirmed

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
_RETAINED_HOSTS: set[Any] = set()


class RiskServiceStartError(RuntimeError):
    """Finite refusal to start or keep running; carries no value."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise RiskServiceStartError(code)


# ----------------------------------------------------------------------------- fence probe


@contextmanager
def _held_fence(journal: Any, scope: Any) -> Iterator[Any]:
    """The journal's writer fence taken with one non-blocking attempt.

    ``RISK_SERVICE_FENCE_HELD`` when another thread or process holds it; any
    other storage failure is ``RISK_SERVICE_STORAGE_UNAVAILABLE``. The lock file
    lies in the owner-only storage directory.
    """
    from bt_api_risk.risk_order_journal import RiskOrderJournalError

    stack = ExitStack()
    try:
        value = stack.enter_context(journal.writer_fence(scope, blocking=False))
    except RiskOrderJournalError as error:
        _fail(
            "RISK_SERVICE_FENCE_HELD"
            if error.code == "WRITER_FENCE_HELD"
            else "RISK_SERVICE_STORAGE_UNAVAILABLE"
        )
    except Exception:
        _fail("RISK_SERVICE_STORAGE_UNAVAILABLE")
    with stack:
        yield value


def probe_writer_fence(journal: Any, scope: Any) -> None:
    """Refuse at once with ``RISK_SERVICE_FENCE_HELD`` when the fence is held elsewhere.

    A point-in-time check only: it takes and drops the fence without waiting,
    which advances the account's fencing token when it succeeds. The host
    itself never probes before acquiring: it takes the fence with one
    non-blocking attempt (``RiskOrderJournal.writer_fence(..., blocking=False)``)
    and holds it for the process lifetime.
    """
    with _held_fence(journal, scope):
        pass


@contextmanager
def _open_fenced_journal(path: Path, scope: Any) -> Iterator[tuple[Any, Any]]:
    """Acquire the account lock before initialization, without waiting on SQLite."""
    from bt_api_risk.risk_order_journal import RiskOrderJournal, RiskOrderJournalError

    stack = ExitStack()
    try:
        result = stack.enter_context(RiskOrderJournal.open_fenced(
            path, writer_id="risk-service", scope=scope, blocking=False
        ))
    except RiskOrderJournalError as error:
        _fail("RISK_SERVICE_FENCE_HELD" if error.code == "WRITER_FENCE_HELD"
              else "RISK_SERVICE_STORAGE_UNAVAILABLE")
    except sqlite3.OperationalError as error:
        code = getattr(error, "sqlite_errorcode", None)
        _fail("RISK_SERVICE_STORAGE_BUSY" if type(code) is int and (code & 0xff) in
              (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else "RISK_SERVICE_STORAGE_UNAVAILABLE")
    except Exception:
        _fail("RISK_SERVICE_STORAGE_UNAVAILABLE")
    with stack:
        yield result


# ----------------------------------------------------------------------------- test-only faults

# TEST ONLY (64.09, plan 64 section 4.3 crash table). When this variable is
# absent the host composes exactly what it composes without this code. When it
# is set to one of FAULT_POINTS the process exits at once (``os._exit``, no
# cleanup, no reply) at that point of the first order through it; any other
# value, including the empty string, refuses to start.
FAULT_POINT_ENVIRONMENT = "BT_RISK_FAULT_POINT"
FAULT_POINTS = frozenset(
    {"after_local_reserve", "after_gateway_reserve", "after_claim_commit", "after_dispatch_sent"}
)
FAULT_EXIT_CODE = 86


def fault_point_from_environment(environ: Mapping[str, str]) -> str | None:
    """The configured test-only fault point, None when unset; refuse unknown values."""
    if FAULT_POINT_ENVIRONMENT not in environ:
        return None
    value = environ[FAULT_POINT_ENVIRONMENT]
    if value not in FAULT_POINTS:
        _fail("RISK_SERVICE_FAULT_POINT_INVALID")
    return value


class _FaultInjectingOrderPort:
    """TEST ONLY: the Gateway order port with one process-exit point (section 4.3).

    ``after_local_reserve``: Risk committed the permit (RESERVING) and its
    occupancy; the delegate reserve has not left. ``after_gateway_reserve``: the
    Gateway answered the delegate reserve; Risk recorded nothing of it.
    ``after_claim_commit``: Risk committed the claim (CLAIMED); the delegate
    dispatch has not left. ``after_dispatch_sent``: the Gateway answered the
    delegate dispatch; Risk recorded nothing of it.
    """

    def __init__(self, port: Any, point: str, exit_now: Callable[[int], Any] = os._exit) -> None:
        if point not in FAULT_POINTS:
            raise ValueError("RISK_SERVICE_FAULT_POINT_INVALID")
        self._port, self._point, self._exit_now = port, point, exit_now

    def _fire(self, point: str) -> None:
        if point == self._point:
            os.write(2, ("RISK_SERVICE_FAULT_INJECTED " + point + "\n").encode("ascii"))
            self._exit_now(FAULT_EXIT_CODE)

    def reserve(self, **kwargs: Any) -> Any:
        self._fire("after_local_reserve")
        reply = self._port.reserve(**kwargs)
        self._fire("after_gateway_reserve")
        return reply

    def dispatch(self, **kwargs: Any) -> Any:
        self._fire("after_claim_commit")
        reply = self._port.dispatch(**kwargs)
        self._fire("after_dispatch_sent")
        return reply

    def __getattr__(self, name: str) -> Any:
        return getattr(self._port, name)


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

    def close(self) -> Any:
        return self.runtime.close(timeout_seconds=5.0)


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
        self._observer_runtime: Any = None
        self._observer_route: Any = None
        self._observer_retry_at = 0.0
        self._delegate_cleanup_unresolved: list[Any] = []
        self._next: dict[str, float] = {}
        self.stop_event = threading.Event()
        self._fault_point: str | None = None  # test only, read by start()

    # ------------------------------------------------------------------ start

    def open_storage(self) -> None:
        from bt_api_risk.core.admission import AccountScope
        from bt_api_risk.gateway_delegate_journal import RiskGatewayDelegateJournal
        from bt_api_risk.pretrade import PolicyStore, PretradeLedger, RuleRegistry
        config = self.config
        ensure_private_directory(config.storage_directory)
        scope = AccountScope(config.provider, config.account_scope, config.environment)
        # Hold the same account lock across schema initialization and ownership;
        # if any later setup fails, the temporary stack releases it immediately.
        with ExitStack() as acquiring:
            journal, fence = acquiring.enter_context(_open_fenced_journal(config.risk_database, scope))
            restrict_file(config.risk_database)
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
            self._stack.enter_context(acquiring.pop_all())

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
                require_unregister_ack=True,
            ),
            owner_extension_factory=extension,
            require_unregister_ack=True,
        )

    def _connect_delegate(self, route: Any) -> _DelegateHandle:
        from bt_api_py.gateway_delegate_risk_host import GatewayRiskDelegateHost
        from bt_api_py.gateway_delegate_service_client import GatewayDelegateServiceClient

        for old in tuple(self._delegate_cleanup_unresolved):
            try:
                closed = old.close(timeout_seconds=5.0)
            except Exception:
                raise RiskServiceStartError("RISK_SERVICE_GATEWAY_DELEGATE_CLEANUP_UNRESOLVED") from None
            if not runtime_cleanup_confirmed(closed):
                raise RiskServiceStartError("RISK_SERVICE_GATEWAY_DELEGATE_CLEANUP_UNRESOLVED")
            self._delegate_cleanup_unresolved.remove(old)
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
                snapshot = runtime.close(timeout_seconds=5.0)
                if not runtime_cleanup_confirmed(snapshot):
                    self._delegate_cleanup_unresolved.append(runtime)
            except Exception:
                self._delegate_cleanup_unresolved.append(runtime)
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
                self.order_port
                if self._fault_point is None
                else _FaultInjectingOrderPort(self.order_port, self._fault_point),
                self.control,
                account_scope=config.account_scope,
                route=config.route,
                now_ns=self._now_ns,
                reservation_ttl_seconds=config.reservation_ttl_seconds,
                actor_principal=gateway.delegate.principal_ref,
                dispatch_recheck_seconds=config.dispatch_recheck_seconds,
                dispatch_recheck_limit=config.dispatch_recheck_limit,
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
                require_unregister_ack=True,
            ),
            owner_extension_factory=extension,
            require_unregister_ack=True,
        )

    def _control_runtime(self) -> Any:
        from bt_api_py.risk_service_gateway import CONTROL_OPERATIONS as CONTROL_OPERATIONS_FOR_RISK

        config, gateway = self.config, self.config.gateway
        mounts = ["application.direct_write"]
        requested = {"application.direct_write": CONTROL_OPERATIONS_FOR_RISK}
        extension = None
        if gateway.account_snapshot is not None:
            from bt_api_base.account_protocol import AccountResourceKey

            from bt_api_py.gateway_strategy_account import GatewayAccountClientSettings

            snap = gateway.account_snapshot
            # Bound by the Gateway's hashed account reference only: this process never
            # holds the raw account or sub-account number (plan 64 section 6.1).
            settings = GatewayAccountClientSettings(
                expected_account=None,
                required_coverage=tuple(AccountResourceKey(a, b) for a, b in snap.resources),
                requested_operations=frozenset({"account.snapshot.typed"}),
                expected_account_key_id=config.account_scope,
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
        observer_route = GatewayAccountObserverRoute(
            "upstream.account", gateway.service_id, gateway.instance_id, gateway.scope_ref,
            account_scope, WireIdentity(WireRole.SERVICE, delegate.principal_ref, delegate.actor_ref),
        )
        observer = GatewayAccountObserverClient(observer_runtime, route=observer_route)
        self._observer_runtime = observer_runtime
        self._observer_route = observer_route
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

    def _reconnect_observer(self) -> None:
        """Replace a stopped observer only after local cleanup; replay before verifying."""
        from bt_api_py.gateway_account_observer_client import GatewayAccountObserverClient

        runtime = self._observer_runtime
        if runtime is None or self.pump is None or self._observer_route is None:
            return
        now = self._monotonic()
        if now < self._observer_retry_at:
            return
        try:
            state = runtime.status().state
        except Exception:
            return
        if state not in ("failed", "closed", "close_failed", "stopping"):
            return
        self._observer_retry_at = now + max(1.0, self.config.maintain_seconds)
        # The persisted cursor is not trusted during registration replacement.
        self.pump._gap("observation_reconnect")
        try:
            closed = runtime.close(timeout_seconds=self.config.call_timeout_seconds)
        except Exception:
            return
        if not runtime_cleanup_confirmed(closed):
            return
        # Unknown remote unregister keeps the original object and blocks a new
        # registration; replacing this read registration never replays an order.
        self._runtimes.remove(runtime)
        route = self._observer_route
        replacement = self._service_runtime("upstream.account", route.scope, OBSERVER_OPERATIONS)
        self._observer_runtime = replacement
        self._runtimes.append(replacement)
        try:
            snapshot = replacement.start(timeout_seconds=self.config.call_timeout_seconds)
            if not _granted(snapshot, "upstream.account", OBSERVER_OPERATIONS):
                replacement.close(timeout_seconds=self.config.call_timeout_seconds)
                return
            observer = GatewayAccountObserverClient(replacement, route=route)
        except Exception:
            return
        self.pump.replace_observer(observer)
        self.order_port.invalidate_registrations()
        self._observer_retry_at = 0.0

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
        from bt_api_py.gateway_strategy_market import GatewayMarketSubscriptionClient
        from bt_api_py.risk_service_gateway import RiskMarketFeed, risk_market_settings

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
        # Includes market.clock_observation: the Gateway gate needs Risk's own attested tick.
        settings = risk_market_settings({name: key for name, (key, _) in keys.items()})
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
            self._fault_point = fault_point_from_environment(os.environ)
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
            self._reconnect_observer()
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

    def close(self) -> bool:
        """Stop owned resources, retaining this host and its fence if cleanup is uncertain."""
        self._started = False
        self.stop_event.set()
        complete = True
        if self.service_host is not None and self.service_host.state in ("started", "failed", "close_failed"):
            try:
                self.service_host.close()
            except Exception:
                _LOG.warning("Risk ServiceHost close failed; cleanup uncertain")
                complete = False
        if self.order_port is not None:
            try:
                if self.order_port.close() is not True:
                    complete = False
            except Exception:
                _LOG.warning("Risk delegate port close failed; cleanup uncertain")
                complete = False
        for runtime in list(self._delegate_cleanup_unresolved):
            try:
                snapshot = runtime.close(timeout_seconds=5.0)
                if runtime_cleanup_confirmed(snapshot):
                    self._delegate_cleanup_unresolved.remove(runtime)
                else:
                    complete = False
            except Exception:
                _LOG.warning("Risk delegate runtime cleanup remains uncertain")
                complete = False
        for runtime in reversed(self._runtimes[:]):
            try:
                snapshot = runtime.close(timeout_seconds=5.0)
                if runtime_cleanup_confirmed(snapshot):
                    self._runtimes.remove(runtime)
                else:
                    complete = False
            except Exception:
                _LOG.warning("Risk Gateway runtime close failed; cleanup uncertain")
                complete = False
        if not complete:
            _RETAINED_HOSTS.add(self)
            return False
        self._stack.close()  # releases the writer fence last
        _RETAINED_HOSTS.discard(self)
        return True


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
        while not host.close():
            _LOG.error("Risk service cleanup incomplete; retaining account fence")
            time.sleep(1.0)
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
            dispatch_recheck_seconds=config.dispatch_recheck_seconds,
            dispatch_recheck_limit=config.dispatch_recheck_limit,
        )
        from bt_api_py.risk_service_component import json_safe

        _print({"running": False, "summary": json_safe(engine.status_summary())})
        return 0
    except RiskServiceStartError as error:
        # Storage failure or a different scope's SQLite writer cannot establish
        # whether this account's service is running.
        _print({"running": True if error.code == "RISK_SERVICE_FENCE_HELD" else None,
                "code": error.code})
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
