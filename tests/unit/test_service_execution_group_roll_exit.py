"""Focused owner-service coverage for DIM05 roll-exit query isolation."""

from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_base.service_protocol import WireEnvelope, WireMessageKind
from bt_api_execution import (
    ExecutionState,
    OrderType,
    PortfolioGroupBudgetPolicy,
    SqliteOrderGroupStore,
    build_instrument_risk_group_budget_quote,
)
from bt_api_risk import InstrumentRiskMetadata, InstrumentRiskRegistry

from bt_api_py.service_components import BorrowedServiceClient
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_execution_group import (
    GROUP_COORDINATOR_OPERATIONS,
    GroupAccountRoute,
    GroupCoordinatorService,
    GroupCoordinatorTarget,
    GroupInstrumentRule,
    _OrdersRpcPort,
    _CURRENT_COORDINATOR_CALL,
    _CoordinatorCall,
)
from bt_api_py.service_execution_group_client import (
    AcceptedGroupBindingError,
    GroupOriginRunBindingStore,
    verify_accepted_group_projection,
)
from bt_api_py.service_registry import (
    ActiveRegistrationContext,
    PrincipalGrant,
    ResourceGrant,
    ServiceCallContext,
    ServiceCapability,
    ServiceRegistry,
    TrustedGroupWritePolicy,
)
from bt_api_py.service_runtime import ServiceClientRuntime, ServiceRuntimeError
from bt_api_py.service_group_roll_exit import GroupRollExitPhase
from tests.unit.test_service_group_roll_exit import (
    _authorization,
    _observation,
    _source_and_request,
)


_COORDINATOR = "group-coordinator"
_INSTANCE = "group-coordinator-instance"
_STRATEGY_PRINCIPAL = "strategy-principal"
_ACCOUNT = "account-a"


class _RiskQueryRuntime:
    state = "registered"
    healthy = True

    def __init__(
        self,
        authorization: Any,
        *,
        allow_prepare: bool = False,
        allow_submit: bool = False,
    ) -> None:
        self.authorization = authorization
        self.allow_prepare = allow_prepare
        self.allow_submit = allow_submit
        self.submitted = False
        self.calls: list[tuple[str, str, dict[str, Any], str | None, str | None]] = []

    def call(
        self,
        mount: str,
        operation: str,
        arguments: dict[str, Any],
        *,
        account_ref: str | None = None,
        portfolio_ref: str | None = None,
        scope_ref: str | None = None,
        max_reply_message_bytes: int | None = None,
    ) -> dict[str, Any]:
        del max_reply_message_bytes
        self.calls.append((mount, operation, dict(arguments), account_ref, scope_ref))
        if operation == "orders.prepare_group_roll_exit" and self.allow_prepare:
            if (
                mount != "upstream.group_roll_exit_order_query"
                or account_ref != _ACCOUNT
                or portfolio_ref != self.authorization.request.portfolio_ref
                or scope_ref != "roll-exit-query-dim05"
                or arguments != self.authorization.request.to_payload()
            ):
                raise AssertionError("Risk prepare lost its source and owner binding")
            return {
                "ok": True,
                "code": "OK",
                "result": {
                    "phase": GroupRollExitPhase.PREPARED.value,
                    "authorization": self.authorization.to_payload(),
                    "observation": None,
                },
            }
        if operation == "orders.query_group_roll_exit":
            if (
                mount != "upstream.group_roll_exit_order_query"
                or account_ref != _ACCOUNT
                or portfolio_ref != self.authorization.request.portfolio_ref
                or scope_ref != "roll-exit-query-dim05"
                or set(arguments) != {"action_id", "authorization_sha256"}
                or arguments["action_id"] != self.authorization.action_id
                or arguments["authorization_sha256"]
                != self.authorization.authorization_sha256
            ):
                raise AssertionError("Risk query lost its fixed action and owner binding")
            observation = _observation(
                self.authorization,
                phase=(
                    GroupRollExitPhase.STATUS_OBSERVED
                    if self.submitted
                    else GroupRollExitPhase.PREPARED
                ),
                state=(ExecutionState.PARTIALLY_FILLED if self.submitted else None),
                filled=(Decimal("0.5") if self.submitted else Decimal("0")),
                revision=(1 if self.submitted else None),
            )
            return {
                "ok": True,
                "code": "OK",
                "result": {
                    "phase": observation.phase.value,
                    "observation": observation.to_payload(),
                },
            }
        if operation == "orders.submit_group_roll_exit" and self.allow_submit:
            if (
                mount != "upstream.group_roll_exit_orders"
                or account_ref != _ACCOUNT
                or portfolio_ref != self.authorization.request.portfolio_ref
                or scope_ref != "roll-exit-order-dim05"
                or set(arguments) != {"authorization"}
                or arguments["authorization"] != self.authorization.to_payload()
                or self.submitted
            ):
                raise AssertionError("Risk submit lost its exact source-bound authorization")
            self.submitted = True
            observation = _observation(
                self.authorization,
                phase=GroupRollExitPhase.STATUS_OBSERVED,
                state=ExecutionState.PARTIALLY_FILLED,
                filled=Decimal("0.5"),
                revision=1,
            )
            return {
                "ok": True,
                "code": "OK",
                "result": {
                    "phase": observation.phase.value,
                    "observation": observation.to_payload(),
                },
            }
        raise AssertionError("coordinator called an unapproved Risk operation")


