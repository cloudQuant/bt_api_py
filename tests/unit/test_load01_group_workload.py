"""Focused source-fake coverage for the LOAD01 N5 group request path.

This exercises the real ServiceClientRuntime selector and the accepted DIM09
three-leg coordinator/Risk/owner/store chain. Only the wire transport boundary
is in-process; the exchange provider remains the fixture's synthetic leaf.
It does not wire the sustained LOAD01 runner topology or establish load
acceptance.
"""

from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal
from typing import Any

from bt_api_base.service_protocol import (
    WireEnvelope,
    WireMessageKind,
)
from bt_api_execution import (
    ExecutionState,
    GroupBudgetReservationState,
    GroupOrderSnapshot,
    GroupOrderState,
)
from bt_api_risk import (
    INSTRUMENT_METADATA_DIGEST_TAG,
    InstrumentRiskMetadata,
    RiskGroupReservationState,
)
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_execution_group import _plain_rpc_json
from bt_api_py.service_runtime import ServiceClientRuntime
from tests.integration import _dim09_three_leg_fixtures as dim09
from tests.unit._load01_group_workload import (
    N5_BASE_UNIT_SCALES,
    N5_GROUP_QUANTITY_UNIT,
    N5_LEG_IDS,
    N5_LEG_SPECS,
    build_n5_group_request,
    query_n5_group,
    submit_n5_group,
)
from tests.unit._load01_profiles import load01_profile


_GROUP_OPERATIONS = frozenset({"groups.submit", "groups.query"})


def _n5_instrument_metadata() -> tuple[InstrumentRiskMetadata, ...]:
    now_ns = time.time_ns()
    return tuple(
        InstrumentRiskMetadata(
            instrument=spec.instrument,
            metadata_version=f"load01-n5-{spec.instrument.lower()}-v1",
            as_of_ns=now_ns - 1_000_000_000,
            expires_at_ns=now_ns + 3_600_000_000_000,
            tick_size=Decimal("0.01"),
            quantity_step=spec.quantity_step,
            contract_multiplier=spec.contract_multiplier,
            max_gross_notional=Decimal("100000"),
            taker_fee_bps=spec.taker_fee_bps,
            fixed_fee=spec.fixed_fee,
            max_slippage_bps=spec.max_slippage_bps,
            quantity_unit=N5_GROUP_QUANTITY_UNIT,
            valuation_unit="USD",
            quote_currency="USD",
        )
        for spec in N5_LEG_SPECS
    )


def _n5_group_client_config(cluster: dim09.Dim09ThreeLegCluster):
    context = cluster.strategy_registration.context
    return parse_client_config(
        {
            "client_config_schema_version": 1,
            "process_role": "strategy_client",
            "registration": {"strategy_ref": context.scope.strategy_ref},
            "clients": {
                "execution": {
                    "config_schema_version": 1,
                    "settings": {
                        "bindings": [
                            {
                                "mount": "broker.order_groups",
                                "service_id": cluster.service_identity.actor_ref,
                                "instance_id": dim09._COORDINATOR_INSTANCE,
                                "scope_ref": dim09._COORDINATOR_SCOPE_REF,
                                "portfolio_ref": context.scope.portfolio_ref,
                                "transport_ref": "load01-n5-coordinator-rpc",
                            }
                        ],
                        "options": {"request_timeout_ms": 1_000},
                    },
                }
            },
            "communication": {
                "config_schema_version": 1,
                "profiles": {
                    "load01-n5-coordinator-rpc": {
                        "backend": "zmq",
                        "config_schema_version": 1,
                        "endpoints": {"rpc_connect": "tcp://127.0.0.1:31991"},
                        "security_profile_ref": "load01-n5-coordinator-security",
                    }
                },
            },
        }
    )


