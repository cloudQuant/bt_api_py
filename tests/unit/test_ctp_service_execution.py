from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

import pytest

from bt_api_execution import (
    ContractValidationError,
    CtpCallbackSessionStoreAdapter,
    CtpDispatchAuthority,
    CtpDispatchCallbackApplyResult,
    CtpDispatchReceipt,
    CtpDispatchTradeFactApplyResult,
    CtpOrderRefLegacyMapping,
    CtpOrderRefSeedProof,
    CtpVerifiedCallbackEvidence,
    CtpVerifiedTradeFactEvidence,
    ExecutionScope,
    InvalidStateTransition,
    OrderIntent,
    PositionEffect,
    Side,
    SqliteExecutionStore,
    WriterLeaseUnavailable,
    payload_sha256,
)

from bt_api_py.ctp_service_execution import CtpOwnerCallbackReconciliationPort


@dataclass(frozen=True)
class _SourceTags:
    source_instance_id: str = "ctp-service-reconcile-source"
    native_client_epoch: str = "a" * 32
    native_api_source_id: str = "ctp-service-reconcile-api"
    native_spi_source_id: str = "ctp-service-reconcile-spi"
    native_api_generation: int = 1
    connection_generation: int = 0


@dataclass(frozen=True)
class _LoginObservation:
    broker_id: str = "broker"
    user_id: str = "user"
    trading_day: str = "20260925"
    connection_generation: int = 1
    request_id: int = 17


