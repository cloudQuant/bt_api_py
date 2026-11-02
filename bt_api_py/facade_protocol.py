"""Public provider-neutral facade protocols used by SDK consumers.

These protocols describe call shape and normalized data boundaries. They do
not attest account, session, authorization, endpoint, write capability, or
provider identity. Write authorization remains inside the SDK execution gate
that owns the relevant capability.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from bt_api_base.account_session_protocol import (
    AccountPreflightRequest,
    AccountPreflightSnapshot,
    AccountSessionFacade,
    AccountSessionPort,
    AccountSessionSnapshot,
    SettlementConfirmationFacade,
    SettlementConfirmationOutcome,
    SettlementConfirmationPort,
    SettlementConfirmationReceipt,
    SettlementConfirmationRequest,
    SettlementVerificationReceipt,
)

try:
    from bt_api_base.account_session_protocol import AccountSessionWorkflowFacade
except ImportError:
    @runtime_checkable
    class AccountSessionWorkflowFacade(Protocol):
        """Compatibility shape when the optional base protocol is unavailable."""

        def account_session_snapshot(
            self,
            exchange_name: str,
        ) -> AccountSessionSnapshot: ...

        def account_preflight(
            self,
            request: AccountPreflightRequest,
        ) -> AccountPreflightSnapshot: ...

        def verify_account_settlement(
            self,
            exchange_name: str,
            *,
            deadline: datetime,
        ) -> SettlementVerificationReceipt: ...

try:
    from bt_api_base.account_session_protocol import SettlementConfirmationWorkflowFacade
except ImportError:
    @runtime_checkable
    class SettlementConfirmationWorkflowFacade(Protocol):
        """Compatibility shape when the optional base protocol is unavailable."""

        def confirm_settlement_once(
            self,
            exchange_name: str,
            request: SettlementConfirmationRequest,
            *,
            authorization: object,
        ) -> SettlementConfirmationOutcome: ...
from bt_api_base.account_balance_snapshot_protocol import (
    AccountBalanceSnapshot,
    AccountBalanceSnapshotPort,
)
from bt_api_base.account_position_snapshot_protocol import (
    AccountPositionSnapshot,
    AccountPositionSnapshotFailureCode,
    AccountPositionSnapshotPort,
)
from bt_api_base.execution_rules_protocol import (
    CommissionRuleFacts,
    ExecutionRuleDecision,
    ExecutionRuleRequest,
    ExecutionRulesPreparationPort,
    ExecutionRulesPreparationUnavailable,
    ExecutionRulesPort,
    ExecutionRulesSnapshotPort,
    InstrumentExecutionFacts,
    InstrumentExecutionRulesSnapshot,
)
from bt_api_base.execution_economics_protocol import (
    EconomicsStatus,
    EconomicsRevalidationStage,
    ExecutionEconomicsDecision,
    ExecutionEconomicsPort,
    ExecutionEconomicsRequest,
    RequirementKind,
)
from bt_api_base.execution_identity_protocol import (
    ExecutionIdentityAssurance,
    ExecutionIdentityAssurancePort,
    ExecutionIdentityAssuranceUnavailable,
)
from bt_api_base import execution_identity_protocol as _execution_identity_protocol

_execution_identity_snapshot_type = getattr(
    _execution_identity_protocol,
    "ExecutionIdentitySnapshot",
    None,
)
if isinstance(_execution_identity_snapshot_type, type):
    # Re-export the exact base-contract class when the installed base supplies
    # this additive capability. Keep older supported base versions importable.
    ExecutionIdentitySnapshot = _execution_identity_snapshot_type

_execution_identity_route_expectation_type = getattr(
    _execution_identity_protocol,
    "ExecutionIdentityRouteExpectation",
    None,
)
_execution_identity_scope_ref_type = getattr(
    _execution_identity_protocol,
    "ExecutionIdentityScopeRef",
    None,
)
_execution_identity_evidence_unavailable_type = getattr(
    _execution_identity_protocol,
    "ExecutionIdentityEvidenceUnavailable",
    None,
)
if isinstance(_execution_identity_route_expectation_type, type):
    ExecutionIdentityRouteExpectation = _execution_identity_route_expectation_type
if isinstance(_execution_identity_scope_ref_type, type):
    ExecutionIdentityScopeRef = _execution_identity_scope_ref_type
if isinstance(_execution_identity_evidence_unavailable_type, type):
    ExecutionIdentityEvidenceUnavailable = _execution_identity_evidence_unavailable_type

try:
    _account_risk_protocol = importlib.import_module("bt_api_base.account_risk_protocol")
except ModuleNotFoundError as exc:
    if exc.name != "bt_api_base.account_risk_protocol":
        raise
    _account_risk_protocol = None

_account_risk_contract_types = tuple(
    getattr(_account_risk_protocol, name, None) if _account_risk_protocol is not None else None
    for name in (
        "AccountRiskSnapshot",
        "AccountRiskSnapshotUnavailable",
        "ExecutionIdentityVectorBinding",
        "ExecutionIdentityVectorBindingRef",
    )
)
if all(isinstance(value, type) for value in _account_risk_contract_types):
    (
        AccountRiskSnapshot,
        AccountRiskSnapshotUnavailable,
        ExecutionIdentityVectorBinding,
        ExecutionIdentityVectorBindingRef,
    ) = _account_risk_contract_types
    _account_risk_fact_fields = getattr(
        _account_risk_protocol, "ACCOUNT_RISK_FACT_FIELDS", None
    )
    if (
        type(_account_risk_fact_fields) is tuple
        and _account_risk_fact_fields
        and all(type(name) is str and name for name in _account_risk_fact_fields)
        and len(set(_account_risk_fact_fields)) == len(_account_risk_fact_fields)
    ):
        ACCOUNT_RISK_FACT_FIELDS = _account_risk_fact_fields
    else:
        _account_risk_contract_types = None
else:
    _account_risk_contract_types = None
from bt_api_base.execution_correlation_protocol import (
    CancelOrderRequestProtocol,
    OrderCorrelation,
    OrderCorrelationIssuer,
    OrderRequestProtocol,
    SelectedExecutionRoute,
    TypedOrderProvider,
    VerifiedOrderAcknowledgement,
)
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.market_history_protocol import MarketBar
_market_history_provider_protocol_name = "bt_api_base.market_history_provider_protocol"
try:
    _market_history_provider_protocol = importlib.import_module(
        _market_history_provider_protocol_name
    )
except ModuleNotFoundError as _history_protocol_import_error:
    if _history_protocol_import_error.name != _market_history_provider_protocol_name:
        raise
    _market_history_provider_types = None
else:
    _market_history_provider_types = tuple(
        getattr(_market_history_provider_protocol, name, None)
        for name in (
            "MarketHistoryProviderAdapter",
            "MarketHistoryProviderBinding",
            "MarketHistoryProviderContractError",
            "MarketHistoryProviderFactory",
            "MarketHistoryProviderRegistryPort",
            "SelectedMarketHistoryRoute",
        )
    )
    if any(not isinstance(value, type) for value in _market_history_provider_types):
        _market_history_provider_types = None
    else:
        for _history_type_name, _history_type in zip(
            (
                "MarketHistoryProviderAdapter",
                "MarketHistoryProviderBinding",
                "MarketHistoryProviderContractError",
                "MarketHistoryProviderFactory",
                "MarketHistoryProviderRegistryPort",
                "SelectedMarketHistoryRoute",
            ),
            _market_history_provider_types,
        ):
            globals()[_history_type_name] = _history_type
from bt_api_base.market_stream_protocol import (
    MarketStreamContractError,
    MarketStreamEvent,
    MarketStreamIdentity,
    MarketStreamIdentityReceipt,
    MarketStreamIdentityStatus,
    MarketStreamPollResult,
    MarketStreamPort,
    MarketStreamRequest,
    MarketStreamSelector,
    MarketStreamState,
    MarketStreamSubscription,
)
from bt_api_base.market_orderbook_protocol import (
    MarketOrderBookContractError,
    MarketOrderBookEvent,
    MarketOrderBookLevel,
)
from bt_api_base.market_stream_provider_protocol import (
    MarketStreamChannelCapability,
    MarketStreamProviderAdapter,
    MarketStreamProviderBinding,
    MarketStreamProviderChannelBinding,
    MarketStreamProviderContractError,
    MarketStreamProviderFactory,
    MarketStreamProviderRegistryPort,
    MarketStreamProviderSession,
    SelectedMarketStreamRoute,
)
from bt_api_base.futures_expected_calendar_protocol import FuturesExpectedCalendarPort
from bt_api_base.order_position_evidence_protocol import (
    OrderPositionEvidence,
    OrderPositionEvidenceFacadePort,
    OrderPositionEvidencePort,
    OrderPositionEvidenceProviderPort,
    OrderPositionEvidenceProviderRegistryPort,
    OrderPositionEvidenceUnavailable,
    OrderPositionQuantities,
)
from bt_api_base.futures_market_protocol import FuturesProductKey
from bt_api_base.market_session_protocol import (
    MarketSessionCalendar,
    MarketSessionContractError,
    MarketSessionProviderFactory,
    MarketSessionProviderRegistryPort,
    MarketSessionProviderRouteError,
    MarketSessionResolution,
    MarketSessionResolutionStatus,
    MarketSessionResolver,
    MarketSessionSegmentKind,
    MarketSessionWindow,
    SelectedMarketSessionRoute,
)
from bt_api_base.managed_runtime_protocol import (
    ManagedRuntimeAccessMode,
    ManagedRuntimeBinding,
    ManagedRuntimeContractError,
    ManagedRuntimeFactory,
    ManagedRuntimeFactoryRegistryPort,
    ManagedRuntimeProviderProduct,
    OperatorWriteGrant,
    SelectedManagedRuntimeRoute,
)
from bt_api_py._contracts.models import CancelOrderRequest, OrderRequest, QueryOrderRequest
from bt_api_py.execution_capacity import ExecutionCapacityFacade, OrderCapacitySnapshot
from bt_api_py.futures_expected_calendar_composition import (
    FuturesExpectedCalendarCompositionError,
)
from bt_api_py.market_stream_contracts import MarketStreamError
from bt_api_py.market_tick_contracts import (
    DataClockBinding,
    MarketClockQuality,
    MarketEventCursor,
    MarketFreshnessStatus,
    MarketTickEvent,
    MarketTickPayload,
)
from bt_api_py.query_facade import QueryFacade
from bt_api_py.reconciliation import ReconciliationResult
from bt_api_py.service_market_tick_projection import (
    MarketTickIdentityStatus,
    MarketTickStreamIdentity,
    ServiceMarketTickProjector,
)


@runtime_checkable
class UnifiedBtApiFacade(Protocol):
    """BT-facing shape of the unified ``bt_api_py`` client facade.

    Route selection is limited to the public exchange-name list. Implementors
    must not require callers to inspect provider configuration values. Typed
    order requests are passed intact to the SDK; adapters must not rebuild them
    or remove managed identity fields. ``normalized=True`` selects the typed
    contract and grants no permission by itself.

    Event mappings returned by :meth:`poll_events` must be SDK-normalized
    records. Provider-native structs, Backtrader objects, credentials and
    endpoint data are outside this boundary. Optional read capabilities are
    intentionally separate protocols so a missing feature can fail closed
    without pretending every facade supports every market/account operation.
    """

    def list_exchanges(self) -> list[str]: ...

    async def async_make_order(
        self,
        exchange_name: str,
        request: OrderRequest,
        *,
        normalized: Literal[True] = True,
        budget_capability: object | None = None,
    ) -> Mapping[str, object]: ...

    async def async_cancel_order(
        self,
        exchange_name: str,
        request: CancelOrderRequest,
        *,
        normalized: Literal[True] = True,
        budget_capability: object | None = None,
    ) -> Mapping[str, object]: ...

    async def async_query_order(
        self,
        exchange_name: str,
        request: QueryOrderRequest,
        *,
        normalized: Literal[True] = True,
    ) -> Mapping[str, object]: ...

    def poll_events(
        self,
        exchange_name: str,
        *,
        max_raw_items: int | None = None,
        coalesce_market_snapshots: Iterable[str] = ("orderbook", "tick"),
    ) -> Iterable[Mapping[str, object]]: ...

    def close(self) -> None: ...


@runtime_checkable
class ExecutionConfigurableFacade(Protocol):
    """Optional configuration capability for an already-created facade.

    A caller must reject a configuration request when this optional method is
    absent. The method's presence is still only interface shape, not authority
    to activate a provider or account.
    """

    def configure_execution(self, execution_config: object) -> object: ...


@runtime_checkable
class CorrelatedOrderExecutionFacade(Protocol):
    """Optional SDK facade capability for reservation-bound order writes.

    The SDK selects exactly one provider-dispatch owner for a route: its
    provider-owned direct session or an explicitly configured managed runtime.
    No failure may fall back from one owner to the other. Callers supply a
    distinct SDK-issued authorization and a provider claim bound by
    ``claim_correlated_order`` to the same exact correlation and request. All
    current-session validation, reservation checks, one-shot consumption and
    provider dispatch remain inside the selected SDK/provider owner.
    The correlation, claim, and authorization are separate values; the
    correlation alone is not authority. A facade that cannot
    implement this full handoff must omit this capability; consumers must
    fail before dispatch rather than fall back to the legacy
    ``async_make_order`` or ``async_cancel_order`` methods. This additive
    capability does not change those legacy methods' semantics.
    Responses are SDK-normalized mappings and must be returned unchanged to
    the matching verifier. A queued/pending receipt is not an acknowledgement;
    callers must not infer acknowledgement from local submission status or a
    callback echo alone. The verifier can return a typed acknowledgement only
    after SDK/provider evidence is available and verified.

    Implementations must validate that the correlation's route, symbol, and
    client order ID match the intact request, and that the SDK reservation is
    still valid in its issuer registry. The exact claim and authorization
    passed to ``claim_correlated_order`` must be passed unchanged to dispatch.
    Submit consumes the one-shot dispatch reservation. Cancel must resolve
    and validate the already registered order target inside the SDK;
    provider-native cancel identity must not be reconstructed by the caller.
    """

    def prepare_order_correlation(self, exchange_name: str, symbol: str) -> OrderCorrelation: ...

    def claim_correlated_order(
        self,
        operation: Literal["submit", "cancel"],
        correlation: OrderCorrelation,
        request: OrderRequest | CancelOrderRequest,
        *,
        authorization: object,
    ) -> object: ...

    async def submit_correlated_order(
        self,
        correlation: OrderCorrelation,
        request: OrderRequest,
        *,
        claim: object,
        authorization: object,
    ) -> Mapping[str, object]: ...

    async def cancel_correlated_order(
        self,
        correlation: OrderCorrelation,
        request: CancelOrderRequest,
        *,
        claim: object,
        authorization: object,
    ) -> Mapping[str, object]: ...

    def verify_order_response(
        self, correlation: OrderCorrelation, response: Mapping[str, object]
    ) -> VerifiedOrderAcknowledgement | None: ...

    def verify_cancel_response(
        self, correlation: OrderCorrelation, response: Mapping[str, object]
    ) -> VerifiedOrderAcknowledgement | None: ...


@runtime_checkable
class CorrelatedOrderExecutionSelector(Protocol):
    """Optional SDK selector for one route-bound correlated execution facade.

    The caller selects an exact exchange route once and retains that returned
    SDK wrapper for the correlation's lifetime.  The wrapper must remain bound
    to the selected route/client generation; it never returns a provider
    implementation object.
    """

    def correlated_order_execution(
        self, exchange_name: str
    ) -> CorrelatedOrderExecutionFacade: ...


@runtime_checkable
class ExecutionRulesFacade(Protocol):
    """Optional unified-facade selector for neutral order-rule decisions.

    This is the Store/Broker preflight path. Its decision is not authorization,
    a claim, a reservation, or dispatch permission.
    """

    def execution_rules(self, exchange_name: str) -> ExecutionRulesPort: ...


@runtime_checkable
class ExecutionRulesSnapshotFacade(Protocol):
    """Optional selector for SDK-issued instrument and commission snapshots."""

    def execution_rules_snapshot(
        self, exchange_name: str
    ) -> ExecutionRulesSnapshotPort: ...


@runtime_checkable
class OrderPositionEvidenceSelector(Protocol):
    """Optional selector for source-issued current position evidence."""

    def order_position_evidence(
        self,
        exchange_name: str,
    ) -> OrderPositionEvidenceFacadePort: ...


@runtime_checkable
class ExecutionEconomicsFacade(Protocol):
    """Optional selector for read-only cash or margin evidence on one route."""

    def execution_economics(self, exchange_name: str) -> ExecutionEconomicsPort: ...


@runtime_checkable
class ExecutionIdentityAssuranceSelector(Protocol):
    """Optional selector for provider-issued execution-account identity proof.

    The returned port is bound to one exact SDK route and retains its issued
    assurance receipt. A configured account alias or the SDK identity mapping
    alone is not account proof; missing provider evidence raises the typed
    unavailable error. This capability establishes identity only and grants no
    order authorization.
    """

    def execution_identity_assurance(
        self,
        exchange_name: str,
    ) -> ExecutionIdentityAssurancePort: ...


@runtime_checkable
class ExecutionIdentitySnapshotSelector(Protocol):
    """Optional selector for an SDK-issued current ledger/config snapshot.

    Snapshot configuration declarations do not establish provider identity or
    write authority. Provider assurance is an independent capability. The
    vector digest method accepts exact snapshots and their exact provider
    assurances so the SDK can preserve its canonical cross-route identity
    encoding without exposing raw credential/config fields to the consumer.
    """

    def execution_identity_snapshot(
        self,
        exchange_name: str,
        *,
        expected_strategy_id: str,
        expected_account_alias: str | None = None,
    ) -> "ExecutionIdentitySnapshot": ...

    def validate_execution_identity_snapshot(
        self,
        snapshot: "ExecutionIdentitySnapshot",
    ) -> bool: ...

    def execution_identity_vector_digest(
        self,
        snapshots: tuple["ExecutionIdentitySnapshot", ...],
        assurances: tuple[ExecutionIdentityAssurance, ...],
    ) -> str: ...


@runtime_checkable
class ExecutionIdentityVectorBindingSelector(Protocol):
    """Issue and revalidate an SDK-owned multi-route identity vector."""

    def execution_identity_vector_binding(
        self,
        snapshots: tuple["ExecutionIdentitySnapshot", ...],
        assurances: tuple[ExecutionIdentityAssurance, ...],
    ) -> "ExecutionIdentityVectorBinding": ...

    def validate_execution_identity_vector_binding(
        self,
        binding: "ExecutionIdentityVectorBinding",
    ) -> bool: ...


@runtime_checkable
class ExecutionIdentityEvidenceSelector(Protocol):
    """Capture and revalidate one SDK-owned identity-evidence vector.

    Route expectations are caller configuration declarations. Implementations
    must compare them with exact selected SDK routes, capture provider
    assurance internally, and return only the SDK-issued vector binding. The
    selector never exposes a provider assurance port to framework consumers.
    """

    def capture_execution_identity_evidence(
        self,
        expectations: tuple["ExecutionIdentityRouteExpectation", ...],
    ) -> "ExecutionIdentityVectorBinding": ...

    def validate_execution_identity_evidence(
        self,
        evidence: "ExecutionIdentityVectorBinding",
        expectations: tuple["ExecutionIdentityRouteExpectation", ...],
    ) -> bool: ...


@runtime_checkable
class AccountRiskSnapshotSelector(Protocol):
    """Return immutable non-identity risk facts bound to an exact SDK vector."""

    def account_risk_snapshot_for_identity_binding(
        self,
        binding: "ExecutionIdentityVectorBinding",
        *,
        initialize_baseline: bool = False,
        reset_loss_latch: bool = False,
    ) -> "AccountRiskSnapshot": ...

    def validate_account_risk_snapshot(
        self,
        snapshot: "AccountRiskSnapshot",
    ) -> bool: ...


@runtime_checkable
class FuturesExpectedCalendarSelector(Protocol):
    """Optional selector for provider-owned expected futures calendars.

    This returns rule-derived calendar facts for one exact SDK route. It does
    not assert that the market is currently open or replace observed market
    clock/session evidence.
    """

    def futures_expected_calendar(
        self,
        exchange_name: str,
    ) -> FuturesExpectedCalendarPort: ...


@runtime_checkable
class QueryFacadeSelector(Protocol):
    """Optional selector for a provider-neutral query facade on one route."""

    def query_facade(self, exchange_name: str) -> QueryFacade: ...


@runtime_checkable
class MarketHistoryFacade(Protocol):
    """Optional exact-route selector for complete neutral historical bars.

    Returned rows are exact ``bt_api_base.MarketBar`` values and do not carry
    provider-specific fields. Callers requiring those fields must fail closed.
    """

    def fetch_market_history(
        self,
        exchange_name: str,
        instrument: MarketInstrumentKey,
        timeframe: str | None,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        preserve_raw_bars: bool = False,
    ) -> tuple[MarketBar, ...]: ...


def is_market_history_facade(value: object) -> bool:
    """Check selector shape only; the returned history remains provider-owned."""

    return isinstance(value, MarketHistoryFacade)


@runtime_checkable
class AccountBalanceSnapshotSelector(Protocol):
    """Optional selector for full-scope neutral account balance snapshots."""

    def account_balance_snapshot(
        self,
        exchange_name: str,
    ) -> AccountBalanceSnapshotPort: ...


@runtime_checkable
class AccountPositionSnapshotSelector(Protocol):
    """Optional selector for full-scope account position snapshots."""

    def account_position_snapshot(
        self,
        exchange_name: str,
    ) -> AccountPositionSnapshotPort: ...


class ReconciliationRequestHandle:
    """Opaque SDK handle for one runtime-issued reconciliation scope.

    Constructing a handle does not authorize a query. A route-bound SDK
    facade accepts only the exact handle object it issued and registered.
    Scope, account, strategy, and provider request data are never exposed here.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "<opaque SDK reconciliation request>"