class _CoordinatorTransport:
    """Fake only the wire leaf; dispatch group RPCs into the real coordinator."""

    def __init__(self, cluster: dim09.Dim09ThreeLegCluster) -> None:
        self.cluster = cluster
        self.peer = cluster.service_identity
        self.context = cluster.strategy_registration.context
        self.registrations: dict[str, dict[str, Any]] = {}
        self.business_routes: list[tuple[str, str, tuple[str, ...], str | None]] = []
        self.closed = False

    def request(self, request: WireEnvelope, *, timeout: float) -> WireEnvelope:
        assert timeout > 0
        operation = request.header.operation
        payload = request.payload
        if operation == "service.discover":
            result: dict[str, Any] = {
                "protocol_version": 2,
                "service_id": self.peer.actor_ref,
                "ready": True,
                "capabilities": [
                    {
                        "instance_id": dim09._COORDINATOR_INSTANCE,
                        "port": "service.order_group_entry",
                        "operations": sorted(dim09.GROUP_COORDINATOR_OPERATIONS),
                    }
                ],
            }
        elif operation == "service.register":
            assert request.header.identity == self.context.identity
            assert request.header.scope == self.context.scope
            registration_id = self.context.registration_id
            registration = {
                "registration_id": registration_id,
                "scope_ref": payload["scope_ref"],
                "operations": frozenset(payload["operations"]),
            }
            self.registrations[registration_id] = registration
            result = {
                "registration_id": registration_id,
                "service_epoch": self.cluster.strategy_registration.service_epoch,
                "policy_version": self.cluster.strategy_registration.policy_version,
                "requirements_version": self.cluster.strategy_registration.requirements_version,
                "operations": list(payload["operations"]),
                "expires_at": time.time() + 3_600,
                "lease_remaining_seconds": 3_600.0,
            }
        elif operation == "service.renew":
            registration = self.registrations[payload["registration_id"]]
            result = {
                "registration_id": registration["registration_id"],
                "service_epoch": self.cluster.strategy_registration.service_epoch,
                "policy_version": self.cluster.strategy_registration.policy_version,
                "requirements_version": self.cluster.strategy_registration.requirements_version,
                "operations": sorted(registration["operations"]),
                "expires_at": time.time() + 3_600,
                "lease_remaining_seconds": 3_600.0,
            }
        elif operation == "service.unregister":
            registration_id = payload["registration_id"]
            result = {
                "unregistered": self.registrations.pop(registration_id, None) is not None,
                "registration_id": registration_id,
            }
        else:
            registration = self.registrations[payload["registration_id"]]
            assert operation in registration["operations"]
            assert registration["scope_ref"] == dim09._COORDINATOR_SCOPE_REF
            assert request.header.identity == self.context.identity
            assert request.header.scope == self.context.scope
            assert payload["service_epoch"] == self.cluster.strategy_registration.service_epoch
            self.business_routes.append(
                (
                    operation,
                    registration["scope_ref"],
                    request.header.scope.account_refs,
                    request.header.scope.portfolio_ref,
                )
            )
            result = self.cluster.coordinator_service.handle(
                operation,
                context=self.context,
                arguments=payload["arguments"],
            )

        return WireEnvelope(
            replace(
                request.header,
                kind=WireMessageKind.REPLY,
                message_id="reply-" + request.header.message_id,
                correlation_id=request.header.message_id,
                identity=self.peer,
            ),
            {"ok": True, "result": result},
        )

    def close(self) -> None:
        self.closed = True


