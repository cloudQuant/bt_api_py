"""Trusted injection bridge for one managed CTP order-provider context.

The module does not create caller identity, account mapping, cancel authority,
Basic/Risk gates, or a RegisteredSim native authority.  A host that already
owns an authenticated CTP execution authority may inject that exact object
into ``CtpExecutionRuntimeConfig.order_provider_dependency_factory``.  The
authority source must bind itself to every exact runtime/feed/session object
and validate the opaque caller authorization; route profiles and framework
write grants are not authorization inputs.

``ExecutionAdmissionContextV1`` is only the trusted BasicAdmission context.
The existing ``ExecutionBasicAdmissionGate`` and Risk gate on the runtime
remain responsible for issuing and consuming the final dispatch claims.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Literal, Protocol, runtime_checkable

from bt_api_base.execution_correlation_protocol import (
    CancelOrderRequestProtocol,
    OrderCorrelation,
    OrderRequestProtocol,
    SelectedExecutionRoute,
)
from bt_api_base.managed_execution_runtime_protocol import OwnerThreadCallPort
from bt_api_execution.basic_admission import (
    ExecutionAdmissionContextV1,
    ExecutionBasicAdmissionGate,
)
from bt_api_execution.contracts import ExecutionScope
from bt_api_execution.facade import ManagedExecutionFacade
from bt_api_execution.cancellation import ManagedCancellationFacade

from .ctp_execution_runtime import (
    CtpRuntimeOrderProviderDependenciesV1,
    CtpRuntimeOrderProviderDependencyContextV1,
)

__all__ = (
    "CTP_FUTURE_EXCHANGE_NAME",
    "CtpOrderProviderAuthoritySourceV1",
    "CtpRuntimeOrderProviderDependenciesFactoryV1",
    "CtpTrustedOrderProviderDependencyError",
)


CTP_FUTURE_EXCHANGE_NAME = "CTP___FUTURE"


class CtpTrustedOrderProviderDependencyError(RuntimeError):
    """Finite failure for explicit managed-order authority injection."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@runtime_checkable
class CtpOrderProviderAuthoritySourceV1(Protocol):
    """Explicit host-owned source of CTP account and caller authorization.

    Implementations must authenticate ``authorization`` using their own
    trusted caller/session state.  In particular, a route profile, provider
    configuration, ``DirectWriteGrant``, or truthy opaque object is not proof
    of caller authority.  The source must bind to the exact selected feed,
    TraderClient, session generation, execution owner, Store, Basic/Risk gates
    and facades carried by ``context``. The operator-grant attempt hooks below
    are mandatory: ``claim`` reserves one bounded operation by exact
    correlation/request, while ``dispatch`` revalidates its expiry and limits
    without counting the same correlation again. A missing hook fails
    provider dependency construction closed.

    ``resolve_registered_sim_authority`` may return the exact already-existing
    SDK-owned authority bound to this context's TraderClient.  It must not
    construct a disconnected authority or infer one from the environment.
    Returning ``None`` explicitly leaves the RegisteredSim write route
    unavailable.
    """

    def is_bound_to(
        self,
        context: CtpRuntimeOrderProviderDependencyContextV1,
        *,
        exchange_name: str,
    ) -> bool: ...

    def validate_request_account(
        self,
        request: OrderRequestProtocol | CancelOrderRequestProtocol,
        scope: ExecutionScope,
        trader_client: object,
    ) -> bool: ...

    def resolve_submit_context(
        self,
        authorization: object,
        correlation: OrderCorrelation,
        request: OrderRequestProtocol,
        scope: ExecutionScope,
    ) -> ExecutionAdmissionContextV1 | None: ...

    def authorize_operator_order_attempt(
        self,
        correlation: OrderCorrelation,
        request: OrderRequestProtocol,
        scope: ExecutionScope,
        *,
        runtime_context: CtpRuntimeOrderProviderDependencyContextV1,
        stage: Literal["claim", "dispatch"],
    ) -> bool: ...

    def validate_cancel_authorization(
        self,
        authorization: object,
        correlation: OrderCorrelation,
        request: CancelOrderRequestProtocol,
        scope: ExecutionScope,
    ) -> bool: ...

    def authorize_operator_cancel_attempt(
        self,
        correlation: OrderCorrelation,
        request: CancelOrderRequestProtocol,
        scope: ExecutionScope,
        *,
        runtime_context: CtpRuntimeOrderProviderDependencyContextV1,
        stage: Literal["claim", "dispatch"],
    ) -> bool: ...

    def resolve_registered_sim_authority(
        self,
        context: CtpRuntimeOrderProviderDependencyContextV1,
        *,
        exchange_name: str,
    ) -> object | None: ...