def _registration(
    *,
    scope: WireScope,
    registration_id: str,
    operations: frozenset[str],
    write_operations: frozenset[str],
    now_ns: int,
) -> ActiveRegistrationContext:
    identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        _STRATEGY_PRINCIPAL,
        actor_ref=scope.strategy_ref,
    )
    context = ServiceCallContext(
        identity=identity,
        scope=scope,
        registration_id=registration_id,
        principal_ref=_STRATEGY_PRINCIPAL,
    )
    return ActiveRegistrationContext(
        context=context,
        instance_id=_INSTANCE,
        port="service.order_group_entry",
        scope_ref="group-scope-dim05",
        operations=operations,
        write_operations=write_operations,
        service_epoch="epoch-dim05",
        policy_version="policy-dim05",
        requirements_version="requirements-dim05",
        expires_at=now_ns / 1_000_000_000 + 3_600,
        strategy_write_run_id=scope.run_id,
    )


def _budget_metadata(
    instrument: str,
    *,
    expires_at_ns: int,
    as_of_ns: int | None = None,
) -> InstrumentRiskMetadata:
    return InstrumentRiskMetadata(
        instrument=instrument,
        metadata_version="source-meta-7",
        as_of_ns=time.time_ns() - 1_000_000 if as_of_ns is None else as_of_ns,
        expires_at_ns=expires_at_ns,
        tick_size=Decimal("0.25"),
        quantity_step=Decimal("0.5"),
        contract_multiplier=Decimal("1"),
        max_gross_notional=Decimal("1000000"),
        quantity_unit="contract",
        valuation_unit="USD",
    )