def test_n5_group_runtime_reaches_dim09_v3_risk_owner_and_durable_query(
    tmp_path, monkeypatch
) -> None:
    metadata = _n5_instrument_metadata()
    expected_provider_orders = (
        ("PUT-USD", Decimal("2"), Decimal("10"), "buy-limit"),
        ("FUT-USD", Decimal("8"), Decimal("20"), "sell-limit"),
        ("CALL-USD", Decimal("4"), Decimal("5"), "buy-limit"),
    )
    cluster = dim09.Dim09ThreeLegCluster(
        tmp_path,
        monkeypatch,
        instrument_metadata=metadata,
        base_units_per_order_unit_by_instrument=dict(N5_BASE_UNIT_SCALES),
        expected_provider_orders=expected_provider_orders,
        provider_exchange_name="FIXTURE___SPOT",
        mapper_exchange_name="OKX___SWAP",
    )
    from bt_api_py._venue_mappers import MAPPERS

    assert cluster.provider_exchange_name == "FIXTURE___SPOT"
    assert cluster.mapper_exchange_name == "OKX___SWAP"
    assert MAPPERS["FIXTURE___SPOT"] is MAPPERS["OKX___SWAP"]
    assert cluster.execution_scope.provider == cluster.risk_scope.provider == "fixture"
    assert cluster.execution_scope.environment == cluster.risk_scope.environment == "simulation"
    assert cluster.execution_scope.account_ref == cluster.account_ref
    assert cluster.owner_scope.account_ref == cluster.account_ref
    proof_target = cluster.proof_source.targets[cluster.owner_scope]
    assert cluster.provider_exchange_name == proof_target.provider_contract.exchange_name
    assert cluster.coordinator_target.accounts[cluster.account_ref].execution_scope == (
        cluster.execution_scope
    )
    assert proof_target.risk_target.account_scope == cluster.risk_scope
    runtime = None
    transport = _CoordinatorTransport(cluster)
    try:
        profile = load01_profile("N5")
        group_id = "load01-n5-a00-cycle1"
        request = build_n5_group_request(
            group_id=group_id,
            account_ref=cluster.account_ref,
            scope=cluster.execution_scope,
            metadata=metadata,
            deadline_ns=cluster.deadline_ns,
        )
        for forbidden_unit in ("contracts", "unknown"):
            invalid_metadata = (
                replace(metadata[0], quantity_unit=forbidden_unit),
                *metadata[1:],
            )
            try:
                build_n5_group_request(
                    group_id=group_id,
                    account_ref=cluster.account_ref,
                    scope=cluster.execution_scope,
                    metadata=invalid_metadata,
                    deadline_ns=cluster.deadline_ns,
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"N5 group request accepted non-native quantity unit {forbidden_unit!r}"
                )
        assert request == cluster.v3_body(
            group_id,
            leg_ids=N5_LEG_IDS,
            sides=tuple(spec.side for spec in N5_LEG_SPECS),
            ratios=tuple(format(spec.ratio, "f") for spec in N5_LEG_SPECS),
            leg_instruments=tuple(spec.instrument for spec in N5_LEG_SPECS),
            quantities=tuple(spec.quantity for spec in N5_LEG_SPECS),
            prices=tuple(spec.price for spec in N5_LEG_SPECS),
            deadline_ns=cluster.deadline_ns,
        )

        context = cluster.strategy_registration.context

        def resolve_scope(scope_ref: str):
            if scope_ref != dim09._COORDINATOR_SCOPE_REF:
                raise AssertionError("unexpected group scope")
            return context.scope

        def resolve_peer(service_id: str):
            if service_id != cluster.service_identity.actor_ref:
                raise AssertionError("unexpected group service")
            return cluster.service_identity

        runtime = ServiceClientRuntime(
            _n5_group_client_config(cluster),
            identity=context.identity,
            scope_resolver=resolve_scope,
            peer_resolver=resolve_peer,
            transport_factory=lambda _profile, _peer: transport,
            client_session_id="load01-n5-sourcefake-session",
            run_id=context.scope.run_id,
            requested_operations={"broker.order_groups": _GROUP_OPERATIONS},
        )
        runtime.start()
        assert len(runtime.bindings) == 1
        binding = runtime.bindings[0]
        assert binding.mount == "broker.order_groups"
        assert binding.scope_ref == dim09._COORDINATOR_SCOPE_REF
        assert binding.operations == _GROUP_OPERATIONS
        assert binding.scope.account_ref is None
        assert binding.scope.account_refs == (cluster.account_ref,)
        assert binding.scope.portfolio_ref == dim09._PORTFOLIO

        wrong_account_request = {**request, "legs": [dict(leg) for leg in request["legs"]]}
        wrong_account_request["legs"][1]["account_ref"] = "load01-n5-unbound-account"
        try:
            submit_n5_group(
                runtime,
                profile,
                strategy_index=0,
                cycle=1,
                request=wrong_account_request,
                account_ref=cluster.account_ref,
                portfolio_ref=dim09._PORTFOLIO,
                scope_ref=dim09._COORDINATOR_SCOPE_REF,
            )
        except ValueError as error:
            assert "does not match its registered cycle" in str(error)
        else:
            raise AssertionError("group request with an unbound leg account was accepted")
        assert transport.business_routes == []

        submitted = submit_n5_group(
            runtime,
            profile,
            strategy_index=0,
            cycle=1,
            request=request,
            account_ref=cluster.account_ref,
            portfolio_ref=dim09._PORTFOLIO,
            scope_ref=dim09._COORDINATOR_SCOPE_REF,
        )
        assert submitted["ok"] is True, submitted
        assert cluster.provider.position_mode_query_count == 0
        queried = query_n5_group(
            runtime,
            profile,
            strategy_index=0,
            cycle=1,
            group_id=group_id,
            portfolio_ref=dim09._PORTFOLIO,
            scope_ref=dim09._COORDINATOR_SCOPE_REF,
        )
        assert queried["ok"] is True, queried
        snapshot = GroupOrderSnapshot.from_payload(
            _plain_rpc_json(queried["result"]["snapshot"])
        )

        assert snapshot.state is GroupOrderState.PAIRED
        assert snapshot.residual_vector == (Decimal("0"), Decimal("0"), Decimal("0"))
        assert snapshot.residual_base_units == Decimal("0")
        assert snapshot.request.version == 3
        assert snapshot.request.budget_quote is not None
        assert len(snapshot.request.budget_quote.legs) == 3
        assert snapshot.budget_reservation is not None
        assert snapshot.budget_reservation.state is GroupBudgetReservationState.HELD
        assert len(snapshot.reservations) == 1
        assert [leg.leg_id for leg in snapshot.legs] == list(N5_LEG_IDS)

        quote = snapshot.request.budget_quote
        assert quote.valuation_unit == "USD"
        assert {
            leg.leg_id: leg.gross_notional for leg in quote.legs
        } == {
            "leg-put": Decimal("40.901"),
            "leg-future": Decimal("161.0608"),
            "leg-call": Decimal("60.9515"),
        }
        expected_reserved_notional = Decimal("262.9133")
        assert quote.gross_notional == expected_reserved_notional
        assert snapshot.budget_reservation.gross_notional == expected_reserved_notional
        assert snapshot.budget_reservation.policy_fingerprint == quote.policy_fingerprint
        assert snapshot.budget_reservation.quote_fingerprint == quote.fingerprint

        plans_by_leg = {plan.leg_id: plan for plan in snapshot.request.ordered_legs}
        quote_by_leg = {leg.leg_id: leg for leg in quote.legs}
        snapshot_legs_by_id = {leg.leg_id: leg for leg in snapshot.legs}
        assert len({spec.instrument for spec in N5_LEG_SPECS}) == 3
        assert len({spec.quantity for spec in N5_LEG_SPECS}) == 3
        assert len({spec.price for spec in N5_LEG_SPECS}) == 3
        assert len({spec.ratio for spec in N5_LEG_SPECS}) == 3
        for spec, metadata_item in zip(N5_LEG_SPECS, metadata, strict=True):
            plan = plans_by_leg[spec.leg_id]
            quote_leg = quote_by_leg[spec.leg_id]
            snapshot_leg = snapshot_legs_by_id[spec.leg_id]
            maximum_intent = plan.maximum_intent
            assert maximum_intent.instrument == spec.instrument
            assert maximum_intent.side is spec.side
            assert maximum_intent.quantity == spec.quantity
            assert maximum_intent.price == spec.price
            assert plan.ratio == spec.ratio
            assert plan.base_units_per_order_unit == spec.contract_multiplier
            assert plan.quantity_step == spec.quantity_step
            assert maximum_intent.metadata_version == metadata_item.metadata_version
            assert maximum_intent.tags[INSTRUMENT_METADATA_DIGEST_TAG] == metadata_item.digest
            assert quote_leg.intent_fingerprint == maximum_intent.fingerprint
            assert quote_leg.metadata_digest == metadata_item.digest
            assert snapshot_leg.submitted_intent.instrument == spec.instrument
            assert snapshot_leg.submitted_intent.quantity == spec.quantity
            assert snapshot_leg.submitted_intent.price == spec.price

        expected_intent_ids = [cluster.bound_intent_id(group_id, leg_id) for leg_id in N5_LEG_IDS]
        assert cluster.loopback.submitted_intent_ids() == expected_intent_ids
        assert cluster.loopback.operation_names().count("reservations.reserve") == 1
        assert cluster.loopback.operation_names().count("orders.submit_group_leg") == 3
        for leg in snapshot.legs:
            intent_id = cluster.bound_intent_id(group_id, leg.leg_id)
            durable_intent = cluster.execution_store.get_intent(
                intent_id, scope=cluster.execution_scope
            )
            durable_record = cluster.execution_store.get(
                intent_id, scope=cluster.execution_scope
            )
            assert durable_intent is not None and durable_record is not None
            assert durable_intent.scope.account_ref == cluster.account_ref
            spec = next(item for item in N5_LEG_SPECS if item.leg_id == leg.leg_id)
            metadata_item = cluster.instrument_metadata[spec.instrument]
            assert durable_intent.instrument == spec.instrument
            assert durable_intent.side is spec.side
            assert durable_intent.quantity == spec.quantity
            assert durable_intent.price == spec.price
            assert durable_intent.metadata_version == metadata_item.metadata_version
            assert durable_intent.tags[INSTRUMENT_METADATA_DIGEST_TAG] == metadata_item.digest
            assert durable_record.state is ExecutionState.FILLED
            assert durable_record.group_binding is not None
            assert durable_record.group_binding.group_id == group_id
            assert durable_record.group_binding.leg_id == leg.leg_id
            assert durable_intent.fingerprint == durable_record.payload_sha256

        risk_vector = cluster.risk_admission.read_group_vector_snapshot(
            cluster.risk_scope, group_id
        )
        assert len(risk_vector.legs) == 3
        risk_legs_by_id = {item.leg_id: item for item in risk_vector.legs}
        assert {item.state for item in risk_vector.legs} == {
            RiskGroupReservationState.SETTLED
        }
        for spec in N5_LEG_SPECS:
            risk_leg = risk_legs_by_id[spec.leg_id]
            intent_id = cluster.bound_intent_id(group_id, spec.leg_id)
            durable_intent = cluster.execution_store.get_intent(
                intent_id, scope=cluster.execution_scope
            )
            assert durable_intent is not None
            assert risk_leg.intent_id == intent_id
            claim = cluster.risk_admission.query_group_leg_claim(
                cluster.risk_scope, group_id, spec.leg_id
            )
            mapped_actual = cluster.risk_admission.map_group_intent(
                durable_intent, scope=cluster.risk_scope
            )
            assert claim is not None and claim.resolved is True
            assert claim.group_id == group_id
            assert claim.leg_id == spec.leg_id
            assert claim.permit.intent_id == intent_id
            assert claim.reserved_intent_hash == risk_leg.reserved_intent_hash
            assert claim.exposure_profile_fingerprint == risk_leg.exposure_profile_fingerprint
            assert claim.actual_intent_hash == risk_leg.actual_intent_hash
            assert mapped_actual.intent_id == intent_id
            assert mapped_actual.scope == cluster.risk_scope
            assert mapped_actual.quantity_unit == N5_GROUP_QUANTITY_UNIT
            assert claim.actual_intent_hash == mapped_actual.fingerprint
            assert claim.actual_quantity == durable_intent.quantity
            assert claim.actual_notional == mapped_actual.notional
            assert mapped_actual.notional == quote_by_leg[spec.leg_id].gross_notional

        writes, provider_counters = cluster.provider.snapshot()
        assert len(writes) == 3 and len(set(writes)) == 3
        assert provider_counters == {"order_queries": 3, "fill_queries": 3}
        assert transport.business_routes == [
            (
                "groups.submit",
                dim09._COORDINATOR_SCOPE_REF,
                cluster.strategy_registration.context.scope.account_refs,
                dim09._PORTFOLIO,
            ),
            (
                "groups.query",
                dim09._COORDINATOR_SCOPE_REF,
                cluster.strategy_registration.context.scope.account_refs,
                dim09._PORTFOLIO,
            ),
        ]
    finally:
        if runtime is not None:
            runtime.close()
        cluster.close()
    assert transport.closed is True


