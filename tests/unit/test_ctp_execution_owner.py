from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
import test_ctp_callback_ingress_client as ctp_client_fixtures
import test_ctp_service_execution as execution_fixtures

from bt_api_execution import (
    CtpCallbackSessionStoreAdapter,
    CtpOwnerCallbackObservationNotReadyV1,
    ContractValidationError,
    ExecutionScope,
    ExecutionState,
    SqliteExecutionStore,
)
from bt_api_execution.ctp_single_worker_candidate import (
    CtpManagedPreparedDispatch,
    stable_managed_command_id,
)
from bt_api_ctp.ctp._ctp_base import is_ctp_native_loaded
from bt_api_ctp.ctp.callback_ingress import (
    CtpTraderCallbackIngressAckV2,
    CtpTraderCallbackIngressRecordV2,
)
from bt_api_ctp.ctp.client import TraderClient

from bt_api_py.ctp_execution_owner import CtpExecutionOwner


def _scope() -> ExecutionScope:
    base = execution_fixtures._scope()
    return ExecutionScope(
        provider=base.provider,
        environment=base.environment,
        account_ref=base.account_ref,
        strategy_id=base.strategy_id,
        trading_day="20260926",
    )


def _session_binding_payload(binding) -> dict[str, object]:
    return {
        "binding_type": "ctp_callback_session_binding.v1",
        "owner_intent_id": binding.owner_intent_id,
        "account_key": binding.account_key,
        "scope_key": binding.scope_key,
        "trading_day": binding.trading_day,
        "session_generation_id": binding.session_generation_id,
        "dispatch_front_id": binding.dispatch_front_id,
        "dispatch_session_id": binding.dispatch_session_id,
        "source_tags": {
            "source_instance_id": binding.source_instance_id,
            "native_client_epoch": binding.native_client_epoch,
            "native_api_source_id": binding.native_api_source_id,
            "native_spi_source_id": binding.native_spi_source_id,
            "native_api_generation": binding.native_api_generation,
            "connection_generation": binding.source_connection_generation,
        },
        "source_high_watermark": binding.source_high_watermark,
    }


def _prepared_submit(case) -> CtpManagedPreparedDispatch:
    intent = case.intent
    reservation = case.reservation
    binding = case.session_binding
    command_id = stable_managed_command_id(
        "submit", intent.intent_id, reservation.runtime_order_id
    )
    local_queue_receipt_id = hashlib.sha256(
        ("queue:" + command_id).encode("ascii")
    ).hexdigest()[:32]
    native_request_id = (
        int.from_bytes(hashlib.sha256(command_id.encode("ascii")).digest()[:4], "big")
        % 2_147_483_646
    ) + 1
    request_payload = ctp_client_fixtures._request_payload(
        InstrumentID=intent.instrument,
        OrderRef=reservation.order_ref,
        VolumeTotalOriginal=int(intent.quantity),
        LimitPrice=float(intent.price),
        ExchangeID="SHFE",
    )
    approval_use_id = "approval-" + command_id
    approval_digest = hashlib.sha256(approval_use_id.encode("ascii")).hexdigest()
    return CtpManagedPreparedDispatch(
        operation="submit",
        command_id=command_id,
        managed_intent_id=intent.intent_id,
        runtime_order_id=reservation.runtime_order_id,
        order_ref=reservation.order_ref,
        request_payload=request_payload,
        order_ref_reservation=reservation,
        approval_use_id=approval_use_id,
        approval_digest=approval_digest,
        session_binding=_session_binding_payload(binding),
        session_generation_id=binding.session_generation_id,
        dispatch_front_id=binding.dispatch_front_id,
        dispatch_session_id=binding.dispatch_session_id,
        native_request_id=native_request_id,
        local_queue_receipt_id=local_queue_receipt_id,
    )


def _queue_receipt(prepared: CtpManagedPreparedDispatch, *, queued: bool = True):
    return {
        "kind": "command_receipt",
        "command": "submit",
        "receipt_id": prepared.local_queue_receipt_id,
        "queued": queued,
    }


