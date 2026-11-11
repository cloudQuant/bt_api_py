"""Trusted-code composition for one CTP Gateway trading-owner profile.

Factories are installed-code closures, never imported from a manifest. This
module creates no session or native resource while building a profile. The
account owner thread alone owns the CTP session, guard and dispatcher. The
local guard is not a cross-host fence or proof of external account state.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bt_api_base.account_protocol import (
    AccountKey,
    AccountResourceKey,
    account_venue_contains_instrument_venue,
)
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireRole, WireScope
from bt_api_gateway.direct_command_journal import DirectCommandJournalError, GatewayDirectCommandJournal
from bt_api_gateway.session_guard import (
    LocalAccountSessionGuard,
    LocalAccountSessionGuardError,
)

from bt_api_py.direct_write import DirectWriteCoordinator
from bt_api_py.gateway_direct_dispatch import (
    compose_gateway_direct_cancel_dispatcher,
    compose_gateway_direct_dispatcher,
)
from bt_api_py.gateway_direct_owner import (
    GatewayDirectOwner,
    GatewayDirectOwnerResources,
    GatewayOwnerStartupCleanupError,
)
from bt_api_py.gateway_direct_service import (
    ACCOUNT_READ_OPERATIONS,
    INSTRUMENT_OPERATIONS,
    CANCEL_OPERATIONS,
    CONTROL_OPERATIONS,
    STRATEGY_POSITION_OPERATIONS,
    ACCOUNT_COMPARISON_OPERATIONS,
    ACCOUNT_OBSERVER_OPERATIONS,
    ACCOUNT_FACTS_OPERATIONS,
    STRATEGY_FINANCIAL_OPERATIONS,
    PARENT_OPERATIONS,
    DELEGATED_STRATEGY_READ_OPERATIONS,
    DELEGATED_STRATEGY_CANCEL_OPERATIONS,
    GatewayControlGrant,
    EVENT_OPERATIONS,
    MARKET_OPERATIONS,
    GatewayDirectGrant,
    GatewayDirectServiceBinding,
)
from bt_api_py.gateway_operator import GatewayOperatorDeployment
from bt_api_py.gateway_operator_application import GatewayOperatorProfile
from bt_api_py.gateway_settlement import (
    GatewaySettlementController,
    GatewaySettlementObservationStaleError,
)
from bt_api_py.service_components import ComponentResources, ServiceResourceConstructionError
from bt_api_py.service_config import ServiceConfig
from bt_api_py.service_host import TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.write_admission import WriteAdmission

_LOG = logging.getLogger(__name__)
ROUTE = "CTP___FUTURE"
# Seconds between two full history checks of the command journal (I63-E135).
_JOURNAL_HISTORY_CHECK_SECONDS = 30.0
_OPERATIONS = (
    frozenset({"direct.reserve", "direct.release", "direct.command_status", "direct.dispatch"})
    | EVENT_OPERATIONS
)
_READ_OPERATIONS = ACCOUNT_READ_OPERATIONS | INSTRUMENT_OPERATIONS | ACCOUNT_COMPARISON_OPERATIONS | ACCOUNT_OBSERVER_OPERATIONS | ACCOUNT_FACTS_OPERATIONS
_MARKET_OPERATIONS = MARKET_OPERATIONS - {"market.history"}


class CtpGatewayTradingError(RuntimeError):
    """Finite composition/cleanup error without account, path or secret values."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# How one account read of the read model can fail without the session having changed. Since the
# read is request and response (I63-E130) its budget also covers the time the owner spends on
# other work between polls, so a read past its budget (QUERY_BUDGET_EXCEEDED) says nothing about
# the session any more: like a failed or incomplete query it fails this read only. The read model
# stays not ready until a later read completes; the owner goes on (I63-E146).
_FAILED_ACCOUNT_READ_CODES = frozenset({"QUERY_FAILED", "QUERY_INCOMPLETE", "QUERY_BUDGET_EXCEEDED"})


def _failed_account_read(error: Exception) -> bool:
    """Whether ``error`` says that one account read of the read model failed, and no more.

    The provider's finite codes above, and the read model's own finding that the read took
    longer than its budget (PROVIDER_RESPONSE_LATE): it measures the same budget from the
    same start, a moment earlier.
    """
    from bt_api_ctp.account_provider import CtpAccountSessionProviderError
    from bt_api_gateway.account_read_model import GatewayAccountSessionWorkerError

    if type(error) is CtpAccountSessionProviderError:
        return error.code in _FAILED_ACCOUNT_READ_CODES
    return type(error) is GatewayAccountSessionWorkerError and error.code == "PROVIDER_RESPONSE_LATE"


def _connection_transition_error(error: Exception, *, account_read: bool = False) -> bool:
    """Recognize finite errors a CTP session change can actually cause.

    Classification alone never authorizes a retry: the caller also checks the
    exact provider session and its current connection state or generation.
    """
    from bt_api_ctp.account_provider import CtpAccountSessionProviderError
    from bt_api_ctp.gateway_return_query import CtpGatewayReturnQueryError
    from bt_api_ctp.gateway_session import CtpGatewaySessionError

    if type(error) is CtpGatewaySessionError:
        return error.code in {"SESSION_CHANGED", "SESSION_NOT_CONNECTED", "SESSION_UNAVAILABLE"}
    if type(error) is CtpGatewayReturnQueryError:
        return error.code == "SESSION_CHANGED"
    if account_read and _failed_account_read(error):
        return True
    if type(error) is CtpAccountSessionProviderError:
        return error.code in {"SESSION_NOT_CONNECTED", "SESSION_SCOPE_UNTRUSTED", "QUERY_SCOPE_CHANGED"}
    if type(error) is CtpGatewayTradingError:
        return error.code == "CTP_SESSION_BINDING_INVALID"
    if type(error) is GatewaySettlementObservationStaleError:
        return True
    return False