def test_n5_actual_okx_directbackend_contract_mapper_and_net_mode(tmp_path) -> None:
    """Exercise OKX mapping under its real venue key with only a fake feed leaf."""

    from bt_api_py._contracts.models import OrderRequest, OrderType, Side

    feed = dim09._ThreeLegMappedFeed(
        str(tmp_path / "n5-okx-mapper-provider.sqlite3"),
        ("filled", "filled", "filled"),
        dim09._V2_PROVIDER_ACCOUNT,
        expected_provider_orders=(
            ("PUT-USD", Decimal("2"), Decimal("10"), "buy-limit"),
            ("FUT-USD", Decimal("8"), Decimal("20"), "sell-limit"),
            ("CALL-USD", Decimal("4"), Decimal("5"), "buy-limit"),
        ),
        expected_size_in_contracts=True,
        expected_position_mode="net",
    )
    api = None
    try:
        with dim09._synthetic_plugin_discovery():
            api = dim09.BtApi(debug=False)
        assert api.transport_mode.value == "direct"
        api.exchange_feeds["OKX___SWAP"] = feed
        for index, spec in enumerate(N5_LEG_SPECS, start=1):
            request = OrderRequest(
                symbol=spec.instrument,
                side=Side(spec.side.value.lower()),
                order_type=OrderType.LIMIT,
                quantity=spec.quantity,
                account_id=dim09._V2_PROVIDER_ACCOUNT,
                client_order_id="grp-" + f"{index:040x}",
                price=spec.price,
                time_in_force="IOC",
                reduce_only=False,
                idempotency_key="grp-" + f"{index:040x}",
                quantity_unit="contracts",
                offset="open",
            )
            ack = api.make_order("OKX___SWAP", request, normalized=True)
            assert ack["client_order_id"] == request.client_order_id
        assert feed.position_mode_query_count == 1
        assert feed._provider_order_index == 3
    finally:
        if api is not None:
            api.close()