def _service_fixture(
    tmp_path: Path,
    *,
    allow_submit: bool = False,
    accept_server_quote: bool = False,
) -> tuple[Any, ...]:
    source, request = _source_and_request()
    authorization = _authorization(source, request)
    now_ns = time.time_ns()
    stable_scope = WireScope(
        execution_domain_ref="simnow",
        account_refs=(_ACCOUNT,),
        strategy_ref=source.strategy_id,
        run_id=None,
        portfolio_ref=source.portfolio_ref,
    )
    strategy_scope = replace(
        stable_scope,
        run_id=source.origin_run_id,
    )
    query_operations = frozenset({"groups.query", "groups.query_roll_exit"})
    write_registration = _registration(
        scope=strategy_scope,
        registration_id="writer-registration",
        operations=GROUP_COORDINATOR_OPERATIONS,
        write_operations=frozenset({"groups.submit", "groups.roll_exit"}),
        now_ns=now_ns,
    )
    query_registration = _registration(
        scope=strategy_scope,
        registration_id="query-registration",
        operations=query_operations,
        write_operations=frozenset(),
        now_ns=now_ns,
    )
    foreign_query_registration = _registration(
        scope=replace(strategy_scope, run_id="strategy-run-other"),
        registration_id="foreign-query-registration",
        operations=query_operations,
        write_operations=frozenset(),
        now_ns=now_ns,
    )

    group_request = source.group_request
    metadata = tuple(
        _budget_metadata(
            plan.maximum_intent.instrument,
            expires_at_ns=group_request.deadline_ns + 1_000_000,
            as_of_ns=now_ns - 1_000_000,
        )
        for plan in group_request.legs
    )
    if accept_server_quote:
        # The quote builder only accepts order facts carrying the digest of
        # the trusted registry metadata that will assess them. Bind those
        # digests before persisting the original request; the server-added
        # budget quote then changes only the accepted snapshot fingerprint.
        tagged_legs = tuple(
            replace(
                plan,
                maximum_intent=replace(
                    plan.maximum_intent,
                    tags={
                        **plan.maximum_intent.tags,
                        "instrument_metadata_digest": item.digest,
                    },
                ),
            )
            for plan, item in zip(group_request.legs, metadata, strict=True)
        )
        group_request = replace(group_request, legs=tagged_legs)
        source_child_intent = replace(
            source.source_child_intent,
            tags=dict(group_request.leg(source.source_leg_id).maximum_intent.tags),
        )
        source_claim = replace(
            source.source_claim,
            actual_child_intent_hash=source_child_intent.fingerprint,
            permit=replace(
                source.source_claim.permit,
                order_intent_hash=source_child_intent.fingerprint,
            ),
        )
        source = replace(
            source,
            group_request=group_request,
            group_fingerprint=group_request.fingerprint,
            source_risk_intent_fingerprint=source_claim.actual_child_intent_hash,
            source_child_intent=source_child_intent,
            source_request_fingerprint=source_child_intent.fingerprint,
            source_claim=source_claim,
            source_risk_resolution=replace(
                source.source_risk_resolution,
                claim=source_claim,
            ),
        )
        authorization = _authorization(source, request)
    policy = PortfolioGroupBudgetPolicy(
        portfolio_ref=source.portfolio_ref,
        policy_id="portfolio-budget-dim05",
        policy_version=1,
        valuation_unit="USD",
        max_group_gross_notional=Decimal("1000000"),
        max_active_gross_notional=Decimal("10000000"),
        max_active_groups=10,
    )
    budget_builder = partial(
        build_instrument_risk_group_budget_quote,
        registry=InstrumentRiskRegistry(metadata),
    )
    instrument_rules = {
        item.instrument: GroupInstrumentRule(
            metadata=item,
            base_units_per_order_unit=Decimal("1"),
            allowed_order_types=frozenset({OrderType.LIMIT}),
        )
        for item in metadata
    }
    route = GroupAccountRoute(
        execution_scope=source.source_execution_scope,
        risk_scope_fingerprint="a" * 64,
        reservation_scope_ref="reservation-dim05",
        order_scope_ref="order-dim05",
        query_scope_ref="query-dim05",
        instruments=instrument_rules,
        roll_exit_order_scope_ref="roll-exit-order-dim05",
        roll_exit_query_scope_ref="roll-exit-query-dim05",
    )
    target = GroupCoordinatorTarget(
        scope_ref="group-scope-dim05",
        accounts={_ACCOUNT: route},
        max_group_lifetime_ns=10**18,
        portfolio_budget_policy=policy,
        budget_quote_builder=budget_builder,
    )
    group_store = SqliteOrderGroupStore(tmp_path / "groups.sqlite3")
    run_bindings = GroupOriginRunBindingStore(tmp_path / "group-runs.sqlite3")
    runtime = _RiskQueryRuntime(
        authorization,
        allow_prepare=accept_server_quote,
        allow_submit=allow_submit,
    )
    service = GroupCoordinatorService(
        group_store,
        {stable_scope: target},
        run_bindings,
        BorrowedServiceClient(runtime),
        WireIdentity(WireRole.SERVICE, _COORDINATOR, "coordinator-actor"),
        instance_id=_INSTANCE,
        clock_ns=lambda: now_ns,
    )
    service.maintain_registrations(
        (write_registration, query_registration, foreign_query_registration), recover=False
    )
    run_bindings.bind(
        write_registration,
        group_request,
        coordinator_principal_ref=_COORDINATOR,
    )
    if accept_server_quote:
        target_scope = service._target_scope_for(write_registration.context.scope)
        assert target_scope is not None
        target = service._targets[target_scope]
        binding = run_bindings.get(group_request.portfolio_ref, group_request.group_id)
        assert binding is not None
        coordinator_call = _CoordinatorCall(write_registration, target_scope, target, binding)
        token = _CURRENT_COORDINATOR_CALL.set(coordinator_call)
        try:
            accepted_snapshot = service._coordinator.accept(binding.request)
        finally:
            _CURRENT_COORDINATOR_CALL.reset(token)
        bound = service._bind_verified_accepted_snapshot(binding, accepted_snapshot)
        assert bound is not None
        source = replace(
            source,
            group_version=accepted_snapshot.request.version,
            group_revision=accepted_snapshot.revision,
            group_fingerprint=accepted_snapshot.request.fingerprint,
            group_request=accepted_snapshot.request,
        )
        authorization = _authorization(source, request)
        runtime.authorization = authorization
    else:
        group_store.create_or_get(group_request, now_ns=now_ns)
    return (
        source,
        request,
        authorization,
        service,
        runtime,
        group_store,
        run_bindings,
        write_registration,
        query_registration,
        foreign_query_registration,
        now_ns,
    )


