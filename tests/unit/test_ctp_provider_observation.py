from __future__ import annotations

import base64
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from decimal import Context, Decimal, Inexact, ROUND_HALF_EVEN, Rounded, localcontext
from types import SimpleNamespace

import pytest
import test_ctp_service_execution as fx

from bt_api_execution import (
    ContractValidationError,
    CtpOwnerCallbackObservationNotReadyV1,
    ExecutionState,
    ProviderObservation,
    SqliteExecutionStore,
    payload_sha256,
)
from bt_api_execution.ctp_single_worker_candidate import CtpManagedDispatchBinding
from bt_api_py.ctp_service_execution import (
    decode_ctp_provider_order_id,
    encode_ctp_provider_order_id,
)


@contextmanager
def _prepared(tmp_path):
    scope = fx._scope()
    store = SqliteExecutionStore(tmp_path / "provider-observation.sqlite3")
    try:
        family, lease = fx._lease(store, scope)
        owner, adapter = fx._active_owner(store, scope, lease)
        session = store.read_ctp_callback_session_context_facts(owner)[0]
        intent = fx._intent(scope, quantity=Decimal(4))
        store.admit_intent(intent, writer_lease=lease)
        reservation = fx._reserve(store, scope, lease, intent, session.session_generation_id)
        command_id = fx._stage_queued_submit(store, scope, lease, owner, intent, reservation)
        command = store.read_ctp_dispatch_command(scope, command_id)
        binding = CtpManagedDispatchBinding.from_command(command, reservation)
        assert binding.local_queue_receipt_queued is True
        port = fx._port(store, scope, lease, adapter, session.session_generation_id)
        yield SimpleNamespace(**locals())
    finally:
        store.close()


def _order(case, sequence, status, filled, remaining):
    case.store.append_ctp_callback_ingress(
        case.owner,
        fx._order_event(case.owner, case.scope, case.reservation, sequence,
                        status=status, volume_traded=filled, volume_total=remaining),
    )
    return case.port.apply_next_ingress()


def _trade(case, sequence, trade_id, native_sequence, volume, price):
    original = fx._trade_event(case.owner, case.reservation, sequence,
                              trade_id=trade_id, native_sequence=native_sequence, volume=volume)
    fields = tuple(
        (row["argument_slot"], row["field_name"],
         price if row["field_name"] == "Price" else row["value"])
        for row in original.to_payload()["flattened_fields"]
    )
    event = fx._record(case.owner, "OnRtnTrade", sequence, phase="ACTIVE",
                       callback_class="ROUTEABLE", fields=fields)
    case.store.append_ctp_callback_ingress(case.owner, event)
    return case.port.apply_next_ingress()


def _read_unchanged(case, intent=None, binding=None):
    changes = case.store._connection.total_changes
    before = case.store.get(case.intent.intent_id, scope=case.scope)
    command = case.store.read_ctp_dispatch_command(case.scope, case.command_id)
    result = case.port.read_provider_observation(
        case.intent if intent is None else intent,
        case.binding if binding is None else binding,
    )
    assert case.store._connection.total_changes == changes
    assert case.store.get(case.intent.intent_id, scope=case.scope) == before
    assert case.store.read_ctp_dispatch_command(case.scope, case.command_id) == command
    return result


def _not_ready(value, reason=None):
    assert type(value) is CtpOwnerCallbackObservationNotReadyV1
    if reason is not None:
        assert value.reason_code == reason


@pytest.mark.unit
def test_provider_observation_uses_callback_states_and_exact_source_amount(tmp_path):
    with _prepared(tmp_path) as case:
        _not_ready(_read_unchanged(case))  # Native queue receipt is not an ACK.
        _order(case, 4, "3", 0, 4)
        ack = _read_unchanged(case)
        assert type(ack) is ProviderObservation
        assert ack.state is ExecutionState.ACKED
        assert ack.filled_quantity == 0 and ack.average_price is None
        assert decode_ctp_provider_order_id(ack.provider_order_id) == (
            "SHFE", "sys-" + case.reservation.order_ref,
        )
        _trade(case, 5, "observation-t1", 1, 1, 98.0)
        duplicate = _trade(case, 6, "observation-t1", 1, 1, 98.0)
        assert duplicate.duplicate is True
        _trade(case, 7, "observation-t2", 2, 2, 100.0)
        _not_ready(_read_unchanged(case), "TRADE_PREFIX_MISMATCH")
        _order(case, 8, "1", 3, 1)
        expected_average = Context(prec=50, rounding=ROUND_HALF_EVEN).divide(Decimal(298), Decimal(3))
        with localcontext() as context:
            context.prec = 6
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            partial = _read_unchanged(case)
        assert partial.state is ExecutionState.PARTIALLY_FILLED
        assert partial.filled_quantity == 3 and partial.average_price == expected_average
        assert partial.provider_order_id == ack.provider_order_id
        assert partial.cumulative_commission is None
        assert partial.cumulative_commission_currency is None
        snapshot = case.store.read_ctp_owner_callback_observation_snapshot(case.scope, case.command_id)
        assert snapshot.trade_notional == Decimal(298)
        assert snapshot.trade_fact_count == 2
        _trade(case, 9, "observation-t3", 3, 1, 99.0)
        _order(case, 10, "0", 4, 0)
        filled = _read_unchanged(case)
        assert filled.state is ExecutionState.FILLED
        assert filled.filled_quantity == 4 and filled.average_price == Decimal("99.25")
        assert filled.provider_order_id == ack.provider_order_id