@contextmanager
def _active_case(tmp_path, monkeypatch, *, native_order_result: int = 0):
    scope = _scope()
    store = SqliteExecutionStore(tmp_path / "execution-owner.sqlite3")
    trader = None
    try:
        _, writer_lease = execution_fixtures._lease(store, scope)
        owner_handle = store.create_ctp_callback_session_owner(
            scope, writer_lease=writer_lease
        )
        session_adapter = CtpCallbackSessionStoreAdapter(
            store,
            scope,
            owner_handle,
            writer_lease,
            CtpTraderCallbackIngressAckV2,
            CtpTraderCallbackIngressRecordV2,
        )

        factory = ctp_client_fixtures._FakeTraderApiFactory
        factory.instances = []
        factory.block_join = False
        factory.order_insert_result = native_order_result
        monkeypatch.setattr(
            ctp_client_fixtures.client_module,
            "_check_native_module",
            lambda: None,
        )
        monkeypatch.setattr(
            ctp_client_fixtures.client_module,
            "_flow_dir",
            lambda _name: str(tmp_path / "fake-flow"),
        )
        monkeypatch.setattr(
            ctp_client_fixtures.client_module, "_register_ctp_native_api", lambda _api: None
        )
        monkeypatch.setattr(
            ctp_client_fixtures.client_module, "CThostFtdcTraderApi", factory
        )

        assert is_ctp_native_loaded() is False
        trader = TraderClient(
            "tcp://127.0.0.1:1", "9999", "investor-1", "fixture-only"
        )
        trader.install_callback_ingress_sink(
            owner_handle,
            session_adapter.sink,
            session_adapter.bind_session,
            session_adapter.verify_native_call,
        )
        trader.start()
        assert factory.instances
        api = factory.instances[-1]
        api.order_insert_result = native_order_result
        assert api.join_started.wait(1.0)
        assert type(trader._api) is ctp_client_fixtures._FakeTraderApi
        assert api is trader._api
        assert is_ctp_native_loaded() is False

        session_binding = store.read_ctp_callback_session_context_facts(owner_handle)[0]
        assert session_binding.session_generation_id
        intent = execution_fixtures._intent(scope, quantity=Decimal("1"))
        store.admit_intent(intent, writer_lease=writer_lease)
        seed_proof = replace(
            execution_fixtures._order_ref_proof(
                scope, session_binding.session_generation_id
            ),
            native_front_id=session_binding.dispatch_front_id,
            native_session_id=session_binding.dispatch_session_id,
        )
        reservation = store.seed_ctp_order_ref_and_reserve_identity(
            scope,
            seed_proof,
            intent.intent_id,
            execution_fixtures._runtime_order_id(intent.intent_id),
            writer_lease=writer_lease,
        )
        reconciliation_port = execution_fixtures._port(
            store,
            scope,
            writer_lease,
            session_adapter,
            session_binding.session_generation_id,
        )
        owner = CtpExecutionOwner(
            store,
            scope,
            writer_lease,
            execution_fixtures._Authority(),
            trader,
            session_adapter,
            reconciliation_port,
            session_generation_id=session_binding.session_generation_id,
        )
        case = SimpleNamespace(
            scope=scope,
            store=store,
            writer_lease=writer_lease,
            owner_handle=owner_handle,
            session_adapter=session_adapter,
            trader=trader,
            api=api,
            session_binding=session_binding,
            intent=intent,
            reservation=reservation,
            reconciliation_port=reconciliation_port,
            owner=owner,
        )
        case.prepared = _prepared_submit(case)
        yield case
    finally:
        try:
            if trader is not None and getattr(trader, "_api", None) is not None:
                # This only tears down the synthetic API/thread created above.
                receipt = trader.stop_and_wait(timeout=1.0)
                assert receipt.complete, receipt
        finally:
            store.close()