def test_roll_exit_accepts_verified_server_quote_and_rejects_tampered_bindings(
    tmp_path: Path,
) -> None:
    (
        _source,
        request,
        authorization,
        service,
        runtime,
        group_store,
        run_bindings,
        write_registration,
        query_registration,
        foreign_query_registration,
        _now_ns,
    ) = _service_fixture(
        tmp_path,
        allow_submit=True,
        accept_server_quote=True,
    )
    try:
        binding = run_bindings.get(request.portfolio_ref, request.group_id)
        snapshot = group_store.get(request.portfolio_ref, request.group_id)
        assert binding is not None
        assert snapshot is not None
        assert snapshot.request.version == 2
        assert snapshot.request.budget_quote is not None
        assert binding.request_fingerprint != snapshot.request.fingerprint
        assert binding.accepted_request_fingerprint == snapshot.request.fingerprint
        history = group_store.events(request.portfolio_ref, request.group_id)
        assert verify_accepted_group_projection(binding, snapshot, history) == (
            snapshot.request.fingerprint
        )
        target_scope = service._target_scope_for(write_registration.context.scope)
        assert target_scope is not None
        target = service._targets[target_scope]
        assert service._recovery_binding_matches(binding, snapshot, target_scope, target)

        quote = snapshot.request.budget_quote
        tampered_leg = replace(quote.legs[0], assessment_fingerprint="f" * 64)
        tampered_quote = replace(quote, legs=(tampered_leg, *quote.legs[1:]))
        tampered_request = replace(snapshot.request, budget_quote=tampered_quote)
        tampered_reservation = replace(
            snapshot.budget_reservation,
            quote_fingerprint=tampered_quote.fingerprint,
        )
        tampered_snapshot = replace(
            snapshot,
            request=tampered_request,
            budget_reservation=tampered_reservation,
        )
        assert tampered_snapshot.request.unquoted_fingerprint == binding.request.unquoted_fingerprint
        with pytest.raises(AcceptedGroupBindingError):
            verify_accepted_group_projection(binding, tampered_snapshot, history)
        assert not service._recovery_binding_matches(
            binding, tampered_snapshot, target_scope, target
        )

        tampered_binding = replace(
            binding,
            accepted_request_fingerprint=(
                "0" * 64
                if binding.accepted_request_fingerprint != "0" * 64
                else "1" * 64
            ),
        )
        with pytest.raises(AcceptedGroupBindingError):
            verify_accepted_group_projection(tampered_binding, snapshot, history)
        assert not service._recovery_binding_matches(
            tampered_binding, snapshot, target_scope, target
        )

        arguments = {
            "group_id": request.group_id,
            "source_leg_id": request.source_leg_id,
            "limit_price": format(request.limit_price, "f"),
        }
        reply = service.handle(
            "groups.roll_exit",
            context=write_registration.context,
            arguments=arguments,
        )
        assert reply["ok"] is True
        assert reply["result"]["action"]["phase"] == "STATUS_OBSERVED"
        assert [call[1] for call in runtime.calls] == [
            "orders.prepare_group_roll_exit",
            "orders.submit_group_roll_exit",
        ]
        assert runtime.calls[0][2] == authorization.request.to_payload()

        calls_before_foreign_query = len(runtime.calls)
        foreign = service.handle(
            "groups.query_roll_exit",
            context=foreign_query_registration.context,
            arguments={"action_id": authorization.action_id},
        )
        assert foreign["ok"] is False
        assert foreign["code"] in {
            "GROUP_BINDING_UNAVAILABLE",
            "GROUP_ROLL_EXIT_NOT_AUTHORIZED",
        }
        assert len(runtime.calls) == calls_before_foreign_query

        queried = service.handle(
            "groups.query_roll_exit",
            context=query_registration.context,
            arguments={"action_id": authorization.action_id},
        )
        assert queried["ok"] is True
        assert queried["result"]["action"]["phase"] == "STATUS_OBSERVED"
        assert [call[1] for call in runtime.calls] == [
            "orders.prepare_group_roll_exit",
            "orders.submit_group_roll_exit",
            "orders.query_group_roll_exit",
        ]
    finally:
        service.close()
        group_store.close()
        run_bindings.close()


