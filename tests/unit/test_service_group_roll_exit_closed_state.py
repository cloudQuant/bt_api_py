"""Source-only UNKNOWN/replay regression for the DIM05 group roll-exit owner."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_execution import (
    ExecutionState,
    GroupAccountReservationSnapshot,
    GroupChildOrderStatus,
    GroupDispatchResolutionReceipt,
    GroupLegClaimReceipt,
    GroupLegOrderReceipt,
    GroupOrderCoordinator,
    GroupReservationPhase,
    GroupRiskLegSnapshot,
    GroupOrderState,
    group_event_chain_sha256,
    payload_sha256,
)

from bt_api_py.service_components import BorrowedServiceClient
from bt_api_py.service_execution_group import GroupCoordinatorService
from bt_api_py.service_group_roll_exit import (
    GroupRollExitAuthorizationV1,
    GroupRollExitObservationV1,
    GroupRollExitPhase,
    GroupRollExitRequestV1,
    group_roll_exit_action_id,
)
from tests.unit.test_service_execution_group_roll_exit import _service_fixture


_ACCOUNT = "account-a"
_RISK_SCOPE = "a" * 64
_POLICY = "b" * 64


def _not_found() -> LookupError:
    error = LookupError("group reservation does not exist")
    error.code = "GROUP_NOT_FOUND"  # type: ignore[attr-defined]
    return error


class _ReservationPort:
    """Small Risk leaf for the real Execution coordinator/store fold."""

    risk_scope_fingerprint = _RISK_SCOPE

    def __init__(self) -> None:
        self._group_id: str | None = None
        self._account_ref = _ACCOUNT
        self._group_fingerprint: str | None = None
        self._policy_fingerprint: str | None = None
        self._deadline_ns: int | None = None
        self._legs: dict[str, dict[str, Any]] = {}

    def query_group_vector(self, group_id: str) -> GroupAccountReservationSnapshot:
        if self._group_id != group_id:
            raise _not_found()
        return self._snapshot()

    def reserve_group_vector(
        self, group_id: str, members: tuple[tuple[str, Any], ...], *, deadline_ns: int
    ) -> GroupAccountReservationSnapshot:
        if self._group_id is not None:
            raise AssertionError("one durable reservation vector is expected")
        self._group_id = group_id
        self._deadline_ns = deadline_ns
        self._group_fingerprint = payload_sha256(
            {"group_id": group_id, "members": [(leg, intent.fingerprint) for leg, intent in members]}
        )
        self._policy_fingerprint = payload_sha256({"policy": "source-test"})
        self._legs = {
            leg_id: {
                "permit_id": f"permit-{leg_id}",
                "intent_id": intent.intent_id,
                "reserved_intent_hash": intent.fingerprint,
                "exposure_profile_fingerprint": payload_sha256(
                    {"instrument": intent.instrument, "side": intent.side.value}
                ),
                "actual_intent_hash": None,
                "state": "ACTIVE",
                "claimed": False,
                "resolved": False,
                "expires_at_ns": deadline_ns - 1,
            }
            for leg_id, intent in members
        }
        return self._snapshot()

    def set_claim(
        self, leg_id: str, claim: GroupLegClaimReceipt, *, actual_hash: str
    ) -> None:
        self._legs[leg_id].update(
            actual_intent_hash=actual_hash,
            state="SETTLED",
            claimed=True,
            resolved=True,
        )

    def release_group_unclaimed(
        self, group_id: str, reason: str
    ) -> GroupAccountReservationSnapshot:
        del reason
        if group_id != self._group_id:
            raise AssertionError("release must use the original group")
        for leg in self._legs.values():
            if not leg["claimed"]:
                leg.update(state="RELEASED", resolved=False)
        return self._snapshot()

    def _snapshot(self) -> GroupAccountReservationSnapshot:
        assert self._group_id is not None
        assert self._group_fingerprint is not None
        assert self._policy_fingerprint is not None
        assert self._deadline_ns is not None
        return GroupAccountReservationSnapshot(
            group_id=self._group_id,
            account_ref=self._account_ref,
            scope_fingerprint=self.risk_scope_fingerprint,
            phase=GroupReservationPhase.ACTIVE,
            group_fingerprint=self._group_fingerprint,
            policy_fingerprint=self._policy_fingerprint,
            deadline_ns=self._deadline_ns,
            legs=tuple(
                GroupRiskLegSnapshot(leg_id=leg_id, **leg)
                # The coordinator supplies Risk's canonical leg-id order.
                for leg_id, leg in self._legs.items()
            ),
        )


class _OrderPort:
    def __init__(self, reservations: _ReservationPort) -> None:
        self._reservations = reservations
        self._receipts: dict[str, GroupLegOrderReceipt] = {}
        self.submits: list[str] = []

    def query(self, intent_id: str) -> GroupLegOrderReceipt | None:
        return self._receipts.get(intent_id)

    def submit_group_leg(self, intent: Any, permit: Any) -> GroupLegOrderReceipt:
        permit.validate_order(intent)
        actual_hash = payload_sha256({"intent": intent.to_payload(), "risk": "mapped"})
        claim = GroupLegClaimReceipt(
            permit=permit,
            actual_child_intent_hash=actual_hash,
            claim_digest=payload_sha256({"permit": permit.permit_id, "intent": intent.fingerprint}),
            actual_quantity=intent.quantity,
            actual_notional=Decimal("100"),
        )
        self._reservations.set_claim(permit.leg_id, claim, actual_hash=actual_hash)
        status = GroupChildOrderStatus(
            intent_id=intent.intent_id,
            state=ExecutionState.FILLED,
            filled_quantity=intent.quantity,
            provider_order_id=f"source-{permit.leg_id}",
            average_price=intent.price,
            cumulative_commission=Decimal("0.01"),
            cumulative_commission_currency="USD",
            order_revision=1,
            trade_count=1,
        )
        resolution = GroupDispatchResolutionReceipt(
            claim=claim,
            resolution_kind="TERMINAL_EXPOSURE_RESOLVED",
            proof_sha256=payload_sha256({"terminal": intent.intent_id}),
            final_state=ExecutionState.FILLED,
            requested_quantity=intent.quantity,
            filled_quantity=intent.quantity,
            trade_count=1,
            order_revision=1,
            journal_revision=2,
            exposure_proof_sha256=payload_sha256({"exposure": intent.intent_id}),
            exposure_reservation_id=f"exposure-{permit.leg_id}",
            exposure_reservation_sha256=payload_sha256({"reservation": intent.intent_id}),
            dispatch_latch_active=False,
            has_other_active_freezes=False,
        )
        self.submits.append(intent.intent_id)
        receipt = GroupLegOrderReceipt(status=status, claim=claim, resolution=resolution)
        self._receipts[intent.intent_id] = receipt
        return receipt

    def resolve_group_leg(self, intent: Any, permit: Any, claim: Any, status: Any) -> Any:
        del permit, claim, status
        return self._receipts[intent.intent_id].resolution


class _RiskRollExitRuntime:
    state = "registered"
    healthy = True

    def __init__(self) -> None:
        self.authorization: GroupRollExitAuthorizationV1 | None = None
        self.calls: list[str] = []
        self.submit_count = 0

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
        authorization = self.authorization
        assert authorization is not None
        assert account_ref == authorization.source.account_ref
        assert portfolio_ref == authorization.request.portfolio_ref
        self.calls.append(operation)
        if operation == "orders.prepare_group_roll_exit":
            assert mount == "upstream.group_roll_exit_order_query"
            assert scope_ref == "roll-exit-query-dim05"
            assert arguments == authorization.request.to_payload()
            return {
                "ok": True,
                "code": "OK",
                "result": {
                    "phase": GroupRollExitPhase.PREPARED.value,
                    "authorization": authorization.to_payload(),
                    "observation": None,
                },
            }
        if operation == "orders.submit_group_roll_exit":
            assert mount == "upstream.group_roll_exit_orders"
            assert scope_ref == "roll-exit-order-dim05"
            assert arguments == {"authorization": authorization.to_payload()}
            self.submit_count += 1
            raise RuntimeError("synthetic lost submit response after dispatch")
        if operation == "orders.query_group_roll_exit":
            assert mount == "upstream.group_roll_exit_order_query"
            assert scope_ref == "roll-exit-query-dim05"
            assert arguments == {
                "action_id": authorization.action_id,
                "authorization_sha256": authorization.authorization_sha256,
            }
            observation = GroupRollExitObservationV1(
                action_id=authorization.action_id,
                phase=GroupRollExitPhase.UNKNOWN,
                source_digest=authorization.source.digest,
                request_fingerprint=authorization.request.fingerprint,
                authorization_sha256=authorization.authorization_sha256,
                intent_id=authorization.intent.intent_id,
                close_state=ExecutionState.UNKNOWN,
                requested_quantity=authorization.intent.quantity,
                filled_quantity=Decimal("0"),
                order_revision=None,
                close_receipt_sha256=None,
                detail_code="RISK_OUTCOME_UNKNOWN",
                risk_intent_id=authorization.risk_intent_id,
            )
            return {
                "ok": True,
                "code": "OK",
                "result": {
                    "phase": observation.phase.value,
                    "observation": observation.to_payload(),
                },
            }
        raise AssertionError(f"unapproved Risk operation: {operation}")


def _risk_authorization(source: Any, request: GroupRollExitRequestV1) -> GroupRollExitAuthorizationV1:
    action_id = group_roll_exit_action_id(source.digest, request.fingerprint)
    from bt_api_execution import OrderIntent, PositionEffect, Side, TimeInForce

    intent = OrderIntent.limit(
        intent_id=action_id,
        scope=source.source_execution_scope,
        signal_id=action_id,
        instrument=source.instrument,
        side=Side.SELL if source.source_side.value == "BUY" else Side.BUY,
        quantity=source.remaining_quantity,
        price=request.limit_price,
        position_effect=PositionEffect.CLOSE,
        time_in_force=TimeInForce.GTC,
        reduce_only=True,
        metadata_version=source.close_metadata_version,
        deadline_ns=source.close_metadata_valid_until_ns - 1,
        position_side=source.source_position_side,
        position_bucket=source.source_position_bucket,
    )
    return GroupRollExitAuthorizationV1.build(
        request=request,
        source=source,
        intent=intent,
        deadline_ns=intent.deadline_ns,
        risk_intent_id=f"risk-{action_id[-24:]}",
    )


def test_unknown_roll_exit_replay_after_service_reconstruction_queries_without_second_submit(
    tmp_path: Path,
) -> None:
    (
        source,
        request,
        _template_authorization,
        service,
        _template_runtime,
        group_store,
        run_bindings,
        write_registration,
        query_registration,
        _foreign_registration,
        now_ns,
    ) = _service_fixture(tmp_path, accept_server_quote=True)
    runtime = _RiskRollExitRuntime()
    account = source.source_execution_scope.account_ref
    reservations = _ReservationPort()
    orders = _OrderPort(reservations)
    try:
        # Drive the accepted v2 group through the actual Execution fold into
        # PAIRED; the roll-exit source below comes from persisted fills/claims.
        source_coordinator = GroupOrderCoordinator(
            group_store,
            reservations={account: reservations},
            orders={account: orders},
            clock_ns=lambda: now_ns + 1,
        )
        paired = source_coordinator.advance_group(source.portfolio_ref, source.group_id)
        assert paired.state is GroupOrderState.PAIRED

        source_plan = paired.request.leg(source.source_leg_id)
        progress = next(item for item in paired.legs if item.leg_id == source.source_leg_id)
        reservation = next(item for item in paired.reservations if item.account_ref == account)
        risk_leg = next(item for item in reservation.legs if item.leg_id == source.source_leg_id)
        assert progress.submitted_intent is not None
        assert progress.child_status is not None
        assert progress.claim_receipt is not None
        assert progress.resolution_receipt is not None
        source = replace(
            source,
            origin_run_id=write_registration.context.scope.run_id,
            group_version=paired.request.version,
            group_revision=paired.revision,
            group_fingerprint=paired.request.fingerprint,
            source_risk_reservation_fingerprint=reservation.group_fingerprint,
            source_risk_intent_fingerprint=risk_leg.actual_intent_hash,
            group_request=paired.request,
            source_intent_id=progress.submitted_intent.intent_id,
            source_request_fingerprint=progress.submitted_intent.fingerprint,
            source_child_intent=progress.submitted_intent,
            source_execution_scope=progress.submitted_intent.scope,
            source_terminal_status=progress.child_status,
            source_requested_quantity=source_plan.maximum_intent.quantity,
            source_claim=progress.claim_receipt,
            source_risk_resolution=progress.resolution_receipt,
            # The real source digest comes from the source journal. This unit
            # has no source journal, so label the local fixture digest as
            # synthetic while binding it to the actual persisted group chain.
            source_event_digest=payload_sha256(
                {
                    "schema": "synthetic-source-journal-v1",
                    "group_event_chain_sha256": group_event_chain_sha256(
                        paired, group_store.events(source.portfolio_ref, source.group_id)
                    ),
                }
            ),
            source_side=progress.submitted_intent.side,
            instrument=progress.submitted_intent.instrument,
            source_metadata_version=progress.submitted_intent.metadata_version,
            source_position_side=progress.submitted_intent.position_side,
            source_position_bucket=progress.submitted_intent.position_bucket,
            source_filled_quantity=progress.child_status.filled_quantity,
            remaining_quantity=progress.child_status.filled_quantity,
            service_write_run_id=write_registration.context.scope.run_id,
        )
        request = GroupRollExitRequestV1(
            portfolio_ref=paired.request.portfolio_ref,
            group_id=paired.request.group_id,
            source_leg_id=source.source_leg_id,
            limit_price=request.limit_price,
        )
        authorization = _risk_authorization(source, request)
        runtime.authorization = authorization
        service.close()
        service = GroupCoordinatorService(
            group_store,
            service._targets,
            run_bindings,
            BorrowedServiceClient(runtime),
            WireIdentity(WireRole.SERVICE, "group-coordinator", "coordinator-actor"),
            instance_id="group-coordinator-instance",
            clock_ns=lambda: now_ns + 2,
        )
        service.maintain_registrations(
            (write_registration, query_registration), recover=False
        )
        arguments = {
            "group_id": request.group_id,
            "source_leg_id": request.source_leg_id,
            "limit_price": format(request.limit_price, "f"),
        }

        first = service.handle(
            "groups.roll_exit", context=write_registration.context, arguments=arguments
        )
        assert first["ok"] is True, (first, runtime.calls)
        assert first["result"]["action"]["phase"] == "UNKNOWN"
        assert runtime.submit_count == 1
        assert (
            group_store.get(request.portfolio_ref, request.group_id).state
            is GroupOrderState.RECOVERY_REQUIRED
        )

        # Reconstructing the service object uses the same durable group/action
        # stores. Its write replay must query the original Risk action and
        # never submit again while the outcome remains UNKNOWN; this unit does
        # not claim a process restart.
        service.close()
        service = GroupCoordinatorService(
            group_store,
            service._targets,
            run_bindings,
            BorrowedServiceClient(runtime),
            WireIdentity(WireRole.SERVICE, "group-coordinator", "coordinator-actor"),
            instance_id="group-coordinator-instance",
            clock_ns=lambda: now_ns + 3,
        )
        service.maintain_registrations(
            (write_registration, query_registration), recover=False
        )
        replay = service.handle(
            "groups.roll_exit", context=write_registration.context, arguments=arguments
        )
        assert replay["ok"] is True, replay
        assert replay["result"]["action"]["phase"] == "UNKNOWN"
        assert runtime.submit_count == 1
        assert runtime.calls.count("orders.prepare_group_roll_exit") == 1
        assert runtime.calls.count("orders.submit_group_roll_exit") == 1
        assert runtime.calls.count("orders.query_group_roll_exit") == 1

        other_leg = service.handle(
            "groups.roll_exit",
            context=write_registration.context,
            arguments={
                "group_id": request.group_id,
                "source_leg_id": "next-contract",
                "limit_price": format(request.limit_price, "f"),
            },
        )
        assert other_leg == {
            "ok": False,
            "code": "GROUP_ROLL_EXIT_RECOVERY_REQUIRED",
            "result": {},
        }
        assert runtime.submit_count == 1
        assert runtime.calls.count("orders.prepare_group_roll_exit") == 1
        assert runtime.calls.count("orders.submit_group_roll_exit") == 1
        assert runtime.calls.count("orders.query_group_roll_exit") == 1

        public_group = service.handle(
            "groups.query",
            context=query_registration.context,
            arguments={"group_id": request.group_id},
        )
        assert public_group["ok"] is True
        snapshot = public_group["result"]["snapshot"]
        assert snapshot["state"] == GroupOrderState.RECOVERY_REQUIRED.value
        assert any(
            event.event_type == "GROUP_ROLL_EXIT_SUBMIT_STARTED"
            for event in group_store.events(request.portfolio_ref, request.group_id)
        )
        assert not any(
            event.event_type == "GROUP_ROLL_EXIT_RESOLVED"
            for event in group_store.events(request.portfolio_ref, request.group_id)
        )
    finally:
        service.close()
        group_store.close()
        run_bindings.close()
