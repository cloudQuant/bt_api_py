"""Authenticated owner.cancel ACK-to-terminal Risk resolution lifecycle."""

from __future__ import annotations

import time
from decimal import Decimal

import pytest
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    CancelObservation,
    ExecutionScope,
    OrderIntent,
    OrderType,
    PositionEffect,
    ProviderObservation,
    Side,
    SqliteExecutionStore,
    TimeInForce,
)
from bt_api_risk import (
    CancelDispatchResolutionReceiptStatus,
    DispatchResolutionReceiptStatus,
    DurableRiskAdmissionAdapter,
    DurableRiskGate,
    InstrumentRiskAdmissionMapper,
    InstrumentRiskMetadata,
    InstrumentRiskRegistry,
    RiskPolicy,
)
from bt_api_py.bt_api import BtApi
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from bt_api_py.service_risk_order_entry import RiskOrderEntryService, RiskOrderTarget
from bt_api_py.service_risk_order_journal import RiskOrderJournal
from bt_api_py.service_runtime import (
    ClientCurveKeys,
    ServiceClientRuntime,
    ZmqTransportFactory,
)
from tests.integration import test_service_execution_risk_cancel_owner_control as owner_case
from tests.unit import test_service_risk_order_exposure_integration as risk_case


class _RiskBackedDispatch:
    """Route the execution owner's submit to the real Risk order service."""

    def __init__(
        self,
        risk_service,
        identity: WireIdentity,
        scope: WireScope,
    ) -> None:
        self._risk_service = risk_service
        self._identity = identity
        self._scope = scope
        self.submitted: list[str] = []
        self.cancelled: list[str] = []
        self.submit_reply: object | None = None

    def submit(self, intent) -> ProviderObservation:
        reply = self._risk_service.handle(
            "orders.submit",
            principal_ref=self._identity.principal_ref,
            identity=self._identity,
            scope=self._scope,
            arguments={
                "intent_id": intent.intent_id,
                "signal_id": intent.signal_id,
                "instrument": intent.instrument,
                "side": intent.side.value,
                "position_effect": intent.position_effect.value,
                "order_type": intent.order_type.value,
                "quantity": format(intent.quantity, "f"),
                "price": None if intent.price is None else format(intent.price, "f"),
                "time_in_force": intent.time_in_force.value,
                "reduce_only": intent.reduce_only,
                "metadata_version": intent.metadata_version,
                "deadline_ns": intent.deadline_ns,
                "tags": dict(intent.tags),
            },
        )
        self.submit_reply = reply
        assert reply["ok"] is True, reply
        record = reply["result"]["record"]
        assert record["intent_id"] == intent.intent_id
        assert record["state"] == "ACKED_TRACKED", reply
        provider_order_id = record["provider_order_id"]
        self.submitted.append(intent.intent_id)
        return ProviderObservation.accepted(intent.intent_id, provider_order_id)

    def cancel(self, intent) -> CancelObservation:
        self.cancelled.append(intent.cancel_id)
        return CancelObservation.accepted(
            intent.cancel_id,
            intent.target_intent_id,
            intent.provider_order_id,
        )