def test_query_roll_exit_reads_prepared_action_without_dispatch_or_local_mutation(
    tmp_path: Path,
) -> None:
    (
        source,
        roll_exit_request,
        authorization,
        service,
        runtime,
        group_store,
        run_bindings,
        _write_registration,
        query_registration,
        foreign_query_registration,
        _now_ns,
    ) = _service_fixture(tmp_path)
    try:
        seeded = service._roll_exit_actions.prepare(authorization)

        reply = service.handle(
            "groups.query_roll_exit",
            context=query_registration.context,
            arguments={"action_id": authorization.action_id},
        )

        assert reply["ok"] is True
        assert reply["result"]["found"] is True
        assert reply["result"]["action"]["phase"] == "PREPARED"
        assert service._roll_exit_actions.get(authorization.action_id) == seeded
        assert [call[1] for call in runtime.calls] == ["orders.query_group_roll_exit"]
        assert runtime.calls[0][0] == "upstream.group_roll_exit_order_query"
        assert runtime.calls[0][3:] == (_ACCOUNT, "roll-exit-query-dim05")

        terminal = _observation(
            authorization,
            phase=GroupRollExitPhase.RESOLVED,
            state=ExecutionState.CANCELLED,
            filled=Decimal("0.5"),
            revision=9,
            receipt="9" * 64,
        )
        service._roll_exit_actions.record_observation(terminal)
        foreign = service.handle(
            "groups.query_roll_exit",
            context=foreign_query_registration.context,
            arguments={"action_id": authorization.action_id},
        )
        assert foreign["ok"] is False
        assert foreign["code"] in {
            "GROUP_BINDING_CONFLICT",
            "GROUP_BINDING_UNAVAILABLE",
            "GROUP_ROLL_EXIT_NOT_AUTHORIZED",
        }
        assert [call[1] for call in runtime.calls] == ["orders.query_group_roll_exit"]
    finally:
        service.close()
        group_store.close()
        run_bindings.close()


def test_roll_exit_write_recovery_queries_prepared_then_uses_risk_cas_once(
    tmp_path: Path,
) -> None:
    (
        source,
        request,
        authorization,
        service,
        runtime,
        group_store,
        run_bindings,
        write_registration,
        _query_registration,
        _foreign_query_registration,
        _now_ns,
    ) = _service_fixture(tmp_path, allow_submit=True)
    try:
        service._roll_exit_actions.prepare(authorization)
        assert service._roll_exit_actions.claim_submit(
            authorization.action_id, authorization.authorization_sha256
        )

        reply = service.handle(
            "groups.roll_exit",
            context=write_registration.context,
            arguments={
                "group_id": request.group_id,
                "source_leg_id": request.source_leg_id,
                "limit_price": format(request.limit_price, "f"),
            },
        )

        assert reply["ok"] is True
        assert reply["result"]["action"]["phase"] == "STATUS_OBSERVED"
        assert [call[1] for call in runtime.calls] == [
            "orders.query_group_roll_exit",
            "orders.submit_group_roll_exit",
        ]
        assert runtime.calls[0][0] == "upstream.group_roll_exit_order_query"
        assert runtime.calls[1][0] == "upstream.group_roll_exit_orders"
        persisted = service._roll_exit_actions.get(authorization.action_id)
        assert persisted is not None
        assert persisted.phase is GroupRollExitPhase.STATUS_OBSERVED
        assert persisted.observation is not None
        assert persisted.observation.filled_quantity == Decimal("0.5")

        replay = service.handle(
            "groups.roll_exit",
            context=write_registration.context,
            arguments={
                "group_id": request.group_id,
                "source_leg_id": request.source_leg_id,
                "limit_price": format(request.limit_price, "f"),
            },
        )
        assert replay["ok"] is True
        assert replay["result"]["action"]["phase"] == "STATUS_OBSERVED"
        assert [call[1] for call in runtime.calls] == [
            "orders.query_group_roll_exit",
            "orders.submit_group_roll_exit",
            "orders.query_group_roll_exit",
        ]
    finally:
        service.close()
        group_store.close()
        run_bindings.close()


