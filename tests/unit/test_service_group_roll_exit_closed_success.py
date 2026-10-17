"""Source-only positive coverage for the DIM05 durable group-close path."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_risk import AccountScope
from bt_api_execution import (
    ExecutionState,
    GroupOrderCoordinator,
    GroupOrderState,
    group_event_chain_sha256,
    payload_sha256,
)
from bt_api_execution.group_roll_exit_contracts import GroupRollExitClosedProofV1

from bt_api_py.service_components import BorrowedServiceClient
from bt_api_py.service_execution_group import GroupCoordinatorService
from bt_api_py.service_group_roll_exit import (
    GroupRollExitAuthorizationV1,
    GroupRollExitCloseFeeEvidenceV1,
    GroupRollExitCloseFeeFillV1,
    GroupRollExitObservationV1,
    GroupRollExitPhase,
    GroupRollExitRequestV1,
    GroupRollExitSourceFactsV1,
)
from tests.unit.test_service_execution_group_roll_exit import _service_fixture
from tests.unit.test_service_group_roll_exit_closed_state import (
    _OrderPort,
    _ReservationPort,
    _risk_authorization,
)


_ACCOUNT = "account-a"


def _source_for_leg(
    template: GroupRollExitSourceFactsV1,
    snapshot: Any,
    leg_id: str,
    *,
    write_registration: Any,
    metadata_by_instrument: dict[str, Any],
) -> GroupRollExitSourceFactsV1:
    plans = tuple(plan for plan in snapshot.request.legs if plan.leg_id == leg_id)
    progress_rows = tuple(progress for progress in snapshot.legs if progress.leg_id == leg_id)
    reservations = tuple(
        item for item in snapshot.reservations if item.account_ref == _ACCOUNT
    )
    if len(plans) != 1 or len(progress_rows) != 1 or len(reservations) != 1:
        raise AssertionError("accepted group projection is not uniquely keyed")
    plan = plans[0]
    progress = progress_rows[0]
    reservation = reservations[0]
    risk_legs = tuple(item for item in reservation.legs if item.leg_id == leg_id)
    if len(risk_legs) != 1:
        raise AssertionError("Risk reservation leg is not uniquely keyed")
    child = progress.submitted_intent
    status = progress.child_status
    claim = progress.claim_receipt
    resolution = progress.resolution_receipt
    if child is None or status is None or claim is None or resolution is None:
        raise AssertionError("paired source leg is missing terminal proof")
    metadata = metadata_by_instrument[child.instrument]
    source_event_digest = payload_sha256(
        {
            "schema": "synthetic-roll-exit-source-event-v1",
            "portfolio_ref": snapshot.request.portfolio_ref,
            "group_id": snapshot.request.group_id,
            "group_fingerprint": snapshot.request.fingerprint,
            "leg_id": leg_id,
            "child_intent": child.to_payload(),
            "terminal_status": status.to_payload(),
            "claim": claim.to_payload(),
            "resolution": resolution.to_payload(),
        }
    )
    return replace(
        template,
        origin_run_id=write_registration.context.scope.run_id,
        group_id=snapshot.request.group_id,
        group_version=snapshot.request.version,
        group_revision=snapshot.revision,
        group_fingerprint=snapshot.request.fingerprint,
        source_risk_reservation_fingerprint=reservation.group_fingerprint,
        source_risk_intent_fingerprint=risk_legs[0].actual_intent_hash,
        group_request=snapshot.request,
        account_ref=plan.account_ref,
        strategy_id=snapshot.request.strategy_id,
        source_leg_id=leg_id,
        source_intent_id=child.intent_id,
        source_request_fingerprint=child.fingerprint,
        source_child_intent=child,
        source_execution_scope=child.scope,
        source_terminal_status=status,
        source_requested_quantity=plan.maximum_intent.quantity,
        source_claim=claim,
        source_risk_resolution=resolution,
        source_event_digest=source_event_digest,
        source_side=child.side,
        instrument=child.instrument,
        source_metadata_version=child.metadata_version,
        close_metadata_version=metadata.metadata_version,
        close_metadata_digest=metadata.digest,
        close_quantity_step=metadata.quantity_step,
        close_tick_size=metadata.tick_size,
        close_metadata_valid_until_ns=metadata.expires_at_ns,
        source_position_side=child.position_side,
        source_position_bucket=child.position_bucket,
        source_filled_quantity=status.filled_quantity,
        prior_resolved_closes=(),
        remaining_quantity=status.filled_quantity,
        closable_position_version=payload_sha256(
            {"schema": "synthetic-closable-position-v1", "leg_id": leg_id}
        ),
        service_write_run_id=write_registration.context.scope.run_id,
    )


class _ClosingRiskRuntime:
    state = "registered"
    healthy = True

    def __init__(
        self,
        *,
        group_store: Any,
        reservations: _ReservationPort,
        template_source: GroupRollExitSourceFactsV1,
        risk_account_scope: AccountScope,
        metadata_by_instrument: dict[str, Any],
        write_registration: Any,
    ) -> None:
        execution_scope = template_source.source_execution_scope
        if (
            type(risk_account_scope) is not AccountScope
            or risk_account_scope.provider.casefold() != execution_scope.provider.casefold()
            or risk_account_scope.environment != execution_scope.environment
            or risk_account_scope.account_id != template_source.account_ref
        ):
            raise AssertionError("synthetic Risk account mapping differs from group fixture")
        self._group_store = group_store
        self._reservations = reservations
        self._template_source = template_source
        self._risk_account_scope = risk_account_scope
        self._metadata_by_instrument = metadata_by_instrument
        self._write_registration = write_registration
        self.authorizations: dict[str, GroupRollExitAuthorizationV1] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _reply(result: object) -> dict[str, Any]:
        return {"ok": True, "code": "OK", "result": result}

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
        self.calls.append((mount, operation))
        assert account_ref == _ACCOUNT
        if operation == "reservations.query":
            assert mount == "upstream.account_reservations"
            assert scope_ref == "reservation-dim05"
            snapshot = self._reservations.query_group_vector(arguments["group_id"])
            return self._reply(snapshot.to_payload())

        if operation == "orders.prepare_group_roll_exit":
            assert mount == "upstream.group_roll_exit_order_query"
            assert scope_ref == "roll-exit-query-dim05"
            request = GroupRollExitRequestV1.from_payload(arguments)
            assert portfolio_ref == request.portfolio_ref
            snapshot = self._group_store.get(request.portfolio_ref, request.group_id)
            assert snapshot is not None
            source = _source_for_leg(
                self._template_source,
                snapshot,
                request.source_leg_id,
                write_registration=self._write_registration,
                metadata_by_instrument=self._metadata_by_instrument,
            )
            authorization = _risk_authorization(source, request)
            self.authorizations[authorization.action_id] = authorization
            return self._reply(
                {
                    "phase": GroupRollExitPhase.PREPARED.value,
                    "authorization": authorization.to_payload(),
                    "observation": None,
                }
            )

        if operation == "orders.submit_group_roll_exit":
            assert mount == "upstream.group_roll_exit_orders"
            assert scope_ref == "roll-exit-order-dim05"
            assert set(arguments) == {"authorization"}
            authorization = GroupRollExitAuthorizationV1.from_payload(
                arguments["authorization"]
            )
            assert self.authorizations[authorization.action_id] == authorization
            receipt = self._close_receipt(authorization, self._risk_account_scope)
            fee = self._close_fee(authorization, receipt)
            observation = GroupRollExitObservationV1(
                action_id=authorization.action_id,
                phase=GroupRollExitPhase.RESOLVED,
                source_digest=authorization.source.digest,
                request_fingerprint=authorization.request.fingerprint,
                authorization_sha256=authorization.authorization_sha256,
                intent_id=authorization.intent.intent_id,
                close_state=ExecutionState.FILLED,
                requested_quantity=authorization.intent.quantity,
                filled_quantity=authorization.intent.quantity,
                order_revision=receipt["order_revision"],
                close_receipt_sha256=receipt["receipt_sha256"],
                detail_code=None,
                risk_intent_id=authorization.risk_intent_id,
                close_receipt=receipt,
                close_fee_evidence=fee,
            )
            self.receipts[authorization.action_id] = receipt
            return self._reply(
                {
                    "phase": observation.phase.value,
                    "observation": observation.to_payload(),
                }
            )

        raise AssertionError(f"unexpected Risk operation {operation}")

    @staticmethod
    def _close_receipt(
        authorization: GroupRollExitAuthorizationV1,
        risk_account_scope: AccountScope,
    ) -> dict[str, Any]:
        from bt_api_execution.group_roll_exit_contracts import (
            GroupRollExitRiskCloseClaimV1,
            GroupRollExitRiskCloseReceiptV1,
        )

        risk_intent_id = authorization.risk_intent_id
        assert risk_intent_id is not None
        claim = GroupRollExitRiskCloseClaimV1(
            cause_id=f"dispatch-inflight:{risk_intent_id}",
            claim_digest=payload_sha256({"claim": risk_intent_id}),
            claimed_at=1.0,
            intent_hash=payload_sha256({"risk-command": risk_intent_id}),
            intent_id=risk_intent_id,
            permit_id=f"close-permit-{risk_intent_id[-12:]}",
            scope_key=risk_account_scope.key,
        )
        return GroupRollExitRiskCloseReceiptV1(
            claim=claim,
            proof_sha256=payload_sha256({"close-proof": authorization.action_id}),
            exposure_proof_sha256=payload_sha256(
                {"close-exposure": authorization.action_id}
            ),
            requested_quantity=authorization.intent.quantity,
            filled_quantity=authorization.intent.quantity,
            trade_count=1,
            order_revision=1,
            journal_revision=2,
            exposure_reservation_id=f"close-exposure-{authorization.action_id[-12:]}",
            exposure_reservation_sha256=payload_sha256(
                {"reservation": authorization.action_id}
            ),
        ).to_payload()

    @staticmethod
    def _close_fee(
        authorization: GroupRollExitAuthorizationV1,
        receipt: dict[str, Any],
    ) -> GroupRollExitCloseFeeEvidenceV1:
        fee = Decimal("0.01")
        fill_id = f"fill-{authorization.action_id[-16:]}"
        fill = GroupRollExitCloseFeeFillV1(
            fill_id=fill_id,
            fill_fingerprint=payload_sha256({"fill": fill_id}),
            source_row_sha256=payload_sha256({"row": fill_id}),
            amount=fee,
            currency="USD",
        )
        return GroupRollExitCloseFeeEvidenceV1(
            intent_id=authorization.action_id,
            journal_revision=receipt["journal_revision"],
            fill_observation_sha256=payload_sha256(
                {"fill-observation": authorization.risk_intent_id}
            ),
            fee_currency="USD",
            fee_amount=fee,
            fills=(fill,),
        )


@pytest.mark.parametrize("transient_close_failure", (False, True))
def test_two_leg_roll_exit_closes_paired_group_with_fee_backed_proof_and_replay(
    tmp_path: Path,
    monkeypatch: Any,
    transient_close_failure: bool,
) -> None:
    (
        template_source,
        first_request,
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
    # The fixed roll-exit fixture grants only roll-exit reads. This success
    # case also checks the aggregate group snapshot, so its local registration
    # must explicitly grant the separate groups.query operation.
    query_registration = replace(
        query_registration,
        operations=frozenset({"groups.query", "groups.query_roll_exit"}),
    )
    reservations = _ReservationPort()
    orders = _OrderPort(reservations)
    runtime: _ClosingRiskRuntime | None = None
    close_attempts = 0
    try:
        account = template_source.source_execution_scope.account_ref
        source_coordinator = GroupOrderCoordinator(
            group_store,
            reservations={account: reservations},
            orders={account: orders},
            clock_ns=lambda: now_ns + 1,
        )
        paired = source_coordinator.advance_group(
            template_source.portfolio_ref, template_source.group_id
        )
        assert paired.state is GroupOrderState.PAIRED
        assert paired.request.version == 2
        assert paired.budget_reservation is not None

        target_scope = service._target_scope_for(write_registration.context.scope)
        assert target_scope is not None
        route = service._targets[target_scope].accounts[account]
        metadata_by_instrument = {
            name: rule.metadata for name, rule in route.instruments.items()
        }
        risk_account_scope = AccountScope(
            provider=template_source.source_execution_scope.provider,
            account_id=account,
            environment=template_source.source_execution_scope.environment,
        )
        assert risk_account_scope.key == (
            '{"account_id":"account-a","environment":"paper","provider":"simnow"}'
        )
        assert risk_account_scope.key != template_source.source_execution_scope.key
        runtime = _ClosingRiskRuntime(
            group_store=group_store,
            reservations=reservations,
            template_source=template_source,
            risk_account_scope=risk_account_scope,
            metadata_by_instrument=metadata_by_instrument,
            write_registration=write_registration,
        )
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

        close_roll_exit = group_store.close_roll_exit

        def fail_first_close(
            snapshot: Any,
            proof: GroupRollExitClosedProofV1,
            *,
            now_ns: int | None = None,
        ) -> Any:
            nonlocal close_attempts
            close_attempts += 1
            if transient_close_failure and close_attempts == 1:
                raise RuntimeError("synthetic transient close persistence failure")
            return close_roll_exit(snapshot, proof, now_ns=now_ns)

        monkeypatch.setattr(group_store, "close_roll_exit", fail_first_close)

        calls_before_close = len(runtime.calls)
        first_arguments = {
            "group_id": paired.request.group_id,
            "source_leg_id": first_request.source_leg_id,
            "limit_price": format(first_request.limit_price, "f"),
        }
        first = service.handle(
            "groups.roll_exit",
            context=write_registration.context,
            arguments=first_arguments,
        )
        assert first["ok"] is True, first
        assert first["result"]["action"]["phase"] == GroupRollExitPhase.RESOLVED.value
        assert len(runtime.calls) > calls_before_close

        second_leg_id = next(
            plan.leg_id
            for plan in paired.request.legs
            if plan.leg_id != first_request.source_leg_id
        )
        second = service.handle(
            "groups.roll_exit",
            context=write_registration.context,
            arguments={
                "group_id": paired.request.group_id,
                "source_leg_id": second_leg_id,
                "limit_price": format(first_request.limit_price, "f"),
            },
        )
        assert second["ok"] is True, second
        assert second["result"]["action"]["phase"] == GroupRollExitPhase.RESOLVED.value

        if transient_close_failure:
            after_transient_close_failure = group_store.get(
                paired.request.portfolio_ref, paired.request.group_id
            )
            assert after_transient_close_failure is not None
            assert after_transient_close_failure.state is GroupOrderState.UNWINDING
            assert close_attempts == 1
            assert not any(
                event.event_type == "GROUP_ROLL_EXIT_CLOSED"
                for event in group_store.events(
                    paired.request.portfolio_ref, paired.request.group_id
                )
            )

            submits_before_recovery = sum(
                operation == "orders.submit_group_roll_exit"
                for _mount, operation in runtime.calls
            )
            risk_calls_before_recovery = len(runtime.calls)
            recovered = service.handle(
                "groups.roll_exit",
                context=write_registration.context,
                arguments=first_arguments,
            )
            assert recovered["ok"] is True, recovered
            assert (
                recovered["result"]["action"]["phase"]
                == GroupRollExitPhase.RESOLVED.value
            )
            assert close_attempts == 2
            assert sum(
                operation == "orders.submit_group_roll_exit"
                for _mount, operation in runtime.calls
            ) == submits_before_recovery
            assert runtime.calls[risk_calls_before_recovery:] == [
                ("upstream.account_reservations", "reservations.query")
            ]
        else:
            assert close_attempts == 1

        closed = group_store.get(paired.request.portfolio_ref, paired.request.group_id)
        assert closed is not None
        assert closed.state is GroupOrderState.CLOSED
        assert closed.budget_reservation is not None
        assert closed.budget_reservation.state.value == "RELEASED"
        assert len(closed.reservations) == 1
        assert closed.reservations[0].phase.value == "RELEASED"
        assert all(
            leg.child_status is not None
            and leg.child_status.cumulative_commission is not None
            and leg.child_status.cumulative_commission_currency == "USD"
            for leg in closed.legs
        )

        history = group_store.events(paired.request.portfolio_ref, paired.request.group_id)
        close_events = [
            event for event in history if event.event_type == "GROUP_ROLL_EXIT_CLOSED"
        ]
        assert len(close_events) == 1
        proof = GroupRollExitClosedProofV1.from_payload(close_events[0].payload["proof"])
        assert proof.expected_revision == closed.revision - 1
        assert proof.group_fingerprint == paired.request.fingerprint
        assert tuple(item.leg_id for item in proof.legs) == tuple(
            plan.leg_id for plan in paired.request.legs
        )
        assert len(proof.reservation_sha256s) == len(paired.request.account_refs)
        assert proof.budget_release is not None
        assert all(len(item.close_actions) == 1 for item in proof.legs)
        for leg in proof.legs:
            assert leg.source_fee.amount == Decimal("0.01")
            assert leg.source_fee.currency == "USD"
            action = leg.close_actions[0]
            assert action.action_id != action.risk_intent_id
            assert runtime.receipts[action.action_id]["receipt_sha256"] == action.receipt_sha256
            authorization = runtime.authorizations[action.action_id]
            assert authorization.risk_intent_id == action.risk_intent_id
            assert action.close_quantity == leg.source_filled_quantity
            assert action.close_fee_amount == Decimal("0.01")
            assert action.close_fee_currency == "USD"

        release_events = [
            event for event in history if event.event_type == "RESERVATION_RELEASED"
        ]
        assert len(release_events) == 1
        assert release_events[0].payload == {
            "account_ref": account,
            "roll_exit_settled": True,
            "budget_state": "HELD",
        }
        preclose_snapshot = replace(closed, revision=proof.expected_revision)
        assert group_event_chain_sha256(
            preclose_snapshot, history[:-1]
        ) == proof.prior_group_event_chain_sha256

        readback = service.handle(
            "groups.query",
            context=query_registration.context,
            arguments={"group_id": paired.request.group_id},
        )
        assert readback["ok"] is True, readback
        assert readback["result"]["snapshot"]["state"] == GroupOrderState.CLOSED.value
        action_readback = service.handle(
            "groups.query_roll_exit",
            context=query_registration.context,
            arguments={"action_id": first["result"]["action"]["action_id"]},
        )
        assert action_readback["ok"] is True
        assert action_readback["result"]["action"]["phase"] == GroupRollExitPhase.RESOLVED.value

        calls_before_replay = tuple(runtime.calls)
        replay = service.handle(
            "groups.roll_exit",
            context=write_registration.context,
            arguments=first_arguments,
        )
        assert replay["ok"] is True, replay
        assert replay["result"]["action"]["phase"] == GroupRollExitPhase.RESOLVED.value
        assert tuple(runtime.calls) == calls_before_replay
        assert close_attempts == (2 if transient_close_failure else 1)
    finally:
        service.close()
        group_store.close()
        run_bindings.close()
