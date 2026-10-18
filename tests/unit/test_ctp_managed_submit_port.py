from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from threading import Thread
from types import SimpleNamespace

import pytest
import test_ctp_execution_owner as ctp_owner_fixtures

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution import (
    ContractValidationError,
    ExecutionAdmissionValidatorRegistry,
    ExecutionBasicAdmissionGate,
    ExecutionProviderValidatorV1,
    ExecutionState,
    ManagedExecutionFacade,
    OrderType,
    TimeInForce,
    WriterLeaseUnavailable,
)
from bt_api_execution.basic_admission import ExecutionAdmissionContextV1
from bt_api_execution.ctp_single_worker_candidate import stable_managed_command_id
from bt_api_py.ctp_admission_authority import CtpNativeDispatchBindingV1
from bt_api_py.ctp_managed_submit_port import CtpManagedSubmitPort


_PRINCIPAL = "ctp-managed-submit-strategy"
_DOMAIN = "ctp-managed-submit-domain"


def _day_intent(case, suffix: str):
    return replace(
        case.intent,
        intent_id=f"ctp-managed-submit-{suffix}",
        signal_id=f"ctp-managed-submit-{suffix}-signal",
        time_in_force=TimeInForce.DAY,
    )


def _admission_context(scope):
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, _PRINCIPAL, scope.strategy_id)
    wire_scope = WireScope(
        _DOMAIN,
        account_ref=scope.account_ref,
        strategy_ref=scope.strategy_id,
    )
    return ExecutionAdmissionContextV1(
        principal_ref=_PRINCIPAL,
        identity=identity,
        wire_scope=wire_scope,
        execution_scope=scope,
        action="orders.submit",
    )


def _compose(case, intent):
    # The port object is registered before the gate is created, and the same
    # object is passed through Facade -> BasicAdmission -> CTP authority.
    port = CtpManagedSubmitPort(
        case.store,
        case.scope,
        case.writer_lease,
        case.trader,
        case.session_adapter,
        case.reconciliation_port,
        CtpNativeDispatchBindingV1(
            account_ref=case.scope.account_ref,
            scope_key=case.scope.key,
            investor_id="investor-1",
            intent_instrument=intent.instrument,
            provider_instrument=intent.instrument,
            exchange_id="SHFE",
        ),
        session_generation_id=case.session_binding.session_generation_id,
    )
    validator = ExecutionProviderValidatorV1(
        provider_id="ctp",
        validator_id="ctp-managed-submit-port-test-v1",
        version="v1",
        dispatch_port=port,
        instrument_map={intent.instrument: intent.instrument},
        quantity_steps={intent.instrument: Decimal("1")},
        max_quantities={intent.instrument: Decimal("100")},
        price_ticks={intent.instrument: Decimal("0.01")},
        supported_order_types=(OrderType.LIMIT,),
        supported_time_in_force=(TimeInForce.DAY,),
        supported_actions=((intent.side, intent.position_effect),),
        supports_reduce_only=intent.reduce_only,
    )
    gate = ExecutionBasicAdmissionGate(
        case.store,
        ExecutionAdmissionValidatorRegistry((validator,)),
        retain_claimed_authorizations=True,
    )
    port.bind_basic_admission(gate)
    facade = ManagedExecutionFacade(
        case.store,
        case.scope,
        writer_id=case.writer_lease.owner_id,
        basic_admission_gate=gate,
    )
    return port, gate, facade, _admission_context(case.scope)


def _submit_calls(case):
    return [call for call in case.api.calls if call[0] == "ReqOrderInsert"]


def _emit_order(case, intent, reservation, request_id, *, status, traded, sequence):
    case.trader._spi.OnRtnOrder(
        SimpleNamespace(
            BrokerID="9999",
            InvestorID="investor-1",
            UserID="investor-1",
            InstrumentID=intent.instrument,
            RequestID=request_id,
            OrderRef=reservation.order_ref,
            ExchangeID="SHFE",
            OrderSysID="SYS" + reservation.order_ref,
            FrontID=case.session_binding.dispatch_front_id,
            SessionID=case.session_binding.dispatch_session_id,
            TradingDay=case.scope.trading_day,
            OrderStatus=status,
            OrderSubmitStatus="3",
            VolumeTraded=traded,
            VolumeTotal=int(intent.quantity) - traded,
            VolumeTotalOriginal=int(intent.quantity),
            NotifySequence=sequence,
            SequenceNo=sequence,
            Direction="0",
            CombOffsetFlag="0",
            CombHedgeFlag="1",
            LimitPrice=float(intent.price),
            TimeCondition="3",
            VolumeCondition="1",
        )
    )