def _owner_context_is_exact(
    context: CtpRuntimeOrderProviderDependencyContextV1,
) -> bool:
    """Check that the runtime's existing managed owners are the exact inputs."""

    if (
        threading.current_thread() is not context.owner_thread
        or type(context.execution_scope) is not ExecutionScope
        or type(context.session_generation_id) is not str
        or not context.session_generation_id
        or context.selected_feed is None
        or context.trader_client is None
        or context.store is None
        or context.execution_owner is None
        or context.submit_port is None
        or context.cancel_port is None
        or type(context.basic_admission_gate) is not ExecutionBasicAdmissionGate
        or context.order_admission_gate is None
        or context.cancel_admission_gate is None
        or not isinstance(context.owner_thread_invoker, OwnerThreadCallPort)
        or context.owner_thread_invoker is None
    ):
        return False
    if (
        type(context.execution_facade) is not ManagedExecutionFacade
        or context.execution_facade.store is not context.store
        or context.execution_facade.scope is not context.execution_scope
        or type(context.cancellation_facade) is not ManagedCancellationFacade
        or context.cancellation_facade.store is not context.store
        or context.cancellation_facade.scope is not context.execution_scope
        or context.basic_admission_gate._store is not context.store
        or context.execution_facade._basic_admission_gate
        is not context.basic_admission_gate
        or context.execution_facade._admission_gate is not context.order_admission_gate
        or context.cancellation_facade._admission_gate is not context.cancel_admission_gate
        or context.execution_owner is not getattr(context.submit_port, "execution_owner", None)
        or context.submit_port._store is not context.store
        or context.submit_port._scope is not context.execution_scope
        or context.submit_port._trader is not context.trader_client
        or context.submit_port._gate is not context.basic_admission_gate
        or context.submit_port._risk_bridge is not context.order_admission_gate
        or context.cancel_port._owner is not context.execution_owner
        or context.cancel_port._store is not context.store
        or context.cancel_port._scope is not context.execution_scope
        or context.cancel_port._admission_gate is not context.cancel_admission_gate
    ):
        return False
    try:
        from bt_api_ctp.ctp.client import TraderClient
        from bt_api_ctp.feeds.live_ctp_feed import (
            CtpRequestDataFuture,
            _CtpRuntimeOwnedRequestFeedBinding,
        )

        if (
            type(context.selected_feed) is not CtpRequestDataFuture
            or type(context.trader_client) is not TraderClient
            or context.selected_feed.trader_client is not context.trader_client
            or context.selected_feed._connected is not True
        ):
            return False
        owner = context.execution_owner
        feed = context.selected_feed
        client = context.trader_client
        binding = feed._managed_runtime_binding
        runtime = getattr(binding, "_runtime", None)
        if (
            owner._trader is not context.trader_client
            or owner._thread is not context.owner_thread
            or owner._session_generation_id != context.session_generation_id
            or context.submit_port._thread is not context.owner_thread
            or context.cancel_port._thread is not context.owner_thread
            or type(binding) is not _CtpRuntimeOwnedRequestFeedBinding
            or binding._feed is not feed
            or binding._trader is not client
            or binding._execution_owner is not owner
            or binding._owner_thread is not context.owner_thread
            or binding._session_generation_id != context.session_generation_id
            or binding._owner_thread_invoker is not context.owner_thread_invoker
            or runtime is None
            or runtime._borrowed_request_feed is not feed
            or runtime._trader is not client
            or runtime._execution_owner is not owner
            or runtime._session_generation_id != context.session_generation_id
            or runtime.owner_thread is not context.owner_thread
            or feed.td_front != feed._execution_bound_td_front
            or feed.md_front != feed._execution_bound_md_front
            or feed.broker_id != feed._execution_bound_broker_id
            or feed.user_id != feed._execution_bound_user_id
            or client._bound_front != feed._execution_bound_td_front
            or client._bound_md_front != feed._execution_bound_md_front
            or client._bound_broker_id != feed._execution_bound_broker_id
            or client._bound_user_id != feed._execution_bound_user_id
            or client.ctp_env_profile != feed._execution_bound_profile
        ):
            return False
    except Exception:
        return False
    return True