@pytest.mark.integration
def test_owner_cancel_ack_keeps_risk_latched_until_persisted_terminal_history(
    tmp_path, monkeypatch
) -> None:
    # The synthetic V2 feed emits its own fixed provider-account identity.
    # Bind the owner fixture to that same account instead of inventing a
    # mapping between distinct service and provider accounts.
    monkeypatch.setattr(owner_case, "_ACCOUNT", risk_case.base._PROVIDER_ACCOUNT)
    endpoint = owner_case._endpoint()
    config = owner_case._service_config(endpoint)
    server_public, server_secret = zmq.curve_keypair()
    strategy_public, strategy_secret = zmq.curve_keypair()
    owner_public, owner_secret = zmq.curve_keypair()

    service_identity = WireIdentity(
        WireRole.SERVICE, owner_case._SERVICE_PRINCIPAL, owner_case._SERVICE_ID
    )
    strategy_identity = WireIdentity(
        WireRole.STRATEGY_CLIENT,
        owner_case._STRATEGY_PRINCIPAL,
        owner_case._STRATEGY_ACTOR,
    )
    owner_identity = WireIdentity(
        WireRole.SERVICE, owner_case._OWNER_PRINCIPAL, owner_case._OWNER_ACTOR
    )
    wire_scope = WireScope(
        owner_case._DOMAIN,
        account_ref=owner_case._ACCOUNT,
        strategy_ref=owner_case._STRATEGY,
        run_id=owner_case._RUN_ID,
    )
    target_scope = WireScope(
        owner_case._DOMAIN,
        account_ref=owner_case._ACCOUNT,
        strategy_ref=owner_case._STRATEGY,
    )
    execution_scope = ExecutionScope(
        risk_case._ACCOUNT_SCOPE.provider,
        "simulation",
        owner_case._ACCOUNT,
        owner_case._STRATEGY,
        "afi08-owner-cancel-session",
    )
    strategy_grant = ResourceGrant(
        owner_case._INSTANCE_ID,
        "service.order_entry",
        owner_case._SCOPE_REF,
        target_scope,
        frozenset({"orders.submit"}),
    )
    owner_grant = ResourceGrant(
        owner_case._INSTANCE_ID,
        "service.owner_control",
        owner_case._SCOPE_REF,
        wire_scope,
        frozenset({"owner.cancel"}),
    )
    authority = TrustedServiceAuthority(
        identity=service_identity,
        scope=WireScope(owner_case._DOMAIN),
        scope_ref=config.scope_ref,
        scope_version="afi08-cancel-service-scope-v1",
        security_ref=owner_case._SERVER_SECURITY_REF,
        security_version="curve-v1",
        registration_policy_ref=config.registration_policy_ref,
        registration_policy_version="afi08-cancel-registration-v1",
        service_requirements_ref=config.service_requirements_ref,
        service_requirements_version="afi08-cancel-requirements-v1",
        curve_public_key=server_public,
        curve_secret_key=server_secret,
        principals={
            strategy_public: owner_case._STRATEGY_PRINCIPAL,
            owner_public: owner_case._OWNER_PRINCIPAL,
        },
        grants={
            owner_case._STRATEGY_PRINCIPAL: (
                PrincipalGrant(strategy_identity, (strategy_grant,)),
            ),
            owner_case._OWNER_PRINCIPAL: (PrincipalGrant(owner_identity, (owner_grant,)),),
        },
    )

    execution_store = SqliteExecutionStore(tmp_path / "execution.sqlite3")
    risk_journal = RiskOrderJournal(
        tmp_path / "risk-order-journal.sqlite3", writer_id="afi08-cancel-terminal-journal"
    )
    # Risk's simulation evidence contract uses the canonical provider token.
    # The provider exchange class retains its provider-specific casing, while
    # the AccountScope authority must use the lowercase fixture identity.
    risk_scope = risk_case._ACCOUNT_SCOPE
    risk_gate = DurableRiskGate(
        tmp_path / "risk.sqlite3",
        RiskPolicy(
            "afi08-cancel-terminal-risk-v1",
            Decimal("1000"),
            max_increase_count=5,
            permit_ttl_seconds=30,
            notional_unit="USD",
        ),
        execution_journal_authority=risk_journal,
    )
    cancel_gate = owner_case.RiskExecutionCancelAdmissionGate(
        risk_gate,
        risk_journal,
        execution_store,
        risk_scope=risk_scope,
        execution_scope=execution_scope,
        wire_scope_template=target_scope,
    )

    resource_state: dict[str, object] = {}
    quota_calls: list[tuple[object, ...]] = []

    def quota_admission(*args) -> None:
        quota_calls.append(args)

    def resource_provider(component, instance_id, _options, _scope):
        assert component == "execution" and instance_id == owner_case._INSTANCE_ID
        now_ns = time.time_ns()
        metadata = InstrumentRiskMetadata(
            instrument=risk_case._V2_INSTRUMENT,
            metadata_version=risk_case._V2_METADATA_VERSION,
            as_of_ns=now_ns - 1_000_000_000,
            expires_at_ns=now_ns + 3_600_000_000_000,
            tick_size=Decimal("0.01"),
            quantity_step=Decimal("0.001"),
            contract_multiplier=Decimal("1"),
            max_gross_notional=Decimal("1000"),
            taker_fee_bps=Decimal("0"),
            fixed_fee=Decimal("0"),
            max_slippage_bps=Decimal("0"),
            quantity_unit="BTC",
            valuation_unit="USD",
        )
        registry = InstrumentRiskRegistry((metadata,))
        target = RiskOrderTarget(
            account_scope=risk_scope,
            strategy_id=owner_case._STRATEGY,
            exchange_name=risk_case._EXCHANGE,
            provider_account_id=risk_case.base._PROVIDER_ACCOUNT,
            registry=registry,
        )
        bt_api = BtApi(debug=False)
        provider_feed = risk_case._V2SyntheticBtApiFeed(
            str(tmp_path / "provider.sqlite3"), ("open",)
        )
        bt_api.exchange_feeds[risk_case._EXCHANGE] = provider_feed
        mapper = InstrumentRiskAdmissionMapper(risk_scope, registry)
        admission = DurableRiskAdmissionAdapter(risk_gate, mapper)
        risk_target_scope = WireScope(
            owner_case._DOMAIN,
            account_ref=owner_case._ACCOUNT,
            strategy_ref=owner_case._STRATEGY,
        )
        risk_service = RiskOrderEntryService(
            bt_api,
            {risk_target_scope: target},
            {risk_target_scope: admission},
            risk_journal,
            risk_gate,
            {risk_target_scope: risk_case._contract(("GTC",))},
        )
        risk_identity = WireIdentity(
            WireRole.STRATEGY_CLIENT,
            owner_case._STRATEGY_PRINCIPAL,
            owner_case._STRATEGY_ACTOR,
        )
        dispatch = _RiskBackedDispatch(risk_service, risk_identity, wire_scope)
        resource_state["bt_api"] = bt_api
        resource_state["risk_service"] = risk_service
        resource_state["dispatch"] = dispatch
        return ComponentResources.borrowed(
            {
                "store": execution_store,
                "targets": {target_scope: execution_scope},
                "dispatch": dispatch,
                "admission": {
                    "cancel_gate": cancel_gate,
                    "cancel_quota_admission": quota_admission,
                    "allow_unprotected": True,
                },
            },
            ready=lambda: True,
        )

    resolved_resource_provider = resource_provider
    cached_resources: list[ComponentResources] = []

    def resource_provider(component, instance_id, options, scope):
        if cached_resources:
            return cached_resources[0]
        try:
            resources = resolved_resource_provider(component, instance_id, options, scope)
            cached_resources.append(resources)
            return resources
        except Exception as error:
            resource_state["resource_error"] = f"{type(error).__name__}: {error}"
            raise

    # Call the same owner dispatch wrapper and Risk orders.submit handler
    # before bringing up ServiceHost. This separates a Risk target/admission or
    # provider-normalization failure from Host registration/transport failures.
    resource_provider("execution", owner_case._INSTANCE_ID, {}, wire_scope)
    preflight_dispatch = resource_state["dispatch"]
    try:
        preflight_observation = preflight_dispatch.submit(
            OrderIntent(
                intent_id="afi08-risk-handler-preflight",
                scope=execution_scope,
                signal_id="afi08-risk-handler-preflight-signal",
                instrument=risk_case._V2_INSTRUMENT,
                side=Side.BUY,
                position_effect=PositionEffect.OPEN,
                order_type=OrderType.LIMIT,
                quantity=Decimal("0.1"),
                price=Decimal("20"),
                time_in_force=TimeInForce.GTC,
                metadata_version=risk_case._V2_METADATA_VERSION,
            )
        )
    except Exception as error:
        raise AssertionError(
            "direct Risk handler preflight failed before Host start",
            type(error).__name__,
            str(error),
            resource_state.get("dispatch").submit_reply,
        ) from error
    assert preflight_observation.intent_id == "afi08-risk-handler-preflight"
    assert resource_state["dispatch"].submitted == ["afi08-risk-handler-preflight"]
    resource_state["dispatch"].submitted.clear()
    resource_state["dispatch"].submit_reply = None

    host = ServiceHost(
        config,
        authority,
        tmp_path / "registry.sqlite3",
        resource_provider,
    )
    host_thread = owner_case._HostThread(host)
    strategy_runtime = owner_runtime = None
    strategy_started = owner_started = False
    try:
        try:
            host_thread.start()
        except AssertionError as error:
            raise AssertionError((*error.args, resource_state.get("resource_error"))) from None
        strategy_runtime = ServiceClientRuntime(
            owner_case._client_config(endpoint, strategy=True),
            identity=strategy_identity,
            scope_resolver=lambda _ref: target_scope,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {
                    owner_case._STRATEGY_SECURITY_REF: ClientCurveKeys(
                        server_public, strategy_public, strategy_secret
                    )
                }
            ),
            run_id=owner_case._RUN_ID,
            requested_operations={"broker.orders": frozenset({"orders.submit"})},
        )
        strategy_summary = strategy_runtime.start()
        strategy_started = True
        assert strategy_summary.degraded_count == 0

        owner_runtime = ServiceClientRuntime(
            owner_case._client_config(endpoint, strategy=False),
            identity=owner_identity,
            scope_resolver=lambda _ref: wire_scope,
            peer_resolver=lambda _service_id: service_identity,
            transport_factory=ZmqTransportFactory(
                {
                    owner_case._OWNER_SECURITY_REF: ClientCurveKeys(
                        server_public, owner_public, owner_secret
                    )
                }
            ),
            requested_operations={"upstream.owner_control": frozenset({"owner.cancel"})},
        )
        owner_summary = owner_runtime.start()
        owner_started = True
        assert owner_summary.degraded_count == 0

        submit = strategy_runtime.call(
            "broker.orders",
            "orders.submit",
            {
                "intent_id": "afi08-terminal-cancel-target",
                "signal_id": "afi08-terminal-cancel-signal",
                "instrument": risk_case._V2_INSTRUMENT,
                "side": "BUY",
                "position_effect": "OPEN",
                "order_type": "LIMIT",
                "quantity": "1",
                "price": "20",
                "time_in_force": "GTC",
                "reduce_only": False,
                "metadata_version": risk_case._V2_METADATA_VERSION,
                "deadline_ns": None,
                "tags": {},
            },
            account_ref=owner_case._ACCOUNT,
            scope_ref=owner_case._SCOPE_REF,
        )
        assert submit["ok"] is True, submit
        target_record = submit["result"]["record"]
        assert target_record["state"] == "ACKED", (
            target_record,
            resource_state["dispatch"].submitted,
            resource_state["dispatch"].submit_reply,
        )
        assert resource_state["dispatch"].submitted == ["afi08-terminal-cancel-target"]

        risk_target_command = risk_journal.get_command(
            risk_scope,
            owner_case._STRATEGY_PRINCIPAL,
            "afi08-terminal-cancel-target",
        )
        assert risk_target_command is not None
        target_receipt = risk_gate.dispatch_resolution_receipt(risk_target_command.permit_id)
        assert target_receipt.status is DispatchResolutionReceiptStatus.ACKED_TRACKED_RESOLVED
        assert target_receipt.dispatch_latch_active is False
        target_ack = dict(risk_target_command.latest_event)
        assert target_ack["claim_digest"] == target_receipt.claim.claim_digest
        assert target_ack["proof_ready_record_sha256"] == target_receipt.proof.journal_record_sha256
        assert target_ack["proof_ready_revision"] == target_receipt.proof.journal_revision
        assert target_ack["proof_sha256"] == target_receipt.proof.fingerprint
        assert target_ack["recovery_attestation_sha256"] is None
        proof_ready_event = risk_journal.get_event(
            risk_scope,
            owner_case._STRATEGY_PRINCIPAL,
            "afi08-terminal-cancel-target",
            target_receipt.proof.journal_revision,
        )
        assert proof_ready_event is not None
        assert proof_ready_event.event_type == "PROOF_READY"
        assert target_ack["proof_ready_revision"] == proof_ready_event.revision
        assert target_ack["proof_ready_record_sha256"] == proof_ready_event.record_sha256
        with risk_journal.writer_fence(risk_scope) as fence:
            assert risk_journal.has_resolved_ack_history(
                fence,
                principal_ref=owner_case._STRATEGY_PRINCIPAL,
                intent_id="afi08-terminal-cancel-target",
                receipt=target_receipt,
            )

        first = owner_runtime.call(
            "upstream.owner_control",
            "owner.cancel",
            {"command_id": "afi08-terminal-cancel-command"},
            account_ref=owner_case._ACCOUNT,
            scope_ref=owner_case._SCOPE_REF,
        )
        assert first["code"] == "OWNER_CANCEL_ACKNOWLEDGED", first
        first_result = first["result"]
        assert first_result["outcome"] == "ACKNOWLEDGED"
        cancel_id = first_result["items"][0]["cancel_id"]
        cancel_record = execution_store.get_cancel(cancel_id, scope=execution_scope)
        assert cancel_record is not None
        assert cancel_record.state.value == "ACKED"
        assert cancel_record.dispatch_attempts == 1
        cancel_receipt = risk_gate.cancel_dispatch_resolution_receipt(
            cancel_record.permit_reference
        )
        assert cancel_receipt.status is CancelDispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE
        assert cancel_receipt.dispatch_latch_active is True
        assert cancel_receipt.proof is None

        execution_component = next(
            item for item in host._components if item.component == "execution"
        )
        owner_adapter = execution_component.handlers["owner.cancel"].__kwdefaults__["_adapter"]
        _order_facade, cancel_facade = owner_adapter._orders._facade_pair(execution_scope)
        reconciled = cancel_facade.reconcile(
            CancelObservation.cancelled(
                cancel_id,
                "afi08-terminal-cancel-target",
                target_record["provider_order_id"],
            )
        )
        assert reconciled.state.value == "CANCELLED"
        still_latched = risk_gate.cancel_dispatch_resolution_receipt(
            cancel_record.permit_reference
        )
        assert still_latched.status is CancelDispatchResolutionReceiptStatus.UNRESOLVED_ACTIVE
        assert still_latched.dispatch_latch_active is True

        source = execution_store.read_cancel_resolution_source_v1(
            cancel_id, scope=execution_scope
        )
        observation_history = [
            (event.event_type, event.state.value)
            for event in source.cancel_events
            if event.event_type
            in {"cancel_provider_observation", "cancel_reconciled_observation"}
        ]
        assert observation_history == [
            ("cancel_provider_observation", "ACKED"),
            ("cancel_reconciled_observation", "CANCELLED"),
        ]

        final = owner_runtime.call(
            "upstream.owner_control",
            "owner.cancel",
            {"command_id": "afi08-terminal-cancel-command"},
            account_ref=owner_case._ACCOUNT,
            scope_ref=owner_case._SCOPE_REF,
        )
        assert final["code"] == "OWNER_CANCEL_APPLIED", final
        assert final["result"]["outcome"] == "APPLIED"
        assert final["result"]["items"][0]["cancel_state"] == "CANCELLED"
        assert resource_state["dispatch"].cancelled == [cancel_id]
        assert len(quota_calls) == 1
        resolved = risk_gate.cancel_dispatch_resolution_receipt(
            cancel_record.permit_reference
        )
        assert resolved.status is CancelDispatchResolutionReceiptStatus.TERMINAL_RESOLVED
        assert resolved.dispatch_latch_active is False
        assert resolved.proof is not None
        assert resolved.proof.terminal_state.value == "CANCELLED"
        # Resolving the action latch preserves the original, separately-owned
        # Risk order proof and its exposure reservation.
        assert risk_gate.dispatch_resolution_receipt(risk_target_command.permit_id) == target_receipt
    finally:
        if owner_runtime is not None and owner_started:
            owner_runtime.close()
        if strategy_runtime is not None and strategy_started:
            strategy_runtime.close()
        host_thread.close()
        bt_api = resource_state.get("bt_api")
        if isinstance(bt_api, BtApi):
            bt_api.close()
        risk_gate.close()
        execution_store.close()