def _order_return(case, *, status: str = "3", traded: int = 0, sequence: int = 1):
    prepared = case.prepared
    reservation = case.reservation
    payload = SimpleNamespace(
        BrokerID="9999",
        InvestorID="investor-1",
        UserID="investor-1",
        InstrumentID=case.intent.instrument,
        RequestID=prepared.native_request_id,
        OrderRef=reservation.order_ref,
        ExchangeID="SHFE",
        OrderSysID="SYS" + reservation.order_ref,
        FrontID=case.session_binding.dispatch_front_id,
        SessionID=case.session_binding.dispatch_session_id,
        TradingDay=case.scope.trading_day,
        OrderStatus=status,
        OrderSubmitStatus="3",
        VolumeTraded=traded,
        VolumeTotal=int(case.intent.quantity) - traded,
        VolumeTotalOriginal=int(case.intent.quantity),
        NotifySequence=sequence,
        SequenceNo=sequence,
        Direction="0",
        CombOffsetFlag="0",
        CombHedgeFlag="1",
        LimitPrice=float(case.intent.price),
        TimeCondition="3",
        VolumeCondition="1",
    )
    case.trader._spi.OnRtnOrder(payload)


@pytest.mark.unit
def test_execution_owner_uses_real_trader_and_callback_for_provider_ack(
    tmp_path, monkeypatch
):
    with _active_case(tmp_path, monkeypatch) as case:
        prepared = case.prepared
        result = case.owner.dispatch_submit(prepared, _queue_receipt(prepared))

        assert result.command.status == "COMPLETED"
        assert result.binding.local_queue_receipt_queued is True
        insert_calls = [call for call in case.api.calls if call[0] == "ReqOrderInsert"]
        assert len(insert_calls) == 1
        _, native_field, native_request_id = insert_calls[0]
        assert native_request_id == prepared.native_request_id
        assert native_field.OrderRef == case.reservation.order_ref

        before = case.store._connection.total_changes
        replay = case.owner.dispatch_submit(prepared, _queue_receipt(prepared))
        assert replay == result
        assert len([call for call in case.api.calls if call[0] == "ReqOrderInsert"]) == 1
        assert case.store._connection.total_changes == before
        local_only = case.owner.read_provider_observation(case.intent, result.binding)
        assert isinstance(local_only, CtpOwnerCallbackObservationNotReadyV1)
        assert local_only.reason_code == "PROVIDER_ORDER_NOT_READY"
        assert case.store._connection.total_changes == before

        _order_return(case)
        applied = case.owner.pump_callbacks()
        assert applied
        observation = case.owner.read_provider_observation(case.intent, result.binding)
        assert observation.state is ExecutionState.ACKED
        assert observation.provider_order_id
        assert observation.filled_quantity == Decimal("0")
        assert observation.average_price is None