class _CallbackRecord:
    """Fake SDK callback leaf with the production Store ingress shape."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = dict(payload)

    def to_payload(self) -> dict[str, object]:
        return dict(self._payload)


def _record(
    owner,
    callback_name: str,
    sequence: int,
    *,
    phase: str,
    callback_class: str,
    fields: tuple[tuple[int, str, object], ...] = (),
    generation: int = 1,
) -> _CallbackRecord:
    if callback_name == "OnFrontConnected":
        named_args: list[dict[str, object]] = []
    elif callback_name in {"OnRspAuthenticate", "OnRspUserLogin"}:
        named_args = [
            {
                "argument_slot": 0,
                "name": "pRspField",
                "present": True,
                "scalar_captured": False,
                "value": None,
            },
            {
                "argument_slot": 1,
                "name": "pRspInfo",
                "present": True,
                "scalar_captured": False,
                "value": None,
            },
            {
                "argument_slot": 2,
                "name": "nRequestID",
                "present": True,
                "scalar_captured": True,
                "value": sequence + 14,
            },
            {
                "argument_slot": 3,
                "name": "bIsLast",
                "present": True,
                "scalar_captured": True,
                "value": True,
            },
        ]
    else:
        argument_name = {
            "OnRtnOrder": "pOrder",
            "OnRtnTrade": "pTrade",
        }.get(callback_name, "pField")
        named_args = [
            {
                "argument_slot": 0,
                "name": argument_name,
                "present": True,
                "scalar_captured": False,
                "value": None,
            }
        ]
    flattened = [
        {
            "argument_slot": slot,
            "field_name": name,
            "present": True,
            "scalar_captured": True,
            "value": value,
        }
        for slot, name, value in fields
    ]
    payload: dict[str, object] = {
        "schema": "ctp_trader_callback_ingress.v2",
        "owner_intent_id": owner.owner_intent_id,
        "callback_name": callback_name,
        "callback_class": callback_class,
        "phase": phase,
        "source_instance_id": _SourceTags.source_instance_id,
        "native_client_epoch": _SourceTags.native_client_epoch,
        "native_api_source_id": _SourceTags.native_api_source_id,
        "native_spi_source_id": _SourceTags.native_spi_source_id,
        "native_api_generation": 1,
        "source_connection_generation": 0,
        "connection_generation": generation,
        "sequence": sequence,
        "monotonic_ns": sequence + 100,
        "named_args": named_args,
        "flattened_fields": flattened,
        "capture_complete": True,
        "missing_getters": [],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    payload["digest"] = hashlib.sha256(encoded).hexdigest()
    return _CallbackRecord(payload)


def _scope(*, strategy_ref: str = "strategy.ctp-reconcile") -> ExecutionScope:
    account_ref = "ctp-account-ref.v1:" + hashlib.sha256(b"ctp service reconcile").hexdigest()
    return ExecutionScope("ctp", "simulation", account_ref, strategy_ref, "20260925")


def _lease(store: SqliteExecutionStore, scope: ExecutionScope):
    family_owner = store.acquire_ctp_account_family_owner(scope)
    lease = store.acquire_or_renew_lease(
        scope,
        "ctp-service-reconcile-owner",
        ttl_ns=60_000_000_000,
        ctp_account_family_owner=family_owner,
    )
    return family_owner, lease


def _active_owner(store: SqliteExecutionStore, scope: ExecutionScope, lease):
    owner = store.create_ctp_callback_session_owner(scope, writer_lease=lease)
    adapter = CtpCallbackSessionStoreAdapter(
        store,
        scope,
        owner,
        lease,
        lambda **kwargs: kwargs,
        _CallbackRecord,
    )
    store.append_ctp_callback_ingress(
        owner,
        _record(
            owner,
            "OnFrontConnected",
            1,
            phase="PRE_LOGIN",
            callback_class="PRE_LOGIN",
            generation=0,
        ),
    )
    store.append_ctp_callback_ingress(
        owner,
        _record(
            owner,
            "OnRspAuthenticate",
            2,
            phase="PRE_LOGIN",
            callback_class="PRE_LOGIN",
            fields=((0, "BrokerID", "broker"), (0, "UserID", "user"), (1, "ErrorID", 0)),
        ),
    )
    store.append_ctp_callback_ingress(
        owner,
        _record(
            owner,
            "OnRspUserLogin",
            3,
            phase="PRE_LOGIN",
            callback_class="PRE_LOGIN",
            fields=(
                (0, "BrokerID", "broker"),
                (0, "UserID", "user"),
                (0, "TradingDay", scope.trading_day),
                (0, "FrontID", 31),
                (0, "SessionID", 41),
                (1, "ErrorID", 0),
            ),
        ),
    )
    store.bind_ctp_callback_session(
        scope, owner, _LoginObservation(), _SourceTags(), 3, writer_lease=lease
    )
    return owner, adapter


def _order_ref_proof(scope: ExecutionScope, session_generation_id: str) -> CtpOrderRefSeedProof:
    legacy_scope = ExecutionScope(
        "ctp", "simulation", scope.account_ref, "strategy.legacy", "20260924"
    )
    source_digests = (
        ("backtrader_prototype", hashlib.sha256(b"prototype fixture").hexdigest()),
        ("sdk_jsonl", hashlib.sha256(b"sdk fixture").hexdigest()),
    )
    mapping = CtpOrderRefLegacyMapping(
        source_name="backtrader_prototype",
        account_key=scope.account_key,
        trading_day="20260924",
        scope_key=legacy_scope.key,
        managed_intent_id="legacy-intent",
        runtime_order_id="legacy-runtime-order",
        order_ref="000000000012",
    )
    return CtpOrderRefSeedProof(
        trading_day=scope.trading_day,
        native_max_order_ref="000000000010",
        legacy_ledger_max_order_ref="000000000012",
        legacy_ledger_sha256=payload_sha256(dict(source_digests)),
        account_key=scope.account_key,
        scope_key=scope.key,
        session_generation_id=session_generation_id,
        native_front_id=31,
        native_session_id=41,
        existing_native_order_refs=("000000000009", "000000000010"),
        legacy_source_sha256=source_digests,
        legacy_mappings=(mapping,),
    )


def _runtime_order_id(intent_id: str) -> str:
    return "bt-managed-v1:" + hashlib.sha256(intent_id.encode("ascii")).hexdigest()


class _Authority:
    def verify_action(self, command, *, now_ns):
        return CtpDispatchAuthority(
            authority_type="ctp_dispatch_authority.v1",
            command_binding_sha256=command.authority_binding_sha256,
            approval_use_id=command.approval_use_id,
            approval_digest=command.approval_digest,
            source_digest_sha256=hashlib.sha256(b"synthetic authority").hexdigest(),
            verifier_id="synthetic-reconciliation-test",
            verified_at_ns=now_ns,
            expires_at_ns=now_ns + 5_000_000_000,
        )


class _CallbackVerifier:
    def verify_callback(self, command, callback, callback_payload, *, now_ns):
        status = callback_payload.get("native_fields", {}).get("OrderStatus")
        projection_state = {
            "0": "FILLED",
            "1": "PARTIALLY_FILLED",
            "3": "ACKNOWLEDGED",
            "5": "CANCELLED",
            "a": "REJECTED",
        }.get(status, "ACKNOWLEDGED")
        return CtpVerifiedCallbackEvidence(
            evidence_type="ctp_verified_callback.v1",
            callback_key=callback,
            callback_payload_sha256=payload_sha256(callback_payload),
            projection_state=projection_state,
            source_digest_sha256=hashlib.sha256(b"synthetic callback verifier").hexdigest(),
            verifier_id="synthetic-reconciliation-callback",
            verified_at_ns=now_ns,
            expires_at_ns=now_ns + 5_000_000_000,
        )


class _TradeVerifier:
    def verify_trade_fact(self, command, trade_fact, source_event, *, now_ns):
        return CtpVerifiedTradeFactEvidence(
            evidence_type="ctp_verified_trade_fact.v2",
            owner_intent_id=source_event.owner_handle.owner_intent_id,
            source_sequence=source_event.source_sequence,
            ingress_record_digest_sha256=source_event.record_digest_sha256,
            event_id=trade_fact.event_id,
            trade_fact_sha256=payload_sha256(trade_fact.to_payload()),
            source_digest_sha256=hashlib.sha256(b"synthetic trade verifier").hexdigest(),
            verifier_id="synthetic-reconciliation-trade",
            verified_at_ns=now_ns,
            expires_at_ns=now_ns + 5_000_000_000,
        )


def _intent(
    scope: ExecutionScope,
    *,
    quantity: Decimal = Decimal("1"),
    price: Decimal = Decimal("100"),
    position_effect: PositionEffect = PositionEffect.OPEN,
    position_bucket: str = "ALL",
) -> OrderIntent:
    return OrderIntent.limit(
        intent_id="venue02-reconcile-integer-lot",
        scope=scope,
        signal_id="synthetic-signal",
        instrument="rb2710",
        side=Side.BUY,
        position_effect=position_effect,
        reduce_only=position_effect is not PositionEffect.OPEN,
        quantity=quantity,
        price=price,
        position_bucket=position_bucket,
    )


def _stage_queued_submit(
    store,
    scope,
    lease,
    owner,
    intent,
    reservation,
    request_id=71,
    comb_offset_flag="0",
):
    binding = store.read_ctp_callback_session_context_facts(owner)[0]
    command_id = "ctp-reconcile-command-1"
    receipt_id = hashlib.sha256(command_id.encode("ascii")).hexdigest()[:32]
    session_payload = {
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
    request = {
        "InstrumentID": intent.instrument,
        "ExchangeID": "SHFE",
        "OrderRef": reservation.order_ref,
        "Direction": "0",
        "CombOffsetFlag": comb_offset_flag,
        "CombHedgeFlag": "1",
        "LimitPrice": 100.0,
        "VolumeTotalOriginal": int(intent.quantity),
        "OrderPriceType": "2",
        "TimeCondition": "3",
    }
    staged = store.stage_ctp_dispatch_command(
        scope,
        command_id,
        "SUBMIT",
        request,
        approval_use_id="approval-" + command_id,
        approval_digest=hashlib.sha256(("approval-" + command_id).encode("ascii")).hexdigest(),
        session_binding=session_payload,
        writer_lease=lease,
        managed_intent_id=reservation.managed_intent_id,
        order_ref=reservation.order_ref,
        managed_action_id=reservation.managed_intent_id,
        session_generation_id=binding.session_generation_id,
        dispatch_front_id=binding.dispatch_front_id,
        dispatch_session_id=binding.dispatch_session_id,
        native_request_id=request_id,
        local_queue_receipt_id=receipt_id,
    )
    store.record_ctp_dispatch_queue_receipt(
        scope,
        command_id,
        {"kind": "command_receipt", "command": "submit", "receipt_id": receipt_id, "queued": True},
        writer_lease=lease,
    )
    claim = store.claim_ctp_dispatch_command_for_session(
        scope,
        command_id,
        owner_handle=owner,
        writer_lease=lease,
        authority_verifier=_Authority(),
        required_local_queue_receipt_id=receipt_id,
    )
    assert claim is not None
    command = claim.command
    receipt = CtpDispatchReceipt(
        receipt_type="ctp_dispatch_receipt.v2",
        command_id=command.command_id,
        account_key=command.account_key,
        scope_key=command.scope_key,
        trading_day=command.trading_day,
        operation=command.operation,
        request_payload_sha256=command.request_payload_sha256,
        reservation_managed_intent_id=command.reservation_managed_intent_id,
        order_ref=command.order_ref,
        cancel_target_order_ref=command.cancel_target_order_ref,
        cancel_target_exchange_id=command.cancel_target_exchange_id,
        cancel_target_order_sys_id=command.cancel_target_order_sys_id,
        cancel_target_front_id=command.cancel_target_front_id,
        cancel_target_session_id=command.cancel_target_session_id,
        approval_use_id=command.approval_use_id,
        approval_digest=command.approval_digest,
        session_binding_sha256=command.session_binding_sha256,
        outcome="QUEUED",
        native_receipt_payload={"request_id": request_id, "queue_code": 0},
        correlation_key=command.correlation_key,
        local_queue_receipt_id=receipt_id,
    )
    store.complete_ctp_dispatch_command_for_session(
        scope, receipt, owner_handle=owner, writer_lease=lease
    )
    return command_id


def _reserve(store, scope, lease, intent, generation):
    return store.seed_ctp_order_ref_and_reserve_identity(
        scope,
        _order_ref_proof(scope, generation),
        intent.intent_id,
        _runtime_order_id(intent.intent_id),
        writer_lease=lease,
    )


def _order_event(owner, scope, reservation, sequence, *, status, volume_traded, volume_total):
    fields = (
        (0, "BrokerID", "broker"),
        (0, "InvestorID", "user"),
        (0, "UserID", "user"),
        (0, "InstrumentID", "rb2710"),
        (0, "RequestID", 71),
        (0, "OrderRef", reservation.order_ref),
        (0, "ExchangeID", "SHFE"),
        (0, "OrderSysID", "sys-" + reservation.order_ref),
        (0, "FrontID", 31),
        (0, "SessionID", 41),
        (0, "TradingDay", scope.trading_day),
        (0, "OrderStatus", status),
        (0, "OrderSubmitStatus", "3"),
        (0, "VolumeTraded", volume_traded),
        (0, "VolumeTotal", volume_total),
        (0, "NotifySequence", sequence),
        (0, "SequenceNo", 800 + sequence),
    )
    return _record(
        owner,
        "OnRtnOrder",
        sequence,
        phase="ACTIVE",
        callback_class="ROUTEABLE",
        fields=fields,
    )


def _trade_event(
    owner,
    reservation,
    sequence,
    *,
    trade_id: str,
    native_sequence: int,
    volume: int = 1,
):
    fields = (
        (0, "BrokerID", "broker"),
        (0, "InvestorID", "user"),
        (0, "UserID", "user"),
        (0, "InstrumentID", "rb2710"),
        (0, "OrderRef", reservation.order_ref),
        (0, "ExchangeID", "SHFE"),
        (0, "TradeID", trade_id),
        (0, "OrderSysID", "sys-" + reservation.order_ref),
        (0, "TradingDay", "20260925"),
        (0, "Direction", "0"),
        (0, "OffsetFlag", "0"),
        (0, "HedgeFlag", "1"),
        (0, "Price", 99.0),
        (0, "Volume", volume),
        (0, "TradeDate", "20260925"),
        (0, "TradeTime", "09:31:00"),
        (0, "SequenceNo", native_sequence),
        (0, "BrokerOrderSeq", 1),
        (0, "TradeSource", "0"),
    )
    return _record(
        owner,
        "OnRtnTrade",
        sequence,
        phase="ACTIVE",
        callback_class="ROUTEABLE",
        fields=fields,
    )


def _port(store, scope, lease, session_adapter, generation):
    return CtpOwnerCallbackReconciliationPort(
        store,
        scope,
        lease,
        session_adapter,
        session_generation_id=generation,
        callback_verifier=_CallbackVerifier(),
        trade_fact_verifier=_TradeVerifier(),
    )


@pytest.mark.unit
def test_owner_reconciliation_projects_verified_order_and_deduped_trade_results(tmp_path):
    scope = _scope()
    store = SqliteExecutionStore(tmp_path / "ctp-owner-reconciliation.sqlite3")
    _, lease = _lease(store, scope)
    try:
        owner, session_adapter = _active_owner(store, scope, lease)
        binding = store.read_ctp_callback_session_context_facts(owner)[0]
        intent = _intent(scope, quantity=Decimal("3"))
        store.admit_intent(intent, writer_lease=lease)
        reservation = _reserve(store, scope, lease, intent, binding.session_generation_id)
        command_id = _stage_queued_submit(store, scope, lease, owner, intent, reservation)
        port = _port(store, scope, lease, session_adapter, binding.session_generation_id)

        # A completed local receipt is still not provider acknowledgement.
        before_callback = port.read_order_projection(intent, command_id)
        assert before_callback is not None
        assert before_callback.command_status == "COMPLETED"
        assert before_callback.local_dispatch_outcome == "QUEUED"
        assert before_callback.provider_order_state.provider_state is None

        store.append_ctp_callback_ingress(
            owner,
            _order_event(
                owner, scope, reservation, 4, status="3", volume_traded=0, volume_total=3
            ),
        )
        acknowledged = port.apply_next_ingress()
        assert type(acknowledged) is CtpDispatchCallbackApplyResult
        assert acknowledged.projection_state == "ACKNOWLEDGED"
        # This flag means an unresolved account blocker exists, not dispatch permission.
        assert acknowledged.account_fence_open is False
        after_ack = port.read_order_projection(intent, command_id)
        assert after_ack is not None
        assert after_ack.provider_order_state.provider_state == "ACKNOWLEDGED"
        assert after_ack.provider_order_state.source_kind == "CALLBACK"
        assert after_ack.local_dispatch_outcome == "QUEUED"

        store.append_ctp_callback_ingress(
            owner,
            _trade_event(
                owner,
                reservation,
                5,
                trade_id="t1",
                native_sequence=1,
                volume=1,
            ),
        )
        first_trade = port.apply_next_ingress()
        assert type(first_trade) is CtpDispatchTradeFactApplyResult
        assert first_trade.trade_quantity == 1
        assert first_trade.cumulative_trade_quantity == 1
        assert first_trade.duplicate is False

        # A repeated provider TradeID advances ingress but does not count twice.
        store.append_ctp_callback_ingress(
            owner,
            _trade_event(
                owner,
                reservation,
                6,
                trade_id="t1",
                native_sequence=1,
                volume=1,
            ),
        )
        duplicate_trade = port.apply_next_ingress()
        assert type(duplicate_trade) is CtpDispatchTradeFactApplyResult
        assert duplicate_trade.duplicate is True
        assert duplicate_trade.cumulative_trade_quantity == 1
        assert duplicate_trade.account_fence_open is False

        store.append_ctp_callback_ingress(
            owner,
            _trade_event(
                owner,
                reservation,
                7,
                trade_id="t2",
                native_sequence=2,
                volume=1,
            ),
        )
        second_trade = port.apply_next_ingress()
        assert type(second_trade) is CtpDispatchTradeFactApplyResult
        assert second_trade.trade_quantity == 1
        assert second_trade.cumulative_trade_quantity == 2
        assert second_trade.duplicate is False

        store.append_ctp_callback_ingress(
            owner,
            _order_event(
                owner, scope, reservation, 8, status="1", volume_traded=2, volume_total=1
            ),
        )
        partial = port.apply_next_ingress()
        assert type(partial) is CtpDispatchCallbackApplyResult
        assert partial.projection_state == "PARTIALLY_FILLED"
        partial_view = port.read_order_projection(intent, command_id)
        assert partial_view is not None
        assert partial_view.requested_quantity == Decimal("3")
        assert partial_view.provider_order_state.provider_state == "PARTIALLY_FILLED"
        assert partial_view.provider_order_state.terminal is False
        assert partial_view.command_status == "COMPLETED"
        assert partial_view.local_dispatch_outcome == "QUEUED"
        assert not hasattr(partial_view, "average_fill_price")
    finally:
        store.close()


@pytest.mark.unit
def test_owner_reconciliation_rejects_wrong_session_and_stale_writer_fence(tmp_path):
    scope = _scope()
    store = SqliteExecutionStore(tmp_path / "ctp-owner-reconciliation-fences.sqlite3")
    family_owner, lease = _lease(store, scope)
    try:
        owner, session_adapter = _active_owner(store, scope, lease)
        binding = store.read_ctp_callback_session_context_facts(owner)[0]
        intent = _intent(scope)
        store.admit_intent(intent, writer_lease=lease)

        with pytest.raises(InvalidStateTransition, match="session generation"):
            _port(store, scope, lease, session_adapter, "stale-session-generation")

        port = _port(store, scope, lease, session_adapter, binding.session_generation_id)
        assert store.release_lease(scope, lease.owner_id, fencing_token=lease.fencing_token)
        replacement = store.acquire_or_renew_lease(
            scope,
            "ctp-service-reconcile-replacement",
            ttl_ns=60_000_000_000,
            ctp_account_family_owner=family_owner,
        )
        assert replacement.fencing_token > lease.fencing_token

        with pytest.raises(WriterLeaseUnavailable):
            port.apply_next_ingress()
    finally:
        store.close()


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["quantity", "price", "scope"])
def test_projection_rejects_noncanonical_intent_without_advancing_ingress(tmp_path, mutation):
    scope = _scope()
    store = SqliteExecutionStore(tmp_path / f"ctp-owner-reconciliation-{mutation}.sqlite3")
    _, lease = _lease(store, scope)
    try:
        owner, session_adapter = _active_owner(store, scope, lease)
        binding = store.read_ctp_callback_session_context_facts(owner)[0]
        intent = _intent(scope, quantity=Decimal("3"))
        store.admit_intent(intent, writer_lease=lease)
        reservation = _reserve(store, scope, lease, intent, binding.session_generation_id)
        port = _port(store, scope, lease, session_adapter, binding.session_generation_id)
        store.append_ctp_callback_ingress(
            owner,
            _order_event(
                owner, scope, reservation, 4, status="3", volume_traded=0, volume_total=3
            ),
        )
        pending_before = session_adapter.read_next_ingress()
        assert pending_before is not None

        if mutation == "quantity":
            candidate = _intent(scope, quantity=Decimal("2"))
            with pytest.raises(InvalidStateTransition, match="durable execution record"):
                port.read_order_projection(candidate, "unread-command")
        elif mutation == "price":
            candidate = _intent(scope, quantity=Decimal("3"), price=Decimal("101"))
            with pytest.raises(InvalidStateTransition, match="durable execution record"):
                port.read_order_projection(candidate, "unread-command")
        else:
            other_scope = _scope(strategy_ref="strategy.ctp-reconcile-other")
            candidate = _intent(other_scope, quantity=Decimal("3"))
            with pytest.raises(ContractValidationError, match="same-scope"):
                port.read_order_projection(candidate, "unread-command")

        assert session_adapter.read_next_ingress() == pending_before
    finally:
        store.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("bucket", "expected_flag", "stored_flag"),
    [
        ("TODAY", "3", "3"),
        ("TODAY", "3", "1"),
        ("YESTERDAY", "4", "4"),
        ("YESTERDAY", "4", "1"),
    ],
)
def test_generic_close_bucket_requires_matching_ctp_offset_without_advancing_ingress(
    tmp_path, bucket, expected_flag, stored_flag
):
    scope = _scope()
    store = SqliteExecutionStore(tmp_path / f"ctp-close-offset-{bucket}-{stored_flag}.sqlite3")
    _, lease = _lease(store, scope)
    try:
        owner, session_adapter = _active_owner(store, scope, lease)
        binding = store.read_ctp_callback_session_context_facts(owner)[0]
        intent = _intent(
            scope,
            quantity=Decimal("3"),
            position_effect=PositionEffect.CLOSE,
            position_bucket=bucket,
        )
        store.admit_intent(intent, writer_lease=lease)
        reservation = _reserve(store, scope, lease, intent, binding.session_generation_id)
        command_id = _stage_queued_submit(
            store,
            scope,
            lease,
            owner,
            intent,
            reservation,
            comb_offset_flag=stored_flag,
        )
        store.append_ctp_callback_ingress(
            owner,
            _order_event(
                owner, scope, reservation, 4, status="3", volume_traded=0, volume_total=3
            ),
        )
        pending_before = session_adapter.read_next_ingress()
        assert pending_before is not None
        port = _port(store, scope, lease, session_adapter, binding.session_generation_id)

        if stored_flag == expected_flag:
            projection = port.read_order_projection(intent, command_id)
            assert projection is not None
            assert projection.command_status == "COMPLETED"
        else:
            with pytest.raises(InvalidStateTransition, match="request differs"):
                port.read_order_projection(intent, command_id)

        assert session_adapter.read_next_ingress() == pending_before
    finally:
        store.close()


@pytest.mark.unit
def test_owner_reconciliation_rejects_adapter_store_mismatch_before_ingress_apply(tmp_path):
    scope = _scope()
    store = SqliteExecutionStore(tmp_path / "ctp-owner-reconciliation-adapter-fence.sqlite3")
    other_store = SqliteExecutionStore(tmp_path / "ctp-owner-reconciliation-other-store.sqlite3")
    _, lease = _lease(store, scope)
    session_adapter = None
    try:
        owner, session_adapter = _active_owner(store, scope, lease)
        binding = store.read_ctp_callback_session_context_facts(owner)[0]
        intent = _intent(scope)
        store.admit_intent(intent, writer_lease=lease)
        reservation = _reserve(store, scope, lease, intent, binding.session_generation_id)
        store.append_ctp_callback_ingress(
            owner,
            _order_event(
                owner, scope, reservation, 4, status="3", volume_traded=0, volume_total=1
            ),
        )
        pending_before = session_adapter.read_next_ingress()
        assert pending_before is not None
        port = _port(store, scope, lease, session_adapter, binding.session_generation_id)

        session_adapter.store = other_store
        with pytest.raises(InvalidStateTransition, match="owner binding changed"):
            port.apply_next_ingress()
        session_adapter.store = store

        assert session_adapter.read_next_ingress() == pending_before
    finally:
        if session_adapter is not None:
            session_adapter.store = store
        other_store.close()
        store.close()