def test_roll_exit_rejects_active_record_from_another_origin_run_before_risk_recovery(
    tmp_path: Path,
) -> None:
    (
        source,
        request,
        _authorization_for_current_run,
        service,
        runtime,
        group_store,
        run_bindings,
        write_registration,
        _query_registration,
        _foreign_query_registration,
        _now_ns,
    ) = _service_fixture(tmp_path, allow_submit=True)
    try:
        stale_source = replace(
            source,
            origin_run_id="strategy-run-prior",
            service_write_run_id="strategy-run-prior",
        )
        stale_authorization = _authorization(stale_source, request)
        seeded = service._roll_exit_actions.prepare(stale_authorization)

        reply = service.handle(
            "groups.roll_exit",
            context=write_registration.context,
            arguments={
                "group_id": request.group_id,
                "source_leg_id": request.source_leg_id,
                "limit_price": format(request.limit_price, "f"),
            },
        )

        assert reply["ok"] is False
        assert reply["code"] == "GROUP_ROLL_EXIT_NOT_AUTHORIZED"
        assert runtime.calls == []
        assert service._roll_exit_actions.get(stale_authorization.action_id) == seeded
        assert seeded.phase is GroupRollExitPhase.PREPARED
    finally:
        service.close()
        group_store.close()
        run_bindings.close()


class _RegisteredRiskRouteTransport:
    """In-process wire transport that keeps Registry ACL checks in the path."""

    def __init__(
        self,
        registry: ServiceRegistry,
        authenticated_principal: str,
        response_identity: WireIdentity,
    ) -> None:
        self.registry = registry
        self.authenticated_principal = authenticated_principal
        self.response_identity = response_identity
        self.operations: list[str] = []
        self.closed = False

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        del timeout
        self.operations.append(request.header.operation)
        payload = self.registry.handle(self.authenticated_principal, request)
        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=self.response_identity,
            ),
            payload,
        )

    def close(self) -> None:
        self.closed = True