@runtime_checkable
class ReconciliationFacadePort(Protocol):
    """Route-bound reconciliation API with SDK-issued scope handles only.

    A returned handle contains no caller-selected account/strategy scope. It
    must be checked by exact identity against the issuing SDK facade before a
    query. Snapshot validation receives an aware UTC time and returns exact
    ``True`` only while that same SDK result and its provider evidence remain
    valid.
    """

    def prepare_order_reconciliation(
        self,
        correlation: OrderCorrelation,
    ) -> ReconciliationRequestHandle: ...

    def prepare_authorized_account_reconciliation(self) -> ReconciliationRequestHandle: ...

    def snapshot(
        self,
        handle: ReconciliationRequestHandle,
        *,
        timeout: float = 5.0,
    ) -> ReconciliationResult: ...

    def validate_snapshot(
        self,
        handle: ReconciliationRequestHandle,
        result: ReconciliationResult,
        *,
        now: datetime,
    ) -> bool: ...


@runtime_checkable
class ReconciliationFacadeSelector(Protocol):
    """Optional selector for a provider-neutral reconciliation facade."""

    def reconciliation(self, exchange_name: str) -> ReconciliationFacadePort: ...


def is_reconciliation_facade(value: object) -> bool:
    """Check route-facade shape only; it does not prove a trusted resolver."""

    return isinstance(value, ReconciliationFacadePort)