def _ref(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 256


def _absolute(value: object) -> bool:
    try:
        return isinstance(value, (str, os.PathLike)) and os.path.isabs(os.fspath(value))
    except (TypeError, ValueError):
        return False


def _duration(value: object, *, minimum: float = 0.001, maximum: float = 3600) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


@dataclass(frozen=True, slots=True)
class CtpGatewayTradingSettings:
    account_key: AccountKey = field(repr=False)
    account_scope: str
    guard_root: str | Path
    owner_id: str
    journal_path: str | Path
    expected_journal_id: str
    minimum_revision: int
    trusted_flow_root: str | Path
    market_source_ref: str
    market_entitlement_ref: str
    write_enabled: bool = False
    startup_timeout_seconds: float = 30.0
    close_timeout_seconds: float = 5.0
    connect_timeout_seconds: float = 30.0
    session_stop_timeout_seconds: float = 3.0
    maintenance_interval_seconds: float = 1.0
    # Proactive settlement re-observation while the confirmed ticket is live
    # (the CTP ticket lives at most 30 s). Each observation is a native query
    # on the owner thread that counts against CTP query flow control; 0 keeps
    # the earlier re-observe-every-cycle behaviour.
    settlement_refresh_seconds: float = 10.0
    queue_size: int = 128
    max_queue_wait_seconds: float = 30.0
    busy_timeout_seconds: float = 30.0
    session_queue_capacity: int = 1024
    journal_max_records: int = 100000
    journal_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        key = self.account_key
        if (
            type(key) is not AccountKey
            or (key.provider, key.venue, key.environment) != ("ctp", "simnow", "simnow")
            or not _ref(key.account_id)
            or not _ref(key.subaccount_id)
            or self.account_scope != key.account_key_id
            or not all(
                _ref(item)
                for item in (
                    self.account_scope,
                    self.owner_id,
                    self.expected_journal_id,
                    self.market_source_ref,
                    self.market_entitlement_ref,
                )
            )
            or not all(
                _absolute(path)
                for path in (self.guard_root, self.journal_path, self.trusted_flow_root)
            )
            or type(self.minimum_revision) is not int
            or self.minimum_revision < 0
            or type(self.write_enabled) is not bool
            or type(self.queue_size) is not int
            or not 1 <= self.queue_size <= 10000
            or type(self.session_queue_capacity) is not int
            or not 1 <= self.session_queue_capacity <= 65536
            or type(self.journal_max_records) is not int
            or not 1 <= self.journal_max_records <= 1000000
            or not all(
                _duration(item)
                for item in (
                    self.startup_timeout_seconds,
                    self.close_timeout_seconds,
                    self.maintenance_interval_seconds,
                    self.max_queue_wait_seconds,
                    self.busy_timeout_seconds,
                )
            )
            or not _duration(self.connect_timeout_seconds, minimum=0.05, maximum=300)
            or not _duration(self.session_stop_timeout_seconds, minimum=0.05, maximum=30)
            or not _duration(self.journal_timeout_seconds, maximum=60)
            or type(self.settlement_refresh_seconds) not in (int, float)
            or not math.isfinite(self.settlement_refresh_seconds)
            or not 0 <= self.settlement_refresh_seconds <= 25
        ):
            raise CtpGatewayTradingError("INVALID_TRADING_SETTINGS")


@dataclass(frozen=True, slots=True)
class CtpGatewayAccountReadSettings:
    journal_path: str | Path
    state_scope: str
    required_coverage: tuple[AccountResourceKey, ...]
    observation_identity_path: str | Path
    expected_observation_issuer_id: str
    observation_instruments: tuple[MarketInstrumentKey, ...]
    refresh_interval_seconds: float = 1.0
    max_staleness_seconds: float = 5.0
    request_timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if (
            not _absolute(self.journal_path)
            or not _absolute(self.observation_identity_path)
            or not _ref(self.state_scope)
            or not _ref(self.expected_observation_issuer_id)
            or type(self.required_coverage) is not tuple
            or not self.required_coverage
            or any(type(item) is not AccountResourceKey for item in self.required_coverage)
            or len(set(self.required_coverage)) != len(self.required_coverage)
            or type(self.observation_instruments) is not tuple
            or not 1 <= len(self.observation_instruments) <= 256
            or any(
                type(item) is not MarketInstrumentKey
                or item.provider != "ctp"
                or item.product_type != "future"
                or item.native_symbol != item.instrument_id
                for item in self.observation_instruments
            )
            or len({(item.venue, item.instrument_id) for item in self.observation_instruments})
            != len(self.observation_instruments)
            or not all(
                _duration(item, minimum=0.05, maximum=300)
                for item in (
                    self.refresh_interval_seconds,
                    self.max_staleness_seconds,
                    self.request_timeout_seconds,
                )
            )
        ):
            raise CtpGatewayTradingError("INVALID_ACCOUNT_READ_SETTINGS")


@dataclass(frozen=True, slots=True)
class CtpGatewayMarketSettings:
    key_universe: frozenset[MarketSubscriptionKey]
    principal_allowed_keys: Mapping[str, frozenset[MarketSubscriptionKey]] = field(repr=False)
    max_provider_events_per_maintain: int = 32
    call_timeout_seconds: float = 5.0
    owner_queue_size: int = 32
    # Market gate quote age limit. Above 2 s only for a SimNow account
    # (user decision 2026-11-10); the profile refuses it elsewhere.
    dispatch_max_latency_seconds: float = 2.0

    def __post_init__(self) -> None:
        try:
            if (
                not _duration(self.dispatch_max_latency_seconds, minimum=0.05, maximum=5)
                or type(self.key_universe) is not frozenset
                or not 1 <= len(self.key_universe) <= 1024
                or any(type(key) is not MarketSubscriptionKey for key in self.key_universe)
                or not isinstance(self.principal_allowed_keys, Mapping)
                or not self.principal_allowed_keys
                or type(self.max_provider_events_per_maintain) is not int
                or not 1 <= self.max_provider_events_per_maintain <= 256
                or not _duration(self.call_timeout_seconds, maximum=30)
                or type(self.owner_queue_size) is not int
                or not 1 <= self.owner_queue_size <= 10000
            ):
                raise ValueError
            keys = frozenset(
                MarketSubscriptionKey.from_dict(k.to_dict()) for k in self.key_universe
            )
            acl = {}
            for principal, allowed in self.principal_allowed_keys.items():
                if (
                    type(principal) is not str
                    or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", principal, re.ASCII)
                    is None
                    or type(allowed) is not frozenset
                    or not allowed
                    or any(type(k) is not MarketSubscriptionKey for k in allowed)
                    or not allowed <= self.key_universe
                ):
                    raise ValueError
                acl[principal] = frozenset(
                    MarketSubscriptionKey.from_dict(k.to_dict()) for k in allowed
                )
            object.__setattr__(self, "key_universe", keys)
            object.__setattr__(self, "principal_allowed_keys", MappingProxyType(acl))
        except Exception:
            raise CtpGatewayTradingError("INVALID_MARKET_SETTINGS") from None


@dataclass(frozen=True, slots=True)
class CtpGatewayReturnSettings:
    instruments: tuple[MarketInstrumentKey, ...]
    query_timeout_seconds: float = 0.5
    query_interval_seconds: float = 30.0
    callback_batch_limit: int = 256

    def __post_init__(self) -> None:
        if (
            type(self.instruments) is not tuple
            or not 1 <= len(self.instruments) <= 256
            or any(
                type(item) is not MarketInstrumentKey
                or item.provider != "ctp"
                or item.product_type != "future"
                or item.native_symbol != item.instrument_id
                for item in self.instruments
            )
            or len({(item.venue, item.instrument_id) for item in self.instruments})
            != len(self.instruments)
            # I63-E130: the owner no longer waits for a return query, so its
            # timeout may cover a full-day order query of a busy account (about
            # 5 s for 110 SimNow orders on 2026-11-11). It still bounds how long
            # other provider queries of the owner are deferred behind it.
            or not _duration(self.query_timeout_seconds, minimum=0.05, maximum=60)
            or not _duration(self.query_interval_seconds, minimum=0.05, maximum=3600)
            or type(self.callback_batch_limit) is not int
            or not 1 <= self.callback_batch_limit <= 256
        ):
            raise CtpGatewayTradingError("INVALID_RETURN_SETTINGS")


def _distinct_journals(left: str | Path, right: str | Path) -> bool:
    try:
        if Path(left).resolve() == Path(right).resolve():
            return False
        return not (
            os.path.exists(left) and os.path.exists(right) and os.path.samefile(left, right)
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _validate_config(
    config: ServiceConfig,
    authority: TrustedServiceAuthority,
    settings: CtpGatewayTradingSettings,
    grants: Mapping[str, GatewayDirectGrant],
    control_grants: Mapping[str, GatewayControlGrant],
    read_settings: CtpGatewayAccountReadSettings | None,
    read_principals: frozenset[str],
    market_settings: CtpGatewayMarketSettings | None,
    delegation_settings: object | None,
    parent_settings: object | None,
) -> str:
    if type(config) is not ServiceConfig or type(authority) is not TrustedServiceAuthority:
        raise CtpGatewayTradingError("INVALID_TRADING_CONFIGURATION")
    try:
        config.to_public_dict()
        components = config.components
        item = components["gateway"]["settings"]
        options = item["options"]
        normalized_options = dict(options)
        normalized_options.setdefault("cancel_enabled", False)
        if "control_enabled" in normalized_options:
            normalized_options["control_enabled"] = normalized_options["control_enabled"]
        if market_settings is not None:
            normalized_options.setdefault("bounded_read_after", False)
        cancel_enabled = normalized_options.get("cancel_enabled")
        bounded = normalized_options.get("bounded_read_after", False)
        control_enabled = normalized_options.get("control_enabled", False)
        ledger_enabled = normalized_options.get("strategy_ledger_enabled", False)
        extensions_enabled = normalized_options.get("account_extensions_enabled", False)
        delegation_enabled = normalized_options.get("delegation_enabled", False)
        parent_enabled = normalized_options.get("parent_authority_enabled", False)
        facts_financial_enabled = normalized_options.get("account_facts_financial_enabled", False)
        actor_stream_enabled = normalized_options.get('actor_stream_enabled', False)
        strategy_origin_enabled = normalized_options.get('strategy_origin_enabled', False)
        control_cancel_enabled = normalized_options.get('control_cancel_enabled', False)
        account_fact_recovery_enabled = normalized_options.get('account_fact_recovery_enabled', False)
        account_event_recovery_enabled = normalized_options.get('account_event_recovery_enabled', False)
        expected_options = {
            "role": "trading_owner",
            "dispatch_enabled": True,
            "cancel_enabled": cancel_enabled,
        }
        if "control_enabled" in normalized_options:
            expected_options["control_enabled"] = control_enabled
        if "strategy_ledger_enabled" in normalized_options:
            expected_options["strategy_ledger_enabled"] = ledger_enabled
        if "account_extensions_enabled" in normalized_options:
            expected_options["account_extensions_enabled"] = extensions_enabled
        if "delegation_enabled" in normalized_options:
            expected_options["delegation_enabled"] = delegation_enabled
        if "parent_authority_enabled" in normalized_options:
            expected_options["parent_authority_enabled"] = parent_enabled
        if "account_facts_financial_enabled" in normalized_options:
            expected_options["account_facts_financial_enabled"] = facts_financial_enabled
        if 'actor_stream_enabled' in normalized_options:
            expected_options['actor_stream_enabled'] = actor_stream_enabled
        if 'strategy_origin_enabled' in normalized_options:
            expected_options['strategy_origin_enabled'] = strategy_origin_enabled
        if 'control_cancel_enabled' in normalized_options:
            expected_options['control_cancel_enabled'] = control_cancel_enabled
        if 'account_fact_recovery_enabled' in normalized_options:
            expected_options['account_fact_recovery_enabled'] = account_fact_recovery_enabled
        if 'account_event_recovery_enabled' in normalized_options:
            expected_options['account_event_recovery_enabled'] = account_event_recovery_enabled
        if market_settings is not None:
            expected_options["bounded_read_after"] = bounded
        market_principals = (
            set() if market_settings is None else set(market_settings.principal_allowed_keys)
        )
        delegates = () if delegation_settings is None else delegation_settings.grants
        delegate_principals = {grant.identity.principal_ref for grant in delegates}
        # D63-37: a strategy whose SUBMIT is left to its Risk delegate may not hold it itself.
        risk_only_submit = (frozenset() if delegation_settings is None
                            else delegation_settings.risk_only_submit_strategies)
        parent_grants = () if parent_settings is None else parent_settings.grants
        parent_principals = {grant.identity.principal_ref for grant in parent_grants}
        all_principals = set(grants) | set(control_grants) | set(read_principals) | market_principals | delegate_principals | parent_principals
        instance = item["instance_id"]
        if (
            set(components) != {"gateway"}
            or frozenset(item["bind_ports"])
            != (
                frozenset({"service.direct_write"})
                | ({"service.account_read"} if read_settings is not None else set())
                | ({"service.market_source"} if market_settings is not None else set())
            )
            or len(item["bind_ports"]) != len(set(item["bind_ports"]))
            or type(cancel_enabled) is not bool
            or type(control_enabled) is not bool
            or control_enabled != bool(control_grants)
            or type(ledger_enabled) is not bool or (ledger_enabled and not control_enabled)
            or type(extensions_enabled) is not bool or (extensions_enabled and not ledger_enabled)
            or type(delegation_enabled) is not bool or delegation_enabled != bool(delegates)
            or (delegation_enabled and not extensions_enabled)
            or type(parent_enabled) is not bool or parent_enabled != bool(parent_grants)
            or (parent_enabled and not extensions_enabled)
            or type(facts_financial_enabled) is not bool or (facts_financial_enabled and not extensions_enabled)
            or type(actor_stream_enabled) is not bool or (actor_stream_enabled and not delegation_enabled)
            or type(strategy_origin_enabled) is not bool or (strategy_origin_enabled and not extensions_enabled)
            or type(control_cancel_enabled) is not bool or (control_cancel_enabled and not (control_enabled and cancel_enabled and extensions_enabled and strategy_origin_enabled))
            or type(account_fact_recovery_enabled) is not bool or (account_fact_recovery_enabled and not (control_cancel_enabled and facts_financial_enabled))
            or type(account_event_recovery_enabled) is not bool or (account_event_recovery_enabled and not account_fact_recovery_enabled)
            or type(bounded) is not bool
            or normalized_options != expected_options
            or not _ref(instance)
            or config.clients
            or config.service_id != authority.identity.actor_ref
            or config.scope_ref != authority.scope_ref
            or config.registration_policy_ref != authority.registration_policy_ref
            or config.service_requirements_ref != authority.service_requirements_ref
            or not isinstance(grants, Mapping)
            or not grants
            or all_principals != set(authority.principals.values())
            or all_principals != set(authority.grants)
        ):
            raise ValueError
        for principal in all_principals:
            grant = grants.get(principal)
            control_grant = control_grants.get(principal)
            if control_grant is not None and type(control_grant) is not GatewayControlGrant:
                raise ValueError
            if not _ref(principal) or (grant is not None and type(grant) is not GatewayDirectGrant):
                raise ValueError
            entries = authority.grants[principal]
            if not entries or len({entry.identity for entry in entries}) != 1:
                raise ValueError
            for entry in entries:
                if (
                    type(entry) is not PrincipalGrant
                    or entry.identity.role
                    not in {WireRole.STRATEGY_CLIENT, WireRole.APPLICATION_CLIENT, WireRole.SERVICE}
                    or entry.identity.principal_ref != principal
                    or entry.identity.delegate_ref is not None
                    or not entry.resources
                ):
                    raise ValueError
                for resource in entry.resources:
                    scope = resource.scope
                    if (
                        type(resource) is not ResourceGrant
                        or type(scope) is not WireScope
                        or resource.instance_id != instance
                        or resource.port
                        not in {
                            "service.direct_write",
                            "service.account_read",
                            "service.market_source",
                        }
                        or resource.scope_ref != config.scope_ref
                        or scope.execution_domain_ref != authority.scope.execution_domain_ref
                        or scope.account_ref != settings.account_scope
                        or scope.account_refs
                        or scope.portfolio_ref is not None
                        or scope.manual_book_ref is not None
                        or scope.group_id is not None
                        or scope.leg_id is not None
                        or (entry.identity.role is WireRole.SERVICE and resource.port != "service.direct_write"
                            and not (resource.port == "service.account_read" and extensions_enabled
                                     and resource.operations <= (ACCOUNT_OBSERVER_OPERATIONS | ACCOUNT_FACTS_OPERATIONS))
                            and not (resource.port == 'service.market_source' and principal in delegate_principals
                                     and market_settings is not None))
                    ):
                        raise ValueError
                    if resource.port == "service.direct_write":
                        if entry.identity.role is WireRole.SERVICE:
                            matching = [item for item in delegates if item.identity == entry.identity
                                        and item.account_scope == settings.account_scope
                                        and item.strategy_scope == scope.strategy_ref
                                        and item.execution_domain_ref == scope.execution_domain_ref]
                            logical = {'direct.delegated_reserve':'direct.reserve','direct.delegated_dispatch':'direct.dispatch',
                                       'direct.delegate_actor_status':'direct.command_status',
                                       'direct.delegate_actor_cancel':'direct.cancel',
                                       'direct.delegate_actor_cancel_status':'direct.cancel_status',
                                       'direct.delegate_actor_release':'direct.release',
                                       'direct.delegate_actor_release_status':'direct.command_status'}
                            logical.update({'direct.delegate_events.read_after':'direct.command_status',
                                            'direct.delegate_events.progress':'direct.command_status',
                                            'direct.delegate_events.ack':'direct.command_status'})
                            parents = [item for item in parent_grants if item.identity == entry.identity
                                       and item.account_scope == settings.account_scope and item.strategy_scope == scope.strategy_ref
                                       and item.execution_domain_ref == scope.execution_domain_ref]
                            def operation_granted(op):
                                if op in logical:
                                    return len(matching) == 1 and logical[op] in matching[0].operations
                                return op in PARENT_OPERATIONS and len(parents) == 1 and op in parents[0].operations
                            if scope.run_id is not None or not all(operation_granted(op) for op in resource.operations):
                                raise ValueError
                        elif entry.identity.role is WireRole.APPLICATION_CLIENT:
                            if (not control_enabled or control_grant is None
                                    or entry.identity.actor_ref != control_grant.actor_ref
                                    or not resource.operations <= (control_grant.operations & CONTROL_OPERATIONS)
                                    or (scope.strategy_ref is None and not control_grant.account_scope_allowed)
                                    or (scope.strategy_ref is not None and scope.strategy_ref not in control_grant.strategies)):
                                raise ValueError
                        elif (
                            grant is None
                            or entry.identity.role is not WireRole.STRATEGY_CLIENT
                            or entry.identity.actor_ref != grant.strategy_ref
                            or scope.strategy_ref != grant.strategy_ref
                            or not resource.operations
                            <= ((_OPERATIONS | (CANCEL_OPERATIONS if cancel_enabled else frozenset()))
                                | (STRATEGY_POSITION_OPERATIONS if ledger_enabled else frozenset()))
                                | (STRATEGY_FINANCIAL_OPERATIONS if facts_financial_enabled else frozenset())
                                | ((DELEGATED_STRATEGY_READ_OPERATIONS - {'direct.delegated_cancel_status'}) if extensions_enabled else frozenset())
                                | ((DELEGATED_STRATEGY_CANCEL_OPERATIONS | {'direct.delegated_cancel_status'}) if extensions_enabled and cancel_enabled else frozenset())
                                | (frozenset().union(*(item.operations for item in parent_grants
                                    if item.identity == entry.identity and item.strategy_scope == scope.strategy_ref)) if parent_enabled else frozenset())
                            or (scope.strategy_ref in risk_only_submit
                                and resource.operations & {"direct.reserve", "direct.dispatch"})
                        ):
                            raise ValueError
                    elif resource.port == "service.account_read" and (
                        principal not in read_principals
                        or read_settings is None
                        or not resource.operations <= _READ_OPERATIONS
                        or (bool(resource.operations & ACCOUNT_OBSERVER_OPERATIONS) and not extensions_enabled)
                        or (bool(resource.operations & ACCOUNT_FACTS_OPERATIONS) and not facts_financial_enabled)
                        or (bool(resource.operations & ACCOUNT_COMPARISON_OPERATIONS)
                            and (not extensions_enabled or entry.identity.role is not WireRole.APPLICATION_CLIENT))
                        or (bool(resource.operations & INSTRUMENT_OPERATIONS)
                            and (not INSTRUMENT_OPERATIONS <= resource.operations
                                 or entry.identity.role is not WireRole.STRATEGY_CLIENT))
                        or scope.strategy_ref
                        != (
                            entry.identity.actor_ref
                            if entry.identity.role is WireRole.STRATEGY_CLIENT
                            else None
                        )
                    ):
                        raise ValueError
                    elif resource.port == "service.market_source" and (
                        principal not in market_principals
                        or not resource.operations
                        <= (
                            _MARKET_OPERATIONS
                            if bounded
                            else _MARKET_OPERATIONS - {"market.read_after_bounded"}
                        )
                        or scope.strategy_ref
                        != (
                            entry.identity.actor_ref
                            if entry.identity.role is WireRole.STRATEGY_CLIENT
                            else None
                        )
                    ):
                        raise ValueError
            ports = {resource.port for entry in entries for resource in entry.resources}
            if (principal in grants or principal in control_grants or principal in delegate_principals or principal in parent_principals) != ("service.direct_write" in ports) or (
                principal in read_principals
            ) != ("service.account_read" in ports):
                raise ValueError
            if (principal in market_principals) != ("service.market_source" in ports):
                raise ValueError
            if principal in market_principals and not any(
                resource.port == "service.market_source"
                and "market.subscribe" in resource.operations
                for entry in entries
                for resource in entry.resources
            ):
                raise ValueError
        return instance
    except CtpGatewayTradingError:
        raise
    except Exception:
        raise CtpGatewayTradingError("INVALID_TRADING_CONFIGURATION") from None


def _load_ctp_types():
    """Resolve fixed provider code, only when the owner starts."""
    from bt_api_ctp.direct_write_provider import CtpDirectWriteProvider
    from bt_api_ctp.gateway_session import CtpGatewaySession

    return CtpGatewaySession, CtpDirectWriteProvider


# Account-session guard refusals the operator must act on (I63-E095).
_SURFACED_GUARD_CODES = ("SESSION_RECONCILIATION_REQUIRED", "SESSION_ALREADY_OWNED")


def _market_gate_latency(settings, market_settings) -> float:
    """Market gate quote age limit; above 2 s only on SimNow (user decision 2026-11-10)."""
    latency = (2.0 if market_settings is None
               else float(getattr(market_settings, "dispatch_max_latency_seconds", 2.0)))
    account_key = getattr(settings, "account_key", None)
    if latency > 2.0 and (getattr(account_key, "venue", None) != "simnow"
                          or getattr(account_key, "environment", None) != "simnow"):
        raise CtpGatewayTradingError("MARKET_GATE_LATENCY_REQUIRES_SIMNOW")
    return latency


def _isolated_storage(settings, registry_path, account_read_settings) -> None:
    """Refuse startup unless Gateway storage is OS-isolated to this user (I63 decision)."""
    from .gateway_storage_trust import assert_gateway_storage_isolated

    files = [Path(registry_path), Path(settings.journal_path)]
    directories = [Path(settings.guard_root), Path(settings.trusted_flow_root)]
    if account_read_settings is not None:
        files += [Path(account_read_settings.journal_path),
                  Path(account_read_settings.observation_identity_path)]
    directories += [path.parent for path in files]
    assert_gateway_storage_isolated(directories, files)


def reconcile_ctp_gateway_account_session(
    settings: CtpGatewayTradingSettings,
    registry_path: str | Path,
    account_read_settings=None,
    *,
    operator_ref: str,
    reason: str,
) -> dict:
    """Operator reconciliation of an abandoned account-session record (I63-E095).

    Runs the same storage-isolation check as owner start, then closes an
    ``active`` guard record only while holding its OS lock and only when the
    command journal has no DISPATCHING/UNKNOWN command or cancel and no
    ATTEMPTING/UNKNOWN settlement confirmation. Never connects to the venue.
    """
    from bt_api_gateway.direct_command_journal import inspect_unresolved_dispatch
    from bt_api_gateway.session_guard import reconcile_abandoned_account_session

    if type(settings) is not CtpGatewayTradingSettings:
        raise CtpGatewayTradingError("INVALID_TRADING_CONFIGURATION")
    _isolated_storage(settings, registry_path, account_read_settings)
    return reconcile_abandoned_account_session(
        Path(settings.guard_root),
        settings.account_scope,
        operator_ref=operator_ref,
        reason=reason,
        blocking_evidence=lambda: inspect_unresolved_dispatch(
            settings.journal_path, settings.account_scope
        ),
    )


def build_ctp_gateway_trading_profile(
    config: ServiceConfig,
    authority: TrustedServiceAuthority,
    registry_path: str | Path,
    *,
    profile_ref: str,
    settings: CtpGatewayTradingSettings,
    grants: Mapping[str, GatewayDirectGrant],
    request_feed_factory: Callable[..., object],
    market_stream_factory: Callable[..., object],
    assert_admitted: Callable[[Any], object],
    return_settings: CtpGatewayReturnSettings | None = None,
    account_read_settings: CtpGatewayAccountReadSettings | None = None,
    account_read_principals: frozenset[str] = frozenset(),
    market_settings: CtpGatewayMarketSettings | None = None,
    control_grants: Mapping[str, GatewayControlGrant] | None = None,
    upgrade_controls_from_v9: bool = False,
    upgrade_strategy_ledger_from_v10: bool = False,
    upgrade_account_extensions_from_v11: bool = False,
    clock_verifier_factory: Callable[[], object] | None = None,
    clock_allowed_servers: frozenset[str] | None = None,
    clock_refresh_settings: object | None = None,
    required_monitor: object | None = None,
    lease_drain_settings: object | None = None,
    cancel_only: tuple[str, str] | None = None,
    trusted_external_classifier: Callable[..., object] | None = None,
    delegation_settings: object | None = None,
    parent_settings: object | None = None,
    parent_authority_factory: Callable[..., object] | None = None,
    upgrade_parent_authority_from_v12: bool = False,
    upgrade_account_facts_financial_from_v13: bool = False,
    upgrade_actor_stream_from_v14: bool = False,
    upgrade_strategy_origin_from_v15: bool = False,
    strategy_origin_source_factory: Callable[..., object] | None = None,
    upgrade_control_cancel_from_v16: bool = False,
    upgrade_account_fact_recovery_from_v17: bool = False,
    upgrade_account_event_recovery_from_v18: bool = False,
    strategy_financial_source_factory: Callable[..., object] | None = None,
    strategy_financial_settings: object | None = None,
    strategy_ledger_external_baseline: object | None = None,
    strategy_ledger_comparison_refresh_seconds: float | None = None,
) -> GatewayOperatorProfile:
    """Build a trusted profile; no guard, provider, native session or owner is started."""
    if (type(upgrade_controls_from_v9) is not bool or type(upgrade_strategy_ledger_from_v10) is not bool
            or type(upgrade_account_extensions_from_v11) is not bool
            or type(upgrade_parent_authority_from_v12) is not bool
            or type(upgrade_account_facts_financial_from_v13) is not bool
            or type(upgrade_actor_stream_from_v14) is not bool
            or type(upgrade_strategy_origin_from_v15) is not bool
            or type(upgrade_control_cancel_from_v16) is not bool
            or type(upgrade_account_fact_recovery_from_v17) is not bool
            or type(upgrade_account_event_recovery_from_v18) is not bool
            or (control_grants is not None and not isinstance(control_grants, Mapping))):
        raise CtpGatewayTradingError("INVALID_CONTROL_CONFIGURATION")
    if clock_verifier_factory is not None and (
        not callable(clock_verifier_factory) or market_settings is None
    ):
        raise CtpGatewayTradingError("INVALID_CLOCK_CONFIGURATION")
    if ((clock_verifier_factory is None) != (clock_allowed_servers is None)
            or (clock_allowed_servers is not None and (
                type(clock_allowed_servers) is not frozenset or not clock_allowed_servers
                or any(type(server) is not str or not server or server != server.strip()
                       for server in clock_allowed_servers)))):
        raise CtpGatewayTradingError("INVALID_CLOCK_CONFIGURATION")
    if clock_refresh_settings is not None:
        from bt_api_py.gateway_server_clock_refresh import GatewayServerClockRefreshSettings
        if type(clock_refresh_settings) is not GatewayServerClockRefreshSettings or clock_verifier_factory is None:
            raise CtpGatewayTradingError('INVALID_CLOCK_CONFIGURATION')
    if trusted_external_classifier is not None and not callable(trusted_external_classifier):
        raise CtpGatewayTradingError("INVALID_EXTERNAL_CLASSIFIER")
    control_grants = {} if control_grants is None else dict(control_grants)
    if upgrade_controls_from_v9 and not control_grants:
        raise CtpGatewayTradingError("INVALID_CONTROL_CONFIGURATION")
    if type(settings) is not CtpGatewayTradingSettings or not _absolute(registry_path):
        raise CtpGatewayTradingError("INVALID_TRADING_SETTINGS")
    if delegation_settings is not None:
        from bt_api_py.gateway_delegation_service import GatewayDelegationSettings
        if (type(delegation_settings) is not GatewayDelegationSettings
                or any(grant.strategy_scope not in {item.strategy_ref for item in grants.values()}
                       or grant.account_scope != settings.account_scope
                       or any(instrument.native_symbol not in frozenset(
                           symbol for item in grants.values() if item.strategy_ref == grant.strategy_scope
                           for symbol in item.symbols)
                           or instrument.provider != settings.account_key.provider
                           or instrument.environment != settings.account_key.environment
                           or not account_venue_contains_instrument_venue(settings.account_key, instrument.venue)
                           for instrument in grant.instruments)
                       for grant in delegation_settings.grants)):
            raise CtpGatewayTradingError("INVALID_GATEWAY_DELEGATION")
    if parent_settings is not None:
        from bt_api_py.gateway_parent_service import GatewayParentSettings
        if (type(parent_settings) is not GatewayParentSettings or not callable(parent_authority_factory)
                or any(grant.account_scope != settings.account_scope
                       or grant.execution_domain_ref != authority.scope.execution_domain_ref
                       or grant.strategy_scope not in {item.strategy_ref for item in grants.values()}
                       or any(instrument.provider != settings.account_key.provider
                              or instrument.environment != settings.account_key.environment
                              or not account_venue_contains_instrument_venue(settings.account_key, instrument.venue)
                              or instrument.native_symbol not in frozenset(symbol for item in grants.values()
                                  if item.strategy_ref == grant.strategy_scope for symbol in item.symbols)
                              for instrument in grant.instruments) for grant in parent_settings.grants)):
            raise CtpGatewayTradingError('INVALID_GATEWAY_PARENT')
    elif parent_authority_factory is not None:
        raise CtpGatewayTradingError('INVALID_GATEWAY_PARENT')
    if required_monitor is not None:
        from bt_api_py.gateway_required_monitor import RequiredMonitorSettings

        if (type(required_monitor) is not RequiredMonitorSettings
                or required_monitor.gateway_identity != authority.identity
                or required_monitor.scope.account_ref != settings.account_scope
                or required_monitor.scope.execution_domain_ref != authority.scope.execution_domain_ref
                or not control_grants):
            raise CtpGatewayTradingError("INVALID_REQUIRED_MONITOR")
    if not all(
        callable(item) for item in (request_feed_factory, market_stream_factory, assert_admitted)
    ):
        raise CtpGatewayTradingError("INVALID_TRUSTED_FACTORY")
    if (settings.write_enabled and return_settings is None) or (
        return_settings is not None
        and (
            type(return_settings) is not CtpGatewayReturnSettings
            or any(
                item.environment != settings.account_key.environment
                or not account_venue_contains_instrument_venue(settings.account_key, item.venue)
                for item in return_settings.instruments
            )
        )
    ):
        raise CtpGatewayTradingError("INVALID_RETURN_SETTINGS")
    if (
        type(account_read_principals) is not frozenset
        or any(not _ref(item) for item in account_read_principals)
        or (account_read_settings is None) != (not account_read_principals)
        or (
            account_read_settings is not None
            and (
                type(account_read_settings) is not CtpGatewayAccountReadSettings
                or not _distinct_journals(settings.journal_path, account_read_settings.journal_path)
                or not _distinct_journals(
                    settings.journal_path, account_read_settings.observation_identity_path
                )
                or not _distinct_journals(
                    account_read_settings.journal_path,
                    account_read_settings.observation_identity_path,
                )
                or any(
                    item.environment != settings.account_key.environment
                    or not account_venue_contains_instrument_venue(settings.account_key, item.venue)
                    for item in account_read_settings.observation_instruments
                )
            )
        )
    ):
        raise CtpGatewayTradingError("INVALID_ACCOUNT_READ_SETTINGS")
    if market_settings is not None:
        if type(market_settings) is not CtpGatewayMarketSettings:
            raise CtpGatewayTradingError("INVALID_MARKET_SETTINGS")
        try:
            from bt_api_ctp.market_key_contract import validate_ctp_market_key

            for key in market_settings.key_universe:
                validate_ctp_market_key(
                    key,
                    source_ref=settings.market_source_ref,
                    entitlement_ref=settings.market_entitlement_ref,
                )
        except Exception:
            raise CtpGatewayTradingError("INVALID_MARKET_SETTINGS") from None
        market_settings = CtpGatewayMarketSettings(
            market_settings.key_universe,
            market_settings.principal_allowed_keys,
            market_settings.max_provider_events_per_maintain,
            market_settings.call_timeout_seconds,
            market_settings.owner_queue_size,
            market_settings.dispatch_max_latency_seconds,
        )
        if any(
            key.source_ref != settings.market_source_ref
            or key.entitlement_ref != settings.market_entitlement_ref
            or key.instrument.provider != "ctp"
            or key.instrument.environment != "simnow"
            or key.instrument.product_type != "future"
            or not account_venue_contains_instrument_venue(
                settings.account_key, key.instrument.venue
            )
            for key in market_settings.key_universe
        ):
            raise CtpGatewayTradingError("INVALID_MARKET_SETTINGS")
    instance = _validate_config(
        config,
        authority,
        settings,
        grants,
        control_grants,
        account_read_settings,
        account_read_principals,
        market_settings,
        delegation_settings,
        parent_settings,
    )
    cancel_enabled = config.components["gateway"]["settings"]["options"].get(
        "cancel_enabled", False
    )
    control_enabled = config.components["gateway"]["settings"]["options"].get("control_enabled", False)
    ledger_enabled = config.components["gateway"]["settings"]["options"].get("strategy_ledger_enabled", False)
    extensions_enabled = config.components["gateway"]["settings"]["options"].get("account_extensions_enabled", False)
    delegation_enabled = config.components["gateway"]["settings"]["options"].get("delegation_enabled", False)
    parent_enabled = config.components["gateway"]["settings"]["options"].get("parent_authority_enabled", False)
    facts_financial_enabled = config.components["gateway"]["settings"]["options"].get("account_facts_financial_enabled", False)
    actor_stream_enabled = config.components["gateway"]["settings"]["options"].get("actor_stream_enabled", False)
    strategy_origin_enabled = config.components["gateway"]["settings"]["options"].get('strategy_origin_enabled', False)
    control_cancel_enabled = config.components["gateway"]["settings"]["options"].get('control_cancel_enabled', False)
    account_fact_recovery_enabled = config.components["gateway"]["settings"]["options"].get('account_fact_recovery_enabled', False)
    account_event_recovery_enabled = config.components["gateway"]["settings"]["options"].get('account_event_recovery_enabled', False)
    gate_latency = _market_gate_latency(settings, market_settings)
    if upgrade_account_event_recovery_from_v18 and not account_event_recovery_enabled:
        raise CtpGatewayTradingError('INVALID_ACCOUNT_EVENT_RECOVERY_CONFIGURATION')
    if upgrade_account_fact_recovery_from_v17 and not account_fact_recovery_enabled:
        raise CtpGatewayTradingError('INVALID_ACCOUNT_FACT_RECOVERY_CONFIGURATION')
    if strategy_financial_source_factory is not None and (not facts_financial_enabled or not callable(strategy_financial_source_factory)):
        raise CtpGatewayTradingError('INVALID_STRATEGY_FINANCIAL_SOURCE')
    last_prices = None
    if strategy_financial_settings is not None:
        # Strategy cash and value computed by this profile itself (budgets, adjustments
        # and rates from the operator's bundle); it replaces a caller-supplied factory.
        from bt_api_py.ctp_gateway_strategy_financial import (
            CtpStrategyFinancialSettings,
            OwnerLastPrices,
        )

        if (not facts_financial_enabled or strategy_financial_source_factory is not None
                or type(strategy_financial_settings) is not CtpStrategyFinancialSettings):
            raise CtpGatewayTradingError('INVALID_STRATEGY_FINANCIAL_SOURCE')
        # Written and read on the account owner thread only (market drain, snapshot pass).
        last_prices = OwnerLastPrices()
    if upgrade_control_cancel_from_v16 and not control_cancel_enabled:
        raise CtpGatewayTradingError('INVALID_CONTROL_CANCEL_CONFIGURATION')
    if (strategy_origin_source_factory is not None and (not strategy_origin_enabled or not callable(strategy_origin_source_factory))):
        raise CtpGatewayTradingError('INVALID_STRATEGY_ORIGIN')
    if upgrade_strategy_origin_from_v15 and not (strategy_origin_enabled or control_cancel_enabled):
        raise CtpGatewayTradingError('INVALID_STRATEGY_ORIGIN')
    if upgrade_parent_authority_from_v12 and not (parent_enabled or facts_financial_enabled or actor_stream_enabled or strategy_origin_enabled or control_cancel_enabled):
        raise CtpGatewayTradingError('INVALID_GATEWAY_PARENT')
    if upgrade_account_facts_financial_from_v13 and not (facts_financial_enabled or actor_stream_enabled or strategy_origin_enabled or control_cancel_enabled):
        raise CtpGatewayTradingError('INVALID_ACCOUNT_FACTS_CONFIGURATION')
    if upgrade_actor_stream_from_v14 and not (actor_stream_enabled or strategy_origin_enabled or control_cancel_enabled):
        raise CtpGatewayTradingError('INVALID_ACTOR_STREAM_CONFIGURATION')
    if cancel_only is not None and (
            type(cancel_only) is not tuple or len(cancel_only) != 2
            or any(type(item) is not str or not item or item != item.strip() or len(item) > 256
                   for item in cancel_only)
            or not control_enabled or not cancel_enabled):
        # D63-58: (operator, reason). The mode needs the control and cancel paths.
        raise CtpGatewayTradingError("CANCEL_ONLY_UNAVAILABLE")
    if lease_drain_settings is not None:
        from bt_api_py.gateway_lease_drain import GatewayLeaseDrainSettings

        if (type(lease_drain_settings) is not GatewayLeaseDrainSettings
                or not control_enabled or not cancel_enabled
                or not lease_drain_settings.strategies <= frozenset(grant.strategy_ref for grant in grants.values())):
            raise CtpGatewayTradingError("INVALID_GATEWAY_LEASE_DRAIN")
        if lease_drain_settings.recovery_source is None:
            # D63-33 / I63-E128: without a proof source a lease cause could never
            # be released, neither by the bridge nor by an operator. The source is
            # bound when the owner builds its return maintenance; a profile without
            # one (no writes) leaves it unbound, and an unbound source proves nothing.
            from dataclasses import replace as _replace

            from bt_api_py.ctp_gateway_lease_recovery import CtpGatewayLeaseRecoverySource

            symbols: dict[str, frozenset[str]] = {}
            for grant in grants.values():
                symbols[grant.strategy_ref] = symbols.get(grant.strategy_ref, frozenset()) | grant.symbols
            lease_drain_settings = _replace(lease_drain_settings, recovery_source=CtpGatewayLeaseRecoverySource(
                account_scope=settings.account_scope, strategy_symbols=symbols))
    if strategy_ledger_external_baseline is not None:
        from bt_api_py.ctp_gateway_strategy_ledger import ExternalBaselineDeclaration

        if (type(strategy_ledger_external_baseline) is not ExternalBaselineDeclaration
                or not ledger_enabled or return_settings is None):
            raise CtpGatewayTradingError("INVALID_CONTROL_CONFIGURATION")
    if upgrade_strategy_ledger_from_v10 and not ledger_enabled:
        raise CtpGatewayTradingError("INVALID_STRATEGY_LEDGER_CONFIGURATION")
    if strategy_ledger_comparison_refresh_seconds is not None and (
            type(strategy_ledger_comparison_refresh_seconds) not in (int, float)
            or not 1 <= strategy_ledger_comparison_refresh_seconds <= 120
            # D63-48 compares at journal schema 12 and needs the ledger's ports.
            or not ledger_enabled or not extensions_enabled or return_settings is None):
        raise CtpGatewayTradingError("INVALID_ACCOUNT_COMPARISON_CONFIGURATION")
    if upgrade_account_extensions_from_v11 and not extensions_enabled:
        raise CtpGatewayTradingError("INVALID_ACCOUNT_EXTENSIONS_CONFIGURATION")
    if trusted_external_classifier is not None and (not extensions_enabled or return_settings is None):
        raise CtpGatewayTradingError("INVALID_EXTERNAL_CLASSIFIER")
    static_control_grants = MappingProxyType(dict(control_grants))
    static_grants = MappingProxyType(dict(grants))

    def resources(
        component: str, instance_id: str, options: Mapping[str, object], scope: WireScope
    ):
        normalized_options = dict(options)
        normalized_options.setdefault("cancel_enabled", False)
        expected_options = {
            "role": "trading_owner",
            "dispatch_enabled": True,
            "cancel_enabled": cancel_enabled,
        }
        if "control_enabled" in normalized_options:
            expected_options["control_enabled"] = control_enabled
        if "strategy_ledger_enabled" in normalized_options:
            expected_options["strategy_ledger_enabled"] = ledger_enabled
        if "account_extensions_enabled" in normalized_options:
            expected_options["account_extensions_enabled"] = extensions_enabled
        if "delegation_enabled" in normalized_options:
            expected_options["delegation_enabled"] = delegation_enabled
        if "parent_authority_enabled" in normalized_options:
            expected_options["parent_authority_enabled"] = parent_enabled
        if "account_facts_financial_enabled" in normalized_options:
            expected_options["account_facts_financial_enabled"] = facts_financial_enabled
        if 'actor_stream_enabled' in normalized_options:
            expected_options['actor_stream_enabled'] = actor_stream_enabled
        if 'strategy_origin_enabled' in normalized_options:
            expected_options['strategy_origin_enabled'] = strategy_origin_enabled
        if 'control_cancel_enabled' in normalized_options:
            expected_options['control_cancel_enabled'] = control_cancel_enabled
        if 'account_fact_recovery_enabled' in normalized_options:
            expected_options['account_fact_recovery_enabled'] = account_fact_recovery_enabled
        if 'account_event_recovery_enabled' in normalized_options:
            expected_options['account_event_recovery_enabled'] = account_event_recovery_enabled
        if market_settings is not None:
            normalized_options.setdefault("bounded_read_after", False)
            expected_options["bounded_read_after"] = config.components["gateway"]["settings"][
                "options"
            ].get("bounded_read_after", False)
        if (
            component != "gateway"
            or instance_id != instance
            or normalized_options != expected_options
            or type(scope) is not WireScope
            or scope != authority.scope
        ):
            raise CtpGatewayTradingError("INVALID_TRADING_CONFIGURATION")
        holder: dict[str, Any] = {}
        closing = threading.Event()

        def owner_factory():
            guard = session = read_model = issuer = issuer_cleanup = None
            session_closed = False
            guard_closed = False
            read_closed = False
            issuer_closed = False
            origin_closed = False
            financial_closed = False

            def cleanup():
                # The owner thread retries this whole function on the next close
                # request; name the step that stopped it (I63-E107).
                try:
                    cleanup_steps()
                except CtpGatewayTradingError as error:
                    _LOG.warning("Gateway owner cleanup incomplete: %s", error.code)
                    raise

            def cleanup_steps():
                nonlocal session_closed, guard_closed, read_closed, issuer_closed, origin_closed, financial_closed
                closing.set()
                deadline = time.monotonic() + settings.close_timeout_seconds
                returns = holder.get("returns")
                if returns is not None:
                    returns.begin_close()
                stream = holder.get("execution_stream")
                if stream is not None:
                    stream.begin_close()
                if returns is not None and not returns.close(max(0.0, deadline - time.monotonic())):
                    raise CtpGatewayTradingError("TRADING_GATE_CLOSE_UNCONFIRMED")
                if stream is not None and not stream.close(max(0.0, deadline - time.monotonic())):
                    raise CtpGatewayTradingError("EXECUTION_STREAM_CLOSE_UNCONFIRMED")
                instrument_service = holder.get("instrument_service")
                if instrument_service is not None and instrument_service.cleanup_on_owner() is not True:
                    raise CtpGatewayTradingError("INSTRUMENT_CLEANUP_UNCONFIRMED")
                origin_source = holder.get('strategy_origin_source')
                if origin_source is not None and not origin_closed:
                    origin_source.close()
                    origin_closed = True
                financial_source = holder.get('strategy_financial_source')
                if financial_source is not None and not financial_closed:
                    financial_source.close()
                    financial_closed = True
                refresher = holder.get('clock_refresher')
                if refresher is not None and not refresher.stop(timeout_seconds=min(30.0, max(0.0, deadline-time.monotonic()))):
                    raise CtpGatewayTradingError('CLOCK_REFRESH_CLOSE_UNCONFIRMED')
                if read_model is not None and not read_closed:
                    read_model.close()
                    read_closed = True
                if session is not None and not session_closed:
                    # A native TD/MD stop can outlast one stop wait. The session
                    # keeps every unconfirmed stop for an owner-thread retry, so
                    # retry here for the rest of this close budget instead of
                    # leaving the whole stop to a second request (I63-E107).
                    started = time.monotonic()
                    attempts = 1
                    while session.close() is not True:
                        if time.monotonic() >= deadline:
                            raise CtpGatewayTradingError("CTP_SESSION_CLOSE_UNCONFIRMED")
                        attempts += 1
                        time.sleep(0.05)
                    if attempts > 1:
                        _LOG.info("CTP session close confirmed after %d attempts in %.1f s",
                                  attempts, time.monotonic() - started)
                    session_closed = True
                if issuer is not None and not issuer_closed:
                    issuer.close()
                    issuer_closed = True
                if issuer_cleanup is not None and not issuer_closed:
                    issuer_cleanup.close()
                    issuer_closed = True
                if guard is not None and not guard_closed:
                    guard.release()
                    guard_closed = True

            try:
                # Rechecked at owner start, before the guard and journal open.
                _isolated_storage(settings, registry_path, account_read_settings)
                guard = LocalAccountSessionGuard(
                    Path(settings.guard_root), settings.account_scope, settings.owner_id
                )
                try:
                    generation = guard.acquire()
                except LocalAccountSessionGuardError as exc:
                    # The owner thread only reports "factory_failed"; keep the
                    # finite guard code for the operator (I63-E095).
                    if exc.code in _SURFACED_GUARD_CODES:
                        holder['startup_code'] = exc.code
                    raise
                from bt_api_py.gateway_dispatch_claim import GatewayDispatchClaimCoordinator
                # The sealer stays inside this trusted owner composition and the
                # service host; components only receive the coordinator.
                (holder['dispatch_claim_coordinator'],
                 holder['dispatch_claim_sealer']) = GatewayDispatchClaimCoordinator.with_coverage_sealer(
                    settings.account_scope, generation)
                holder['dispatch_claim_wired'] = set()
                if account_read_settings is not None:
                    from bt_api_py.order_correlation_issuer import (
                        DurableOrderCorrelationIssuer,
                        OrderCorrelationIssuerCleanupError,
                    )

                    if any(
                        not _distinct_journals(a, b)
                        for a, b in (
                            (settings.journal_path, account_read_settings.journal_path),
                            (
                                settings.journal_path,
                                account_read_settings.observation_identity_path,
                            ),
                            (
                                account_read_settings.journal_path,
                                account_read_settings.observation_identity_path,
                            ),
                        )
                    ):
                        raise CtpGatewayTradingError("INVALID_ACCOUNT_READ_SETTINGS")
                    try:
                        issuer = DurableOrderCorrelationIssuer.open_existing(
                            account_read_settings.observation_identity_path,
                            expected_issuer_id=account_read_settings.expected_observation_issuer_id,
                        )
                    except OrderCorrelationIssuerCleanupError as error:
                        issuer_cleanup = error
                        raise
                journal_options = dict(
                    generation=generation,
                    expected_journal_id=settings.expected_journal_id,
                    minimum_revision=settings.minimum_revision,
                    max_records=settings.journal_max_records,
                    timeout_seconds=settings.journal_timeout_seconds,
                )
                target_version = 19 if account_event_recovery_enabled else 18 if account_fact_recovery_enabled else 17 if control_cancel_enabled else 16 if strategy_origin_enabled else 15 if actor_stream_enabled else 14 if facts_financial_enabled else 13 if parent_enabled else 12 if extensions_enabled else 11 if ledger_enabled else 10 if control_enabled else 9
                candidates = [target_version]
                if target_version == 19 and upgrade_account_event_recovery_from_v18:
                    candidates.append(18)
                if target_version >= 18 and 18 in candidates and upgrade_account_fact_recovery_from_v17:
                    candidates.append(17)
                if target_version >= 17 and 17 in candidates and upgrade_control_cancel_from_v16:
                    candidates.append(16)
                if target_version >= 16 and 16 in candidates and upgrade_strategy_origin_from_v15:
                    candidates.append(15)
                if target_version >= 15 and 15 in candidates and upgrade_actor_stream_from_v14:
                    candidates.append(14)
                if target_version >= 14 and 14 in candidates and upgrade_account_facts_financial_from_v13:
                    candidates.append(13)
                if target_version >= 13 and 13 in candidates and upgrade_parent_authority_from_v12:
                    candidates.append(12)
                if target_version >= 12 and 12 in candidates and upgrade_account_extensions_from_v11:
                    candidates.append(11)
                if target_version >= 11 and 11 in candidates and upgrade_strategy_ledger_from_v10:
                    candidates.append(10)
                if target_version >= 10 and 10 in candidates and upgrade_controls_from_v9:
                    candidates.append(9)
                last_schema_error = None
                for version in candidates:
                    try:
                        journal = GatewayDirectCommandJournal(settings.journal_path, settings.account_scope,
                            controls_enabled=version >= 10, strategy_ledger_enabled=version >= 11,
                            account_extensions_enabled=version >= 12,
                            parent_authority_enabled=version >= 13,
                            account_facts_financial_enabled=version >= 14,
                            actor_stream_enabled=version >= 15, strategy_origin_enabled=version >= 16,
                            control_cancel_enabled=version >= 17,
                            account_fact_recovery_enabled=version >= 18,
                            account_event_recovery_enabled=version >= 19, **journal_options)
                        break
                    except DirectCommandJournalError as error:
                        if error.code != "SCHEMA_OR_CAPACITY_MISMATCH":
                            raise
                        last_schema_error = error
                else:
                    raise last_schema_error
                if journal._schema_version == 9 and target_version >= 10:
                    journal.enable_controls(guard)
                if journal._schema_version == 10 and target_version >= 11:
                    journal.enable_strategy_ledger(guard)
                if journal._schema_version == 11 and target_version >= 12:
                    journal.enable_account_extensions(guard)
                if journal._schema_version == 12 and target_version >= 13:
                    journal.enable_parent_authority(guard)
                if journal._schema_version == 13 and target_version >= 14:
                    journal.enable_account_facts_financial(guard)
                if journal._schema_version == 14 and target_version >= 15:
                    journal.enable_actor_stream(guard)
                if journal._schema_version == 15 and target_version >= 16:
                    journal.enable_strategy_origin(guard)
                if journal._schema_version == 16 and target_version >= 17:
                    journal.enable_control_cancel(guard)
                if journal._schema_version == 17 and target_version >= 18:
                    journal.enable_account_fact_recovery(guard)
                if journal._schema_version == 18 and target_version >= 19:
                    journal.enable_account_event_recovery(guard)
                # I63-E135: the journal's whole-table integrity checks cost time in
                # proportion to its history and ran in every transaction. From schema
                # 14 on that made the owner too slow to dispatch on a journal with a
                # few hundred returns (105 ms per transaction at 256 facts). The
                # journal was checked in full when it was opened above; from here on
                # it is checked in full after any write by another connection and at
                # least once per interval.
                journal.set_deep_verification_interval(_JOURNAL_HISTORY_CHECK_SECONDS)
                if control_enabled:
                    from bt_api_gateway.direct_control import GatewayDirectControl
                    holder["control"] = GatewayDirectControl(journal, guard)
                    from bt_api_py.gateway_instrument_block import GatewayInstrumentBlock

                    # D63-59: a return that belongs to no known order freezes its contract for
                    # every strategy until it is attributed or an operator releases the cause.
                    def record_released(cause, digests):
                        # The strategy ledger is created later in this start-up and only with
                        # the ledger option; without it there is no external ledger to record in.
                        ledger = holder.get("strategy_ledger")
                        if ledger is None:
                            raise CtpGatewayTradingError("STRATEGY_LEDGER_UNAVAILABLE")
                        return ledger.record_released(cause, digests)

                    holder["instrument_block"] = GatewayInstrumentBlock(
                        journal=journal, control=holder["control"], record_released=record_released)
                    from bt_api_py.gateway_drain_observer import GatewayDrainTargetObserver

                    # The stored states of DRAIN targets follow the order returns without an
                    # operator's reconcile: a listing is current, also for a view-only operator.
                    holder["drain_observer"] = GatewayDrainTargetObserver(control=holder["control"])
                if required_monitor is not None:
                    from bt_api_py.gateway_required_monitor import RequiredMonitor

                    monitor = RequiredMonitor(required_monitor, generation)
                    monitor.initialize_on_owner(journal, holder["control"], guard)
                    holder["required_monitor"] = monitor
                session_type, provider_type = _load_ctp_types()
                # Opt-in only: the calibration must be acquired before TD login.
                # A failed sample leaves the clock capability unavailable while
                # preserving the existing trading/market startup paths.
                clock_calibration = None
                clock_refresher = None
                if clock_verifier_factory is not None:
                    try:
                        from bt_api_py.ctp_gateway_clock_source import capture_gateway_server_clock_calibration

                        verifier = clock_verifier_factory()
                        clock_calibration = capture_gateway_server_clock_calibration(
                            verifier, allowed_servers=clock_allowed_servers
                        )
                        if clock_refresh_settings is not None:
                            from bt_api_py.gateway_server_clock_refresh import GatewayServerClockRefresher
                            clock_refresher = GatewayServerClockRefresher(verifier, allowed_servers=clock_allowed_servers,
                                max_error_bound_ns=clock_refresh_settings.max_error_bound_ns,
                                max_sample_validity_seconds=clock_refresh_settings.max_sample_validity_seconds,
                                refresh_interval_seconds=clock_refresh_settings.refresh_interval_seconds,
                                claim_coordinator=holder['dispatch_claim_coordinator'])
                            holder['clock_refresher'] = clock_refresher
                            holder['dispatch_claim_wired'].add(
                                'gateway_server_clock_refresh.sample_change')
                            clock_refresher.start()
                    except Exception:
                        if clock_refresher is not None and not clock_refresher.stop(timeout_seconds=0):
                            raise CtpGatewayTradingError('CLOCK_REFRESH_CLOSE_UNCONFIRMED')
                        holder.pop('clock_refresher', None)
                        clock_refresher = None
                observation_options = (
                    {}
                    if account_read_settings is None
                    else {
                        "observation_issuer": issuer,
                        "observation_instruments": account_read_settings.observation_instruments,
                    }
                )
                session = session_type(
                    settings.account_key,
                    request_feed_factory,
                    market_stream_factory,
                    trusted_flow_root=settings.trusted_flow_root,
                    flow_account_scope=settings.account_scope,
                    source_ref=settings.market_source_ref,
                    entitlement_ref=settings.market_entitlement_ref,
                    connect_timeout_seconds=settings.connect_timeout_seconds,
                    stop_timeout_seconds=settings.session_stop_timeout_seconds,
                    queue_capacity=settings.session_queue_capacity,
                    **observation_options,
                )
                # Only the exact provider session can bind this owner-scoped
                # capability. It applies the hook to each freshly validated
                # MD stream before that stream starts its native client.
                from bt_api_ctp.gateway_session import CtpGatewaySession
                if type(session) is CtpGatewaySession:
                    session._bind_gateway_md_stream_dispatch_coordinator(
                        holder['dispatch_claim_coordinator'], account_scope=settings.account_scope,
                        service_generation=generation)
                    holder['dispatch_claim_wired'].add('ctp.md_stream.source_change')
                session.connect(generation=generation, timeout=settings.connect_timeout_seconds)
                holder["session"] = session
                from bt_api_ctp.ctp.client import TraderClient
                trader = getattr(session, '_trader', None)
                if type(trader) is TraderClient:
                    trader._bind_gateway_td_dispatch_coordinator(
                        holder['dispatch_claim_coordinator'], account_scope=settings.account_scope,
                        service_generation=generation)
                    holder['dispatch_claim_wired'].add('ctp.trader.connection_change')
                if strategy_origin_source_factory is not None:
                    from bt_api_py.gateway_strategy_origin_source import GatewayStrategyOriginSource
                    origin_source = strategy_origin_source_factory(journal, guard, session)
                    if type(origin_source) is not GatewayStrategyOriginSource:
                        raise CtpGatewayTradingError('INVALID_STRATEGY_ORIGIN')
                    holder['strategy_origin_source'] = origin_source
                if strategy_financial_source_factory is not None:
                    from bt_api_py.gateway_strategy_financial_source import GatewayStrategyFinancialSource
                    financial_source = strategy_financial_source_factory(journal, guard, session)
                    if type(financial_source) is not GatewayStrategyFinancialSource or not financial_source.bound_to(journal, guard):
                        raise CtpGatewayTradingError('INVALID_STRATEGY_FINANCIAL_SOURCE')
                    holder['strategy_financial_source'] = financial_source
                    financial_source.start()
                if strategy_financial_settings is not None:
                    from bt_api_py.ctp_gateway_strategy_financial import CtpGatewayStrategyFinancial

                    # Returns do not exist yet at this point; until they are complete the
                    # producer stores nothing and every strategy's cash is unavailable.
                    financial_source = CtpGatewayStrategyFinancial(
                        strategy_financial_settings, journal=journal, guard=guard,
                        returns_continuous=lambda: (holder.get("returns") is not None
                                                    and holder["returns"].ledger_continuous() is True),
                        last_price=last_prices.last).source()
                    holder['strategy_financial_source'] = financial_source
                    financial_source.start()
                if parent_enabled:
                    from bt_api_gateway.direct_parent_authority import GatewayParentAuthority
                    parent_authority = parent_authority_factory(journal, guard, session)
                    if (type(parent_authority) is not GatewayParentAuthority
                            or parent_authority._journal is not journal or parent_authority._guard is not guard):
                        raise CtpGatewayTradingError('INVALID_GATEWAY_PARENT')
                    holder['parent_authority'] = parent_authority
                if clock_calibration is not None:
                    try:
                        from bt_api_py.ctp_gateway_clock_source import CtpGatewayCalibratedClockSource

                        holder["clock_source"] = CtpGatewayCalibratedClockSource(
                            session, clock_calibration, max_age_seconds=300,
                            **({'refresher': clock_refresher} if clock_refresher is not None else {})
                        )
                    except Exception:
                        holder["clock_source"] = None
                provider = session.direct_provider
                account_binding = session.account_binding
                if type(provider) is not provider_type or not _ref(account_binding):
                    raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")
                admission = WriteAdmission({ROUTE: settings.write_enabled})
                coordinator = DirectWriteCoordinator(
                    admission, lambda route: provider if route == ROUTE else None
                )
                if coordinator.provider(ROUTE) is not provider:
                    raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")
                state: dict[str, Any] = {}

                if return_settings is not None:
                    from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance

                    state["returns"] = CtpGatewayReturnMaintenance(
                        session, provider, journal, guard, settings.account_key, return_settings,
                        **({"external_classifier": trusted_external_classifier}
                           if trusted_external_classifier is not None else {})
                    )
                    holder["returns"] = state["returns"]
                    lease_source = getattr(lease_drain_settings, "recovery_source", None)
                    if callable(getattr(lease_source, "bind", None)):
                        lease_source.bind(session=session, provider=provider, returns=state["returns"],
                                          journal=journal)
                    holder["execution_stream"] = state["returns"].create_execution_stream(
                        journal,
                        {key: grant.strategy_ref for key, grant in static_grants.items()},
                        execution_domain_ref=authority.scope.execution_domain_ref,
                    )
                    if ledger_enabled:
                        # I63-E111: without these owner ports and a baseline the
                        # journal can admit opens but never a close.
                        from bt_api_ctp.close_offset_policy import split_close_required
                        from bt_api_py.ctp_gateway_strategy_ledger import CtpGatewayStrategyLedger

                        state["strategy_ledger"] = CtpGatewayStrategyLedger(
                            journal=journal, guard=guard, session=session, provider=provider,
                            returns=state["returns"], instruments=return_settings.instruments,
                            account_scope=settings.account_scope, route=ROUTE,
                            service_generation=generation, split_close_required=split_close_required,
                            external_baseline=strategy_ledger_external_baseline,
                            comparison_refresh_seconds=strategy_ledger_comparison_refresh_seconds)
                        state["strategy_ledger"].install()
                        holder["strategy_ledger"] = state["strategy_ledger"]

                def gate(record):
                    return (
                        admission.is_enabled(ROUTE)
                        and state["controller"].ready
                        and state.get("returns") is not None
                        and state["returns"].dispatch_ready()
                        and assert_admitted(record) is True
                    )

                from bt_api_py.gateway_dispatch_claim import (
                    GatewayJournalDispatchChangePort,
                    GatewayNativeDispatchClaimPort,
                )

                claim_coordinator = holder['dispatch_claim_coordinator']
                # Every journal transaction (controls, parents, ledgers, markers)
                # now invalidates the journal-held dispatch domains first.
                journal.install_dispatch_change_port(
                    guard=guard, port=GatewayJournalDispatchChangePort(claim_coordinator))
                holder['dispatch_claim_wired'].add('direct_command_journal.authorizer')
                dispatcher = compose_gateway_direct_dispatcher(
                    coordinator,
                    ROUTE,
                    journal,
                    guard,
                    gate,
                    expected_local_provider=provider,
                    prepared_place=True,
                    claim_port=GatewayNativeDispatchClaimPort(
                        claim_coordinator,
                        journal_id=getattr(journal, '_journal_id', None)
                        or 'gateway-direct-command-journal'),
                )
                cancel_dispatcher = None
                if cancel_enabled:

                    def cancel_gate(_record):
                        if (
                            closing.is_set()
                            or not admission.is_enabled(ROUTE)
                            or coordinator.provider(ROUTE) is not provider
                            or session.connection_status() != "ready"
                        ):
                            return False
                        provider.assert_cancel_ready(expected_account_binding=account_binding)
                        native_generation = provider.session_generation()
                        return (
                            not closing.is_set()
                            and type(native_generation) is int
                            and 0 < native_generation < 2**63
                        )

                    cancel_dispatcher = compose_gateway_direct_cancel_dispatcher(
                        coordinator,
                        ROUTE,
                        journal,
                        guard,
                        dispatcher,
                        cancel_gate,
                        expected_local_provider=provider,
                    )
                controller = GatewaySettlementController(
                    coordinator,
                    ROUTE,
                    expected_local_provider=provider,
                    expected_account_binding=session.account_binding,
                    journal=journal,
                    dispatcher=dispatcher,
                )
                state["controller"] = controller
                holder["journal"] = journal

                if account_read_settings is not None:
                    from bt_api_gateway.account_read_model import GatewayAccountReadModel
                    from bt_api_gateway.event_journal import GatewayEventJournal

                    if not _distinct_journals(
                        settings.journal_path, account_read_settings.journal_path
                    ):
                        raise CtpGatewayTradingError("INVALID_ACCOUNT_READ_SETTINGS")
                    read_journal = GatewayEventJournal(account_read_settings.journal_path)
                    native_generation = provider.session_generation()
                    read_epoch = generation

                    def assert_current_generation(candidate):
                        dispatcher.assert_owned()
                        session.refresh_session()
                        if (
                            candidate != read_epoch
                            or session.account_binding != account_binding
                            or provider.session_generation() != native_generation
                        ):
                            raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")

                    def assert_read_recoverable():
                        dispatcher.assert_owned()
                        session.refresh_session()
                        if session.account_binding != account_binding or (
                            provider.session_generation() != native_generation
                            and state.get("returns") is None
                        ):
                            raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")

                    read_model = GatewayAccountReadModel(
                        read_journal,
                        account_key=settings.account_key,
                        journal_account_scope=settings.account_scope,
                        state_scope=account_read_settings.state_scope,
                        required_coverage=account_read_settings.required_coverage,
                        assert_current_generation=assert_current_generation,
                        refresh_interval_seconds=account_read_settings.refresh_interval_seconds,
                        max_staleness_seconds=account_read_settings.max_staleness_seconds,
                        request_timeout_seconds=account_read_settings.request_timeout_seconds,
                        require_open_orders=True,
                    )
                    read_model.start_generation(read_epoch)
                    holder["read_model"] = read_model
                    holder["read_journal"] = read_journal
                next_read = 0.0
                next_choice = "returns"
                read_rebind_pending = False
                read_rebind_attempt = 0
                last_confirmed_generation = provider.session_generation()
                if (type(last_confirmed_generation) is not int
                        or not 0 < last_confirmed_generation < 2**63):
                    raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")

                def connection_sample():
                    """Classify the exact session; no provider connection or native I/O."""
                    def status_or_reject():
                        try:
                            return session.connection_status()
                        except Exception:
                            # The provider returns explicit reconnecting/busy
                            # states; its exception means the exact source or
                            # immutable binding failed, never a retry token.
                            raise CtpGatewayTradingError("CTP_CONNECTION_PROBE_INVALID") from None

                    status = status_or_reject()
                    if status in {"reconnecting", "busy"}:
                        return status, None
                    if status != "ready":
                        raise CtpGatewayTradingError("CTP_CONNECTION_PROBE_INVALID")
                    first = provider.session_generation()
                    status = status_or_reject()
                    if status in {"reconnecting", "busy"}:
                        return status, None
                    second = provider.session_generation()
                    if (status != "ready" or type(first) is not int or type(second) is not int
                            or not 0 < first <= second < 2**63):
                        raise CtpGatewayTradingError("CTP_CONNECTION_PROBE_INVALID")
                    if second != first:
                        return "changed", second
                    return "ready", second

                def pause_for_reconnect():
                    # Neither the owner, its guard, nor its exact session is
                    # released; old account and write evidence is revoked.
                    returns = state.get("returns")
                    if returns is not None:
                        returns.pause_for_reconnect()
                    financial_source = holder.get('strategy_financial_source')
                    if financial_source is not None:
                        financial_source.invalidate()
                    controller.invalidate()
                    if read_model is not None:
                        # A read begun before the pause never completes after it (I63-E146): its
                        # answers belong to a link that was interrupted in between.
                        drop_model_read()
                        read_model.invalidate()

                def drop_model_read():
                    """Discard the read model's outstanding account read, if any; nothing of it is used."""
                    outstanding, model_read[0] = model_read[0], None
                    if outstanding is None:
                        return
                    token, request = outstanding
                    for discard, argument in ((session.cancel_read_account, request),
                                              (read_model.fail_refresh, token)):
                        try:
                            discard(argument)
                        except Exception as error:
                            # The session is closing or the model already started a new
                            # generation: the read is gone either way.
                            _LOG.debug("Outstanding account read not discarded cleanly: %s",
                                       getattr(error, "code", None) or type(error).__name__)

                def read_failure_is_recoverable(error, before):
                    from bt_api_ctp.account_provider import CtpAccountSessionProviderError

                    if not _connection_transition_error(error, account_read=True):
                        raise error
                    status, current = connection_sample()
                    if status != "ready" or current != before:
                        raise error
                    if _failed_account_read(error):
                        assert_read_recoverable()
                        if not model_read_failing[0]:
                            model_read_failing[0] = True
                            _LOG.warning(
                                "Account read failed (%s); the account read model is not ready "
                                "until a later read completes", error.code)
                        return
                    raise error

                # [next proactive refresh, next retry while not ready]
                settlement_due = [0.0, 0.0]
                settlement_refresh = float(settings.settlement_refresh_seconds)
                settlement_retry = min(1.0, settlement_refresh)
                # I63-E130: since when a due settlement observation has waited
                # for the session's query lane only (nothing of this owner
                # outstanding), and how long it waits before it runs anyway
                # and reports a lane that stays busy (an unanswered query) itself.
                settlement_lane_since = [None]
                settlement_lane_wait = 5.0
                # (read model token, account read request) of the read model's
                # outstanding request/response account read (I63-E130).
                model_read = [None]
                # Whether the last read of the read model failed (one log line per run of failures).
                model_read_failing = [False]

                def maintain_tick(before):
                    nonlocal next_read, next_choice, native_generation, read_epoch
                    nonlocal read_rebind_pending, read_rebind_attempt
                    session.refresh_session()
                    if read_model is not None:
                        current_native = provider.session_generation()
                        if current_native != native_generation or read_rebind_pending:
                            read_rebind_pending = True
                            read_rebind_attempt += 1
                            # A read begun on the previous native session is
                            # discarded with its token, never finished.
                            drop_model_read()
                            read_model.invalidate()
                            if state.get("returns") is not None:
                                state["returns"].refresh_generation()
                            native_generation = current_native
                            read_epoch = f"{generation}:native:{current_native}:attempt:{read_rebind_attempt}"
                            read_model.start_generation(read_epoch)
                            read_rebind_pending = False
                            next_read = 0.0
                        assert_current_generation(read_epoch)
                    # I63-E130: no provider query holds this owner thread except
                    # the settlement observation. Return queries and account
                    # reads (read model, strategy ledger baseline and comparison
                    # cut) are sent here and answered over later ticks. They
                    # share the session's query lane (one native query at a
                    # time, spaced), so at most one of them is outstanding: none
                    # starts while another is. A tick that ended a return query
                    # does not start the next one (orders are followed at once
                    # by trades), so the others get their turn in between.
                    # The settlement observation still waits for the venue on
                    # this thread (bounded by its own 2 s readback limit). It
                    # runs only with nothing outstanding and, normally, a lane
                    # that sends at once, so it waits for its answer only; while
                    # it is due no new query starts, so it is never starved.
                    # Re-observing every cycle starved queued owner work (market
                    # tasks, dispatch) on SimNow: a live confirmed ticket
                    # (validated locally by ``ready``) is refreshed before it
                    # expires; without one, retry at most once per second.
                    returns = state.get("returns")
                    strategy_ledger = state.get("strategy_ledger")

                    def account_read_outstanding():
                        return model_read[0] is not None or (
                            strategy_ledger is not None and strategy_ledger.read_pending)

                    def poll_model_read():
                        nonlocal next_read
                        token, request = model_read[0]
                        state["account_read_active"] = True
                        try:
                            observation = session.poll_read_account(request)
                        except Exception as error:
                            model_read[0] = None
                            next_read = (
                                time.monotonic() + account_read_settings.refresh_interval_seconds
                            )
                            read_model.fail_refresh(token)
                            read_failure_is_recoverable(error, before)
                        else:
                            if observation is not None:
                                model_read[0] = None
                                next_read = (time.monotonic()
                                             + account_read_settings.refresh_interval_seconds)
                                try:
                                    read_model.finish_refresh(token, observation)
                                except Exception as error:
                                    read_failure_is_recoverable(error, before)
                                else:
                                    if model_read_failing[0]:
                                        model_read_failing[0] = False
                                        _LOG.info("Account read completed; the account read model "
                                                  "is ready again")
                        state["account_read_active"] = False

                    query_outstanding = returns is not None and returns.query_pending()
                    now = time.monotonic()
                    settlement_held = False
                    if now >= settlement_due[0] or (
                            now >= settlement_due[1] and not controller.ready):
                        if query_outstanding or account_read_outstanding():
                            settlement_held, settlement_lane_since[0] = True, None
                        elif session.query_lane_ready() or (
                                settlement_lane_since[0] is not None
                                and now - settlement_lane_since[0] >= settlement_lane_wait):
                            settlement_lane_since[0] = None
                            settlement_due[0] = now + settlement_refresh
                            settlement_due[1] = now + settlement_retry
                            controller.tick()
                        else:
                            settlement_held = True
                            if settlement_lane_since[0] is None:
                                settlement_lane_since[0] = now
                    else:
                        settlement_lane_since[0] = None
                    query_ended = False
                    if returns is not None:
                        returns.poll()
                        query_ended = query_outstanding
                        query_outstanding = returns.advance_query()
                        query_ended = query_ended and not query_outstanding
                    if model_read[0] is not None:
                        poll_model_read()
                    # The ledger's baseline and comparison reads are account
                    # reads too: it polls its own read on every tick and starts
                    # one only when nothing else is outstanding.
                    if strategy_ledger is not None and (strategy_ledger.read_pending or (
                            not settlement_held and not query_outstanding
                            and model_read[0] is None)):
                        strategy_ledger.maintain()
                    now = time.monotonic()
                    idle = (not settlement_held and not query_outstanding
                            and not account_read_outstanding())
                    read_due = read_model is not None and idle and now >= next_read
                    return_due = (returns is not None and idle and not query_ended
                                  and returns.due(now))
                    choose_read = next_choice == "read" if read_due and return_due else read_due
                    if choose_read:
                        next_choice = "returns"
                        state["account_read_active"] = True
                        token = read_model.begin_refresh()
                        try:
                            request = session.begin_read_account(
                                generation=generation,
                                timeout=account_read_settings.request_timeout_seconds,
                            )
                        except Exception as error:
                            next_read = (
                                time.monotonic() + account_read_settings.refresh_interval_seconds
                            )
                            read_model.fail_refresh(token)
                            read_failure_is_recoverable(error, before)
                        else:
                            model_read[0] = (token, request)
                            # Sends the first query at once when the lane allows.
                            poll_model_read()
                        state["account_read_active"] = False
                    elif return_due:
                        next_choice = "read"
                        returns.start_query()
                    instrument_block = holder.get("instrument_block")
                    if instrument_block is not None:
                        instrument_block.maintain()
                    drain_observer = holder.get("drain_observer")
                    if drain_observer is not None:
                        drain_observer.maintain()

                def maintain():
                    nonlocal last_confirmed_generation
                    before = last_confirmed_generation
                    state["account_read_active"] = False
                    try:
                        status, current = connection_sample()
                        if status != "ready":
                            pause_for_reconnect()
                            if status == "changed":
                                if current <= last_confirmed_generation:
                                    raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")
                                last_confirmed_generation = current
                            return
                        if current < last_confirmed_generation:
                            raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")
                        if current > last_confirmed_generation:
                            pause_for_reconnect()
                            last_confirmed_generation = current
                            return
                        before = current
                        maintain_tick(before)
                        status, current = connection_sample()
                        if status != "ready" or current > before:
                            pause_for_reconnect()
                            if status == "changed" or (status == "ready" and current > before):
                                if current <= last_confirmed_generation:
                                    raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")
                                last_confirmed_generation = current
                            return
                        if current != before:
                            raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")
                        financial_source = holder.get('strategy_financial_source')
                        if financial_source is not None:
                            financial_source.maintain()
                            status, current = connection_sample()
                            if status != 'ready' or current != before:
                                pause_for_reconnect()
                                return
                        last_confirmed_generation = current
                    except Exception as error:
                        retry = False
                        if _connection_transition_error(
                            error, account_read=state["account_read_active"]
                        ):
                            try:
                                status, current = connection_sample()
                                retry = status in {"reconnecting", "busy", "changed"} or (
                                    status == "ready" and current > before
                                )
                            except Exception:
                                retry = False
                        if retry:
                            pause_for_reconnect()
                            if status in {"ready", "changed"}:
                                if current <= last_confirmed_generation:
                                    raise CtpGatewayTradingError("CTP_SESSION_BINDING_INVALID")
                                last_confirmed_generation = current
                            return
                        if state.get("returns") is not None:
                            state["returns"].begin_close()
                        if read_model is not None:
                            read_model.invalidate()
                        raise
                    except BaseException:
                        if state.get("returns") is not None:
                            state["returns"].begin_close()
                        if read_model is not None:
                            read_model.invalidate()
                        raise

                return GatewayDirectOwnerResources(
                    dispatcher,
                    journal,
                    cleanup,
                    maintain,
                    min(
                        settings.maintenance_interval_seconds,
                        account_read_settings.refresh_interval_seconds,
                    )
                    if account_read_settings is not None
                    else settings.maintenance_interval_seconds,
                    cancel_dispatcher=cancel_dispatcher,
                )
            except BaseException:
                raise GatewayOwnerStartupCleanupError(cleanup) from None

        owner = GatewayDirectOwner(
            owner_factory,
            queue_size=settings.queue_size,
            max_queue_wait_seconds=settings.max_queue_wait_seconds,
            busy_timeout_seconds=settings.busy_timeout_seconds,
            service_queue_size=32 if market_settings is None else market_settings.owner_queue_size,
        )

        def close_owner():
            closing.set()
            deadline = time.monotonic() + settings.close_timeout_seconds
            returns = holder.get("returns")
            if returns is not None:
                returns.begin_close()
            stream = holder.get("execution_stream")
            if stream is not None:
                stream.begin_close()
            if returns is not None and not returns.close(max(0.0, deadline - time.monotonic())):
                raise CtpGatewayTradingError("TRADING_OWNER_CLOSE_UNCONFIRMED")
            if stream is not None and not stream.close(max(0.0, deadline - time.monotonic())):
                raise CtpGatewayTradingError("TRADING_OWNER_CLOSE_UNCONFIRMED")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or owner.close(remaining) is not True:
                raise CtpGatewayTradingError("TRADING_OWNER_CLOSE_UNCONFIRMED")

        def ready():
            status = owner.status()
            return status.thread_alive and owner.binding() is not None

        market_hub = market_proxy = None
        hub_closed = proxy_closed = False

        def close_resources():
            nonlocal hub_closed, proxy_closed
            closing.set()
            monitor = holder.get("required_monitor")
            if monitor is not None and not monitor.close(settings.close_timeout_seconds):
                raise CtpGatewayTradingError("MONITOR_CLOSE_UNCONFIRMED")
            begin_error = None
            instrument_service = holder.get("instrument_service")
            if instrument_service is not None:
                instrument_service.begin_close()
                instrument_service.maintain_registrations(())
            if market_proxy is not None:
                try:
                    market_proxy.begin_close()
                except BaseException as exc:
                    begin_error = exc
            owner_error = None
            try:
                close_owner()
            except BaseException as exc:
                owner_error = exc
            if owner.closed:
                if market_proxy is not None and not proxy_closed:
                    try:
                        proxy_closed = market_proxy.close() is True
                    except BaseException:
                        pass
                if market_hub is not None and not hub_closed:
                    try:
                        market_hub.close()
                        hub_closed = True
                    except BaseException:
                        pass
            if (
                owner_error is not None
                or begin_error is not None
                or not owner.closed
                or (market_hub is not None and not hub_closed)
                or (market_proxy is not None and not proxy_closed)
            ):
                raise CtpGatewayTradingError("TRADING_OWNER_CLOSE_UNCONFIRMED")

        try:
            startup_deadline = time.monotonic() + settings.startup_timeout_seconds
            if owner.start(settings.startup_timeout_seconds) is not True:
                raise CtpGatewayTradingError(
                    holder.get("startup_code") or "TRADING_OWNER_START_FAILED"
                )
            journal = holder["journal"]
            if required_monitor is not None:
                monitor = holder["required_monitor"]
                monitor.start(owner, holder["control"])
                category = monitor.startup_probe(max(0.0, startup_deadline - time.monotonic()))
                if category == "INCOMPATIBLE":
                    raise CtpGatewayTradingError("MONITOR_INCOMPATIBLE")
            returns = holder.get("returns")
            strategy_ledger = holder.get("strategy_ledger")
            binding = GatewayDirectServiceBinding(
                journal,
                static_grants,
                owner=owner,
                # With the strategy ledger enabled nothing is reserved before its
                # baseline exists: an open that cannot be closed is not admitted.
                reservation_fence=(None if returns is None else returns.reservation_fence
                                   if strategy_ledger is None else strategy_ledger.reservation_fence),
                reservation_owner_serialized=returns is not None,
                execution_stream=holder.get("execution_stream"),
                execution_domain_ref=authority.scope.execution_domain_ref,
                cancel_enabled=cancel_enabled,
                control=holder.get("control"),
                control_grants=static_control_grants if control_enabled else None,
                market_gate_required=True,
                strategy_financial_enabled=facts_financial_enabled,
                strategy_origin_source=holder.get('strategy_origin_source'),
                market_gate_max_latency_seconds=gate_latency,
                market_gate_simnow_relaxed=gate_latency > 2.0,
                risk_only_submit_strategies=(frozenset() if delegation_settings is None
                                             else delegation_settings.risk_only_submit_strategies),
            )
            values = {"binding": binding, 'claim_coordinator': holder['dispatch_claim_coordinator'],
                      'claim_sealer': holder['dispatch_claim_sealer'],
                      'claim_wired': holder['dispatch_claim_wired']}
            if delegation_settings is not None:
                from bt_api_py.gateway_delegation_service import GatewayDelegationService

                effective_delegation = (replace(delegation_settings, parent_recheck=holder['parent_authority'].recheck_child)
                                        if parent_enabled else delegation_settings)
                values['delegation_service'] = GatewayDelegationService(effective_delegation,
                    owner=owner, journal=journal, instance_id=instance, scope_ref=config.scope_ref,
                    market_gate_required=True)
                if actor_stream_enabled:
                    if holder.get('returns') is None:
                        raise CtpGatewayTradingError('ACTOR_STREAM_UNAVAILABLE')
                    from bt_api_py.gateway_delegate_actor_stream_service import GatewayDelegateActorStreamService
                    values['delegate_actor_stream'] = GatewayDelegateActorStreamService(
                        owner, journal, holder['returns']._ledger, effective_delegation)
                actor_logical = {'direct.command_status', 'direct.cancel', 'direct.cancel_status', 'direct.release'}
                if any(grant.operations & actor_logical for grant in effective_delegation.grants):
                    if not cancel_enabled:
                        raise CtpGatewayTradingError('INVALID_GATEWAY_DELEGATION')
                    from bt_api_py.gateway_delegate_actor_service import GatewayDelegateActorService
                    def current_parent_grant(context, parent_operation):
                        from bt_api_py.service_registry import ServiceDispatchAdmission
                        admission = context.dispatch_admission
                        if type(admission) is not ServiceDispatchAdmission or not admission.matches_context(context):
                            return None
                        registry = admission._registry
                        with registry._lock:
                            row = registry._db.execute('SELECT scope_ref FROM sessions WHERE registration_id=?',
                                                       (context.registration_id,)).fetchone()
                            if row is None:
                                return None
                            if (parent_operation not in {'direct.parent.cancel', 'direct.parent.query'}
                                    or parent_operation != admission._binding.parent_operation):
                                return None
                            return registry.gateway_parent_grant(instance_id=instance, scope_ref=row['scope_ref'],
                                identity=context.identity, scope=context.scope, operation=parent_operation)
                    values['delegate_actor_service'] = GatewayDelegateActorService(
                        values['delegation_service'], release_target_recheck=(
                            holder['parent_authority'].check_release_target if parent_enabled else None),
                        parent_cancel_recheck=(holder['parent_authority'].check_cancel_target if parent_enabled else None),
                        parent_grant_resolver=current_parent_grant if parent_enabled else None)
            if parent_enabled:
                from bt_api_py.gateway_parent_service import GatewayParentService
                values['parent_service'] = GatewayParentService(parent_settings, owner=owner, journal=journal,
                    authority=holder['parent_authority'], instance_id=instance, scope_ref=config.scope_ref)
            if "instrument_block" in holder:
                # Shown to the host and to tests; the owner thread alone drives it.
                values["instrument_block"] = holder["instrument_block"]
            if lease_drain_settings is not None:
                from bt_api_py.gateway_lease_drain import GatewayLeaseDrain

                values["lease_drain"] = GatewayLeaseDrain(lease_drain_settings, instance_id=instance,
                    account_ref=settings.account_scope, owner=owner, control=holder["control"], binding=binding,
                    service_identity=authority.identity)
            if cancel_only is not None:
                from bt_api_py.gateway_cancel_only import GatewayCancelOnlyRun

                cancel_run = GatewayCancelOnlyRun(
                    instance_id=instance, account_ref=settings.account_scope, owner=owner,
                    control=holder["control"], operator=cancel_only[0], reason=cancel_only[1],
                    service_identity=authority.identity,
                    returns_ready=lambda: (holder.get("returns") is not None
                                           and holder["returns"].ledger_continuous() is True))
                # The account DRAIN cause is stored here, before the host serves: no
                # reservation can be admitted first. A failure fails the start.
                owner.submit_service_call(cancel_run.apply_on_owner).result(
                    timeout_seconds=settings.startup_timeout_seconds)
                values["cancel_only"] = cancel_run
            if market_settings is not None:
                from bt_api_gateway.market_hub import (
                    GatewayMarketHub,
                    MAX_TOTAL_MARKET_REPLAY_BYTES,
                )
                from bt_api_py.gateway_owner_market import GatewayOwnerMarketProvider

                market_proxy = GatewayOwnerMarketProvider(
                    owner,
                    lambda: holder["session"].market_provider,
                    clock_source_getter=lambda: holder.get("clock_source"),
                    call_timeout_seconds=market_settings.call_timeout_seconds,
                    max_handles=1024,
                    event_observer=None if last_prices is None else last_prices.observe,
                )
                market_hub = GatewayMarketHub(
                    market_proxy,
                    key_universe=market_settings.key_universe,
                    principal_allowed_keys=market_settings.principal_allowed_keys,
                    service_identity=authority.identity,
                    service_scope=authority.scope,
                    provider_cleanup_confirmed=lambda: owner.closed,
                    max_replay_bytes=min(
                        256 * 1024,
                        MAX_TOTAL_MARKET_REPLAY_BYTES // len(market_settings.key_universe),
                    ),
                    max_provider_events_per_maintain=market_settings.max_provider_events_per_maintain,
                )
                values["hub"] = market_hub
            if account_read_settings is not None:
                from bt_api_gateway.router import GatewayCommandKind, GatewayPrincipal
                from bt_api_gateway.service_component import (
                    GatewayReadService,
                    GatewayServiceAccountScope,
                )

                principals = {}
                strategy_refs = set()
                for principal in account_read_principals:
                    identity = authority.grants[principal][0].identity
                    if identity.role is WireRole.STRATEGY_CLIENT:
                        strategy_refs.add(identity.actor_ref)
                    principals[principal] = GatewayPrincipal(
                        principal,
                        frozenset({settings.account_scope}),
                        frozenset({account_read_settings.state_scope}),
                        frozenset({GatewayCommandKind.READ}),
                    )
                account = GatewayServiceAccountScope(
                    account_ref=settings.account_scope,
                    execution_domain_ref=authority.scope.execution_domain_ref,
                    journal_account_scope=settings.account_scope,
                    account_read_strategy_scope=account_read_settings.state_scope,
                    shared_account_facts_scope=account_read_settings.state_scope
                    if strategy_refs
                    else None,
                    shared_fact_strategy_refs=frozenset(strategy_refs),
                )
                values["read_service"] = GatewayReadService(
                    holder["read_journal"],
                    accounts={settings.account_scope: account},
                    principals=principals,
                    state_worker=holder["read_model"],
                )
                if extensions_enabled:
                    from bt_api_py.gateway_account_comparison import GatewayAccountComparison

                    comparison_principals = frozenset(
                        principal for principal in account_read_principals
                        if authority.grants[principal][0].identity.role is WireRole.APPLICATION_CLIENT
                        and any(ACCOUNT_COMPARISON_OPERATIONS <= resource.operations
                                for entry in authority.grants[principal] for resource in entry.resources
                                if resource.port == "service.account_read")
                    )
                    values["account_comparison"] = GatewayAccountComparison(owner, journal,
                        account_scope=settings.account_scope, principals=comparison_principals,
                        instruments=frozenset(account_read_settings.observation_instruments))
                    observer_principals = frozenset(
                        principal for principal in account_read_principals
                        if any(resource.operations & ACCOUNT_OBSERVER_OPERATIONS
                               for entry in authority.grants[principal] for resource in entry.resources
                               if resource.port == "service.account_read")
                    )
                    if observer_principals:
                        if holder.get("returns") is None:
                            raise CtpGatewayTradingError("ACCOUNT_OBSERVER_UNAVAILABLE")
                        from bt_api_py.gateway_account_observer import GatewayAccountObserver
                        values["account_observer"] = GatewayAccountObserver(
                            owner, journal, holder["returns"]._ledger, account_scope=settings.account_scope,
                            execution_domain_ref=authority.scope.execution_domain_ref,
                            principals=observer_principals,
                        )
                    fact_principals = frozenset(
                        principal for principal in account_read_principals
                        if any(resource.operations & ACCOUNT_FACTS_OPERATIONS
                               for entry in authority.grants[principal] for resource in entry.resources
                               if resource.port == "service.account_read")
                    )
                    if fact_principals:
                        if not facts_financial_enabled or holder.get("returns") is None:
                            raise CtpGatewayTradingError('ACCOUNT_FACTS_UNAVAILABLE')
                        from bt_api_py.gateway_account_facts import GatewayAccountFactsObserver
                        values['account_facts'] = GatewayAccountFactsObserver(owner, journal, holder['returns']._ledger,
                            account_scope=settings.account_scope, execution_domain_ref=authority.scope.execution_domain_ref,
                            principals=fact_principals, status_source=holder['returns'])
                from bt_api_py.gateway_instrument_service import GatewayInstrumentService

                instrument_service = GatewayInstrumentService(
                    owner, holder["session"], journal, values["read_service"],
                    account_read_settings.observation_instruments, account_read_principals,
                )
                holder["instrument_service"] = instrument_service
                values["instrument_service"] = instrument_service
            return ComponentResources.owned(values, ready=ready, close=close_resources)
        except BaseException as error:
            partial = ComponentResources.owned(
                {"binding": None}, ready=lambda: False, close=close_resources
            )
            surfaced = None
            if type(error) in (LocalAccountSessionGuardError, CtpGatewayTradingError) and (
                error.code in _SURFACED_GUARD_CODES
            ):
                # I63-E095: tell the operator to reconcile (or stop the other owner).
                surfaced = error.code
            raise ServiceResourceConstructionError(partial, monitor_incompatible=(
                type(error) is CtpGatewayTradingError and error.code == "MONITOR_INCOMPATIBLE"
            ), code=surfaced) from None

    def deployment_factory():
        # Before the Registry opens: refuse a storage layout another OS user
        # could write or redirect (the error code is a category only).
        _isolated_storage(settings, registry_path, account_read_settings)
        try:
            return GatewayOperatorDeployment(
                config, authority, registry_path, resource_provider=resources
            )
        except Exception:
            raise CtpGatewayTradingError("TRADING_DEPLOYMENT_INVALID") from None

    try:
        return GatewayOperatorProfile(profile_ref, "trading_owner", deployment_factory)
    except Exception:
        raise CtpGatewayTradingError("INVALID_TRADING_PROFILE") from None