def test_roll_exit_rpc_uses_only_dedicated_registered_mounts(tmp_path: Path) -> None:
    source, request = _source_and_request()
    authorization = _authorization(source, request)
    coordinator_identity = WireIdentity(
        WireRole.SERVICE, _COORDINATOR, "group-coordinator-service"
    )
    risk_identity = WireIdentity(
        WireRole.SERVICE, "risk-owner-principal", "dim05-risk-owner"
    )
    risk_instance = "dim05-risk-owner-instance"
    entry_scope_ref = "roll-exit-order-dim05"
    query_scope_ref = "roll-exit-query-dim05"
    owner_scope = WireScope(
        execution_domain_ref=source.source_execution_scope.provider,
        account_ref=source.account_ref,
        strategy_ref=source.strategy_id,
        portfolio_ref=source.portfolio_ref,
    )
    submit_operation = "orders.submit_group_roll_exit"
    prepare_operation = "orders.prepare_group_roll_exit"
    query_operation = "orders.query_group_roll_exit"
    entry_operations = frozenset({submit_operation})
    query_operations = frozenset({prepare_operation, query_operation})
    registry = ServiceRegistry(
        tmp_path / "risk-registry.sqlite3",
        service_id=risk_identity.actor_ref,
        capabilities=(
            ServiceCapability(
                risk_instance,
                "service.order_entry",
                entry_operations,
                entry_operations,
            ),
            ServiceCapability(
                risk_instance,
                "service.order_query",
                query_operations,
            ),
        ),
        grants={
            coordinator_identity.principal_ref: (
                PrincipalGrant(
                    coordinator_identity,
                    (
                        ResourceGrant(
                            risk_instance,
                            "service.order_entry",
                            entry_scope_ref,
                            owner_scope,
                            entry_operations,
                        ),
                        ResourceGrant(
                            risk_instance,
                            "service.order_query",
                            query_scope_ref,
                            owner_scope,
                            query_operations,
                        ),
                    ),
                ),
            )
        },
        policy_version="roll-exit-access-v1",
        requirements_version="roll-exit-requirements-v1",
        trusted_group_write_policies=(
            TrustedGroupWritePolicy(
                risk_instance,
                coordinator_identity.principal_ref,
                coordinator_identity,
                entry_scope_ref,
                owner_scope,
                source.origin_run_id,
                operation=submit_operation,
            ),
        ),
    )
    observation = _observation(
        authorization,
        phase=GroupRollExitPhase.STATUS_OBSERVED,
        state=ExecutionState.PARTIALLY_FILLED,
        filled=Decimal("0.5"),
        revision=1,
    )
    dispatched: list[str] = []

    def prepare_handler(context: ServiceCallContext, arguments: Any) -> dict[str, Any]:
        assert context.identity == coordinator_identity
        assert context.scope == owner_scope
        assert dict(arguments) == request.to_payload()
        dispatched.append(prepare_operation)
        return {
            "phase": GroupRollExitPhase.PREPARED.value,
            "authorization": authorization.to_payload(),
            "observation": None,
        }

    def submit_handler(context: ServiceCallContext, arguments: Any) -> dict[str, Any]:
        assert context.identity == coordinator_identity
        assert context.scope == owner_scope
        assert dict(arguments) == {"authorization": authorization.to_payload()}
        dispatched.append(submit_operation)
        return observation.to_payload()

    def query_handler(context: ServiceCallContext, arguments: Any) -> dict[str, Any]:
        assert context.identity == coordinator_identity
        assert context.scope == owner_scope
        assert dict(arguments) == {
            "action_id": authorization.action_id,
            "authorization_sha256": authorization.authorization_sha256,
        }
        dispatched.append(query_operation)
        return {
            "phase": observation.phase.value,
            "observation": observation.to_payload(),
        }

    registry.register_handler(risk_instance, prepare_operation, prepare_handler)
    registry.register_handler(risk_instance, submit_operation, submit_handler)
    registry.register_handler(risk_instance, query_operation, query_handler)

    risk_config = parse_service_config(
        {
            "service_config_schema_version": 1,
            "process_role": "service",
            "service_id": coordinator_identity.actor_ref,
            "scope_ref": "group-coordinator-root",
            "transport_ref": "listener",
            "registration_policy_ref": "group-coordinator-access-v1",
            "service_requirements_ref": "group-coordinator-requirements-v1",
            "components": {
                "execution": {
                    "config_schema_version": 2,
                    "settings": {
                        "instance_id": _INSTANCE,
                        "bind_ports": ["service.order_group_entry"],
                        "options": {
                            "role": "group_coordinator",
                            "portfolio_ref": source.portfolio_ref,
                        },
                    },
                }
            },
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "upstream.group_roll_exit_orders",
                                "service_id": risk_identity.actor_ref,
                                "instance_id": risk_instance,
                                "scope_ref": entry_scope_ref,
                                "account_ref": source.account_ref,
                                "portfolio_ref": source.portfolio_ref,
                                "transport_ref": "risk-rpc",
                            },
                            {
                                "mount": "upstream.group_roll_exit_order_query",
                                "service_id": risk_identity.actor_ref,
                                "instance_id": risk_instance,
                                "scope_ref": query_scope_ref,
                                "account_ref": source.account_ref,
                                "portfolio_ref": source.portfolio_ref,
                                "transport_ref": "risk-rpc",
                            },
                        ],
                        "options": {"request_timeout_ms": 1000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "listener": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_bind": "tcp://127.0.0.1:7600"},
                        "security_profile_ref": "listener-security",
                    },
                    "risk-rpc": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:7601"},
                        "security_profile_ref": "risk-security",
                    },
                },
            },
        }
    )
    transport = _RegisteredRiskRouteTransport(
        registry,
        coordinator_identity.principal_ref,
        risk_identity,
    )
    runtime = ServiceClientRuntime(
        risk_config,
        identity=coordinator_identity,
        scope_resolver=lambda _scope_ref: owner_scope,
        peer_resolver=lambda _service_id: risk_identity,
        transport_factory=lambda *_args: transport,
        requested_operations={
            "upstream.group_roll_exit_orders": entry_operations,
            "upstream.group_roll_exit_order_query": query_operations,
        },
        run_id=source.origin_run_id,
    )

    group_scope = WireScope(
        execution_domain_ref=source.source_execution_scope.provider,
        account_refs=(source.account_ref,),
        strategy_ref=source.strategy_id,
        portfolio_ref=source.portfolio_ref,
    )
    strategy_scope = replace(group_scope, run_id=source.origin_run_id)
    now_ns = time.time_ns()
    strategy_registration = _registration(
        scope=strategy_scope,
        registration_id="roll-exit-strategy-registration",
        operations=GROUP_COORDINATOR_OPERATIONS,
        write_operations=frozenset({"groups.submit", "groups.roll_exit"}),
        now_ns=now_ns,
    )
    group_bindings = GroupOriginRunBindingStore(tmp_path / "coordinator-runs.sqlite3")

    try:
        runtime.start()
        registered_entry = next(
            binding
            for binding in runtime.bindings
            if binding.mount == "upstream.group_roll_exit_orders"
        )
        assert registry.bind_trusted_group_run(
            owner_scope.execution_domain_ref,
            owner_scope.strategy_ref,
            owner_scope.account_ref,
            registration_id=registered_entry.registration_id,
        ) == source.origin_run_id
        assert {
            (binding.mount, binding.operations) for binding in runtime.bindings
        } == {
            ("upstream.group_roll_exit_orders", entry_operations),
            ("upstream.group_roll_exit_order_query", query_operations),
        }

        binding = group_bindings.bind(
            strategy_registration,
            source.group_request,
            coordinator_principal_ref=_COORDINATOR,
        )
        route = GroupAccountRoute(
            execution_scope=source.source_execution_scope,
            risk_scope_fingerprint="a" * 64,
            reservation_scope_ref="reservation-dim05",
            order_scope_ref="order-dim05",
            query_scope_ref="query-dim05",
            instruments={
                plan.maximum_intent.instrument: GroupInstrumentRule(
                    metadata=_budget_metadata(
                        plan.maximum_intent.instrument,
                        expires_at_ns=source.group_request.deadline_ns + 1_000_000,
                        as_of_ns=now_ns - 1_000_000,
                    ),
                    base_units_per_order_unit=Decimal("1"),
                    allowed_order_types=frozenset({OrderType.LIMIT}),
                )
                for plan in source.group_request.legs
            },
            roll_exit_order_scope_ref=entry_scope_ref,
            roll_exit_query_scope_ref=query_scope_ref,
        )
        target = GroupCoordinatorTarget(
            scope_ref="group-scope-dim05",
            accounts={source.account_ref: route},
            max_group_lifetime_ns=10**18,
        )
        coordinator_call = _CoordinatorCall(
            strategy_registration,
            group_scope,
            target,
            binding,
        )
        token = _CURRENT_COORDINATOR_CALL.set(coordinator_call)
        try:
            port = _OrdersRpcPort(source.account_ref, BorrowedServiceClient(runtime))
            phase, prepared_authorization, prepared_observation = (
                port.prepare_group_roll_exit(request)
            )
            assert phase is GroupRollExitPhase.PREPARED
            assert prepared_authorization == authorization
            assert prepared_observation is None
            assert port.submit_group_roll_exit(prepared_authorization) == observation
            assert port.query_group_roll_exit(prepared_authorization) == observation
        finally:
            _CURRENT_COORDINATOR_CALL.reset(token)

        assert dispatched == [prepare_operation, submit_operation, query_operation]
        calls_before_wrong_mount = len(transport.operations)
        with pytest.raises(ServiceRuntimeError) as wrong_mount:
            runtime.call(
                "upstream.orders",
                submit_operation,
                {"authorization": authorization.to_payload()},
                account_ref=source.account_ref,
                portfolio_ref=source.portfolio_ref,
                scope_ref=entry_scope_ref,
            )
        assert wrong_mount.value.code == "AMBIGUOUS_OR_MISSING_ROUTE"
        assert len(transport.operations) == calls_before_wrong_mount
    finally:
        runtime.close()
        group_bindings.close()
        registry.close()