def is_reconciliation_facade_selector(value: object) -> bool:
    """Check selector shape only; the returned SDK wrapper must validate scope."""

    return isinstance(value, ReconciliationFacadeSelector)


@runtime_checkable
class ExecutionCapacitySelector(Protocol):
    """Optional selector for bounded order-capacity facts on one route."""

    def order_capacity(self, exchange_name: str) -> ExecutionCapacityFacade: ...


@runtime_checkable
class MarketTickProjectorSelector(Protocol):
    """Optional exact-route selector for an SDK-owned live tick projector.

    The returned projector owns the provider subscription lifecycle. Its
    neutral events carry a clock binding; an unknown/unavailable clock or
    freshness status must remain unavailable to consumers.
    """

    def market_tick_projector(
        self,
        exchange_name: str,
        instrument: MarketInstrumentKey,
    ) -> ServiceMarketTickProjector: ...


@runtime_checkable
class MarketSessionResolverSelector(Protocol):
    """Optional exact-route selector for a neutral market-session resolver.

    The caller supplies its already-selected exact instrument and neutral
    calendar. A resolver interprets the event against that calendar; it does
    not create schedules, infer instruments from event strings, or make an
    unqualified clock fresh.
    """

    def market_session_resolver(
        self,
        exchange_name: str,
        instrument: MarketInstrumentKey,
    ) -> MarketSessionResolver: ...