def _create_selected_ctp_position_evidence_pair(
    context: CtpRuntimeOrderProviderDependencyContextV1,
) -> tuple[object, object] | None:
    """Create rules/position evidence only from a verified selected CTP feed."""

    if (
        type(context) is not CtpRuntimeOrderProviderDependencyContextV1
        or threading.current_thread() is not context.owner_thread
        or not _owner_context_is_exact(context)
    ):
        return None
    try:
        feed = context.selected_feed
        if not _selected_feed_environment_is_verified_demo(feed):
            return None

        from bt_api_ctp.execution_economics_registration import (
            CtpExecutionEconomicsOwner,
        )
        from bt_api_ctp.execution_rules_source_issuer import (
            CtpExecutionRulesSnapshotSourceDependenciesV1,
        )
        from bt_api_ctp.order_position_evidence_provider import (
            create_ctp_order_position_evidence_issuer,
        )
        from bt_api_ctp.registered_sim_session import (
            capture_query_session,
            validate_query_session,
        )

        binding = capture_query_session(
            context.trader_client,
            environment="demo",
        )
        if (
            binding.environment != "demo"
            or validate_query_session(
                context.trader_client,
                binding,
                now_monotonic=time.monotonic(),
            )
            is not True
        ):
            return None
        source_dependencies = CtpExecutionRulesSnapshotSourceDependenciesV1(
            request_feed=feed,
            query_session_binding=binding,
        )
        issuer = create_ctp_order_position_evidence_issuer(source_dependencies)
        owner = CtpExecutionEconomicsOwner(issuer)
        if owner._supports_rules_snapshots is not True:
            return None
        return owner, issuer
    except Exception:
        return None


def _selected_feed_environment_is_verified_demo(feed: object) -> bool:
    try:
        get_environment_info = getattr(feed, "get_environment_info", None)
        if not callable(get_environment_info):
            return False
        environment_info = get_environment_info()
        client = getattr(feed, "trader_client", None)
        return (
            type(environment_info) is dict
            and environment_info.get("environment") == "demo"
            and environment_info.get("verified") is True
            and environment_info.get("simulated") is True
            and type(environment_info.get("profile")) is str
            and environment_info.get("profile")
            == getattr(feed, "ctp_env_profile", None)
            and getattr(feed, "ctp_environment", None) == "simnow"
            and getattr(feed, "td_front", None)
            == getattr(feed, "_execution_bound_td_front", None)
            and getattr(feed, "md_front", None)
            == getattr(feed, "_execution_bound_md_front", None)
            and getattr(client, "ctp_env_profile", None)
            == environment_info.get("profile")
            and getattr(client, "_bound_front", None)
            == getattr(feed, "_execution_bound_td_front", None)
            and getattr(client, "_bound_md_front", None)
            == getattr(feed, "_execution_bound_md_front", None)
        )
    except Exception:
        return False