@pytest.mark.unit
def test_provider_observation_rejects_mismatches_and_expired_owner(tmp_path, monkeypatch):
    with _prepared(tmp_path) as case:
        _order(case, 4, "3", 0, 4)
        assert type(_read_unchanged(case)) is ProviderObservation
        for binding in (
            object(),
            replace(case.binding, command_id="absent-command"),
            replace(case.binding, managed_intent_id="different-intent"),
            replace(case.binding, scope_key=fx._scope(strategy_ref="different").key),
            replace(case.binding, session_generation_id="different-session"),
            replace(case.binding, local_queue_receipt_queued=None),
        ):
            _not_ready(_read_unchanged(case, binding=binding))
        cancel_payload = dict(case.binding.request_payload, OrderActionRef=1)
        forged_cancel = replace(case.binding, operation="cancel", native_action_ref=1,
                                native_request_payload=cancel_payload,
                                native_request_payload_sha256=payload_sha256(cancel_payload))
        _not_ready(_read_unchanged(case, binding=forged_cancel))
        _not_ready(_read_unchanged(case, intent=replace(case.intent, price=Decimal(99))))
        _not_ready(_read_unchanged(case, intent=replace(case.intent, scope=fx._scope(strategy_ref="other"))))

        with monkeypatch.context() as patch:
            def unavailable(*args, **kwargs):
                raise sqlite3.OperationalError("synthetic storage failure")
            patch.setattr(case.store, "read_ctp_owner_callback_observation_snapshot", unavailable)
            with pytest.raises(sqlite3.OperationalError):
                case.port.read_provider_observation(case.intent, case.binding)

        assert case.store.release_lease(case.scope, case.lease.owner_id,
                                        fencing_token=case.lease.fencing_token)
        replacement = case.store.acquire_or_renew_lease(
            case.scope, "replacement-owner", ttl_ns=60_000_000_000,
            ctp_account_family_owner=case.family,
        )
        assert replacement.fencing_token > case.lease.fencing_token
        _not_ready(_read_unchanged(case), "CALLBACK_OWNER_UNAVAILABLE")


@pytest.mark.unit
def test_provider_observation_preserves_unknown_and_account_fence(tmp_path):
    from test_ctp_callback_session_router import _queue_and_complete

    with _prepared(tmp_path) as case:
        _order(case, 4, "3", 0, 4)
        second_intent = replace(case.intent, intent_id="second-unknown-intent", signal_id="second-signal")
        case.store.admit_intent(second_intent, writer_lease=case.lease)
        reservation = fx._reserve(case.store, case.scope, case.lease, second_intent,
                                  case.session.session_generation_id)
        request = dict(case.command.request_payload, OrderRef=reservation.order_ref)
        staged, claim = _queue_and_complete(
            case.store, case.scope, case.lease, case.owner, reservation,
            "second-unknown-command", request, 72, record_queue_receipt=False, claim_command=False,
        )
        assert claim is None
        binding = CtpManagedDispatchBinding.from_command(staged, reservation)
        case.store.fail_ctp_dispatch_command_before_native(
            case.scope, staged, owner_handle=case.owner, writer_lease=case.lease,
            failure_code="post_stage_failure",
        )
        changes = case.store._connection.total_changes
        unknown_before = case.store.read_ctp_dispatch_command(case.scope, staged.command_id)
        owner_before = tuple(case.store._connection.execute(
            "SELECT owner_state, poison_code FROM ctp_dispatch_callback_session_owners "
            "WHERE owner_intent_id = ?", (case.owner.owner_intent_id,),
        ).fetchone())
        _not_ready(_read_unchanged(case, intent=second_intent, binding=binding), "COMMAND_UNKNOWN")
        _not_ready(_read_unchanged(case), "ACCOUNT_FENCED")
        assert case.store._connection.total_changes == changes
        assert case.store.read_ctp_dispatch_command(case.scope, staged.command_id) == unknown_before
        assert unknown_before.status == "UNKNOWN"
        assert tuple(case.store._connection.execute(
            "SELECT owner_state, poison_code FROM ctp_dispatch_callback_session_owners "
            "WHERE owner_intent_id = ?", (case.owner.owner_intent_id,),
        ).fetchone()) == owner_before


@pytest.mark.unit
def test_ctp_provider_order_id_codec_is_exact_bounded_and_canonical():
    pairs = (("SHFE", "00001234"), ("A:B", "C"), ("A", "B:C"), ("EX", "sys-_.:01"))
    ids = [encode_ctp_provider_order_id(*pair) for pair in pairs]
    assert len(set(ids)) == len(pairs)
    for pair, identifier in zip(pairs, ids):
        assert len(identifier) <= 128
        assert decode_ctp_provider_order_id(identifier) == pair
    noncanonical = "ctp:v1:" + base64.urlsafe_b64encode(json.dumps(list(pairs[0])).encode()).decode().rstrip("=")
    for identifier in ("", "ctp:v2:" + ids[0][7:], ids[0] + "=", "ctp:v1:A", noncanonical,
                       "ctp:v1:" + "A" * 128):
        with pytest.raises(ContractValidationError):
            decode_ctp_provider_order_id(identifier)
    for pair in (("", "x"), (" SHFE", "x"), ("SHFE", "x\n"), ("SHFE", "x" * 128)):
        with pytest.raises(ContractValidationError):
            encode_ctp_provider_order_id(*pair)