def is_unified_bt_api_facade(value: object) -> bool:
    """Check runtime call-shape only; this result is never a write capability."""

    return isinstance(value, UnifiedBtApiFacade)


def is_unified_bt_api_facade_class(value: object) -> bool:
    """Check a factory class's call-shape only; this is not write authority."""

    if not isinstance(value, type):
        return False
    try:
        return issubclass(value, UnifiedBtApiFacade)
    except TypeError:
        return False


def is_execution_configurable_facade(value: object) -> bool:
    """Check whether an injected object exposes the optional config method."""

    return isinstance(value, ExecutionConfigurableFacade)


def is_correlated_order_execution_facade(value: object) -> bool:
    """Check capability shape only; never treat this result as authorization."""

    return isinstance(value, CorrelatedOrderExecutionFacade)


def is_correlated_order_execution_selector(value: object) -> bool:
    """Check selector shape only; it is neither provider proof nor authority."""

    return isinstance(value, CorrelatedOrderExecutionSelector)


def is_market_tick_projector_selector(value: object) -> bool:
    """Check selector shape only; it does not prove a clock or data source."""

    return isinstance(value, MarketTickProjectorSelector)


def is_market_stream_selector(value: object) -> bool:
    """Check selector shape only; it does not prove a source or freshness."""

    return isinstance(value, MarketStreamSelector)