def _emit_trade(case, intent, reservation, *, sequence: int):
    case.trader._spi.OnRtnTrade(
        SimpleNamespace(
            BrokerID="9999",
            InvestorID="investor-1",
            UserID="investor-1",
            InstrumentID=intent.instrument,
            OrderRef=reservation.order_ref,
            ExchangeID="SHFE",
            TradeID=f"MSPTRD{sequence:02d}",
            OrderSysID="SYS" + reservation.order_ref,
            TradingDay=case.scope.trading_day,
            Direction="0",
            OffsetFlag="0",
            HedgeFlag="1",
            Price=float(intent.price),
            Volume=int(intent.quantity),
            TradeDate=case.scope.trading_day,
            TradeTime="09:31:00",
            SequenceNo=sequence,
            BrokerOrderSeq=1,
            TradeSource="0",
        )
    )


@pytest.mark.unit
def test_managed_facade_dispatches_once_then_reconciles_real_ctp_callbacks(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        intent = _day_intent(case, "callback-chain")
        port, _gate, facade, context = _compose(case, intent)

        renewed = facade.acquire_writer_lease()
        assert renewed.owner_id == case.writer_lease.owner_id
        assert renewed.fencing_token == case.writer_lease.fencing_token

        # This fresh, still-unadmitted intent reuses an existing typed Store
        # reservation created under the established session cutover. Its
        # runtime ID intentionally differs from the port's new-ID algorithm.
        pre_reserved = case.store.reserve_ctp_order_identity(
            case.scope, intent.intent_id, "bt-managed-v1:" + "a" * 64
        )
        assert pre_reserved.managed_intent_id == intent.intent_id
        assert pre_reserved.runtime_order_id == "bt-managed-v1:" + "a" * 64
        assert case.store.read_ctp_order_identity(case.scope, intent.intent_id) == pre_reserved

        # The normal facade path admits and claims the fresh intent. The local
        # CTP queue receipt proves only that ReqOrderInsert was queued; without
        # a callback the provider-neutral execution record remains UNKNOWN.
        first = facade.submit(intent, port, admission_context=context)
        assert first.state is ExecutionState.UNKNOWN
        assert first.review_required is True
        assert first.dispatch_attempts == 1
        assert port._queue.unfinished_tasks == 0
        reservation = case.store.read_ctp_order_identity(case.scope, intent.intent_id)
        assert reservation == pre_reserved
        inserts = _submit_calls(case)
        assert len(inserts) == 1
        request_id = inserts[0][2]
        assert inserts[0][1].OrderRef == reservation.order_ref

        command_id = stable_managed_command_id(
            "submit", intent.intent_id, reservation.runtime_order_id
        )
        command = case.store.read_ctp_dispatch_command(case.scope, command_id)
        assert command is not None
        assert command.status == "COMPLETED"
        assert command.local_queue_receipt_queued is True

        replay = facade.submit(intent, port, admission_context=context)
        assert replay.state is ExecutionState.UNKNOWN
        assert replay.dispatch_attempts == 1
        assert len(_submit_calls(case)) == 1

        # A bounded local query with no callback does not change UNKNOWN or
        # produce another native request.
        no_evidence = facade.reconcile_with_port(intent.intent_id, port)
        assert no_evidence.state is ExecutionState.UNKNOWN
        assert no_evidence.review_required is True
        assert len(_submit_calls(case)) == 1

        # The borrowed CTP owner is thread-confined. A query from another
        # thread fails closed before it can dispatch or allocate a request ID.
        thread_errors = []

        def wrong_thread_query():
            try:
                port.query(intent)
            except Exception as error:  # captured for assertion on the owner thread
                thread_errors.append(error)

        worker = Thread(target=wrong_thread_query)
        worker.start()
        worker.join(timeout=1.0)
        assert not worker.is_alive()
        assert len(thread_errors) == 1
        assert isinstance(thread_errors[0], ContractValidationError)
        assert len(_submit_calls(case)) == 1

        foreign_scope = replace(case.scope, trading_day="20260927")
        foreign_intent = replace(
            intent,
            intent_id="ctp-managed-submit-foreign-scope",
            signal_id="ctp-managed-submit-foreign-scope-signal",
            scope=foreign_scope,
        )
        with pytest.raises(ContractValidationError):
            facade.submit(
                foreign_intent,
                port,
                admission_context=_admission_context(foreign_scope),
            )
        assert case.store.get(foreign_intent.intent_id, scope=case.scope) is None

        other_writer = ManagedExecutionFacade(
            case.store,
            case.scope,
            writer_id="different-ctp-managed-writer",
            basic_admission_gate=_gate,
        )
        with pytest.raises(WriterLeaseUnavailable):
            other_writer.acquire_writer_lease()
        still_owned = facade.acquire_writer_lease()
        assert still_owned.fencing_token == case.writer_lease.fencing_token
        assert len(_submit_calls(case)) == 1

        # Actual TraderClient SPI callbacks are persisted through ingress. The
        # port's bounded query pumps them and returns only the durable snapshot
        # projection for Facade reconciliation.
        _emit_order(
            case, intent, reservation, request_id, status="3", traded=0, sequence=1
        )
        acknowledged = facade.reconcile_with_port(intent.intent_id, port)
        assert acknowledged.state is ExecutionState.ACKED
        assert acknowledged.filled_quantity == Decimal("0")

        _emit_trade(case, intent, reservation, sequence=2)
        _emit_order(
            case, intent, reservation, request_id, status="0", traded=1, sequence=3
        )
        filled = facade.reconcile_with_port(intent.intent_id, port)
        assert filled.state is ExecutionState.FILLED
        assert filled.filled_quantity == Decimal("1")
        assert filled.average_price == Decimal("100")
        assert filled.review_required is False
        assert len(_submit_calls(case)) == 1
        assert port._queue.unfinished_tasks == 0


@pytest.mark.unit
def test_copied_authorization_is_rejected_before_orderref_or_request_id_allocation(
    tmp_path, monkeypatch
):
    with ctp_owner_fixtures._active_case(tmp_path, monkeypatch) as case:
        copied_intent = _day_intent(case, "copied-auth")
        port, _gate, facade, context = _compose(case, copied_intent)

        # Observe, then alter only the Gate's exact claimed authorization at
        # the registered port boundary. The real port still performs all
        # validation and must reject the copied object before either durable
        # OrderRef reservation or TraderClient request-ID allocation.
        request_ids = []
        next_request_id = case.trader._next_request_id

        def observed_next_request_id():
            value = next_request_id()
            request_ids.append(value)
            return value

        monkeypatch.setattr(case.trader, "_next_request_id", observed_next_request_id)
        port_type = type(port)
        original_submit = port_type.submit_authorized

        def copied_authorization_submit(self, intent_arg, authorization):
            if intent_arg.intent_id == copied_intent.intent_id:
                authorization = replace(authorization)
            return original_submit(self, intent_arg, authorization)

        monkeypatch.setattr(port_type, "submit_authorized", copied_authorization_submit)

        rejected = facade.submit(copied_intent, port, admission_context=context)
        assert rejected.state is ExecutionState.UNKNOWN
        assert case.store.read_ctp_order_identity(case.scope, copied_intent.intent_id) is None
        assert request_ids == []
        assert _submit_calls(case) == []
        assert port._queue.unfinished_tasks == 0

        # The unknown dispatch is never retried. Two independent valid intents
        # then prove the shared TraderClient request sequence advances once
        # per actual order and each gets its own durable OrderRef.
        replay = facade.submit(copied_intent, port, admission_context=context)
        assert replay.state is ExecutionState.UNKNOWN
        assert request_ids == []
        assert _submit_calls(case) == []

        first_intent = _day_intent(case, "sequence-one")
        second_intent = _day_intent(case, "sequence-two")
        first = facade.submit(first_intent, port, admission_context=context)
        second = facade.submit(second_intent, port, admission_context=context)
        assert first.state is ExecutionState.UNKNOWN
        assert second.state is ExecutionState.UNKNOWN
        assert len(request_ids) == 2
        assert request_ids[1] > request_ids[0]

        first_reservation = case.store.read_ctp_order_identity(case.scope, first_intent.intent_id)
        second_reservation = case.store.read_ctp_order_identity(case.scope, second_intent.intent_id)
        assert first_reservation is not None
        assert second_reservation is not None
        assert first_reservation.order_ref != second_reservation.order_ref
        inserts = _submit_calls(case)
        assert len(inserts) == 2
        assert [call[2] for call in inserts] == request_ids
        assert [call[1].OrderRef for call in inserts] == [
            first_reservation.order_ref,
            second_reservation.order_ref,
        ]
        assert port._queue.unfinished_tasks == 0