class _BoundAuthorityMethods:
    """Private request adapters sealed to one source and exact runtime context."""

    __slots__ = (
        "_factory",
        "_context",
        "_source",
        "_operator_order_attempts",
        "_operator_order_lock",
        "_operator_cancel_attempts",
        "_operator_cancel_lock",
    )

    def __init__(
        self,
        factory: "CtpRuntimeOrderProviderDependenciesFactoryV1",
        context: CtpRuntimeOrderProviderDependencyContextV1,
    ) -> None:
        self._factory = factory
        self._context = context
        self._source = factory.authority_source
        # The CTP managed bridge calls the submit resolver twice for one
        # correlated operation: once while creating the local provider claim,
        # and again immediately before the durable Basic/Risk submit call.
        # Keep those stages explicit so a trusted host grant authority can
        # reserve one correlation once and revalidate it without double-counting.
        self._operator_order_attempts: dict[str, tuple[object, object, str]] = {}
        self._operator_order_lock = threading.Lock()
        self._operator_cancel_attempts: dict[str, tuple[object, object, str]] = {}
        self._operator_cancel_lock = threading.Lock()

    def _valid_call(
        self,
        scope: ExecutionScope,
        trader_client: object | None = None,
    ) -> bool:
        if (
            scope is not self._context.execution_scope
            or (trader_client is not None and trader_client is not self._context.trader_client)
            or not _owner_context_is_exact(self._context)
            or self._source is not self._factory.authority_source
        ):
            return False
        return self._factory._source_is_bound(self._context)

    def validate_request_account(
        self,
        request: OrderRequestProtocol | CancelOrderRequestProtocol,
        scope: ExecutionScope,
        trader_client: object,
    ) -> bool:
        if not self._valid_call(scope, trader_client):
            return False
        try:
            result = self._source.validate_request_account(
                request,
                scope,
                trader_client,
            )
        except Exception:
            return False
        return type(result) is bool and result is True

    def resolve_submit_context(
        self,
        authorization: object,
        correlation: OrderCorrelation,
        request: OrderRequestProtocol,
        scope: ExecutionScope,
    ) -> ExecutionAdmissionContextV1 | None:
        if (
            authorization is None
            or type(authorization) is bool
            or type(correlation) is not OrderCorrelation
            or not self._valid_call(scope)
        ):
            return None
        stage = self._next_operator_attempt_stage(
            self._operator_order_attempts,
            self._operator_order_lock,
            correlation,
            request,
        )
        if stage is None:
            return None
        operator_authorizer = getattr(
            self._source,
            "authorize_operator_order_attempt",
            None,
        )
        if not callable(operator_authorizer):
            self._finish_operator_attempt(
                self._operator_order_attempts,
                self._operator_order_lock,
                correlation,
                request,
                stage,
                accepted=False,
            )
            return None
        try:
            authorized = operator_authorizer(
                correlation,
                request,
                scope,
                runtime_context=self._context,
                stage=stage,
            )
        except Exception:
            authorized = False
        if type(authorized) is not bool or authorized is not True:
            self._finish_operator_attempt(
                self._operator_order_attempts,
                self._operator_order_lock,
                correlation,
                request,
                stage,
                accepted=False,
            )
            return None
        try:
            result = self._source.resolve_submit_context(
                authorization,
                correlation,
                request,
                scope,
            )
        except Exception:
            self._finish_operator_attempt(
                self._operator_order_attempts,
                self._operator_order_lock,
                correlation,
                request,
                stage,
                accepted=False,
            )
            return None
        if (
            type(result) is not ExecutionAdmissionContextV1
            or result.execution_scope is not scope
            or result.action != "orders.submit"
        ):
            self._finish_operator_attempt(
                self._operator_order_attempts,
                self._operator_order_lock,
                correlation,
                request,
                stage,
                accepted=False,
            )
            return None
        self._finish_operator_attempt(
            self._operator_order_attempts,
            self._operator_order_lock,
            correlation,
            request,
            stage,
            accepted=True,
        )
        return result

    def validate_cancel_authorization(
        self,
        authorization: object,
        correlation: OrderCorrelation,
        request: CancelOrderRequestProtocol,
        scope: ExecutionScope,
    ) -> bool:
        if (
            authorization is None
            or type(authorization) is bool
            or type(correlation) is not OrderCorrelation
            or not self._valid_call(scope)
        ):
            return False
        stage = self._next_operator_attempt_stage(
            self._operator_cancel_attempts,
            self._operator_cancel_lock,
            correlation,
            request,
        )
        if stage is None:
            return False
        operator_authorizer = getattr(
            self._source,
            "authorize_operator_cancel_attempt",
            None,
        )
        if not callable(operator_authorizer):
            self._finish_operator_attempt(
                self._operator_cancel_attempts,
                self._operator_cancel_lock,
                correlation,
                request,
                stage,
                accepted=False,
            )
            return False
        try:
            authorized = operator_authorizer(
                correlation,
                request,
                scope,
                runtime_context=self._context,
                stage=stage,
            )
        except Exception:
            authorized = False
        if type(authorized) is not bool or authorized is not True:
            self._finish_operator_attempt(
                self._operator_cancel_attempts,
                self._operator_cancel_lock,
                correlation,
                request,
                stage,
                accepted=False,
            )
            return False
        try:
            result = self._source.validate_cancel_authorization(
                authorization,
                correlation,
                request,
                scope,
            )
        except Exception:
            self._finish_operator_attempt(
                self._operator_cancel_attempts,
                self._operator_cancel_lock,
                correlation,
                request,
                stage,
                accepted=False,
            )
            return False
        accepted = type(result) is bool and result is True
        self._finish_operator_attempt(
            self._operator_cancel_attempts,
            self._operator_cancel_lock,
            correlation,
            request,
            stage,
            accepted=accepted,
        )
        return accepted

    @staticmethod
    def _next_operator_attempt_stage(
        attempts: dict[str, tuple[object, object, str]],
        lock: threading.Lock,
        correlation: OrderCorrelation,
        request: object,
    ) -> str | None:
        key = correlation.reservation_ref
        with lock:
            previous = attempts.get(key)
            if previous is None:
                attempts[key] = (correlation, request, "claim_pending")
                return "claim"
            if (
                previous[0] is correlation
                and previous[1] is request
                and previous[2] == "claim_accepted"
            ):
                attempts[key] = (correlation, request, "dispatch_pending")
                return "dispatch"
            return None

    @staticmethod
    def _finish_operator_attempt(
        attempts: dict[str, tuple[object, object, str]],
        lock: threading.Lock,
        correlation: OrderCorrelation,
        request: object,
        stage: str,
        *,
        accepted: bool,
    ) -> None:
        key = correlation.reservation_ref
        expected_state = "claim_pending" if stage == "claim" else "dispatch_pending"
        final_state = (
            ("claim_accepted" if accepted else "claim_rejected")
            if stage == "claim"
            else ("dispatch_accepted" if accepted else "dispatch_rejected")
        )
        with lock:
            previous = attempts.get(key)
            if (
                previous is None
                or previous[0] is not correlation
                or previous[1] is not request
                or previous[2] != expected_state
            ):
                attempts[key] = (correlation, request, "unknown")
                return
            attempts[key] = (correlation, request, final_state)