def is_market_session_resolver_selector(value: object) -> bool:
    """Check selector shape only; it does not establish schedule evidence."""

    return isinstance(value, MarketSessionResolverSelector)


def is_order_position_evidence_selector(value: object) -> bool:
    """Check selector shape only; it does not establish position authority."""

    return isinstance(value, OrderPositionEvidenceSelector)


def is_futures_expected_calendar_selector(value: object) -> bool:
    """Check selector shape only; it does not establish calendar evidence."""

    return isinstance(value, FuturesExpectedCalendarSelector)


def is_account_balance_snapshot_selector(value: object) -> bool:
    """Check selector shape only; this does not establish account evidence."""

    return isinstance(value, AccountBalanceSnapshotSelector)


def is_account_position_snapshot_selector(value: object) -> bool:
    """Check selector shape only; this does not establish position evidence."""

    return isinstance(value, AccountPositionSnapshotSelector)


def is_account_risk_snapshot_selector(value: object) -> bool:
    """Check shape only; the SDK must still validate exact issued objects."""

    return isinstance(value, AccountRiskSnapshotSelector)


def is_execution_identity_evidence_selector(value: object) -> bool:
    """Check the optional SDK-owned identity capture/validation surface."""

    return isinstance(value, ExecutionIdentityEvidenceSelector)