@pytest.mark.unit
def test_execution_owner_rejects_wrong_scope_thread_and_cancel_before_staging(
    tmp_path, monkeypatch
):
    with _active_case(tmp_path, monkeypatch) as case:
        prepared = case.prepared
        before = case.store._connection.total_changes

        foreign_scope = ExecutionScope(
            provider=case.scope.provider,
            environment=case.scope.environment,
            account_ref=case.scope.account_ref,
            strategy_id=case.scope.strategy_id + "-other",
            trading_day=case.scope.trading_day,
        )
        with pytest.raises(ContractValidationError):
            CtpExecutionOwner(
                case.store,
                foreign_scope,
                case.writer_lease,
                execution_fixtures._Authority(),
                case.trader,
                case.session_adapter,
                case.reconciliation_port,
                session_generation_id=case.session_binding.session_generation_id,
            )

        errors = []

        def wrong_thread_call():
            try:
                case.owner.dispatch_submit(prepared, _queue_receipt(prepared))
            except BaseException as exc:  # capture only for this bounded negative case
                errors.append(exc)

        thread = threading.Thread(target=wrong_thread_call, name="wrong-owner-thread")
        thread.start()
        thread.join(timeout=2.0)
        assert not thread.is_alive()
        assert len(errors) == 1
        assert type(errors[0]) is ContractValidationError

        cancel_id = "cancel-" + case.intent.intent_id
        cancel_command_id = stable_managed_command_id(
            "cancel", case.intent.intent_id, case.reservation.runtime_order_id, cancel_id
        )
        cancel_queue_id = hashlib.sha256(
            ("queue:" + cancel_command_id).encode("ascii")
        ).hexdigest()[:32]
        cancel_use_id = "approval-" + cancel_command_id
        cancel_prepared = CtpManagedPreparedDispatch(
            operation="cancel",
            command_id=cancel_command_id,
            managed_intent_id=case.intent.intent_id,
            runtime_order_id=case.reservation.runtime_order_id,
            order_ref=case.reservation.order_ref,
            request_payload={
                "InstrumentID": case.intent.instrument,
                "OrderRef": case.reservation.order_ref,
                "ExchangeID": "SHFE",
                "OrderSysID": "SYS" + case.reservation.order_ref,
                "FrontID": case.session_binding.dispatch_front_id,
                "SessionID": case.session_binding.dispatch_session_id,
                "ActionFlag": "0",
            },
            order_ref_reservation=case.reservation,
            approval_use_id=cancel_use_id,
            approval_digest=hashlib.sha256(cancel_use_id.encode("ascii")).hexdigest(),
            session_binding=_session_binding_payload(case.session_binding),
            session_generation_id=case.session_binding.session_generation_id,
            dispatch_front_id=case.session_binding.dispatch_front_id,
            dispatch_session_id=case.session_binding.dispatch_session_id,
            native_request_id=prepared.native_request_id + 1,
            local_queue_receipt_id=cancel_queue_id,
            managed_cancel_intent_id=cancel_id,
            cancel_target_exchange_id="SHFE",
            cancel_target_order_sys_id="SYS" + case.reservation.order_ref,
            cancel_target_front_id=case.session_binding.dispatch_front_id,
            cancel_target_session_id=case.session_binding.dispatch_session_id,
        )
        with pytest.raises(ContractValidationError):
            case.owner.dispatch_submit(cancel_prepared, _queue_receipt(cancel_prepared))

        assert case.store._connection.total_changes == before
        assert not [call for call in case.api.calls if call[0] == "ReqOrderInsert"]


@pytest.mark.unit
def test_execution_owner_unknown_native_result_replays_without_second_send(
    tmp_path, monkeypatch
):
    with _active_case(tmp_path, monkeypatch, native_order_result=7) as case:
        prepared = case.prepared
        receipt = _queue_receipt(prepared)

        first = case.owner.dispatch_submit(prepared, receipt)
        assert len([call for call in case.api.calls if call[0] == "ReqOrderInsert"]) == 1
        # Native ambiguity poisons the account before a completion receipt can
        # be written. Preserve the durable in-flight claim instead of inventing
        # a completed UNKNOWN row or bypassing the account writer fence.
        assert first.command.status == "CLAIMED"
        assert first.command.native_receipt_payload is None
        assert first.command.completion_echo_sha256 is None
        assert first.binding.local_queue_receipt_queued is True
        assert case.trader._callback_ingress.poisoned is True
        assert case.trader._callback_ingress.poison_reason == "native_call_ambiguous"
        assert case.store._connection.execute(
            "SELECT owner_state, poison_code FROM ctp_account_family_owners"
        ).fetchall()[0][:] == ("POISONED", "native_call_ambiguous")
        assert case.store._connection.execute(
            "SELECT status, native_call_inflight FROM ctp_dispatch_commands"
        ).fetchall()[0][:] == ("CLAIMED", 1)

        before = case.store._connection.total_changes
        second = case.owner.dispatch_submit(prepared, receipt)
        assert second == first
        assert len([call for call in case.api.calls if call[0] == "ReqOrderInsert"]) == 1
        observation = case.owner.read_provider_observation(case.intent, first.binding)
        assert isinstance(observation, CtpOwnerCallbackObservationNotReadyV1)
        assert observation.reason_code == "ACCOUNT_FENCED"
        assert case.store._connection.total_changes == before