class CtpRuntimeOrderProviderDependenciesFactoryV1:
    """Build exact CTP provider dependencies from one explicit trusted source.

    Pass an instance to ``CtpExecutionRuntimeConfig.order_provider_dependency_factory``.
    The same ``authority_source`` object must be supplied by the managed runtime
    composition to the selected CTP route and match the sealed runtime context
    by identity. This class only adapts the trusted source to the existing CTP
    provider callback contracts; BasicAdmission/Risk still own dispatch claims.
    """

    def __init__(
        self,
        authority_source: CtpOrderProviderAuthoritySourceV1,
        *,
        execution_economics_owner: object | None = None,
        execution_economics_issuer: object | None = None,
    ) -> None:
        if not isinstance(authority_source, CtpOrderProviderAuthoritySourceV1):
            raise TypeError("a typed CTP order provider authority source is required")
        if execution_economics_owner is not None and execution_economics_issuer is None:
            raise TypeError("an execution evidence owner requires its exact issuer")
        if execution_economics_owner is not None:
            try:
                from bt_api_ctp.execution_economics_registration import (
                    CtpExecutionEconomicsOwner,
                )

                if (
                    type(execution_economics_owner) is not CtpExecutionEconomicsOwner
                    or execution_economics_owner._issuer is not execution_economics_issuer
                    or execution_economics_owner._supports_rules_snapshots is not True
                ):
                    raise TypeError(
                        "exact CTP rules/economics owner and issuer are required"
                    )
            except ImportError:
                raise TypeError("CTP evidence owner is unavailable") from None
        self._authority_source = authority_source
        self._execution_economics_owner = execution_economics_owner
        self._execution_economics_issuer = execution_economics_issuer
        self._evidence_pair_injected = execution_economics_issuer is not None
        self._evidence_pair_attempted = execution_economics_issuer is not None
        self._issued: dict[int, tuple[object, CtpRuntimeOrderProviderDependenciesV1]] = {}
        self._lock = threading.RLock()

    @property
    def authority_source(self) -> CtpOrderProviderAuthoritySourceV1:
        """Return the exact host-injected source for identity binding only."""

        return self._authority_source

    def resolve(
        self,
        context: CtpRuntimeOrderProviderDependencyContextV1,
    ) -> CtpRuntimeOrderProviderDependenciesV1:
        if (
            type(context) is not CtpRuntimeOrderProviderDependencyContextV1
            or not _owner_context_is_exact(context)
            or not self._source_is_bound(context)
        ):
            raise CtpTrustedOrderProviderDependencyError(
                "ctp_order_provider_authority_binding_unavailable"
            )
        evidence_owner, evidence_issuer = self._resolve_evidence_pair(context)
        if not self._evidence_pair_is_bound(
            context,
            evidence_owner,
            evidence_issuer,
        ):
            raise CtpTrustedOrderProviderDependencyError(
                "ctp_execution_evidence_binding_unavailable"
            )
        try:
            registered_sim_authority = self._authority_source.resolve_registered_sim_authority(
                context,
                exchange_name=CTP_FUTURE_EXCHANGE_NAME,
            )
        except Exception:
            raise CtpTrustedOrderProviderDependencyError(
                "ctp_registered_sim_authority_unavailable"
            ) from None
        if registered_sim_authority is not None:
            try:
                from bt_api_ctp.managed_order_authority import (
                    CtpRegisteredSimNativeAuthority,
                )

                if (
                    type(registered_sim_authority)
                    is not CtpRegisteredSimNativeAuthority
                    or registered_sim_authority.is_bound_to_client(
                        context.trader_client
                    )
                    is not True
                    or context.execution_scope.environment != "simulation"
                ):
                    raise CtpTrustedOrderProviderDependencyError(
                        "ctp_registered_sim_authority_binding_invalid"
                    )
            except CtpTrustedOrderProviderDependencyError:
                raise
            except Exception:
                raise CtpTrustedOrderProviderDependencyError(
                    "ctp_registered_sim_authority_binding_invalid"
                ) from None

        adapters = _BoundAuthorityMethods(self, context)
        dependencies = CtpRuntimeOrderProviderDependenciesV1(
            context=context,
            registered_sim_authority=registered_sim_authority,
            request_account_validator=adapters.validate_request_account,
            submit_context_resolver=adapters.resolve_submit_context,
            cancel_authorization_validator=adapters.validate_cancel_authorization,
            order_provider_authority_source=self._authority_source,
            execution_economics_owner=evidence_owner,
            execution_economics_issuer=evidence_issuer,
        )
        with self._lock:
            key = id(context)
            existing = self._issued.get(key)
            if existing is not None and existing[0] is context:
                if existing[1] is not dependencies:
                    raise CtpTrustedOrderProviderDependencyError(
                        "ctp_order_provider_dependencies_already_resolved"
                    )
            self._issued[key] = (context, dependencies)
        return dependencies

    def validate(
        self,
        context: CtpRuntimeOrderProviderDependencyContextV1,
        dependencies: CtpRuntimeOrderProviderDependenciesV1,
    ) -> bool:
        if (
            type(context) is not CtpRuntimeOrderProviderDependencyContextV1
            or type(dependencies) is not CtpRuntimeOrderProviderDependenciesV1
            or dependencies.context is not context
            or dependencies.order_provider_authority_source is not self._authority_source
            or not _owner_context_is_exact(context)
            or not self._source_is_bound(context)
            or (
                self._execution_economics_owner is not None
                and dependencies.execution_economics_owner
                is not self._execution_economics_owner
            )
            or (
                self._execution_economics_issuer is not None
                and dependencies.execution_economics_issuer
                is not self._execution_economics_issuer
            )
        ):
            return False
        try:
            with self._lock:
                issued = self._issued.get(id(context))
            if issued is None or issued[0] is not context or issued[1] is not dependencies:
                return False
            if not self._evidence_pair_is_bound(
                context,
                dependencies.execution_economics_owner,
                dependencies.execution_economics_issuer,
            ):
                return False
            authority = dependencies.registered_sim_authority
            if authority is not None:
                from bt_api_ctp.managed_order_authority import (
                    CtpRegisteredSimNativeAuthority,
                )

                if (
                    type(authority) is not CtpRegisteredSimNativeAuthority
                    or authority.is_bound_to_client(context.trader_client) is not True
                    or context.execution_scope.environment != "simulation"
                ):
                    return False
            return (
                dependencies.request_account_validator
                is not None
                and dependencies.submit_context_resolver is not None
                and dependencies.cancel_authorization_validator is not None
            )
        except Exception:
            return False

    def revoke(self, context: CtpRuntimeOrderProviderDependencyContextV1) -> bool:
        """Forget one exact context after its selected feed has detached."""

        if (
            type(context) is not CtpRuntimeOrderProviderDependencyContextV1
            or threading.current_thread() is not context.owner_thread
        ):
            return False
        with self._lock:
            issued = self._issued.get(id(context))
            if issued is None or issued[0] is not context:
                return False
            self._issued.pop(id(context), None)
            dependencies = issued[1]
            if (
                not self._evidence_pair_injected
                and dependencies.execution_economics_issuer
                is self._execution_economics_issuer
            ):
                self._execution_economics_owner = None
                self._execution_economics_issuer = None
                self._evidence_pair_attempted = False
        return True

    def _source_is_bound(
        self,
        context: CtpRuntimeOrderProviderDependencyContextV1,
    ) -> bool:
        return self._source_is_bound_to(self._authority_source, context)

    def _source_is_bound_to(
        self,
        source: CtpOrderProviderAuthoritySourceV1,
        context: CtpRuntimeOrderProviderDependencyContextV1,
    ) -> bool:
        try:
            result = source.is_bound_to(
                context,
                exchange_name=CTP_FUTURE_EXCHANGE_NAME,
            )
        except Exception:
            return False
        return type(result) is bool and result is True

    def _resolve_evidence_pair(
        self,
        context: CtpRuntimeOrderProviderDependencyContextV1,
    ) -> tuple[object | None, object | None]:
        """Create or reuse one exact CTP evidence pair on the runtime owner."""

        if threading.current_thread() is not context.owner_thread:
            return None, None
        with self._lock:
            issuer = self._execution_economics_issuer
            owner = self._execution_economics_owner
            if issuer is not None:
                if owner is None:
                    try:
                        from bt_api_ctp.execution_economics_registration import (
                            CtpExecutionEconomicsOwner,
                        )

                        owner = CtpExecutionEconomicsOwner(issuer)
                    except Exception:
                        raise CtpTrustedOrderProviderDependencyError(
                            "ctp_execution_evidence_binding_unavailable"
                        ) from None
                    self._execution_economics_owner = owner
                return owner, issuer
            if self._evidence_pair_attempted:
                return None, None
            self._evidence_pair_attempted = True

        pair = _create_selected_ctp_position_evidence_pair(context)
        if pair is None:
            return None, None
        owner, issuer = pair
        with self._lock:
            existing_issuer = self._execution_economics_issuer
            existing_owner = self._execution_economics_owner
            if existing_issuer is not None:
                if existing_issuer is not issuer:
                    return existing_owner, existing_issuer
                return existing_owner, existing_issuer
            self._execution_economics_owner = owner
            self._execution_economics_issuer = issuer
        return owner, issuer

    def _evidence_pair_is_bound(
        self,
        context: CtpRuntimeOrderProviderDependencyContextV1,
        owner: object | None,
        issuer: object | None,
    ) -> bool:
        if (owner is None) != (issuer is None):
            return False
        if owner is None:
            return True
        if threading.current_thread() is not context.owner_thread:
            return False
        try:
            from bt_api_ctp.execution_economics_registration import (
                CtpExecutionEconomicsOwner,
            )

            if type(owner) is not CtpExecutionEconomicsOwner or owner._issuer is not issuer:
                return False
            if not _selected_feed_environment_is_verified_demo(context.selected_feed):
                return False
            route = SelectedExecutionRoute(
                exchange_name=CTP_FUTURE_EXCHANGE_NAME,
                client=context.trader_client,
                # CTP's selected query/session environment is the neutral
                # provider label ``demo``; the runtime ExecutionScope keeps
                # the distinct framework mode ``simulation``. The issuer
                # validates that this exact selected feed is actually bound
                # to its query-session environment.
                environment="demo",
                execution_context=context.selected_feed,
            )
            is_bound = getattr(issuer, "is_bound_to", None)
            if not callable(is_bound):
                return False
            result = is_bound(route=route, runtime_context=context)
        except Exception:
            return False
        return type(result) is bool and result is True

    def validate_final_context(
        self,
        context: CtpRuntimeOrderProviderDependencyContextV1,
        dependencies: CtpRuntimeOrderProviderDependenciesV1,
        runtime_context: object,
    ) -> bool:
        """Revalidate the same evidence issuer against the final runtime seal."""

        if (
            type(context) is not CtpRuntimeOrderProviderDependencyContextV1
            or type(dependencies) is not CtpRuntimeOrderProviderDependenciesV1
            or dependencies.context is not context
            or threading.current_thread() is not context.owner_thread
            or not _owner_context_is_exact(context)
            or not self._source_is_bound(context)
        ):
            return False
        try:
            from bt_api_base.execution_correlation_protocol import SelectedExecutionRoute
            from bt_api_ctp.order_provider import CtpOrderProviderRuntimeContext

            if (
                type(runtime_context) is not CtpOrderProviderRuntimeContext
                or runtime_context.runtime_binding is None
                or runtime_context.execution_store is not context.store
                or runtime_context.scope is not context.execution_scope
                or runtime_context.instrument_metadata_reader
                is not context.instrument_metadata_reader
                or runtime_context.execution_facade is not context.execution_facade
                or runtime_context.cancellation_facade
                is not context.cancellation_facade
                or runtime_context.submit_port is not context.submit_port
                or runtime_context.cancel_port is not context.cancel_port
                or runtime_context.owner_thread_invoker is not context.owner_thread_invoker
                or runtime_context.registered_sim_authority
                is not dependencies.registered_sim_authority
                or runtime_context.request_account_validator
                is not dependencies.request_account_validator
                or runtime_context.submit_context_resolver
                is not dependencies.submit_context_resolver
                or runtime_context.cancel_authorization_validator
                is not dependencies.cancel_authorization_validator
                or runtime_context.order_provider_authority_source
                is not self._authority_source
                or runtime_context.execution_economics_owner
                is not dependencies.execution_economics_owner
                or runtime_context.execution_economics_issuer
                is not dependencies.execution_economics_issuer
            ):
                return False
            with self._lock:
                issued = self._issued.get(id(context))
                if issued is None or issued[0] is not context or issued[1] is not dependencies:
                    return False
            owner = dependencies.execution_economics_owner
            issuer = dependencies.execution_economics_issuer
            if owner is None and issuer is None:
                return True
            if not self._evidence_pair_is_bound(
                context,
                owner,
                issuer,
            ):
                return False
            if not _selected_feed_environment_is_verified_demo(context.selected_feed):
                return False
            route = SelectedExecutionRoute(
                exchange_name=CTP_FUTURE_EXCHANGE_NAME,
                client=context.selected_feed,
                environment="demo",
                execution_context=runtime_context,
            )
            result = issuer.is_bound_to(
                route=route,
                runtime_context=runtime_context,
            )
        except Exception:
            return False
        return type(result) is bool and result is True