__all__ = [
    "ManagedRuntimeAccessMode",
    "ManagedRuntimeBinding",
    "ManagedRuntimeContractError",
    "ManagedRuntimeFactory",
    "ManagedRuntimeFactoryRegistryPort",
    "ManagedRuntimeProviderProduct",
    "OperatorWriteGrant",
    "SelectedManagedRuntimeRoute",
    "AccountPreflightRequest",
    "AccountPreflightSnapshot",
    "AccountBalanceSnapshot",
    "AccountBalanceSnapshotPort",
    "AccountBalanceSnapshotSelector",
    "AccountPositionSnapshot",
    "AccountPositionSnapshotFailureCode",
    "AccountPositionSnapshotPort",
    "AccountPositionSnapshotSelector",
    "AccountRiskSnapshotSelector",
    "AccountSessionFacade",
    "AccountSessionPort",
    "AccountSessionSnapshot",
    "AccountSessionWorkflowFacade",
    "CommissionRuleFacts",
    "CorrelatedOrderExecutionFacade",
    "CorrelatedOrderExecutionSelector",
    "ExecutionCapacitySelector",
    "ExecutionCapacityFacade",
    "EconomicsStatus",
    "EconomicsRevalidationStage",
    "ExecutionEconomicsDecision",
    "ExecutionEconomicsFacade",
    "ExecutionEconomicsPort",
    "ExecutionEconomicsRequest",
    "ExecutionIdentityAssurance",
    "ExecutionIdentityAssurancePort",
    "ExecutionIdentityAssuranceSelector",
    "ExecutionIdentityAssuranceUnavailable",
    "ExecutionIdentityEvidenceSelector",
    "ExecutionIdentitySnapshotSelector",
    "ExecutionIdentityVectorBindingSelector",
    "FuturesExpectedCalendarPort",
    "FuturesExpectedCalendarCompositionError",
    "FuturesExpectedCalendarSelector",
    "ExecutionRuleDecision",
    "ExecutionRuleRequest",
    "ExecutionRulesFacade",
    "ExecutionRulesPreparationPort",
    "ExecutionRulesPreparationUnavailable",
    "ExecutionRulesPort",
    "ExecutionRulesSnapshotFacade",
    "ExecutionRulesSnapshotPort",
    "InstrumentExecutionRulesSnapshot",
    "ExecutionConfigurableFacade",
    "InstrumentExecutionFacts",
    "MarketInstrumentKey",
    "MarketBar",
    "MarketHistoryFacade",
    "MarketClockQuality",
    "MarketEventCursor",
    "MarketFreshnessStatus",
    "MarketTickEvent",
    "MarketTickPayload",
    "DataClockBinding",
    "MarketTickProjectorSelector",
    "MarketTickIdentityStatus",
    "MarketTickStreamIdentity",
    "MarketStreamContractError",
    "MarketStreamError",
    "MarketOrderBookContractError",
    "MarketOrderBookEvent",
    "MarketOrderBookLevel",
    "MarketStreamChannelCapability",
    "MarketStreamProviderAdapter",
    "MarketStreamProviderBinding",
    "MarketStreamProviderChannelBinding",
    "MarketStreamProviderContractError",
    "MarketStreamProviderFactory",
    "MarketStreamProviderRegistryPort",
    "MarketStreamProviderSession",
    "MarketStreamEvent",
    "MarketStreamIdentity",
    "MarketStreamIdentityReceipt",
    "MarketStreamIdentityStatus",
    "MarketStreamPollResult",
    "MarketStreamPort",
    "MarketStreamRequest",
    "MarketStreamSelector",
    "MarketStreamState",
    "MarketStreamSubscription",
    "SelectedMarketStreamRoute",
    "MarketSessionCalendar",
    "MarketSessionContractError",
    "MarketSessionProviderFactory",
    "MarketSessionProviderRegistryPort",
    "MarketSessionProviderRouteError",
    "MarketSessionResolution",
    "MarketSessionResolutionStatus",
    "MarketSessionResolver",
    "MarketSessionResolverSelector",
    "MarketSessionSegmentKind",
    "MarketSessionWindow",
    "FuturesProductKey",
    "SelectedMarketSessionRoute",
    "OrderCorrelation",
    "OrderCorrelationIssuer",
    "OrderRequestProtocol",
    "CancelOrderRequestProtocol",
    "SelectedExecutionRoute",
    "OrderCapacitySnapshot",
    "OrderPositionEvidence",
    "OrderPositionEvidenceFacadePort",
    "OrderPositionEvidencePort",
    "OrderPositionEvidenceProviderPort",
    "OrderPositionEvidenceProviderRegistryPort",
    "OrderPositionEvidenceSelector",
    "OrderPositionEvidenceUnavailable",
    "OrderPositionQuantities",
    "QueryFacadeSelector",
    "QueryFacade",
    "ReconciliationFacadeSelector",
    "ReconciliationFacadePort",
    "ReconciliationRequestHandle",
    "ReconciliationResult",
    "RequirementKind",
    "SettlementConfirmationFacade",
    "SettlementConfirmationOutcome",
    "SettlementConfirmationPort",
    "SettlementConfirmationReceipt",
    "SettlementConfirmationRequest",
    "SettlementConfirmationWorkflowFacade",
    "SettlementVerificationReceipt",
    "TypedOrderProvider",
    "UnifiedBtApiFacade",
    "VerifiedOrderAcknowledgement",
    "is_correlated_order_execution_facade",
    "is_correlated_order_execution_selector",
    "is_market_tick_projector_selector",
    "is_market_stream_selector",
    "is_market_session_resolver_selector",
    "is_market_history_facade",
    "is_order_position_evidence_selector",
    "is_futures_expected_calendar_selector",
    "is_account_balance_snapshot_selector",
    "is_account_position_snapshot_selector",
    "is_account_risk_snapshot_selector",
    "is_execution_identity_evidence_selector",
    "is_reconciliation_facade",
    "is_reconciliation_facade_selector",
    "is_execution_configurable_facade",
    "is_unified_bt_api_facade",
    "is_unified_bt_api_facade_class",
]

if isinstance(_execution_identity_snapshot_type, type):
    __all__.append("ExecutionIdentitySnapshot")

if isinstance(_execution_identity_route_expectation_type, type):
    __all__.append("ExecutionIdentityRouteExpectation")
if isinstance(_execution_identity_scope_ref_type, type):
    __all__.append("ExecutionIdentityScopeRef")
if isinstance(_execution_identity_evidence_unavailable_type, type):
    __all__.append("ExecutionIdentityEvidenceUnavailable")

if _account_risk_contract_types is not None:
    __all__.extend(
        (
            "AccountRiskSnapshot",
            "AccountRiskSnapshotUnavailable",
            "ACCOUNT_RISK_FACT_FIELDS",
            "ExecutionIdentityVectorBinding",
            "ExecutionIdentityVectorBindingRef",
        )
    )

if _market_history_provider_types is not None:
    __all__.extend(
        (
            "MarketHistoryProviderAdapter",
            "MarketHistoryProviderBinding",
            "MarketHistoryProviderContractError",
            "MarketHistoryProviderFactory",
            "MarketHistoryProviderRegistryPort",
            "SelectedMarketHistoryRoute",
        )
    )
