"""Local reconstruction tests; this fake verifier is not provider evidence."""

from __future__ import annotations

import multiprocessing
import threading
from dataclasses import replace

import pytest
from bt_api_base.account_protocol import AccountKey, AccountPosition, AccountResourceKey
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.position_bootstrap_protocol import (
    PositionStreamBinding,
    PositionStreamEvent,
    PositionStreamSnapshot,
)

from bt_api_py.position_stream_bootstrap import PositionBootstrapError, PositionStreamBootstrap


def _binding(**overrides):
    values = {
        "account_key": AccountKey("ctp", "simnow", "simnow", "local-account"),
        "account_ref": "account-a",
        "route_ref": "CTP___FUTURE",
        "session_ref": "session-1",
        "connection_epoch": 1,
        "subscription_epoch": 1,
        "subscription_ready_ref": "ready-receipt",
        "valid_until_ns": 200,
    }
    values.update(overrides)
    return PositionStreamBinding(**values)


def _row(quantity="1", *, instrument="SA2701", venue="CZCE"):
    return AccountPosition(
        AccountResourceKey("future", "default"),
        MarketInstrumentKey("ctp", venue, "simnow", "future", instrument, instrument),
        "net",
        quantity,
        "contracts",
        None,
        None,
        "2026-11-03T00:00:00Z",
    )


class _Verifier:
    """A host-selected test double, not an authenticating provider adapter."""

    def __init__(self, binding):
        self.binding = replace(
            binding, account_key=AccountKey.from_dict(binding.account_key.to_dict())
        )
        self.snapshots = []
        self.events = []
        self.live = True

    def verify_binding(self, binding):
        return binding == self.binding

    def verify_snapshot(self, binding, snapshot):
        return binding == self.binding and snapshot in self.snapshots

    def verify_event(self, binding, event):
        return binding == self.binding and event in self.events

    def verify_live(self, binding, now_ns):
        return binding == self.binding and self.live


def _build(**kwargs):
    binding = _binding()
    verifier = _Verifier(binding)
    now = [100]
    stream = PositionStreamBootstrap(binding, verifier, clock_ns=lambda: now[0], **kwargs)
    return binding, verifier, now, stream


def _snapshot(binding, verifier, *, cut=10, rows=(), observed=90):
    snapshot = PositionStreamSnapshot(binding, cut, observed, "snapshot-receipt", tuple(rows))
    verifier.snapshots.append(snapshot)
    return snapshot


def _event(binding, verifier, seq, quantity="1", *, observed=95, instrument="SA2701"):
    event = PositionStreamEvent(
        binding, seq, observed, f"event-{seq}", _row(quantity, instrument=instrument)
    )
    verifier.events.append(event)
    return event


def test_buffered_cut_and_live_events_project_without_ledger_authority():
    binding, verifier, _, stream = _build()
    stream.push(_event(binding, verifier, 10, "5", observed=91))
    stream.push(_event(binding, verifier, 11, "2", observed=92))
    stream.install_snapshot(_snapshot(binding, verifier, cut=10, rows=[_row("5")]))
    projection = stream.projection()
    assert projection.snapshot_receipt_ref == "snapshot-receipt"
    assert projection.snapshot_cut_sequence == 10
    assert projection.sequence == 11
    assert projection.positions == (_row("2"),)
    assert not hasattr(projection, "complete")
    stream.push(_event(binding, verifier, 12, "0", observed=93))
    latest = stream.projection()
    assert latest.positions == ()
    assert latest.snapshot_receipt_ref == "snapshot-receipt"
    assert latest.snapshot_cut_sequence == 10


def test_snapshot_cut_advances_past_buffered_prior_events():
    binding, verifier, _, stream = _build()
    stream.push(_event(binding, verifier, 8, observed=90))
    stream.install_snapshot(_snapshot(binding, verifier, cut=10, rows=[_row("5")]))
    stream.push(_event(binding, verifier, 11, "2", observed=91))
    assert stream.projection().positions == (_row("2"),)


def test_buffer_does_not_retain_source_event_or_row_references():
    binding, verifier, _, stream = _build()
    event = _event(binding, verifier, 11, "2")
    stream.push(event)
    # The adapter has returned. A later mutation of its object must not
    # rewrite the values admitted into the pending stream buffer.
    object.__setattr__(event.position, "quantity", "999")
    object.__setattr__(event, "sequence", 900)
    stream.install_snapshot(_snapshot(binding, verifier, cut=10))
    assert stream.projection().sequence == 11
    assert stream.projection().positions == (_row("2"),)


def test_projection_and_all_nested_identity_values_are_detached_per_consumer():
    binding, verifier, _, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier, rows=[_row("1")]))
    first = stream.projection()
    second = stream.projection()
    object.__setattr__(first.positions[0], "quantity", "999")
    object.__setattr__(first.positions[0].instrument, "instrument_id", "tampered")
    object.__setattr__(first.positions[0].resource, "margin_pool", "tampered")
    object.__setattr__(first.binding.account_key, "account_id", "tampered")
    object.__setattr__(first.binding, "account_ref", "tampered")
    assert second.positions == (_row("1"),)
    assert second.binding == _binding()
    fresh = stream.projection()
    assert fresh.positions == (_row("1"),)
    assert fresh.binding == _binding()


def test_mutating_the_original_binding_does_not_change_the_stream():
    binding, verifier, _, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier))
    object.__setattr__(binding.account_key, "account_id", "other-account")
    object.__setattr__(binding, "account_ref", "other-account")
    assert stream.projection().binding == _binding()


@pytest.mark.parametrize("phase", ["binding", "snapshot", "event", "live"])
def test_verifier_mutating_its_detached_inputs_is_rejected(phase):
    binding = _binding()

    class MutatingVerifier(_Verifier):
        target = None

        def verify_binding(self, value):
            accepted = super().verify_binding(value)
            if self.target == "binding":
                object.__setattr__(value.account_key, "account_id", "adapter-mutated")
            return accepted

        def verify_snapshot(self, value, snapshot):
            accepted = super().verify_snapshot(value, snapshot)
            if self.target == "snapshot":
                object.__setattr__(snapshot.positions[0], "quantity", "999")
            return accepted

        def verify_event(self, value, event):
            accepted = super().verify_event(value, event)
            if self.target == "event":
                object.__setattr__(event.binding, "account_ref", "adapter-mutated")
            return accepted

        def verify_live(self, value, now_ns):
            accepted = super().verify_live(value, now_ns)
            if self.target == "live":
                object.__setattr__(value, "account_ref", "adapter-mutated")
            return accepted

    verifier = MutatingVerifier(binding)
    if phase == "binding":
        verifier.target = phase
        with pytest.raises(PositionBootstrapError, match="VERIFIER_MUTATED_INPUT"):
            PositionStreamBootstrap(binding, verifier, clock_ns=lambda: 100)
        assert binding == _binding()
        return
    stream = PositionStreamBootstrap(binding, verifier, clock_ns=lambda: 100)
    if phase != "snapshot":
        stream.install_snapshot(_snapshot(binding, verifier, rows=[_row("1")]))
    verifier.target = phase
    with pytest.raises(PositionBootstrapError, match="VERIFIER_MUTATED_INPUT"):
        if phase == "snapshot":
            stream.install_snapshot(_snapshot(binding, verifier, rows=[_row("1")]))
        elif phase == "event":
            stream.push(_event(binding, verifier, 11, "2"))
        else:
            stream.projection()
    assert binding == _binding()


def test_empty_snapshot_requires_explicit_verifier_attestation():
    binding, verifier, _, stream = _build()
    unverified = PositionStreamSnapshot(binding, 5, 90, "fake-claim", ())
    with pytest.raises(PositionBootstrapError, match="VERIFIER_REJECTED"):
        stream.install_snapshot(unverified)
    with pytest.raises(PositionBootstrapError):
        stream.projection()
    binding, verifier, _, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier, cut=5))
    assert stream.projection().positions == ()


@pytest.mark.parametrize("case", ["wrong_binding", "wrong_account", "wrong_epoch", "unverified"])
def test_rejects_unbound_or_unverified_events(case):
    binding, verifier, _, stream = _build()
    if case == "wrong_binding":
        event = _event(replace(binding, route_ref="another-route"), verifier, 11)
    elif case == "wrong_account":
        event = _event(replace(binding, account_ref="account-b"), verifier, 11)
    elif case == "wrong_epoch":
        event = _event(replace(binding, subscription_epoch=2), verifier, 11)
    else:
        event = PositionStreamEvent(binding, 11, 95, "not-verified", _row())
    with pytest.raises(PositionBootstrapError):
        stream.push(event)
    with pytest.raises(PositionBootstrapError):
        stream.projection()


@pytest.mark.parametrize("seqs", [(11, 13), (11, 11), (12, 11)])
def test_rejects_gaps_duplicates_and_out_of_order_before_snapshot(seqs):
    binding, verifier, _, stream = _build()
    stream.push(_event(binding, verifier, seqs[0]))
    with pytest.raises(PositionBootstrapError, match="EVENT_SEQUENCE_GAP"):
        stream.push(_event(binding, verifier, seqs[1]))


def test_rejects_gap_between_cut_and_buffer():
    binding, verifier, _, stream = _build()
    stream.push(_event(binding, verifier, 12))
    with pytest.raises(PositionBootstrapError, match="SNAPSHOT_STREAM_GAP"):
        stream.install_snapshot(_snapshot(binding, verifier, cut=10))


def test_rejects_overflow_and_poisons_stream():
    binding, verifier, _, stream = _build(max_buffer=1)
    stream.push(_event(binding, verifier, 11))
    with pytest.raises(PositionBootstrapError, match="BUFFER_OVERFLOW"):
        stream.push(_event(binding, verifier, 12))
    with pytest.raises(PositionBootstrapError):
        stream.install_snapshot(_snapshot(binding, verifier))


def test_wrong_scope_duplicate_and_row_limit_rejected():
    binding, verifier, _, stream = _build()
    with pytest.raises(PositionBootstrapError, match="POSITION_SCOPE_MISMATCH"):
        stream.install_snapshot(_snapshot(binding, verifier, rows=[_row(venue="other")]))
    binding, verifier, _, stream = _build()
    with pytest.raises(PositionBootstrapError, match="DUPLICATE_SNAPSHOT_ROW"):
        stream.install_snapshot(_snapshot(binding, verifier, rows=[_row(), _row("2")]))
    binding, verifier, _, stream = _build(max_rows=1)
    stream.install_snapshot(_snapshot(binding, verifier, rows=[_row()]))
    with pytest.raises(PositionBootstrapError, match="ROW_LIMIT_EXCEEDED"):
        stream.push(_event(binding, verifier, 11, instrument="RB2701"))


@pytest.mark.parametrize("kind", ["event", "snapshot"])
def test_binding_changed_between_outer_check_and_copy_is_rejected(kind):
    binding, verifier, _, stream = _build()
    wrong = replace(binding, account_ref="account-b")
    if kind == "event":
        source = _event(binding, verifier, 11)
        original = stream._copy_event

        def changed(value):
            if value is source:
                object.__setattr__(value, "binding", wrong)
            return original(value)

        stream._copy_event = changed
        with pytest.raises(PositionBootstrapError, match="EVENT_BINDING_INVALID"):
            stream.push(source)
    else:
        source = _snapshot(binding, verifier)
        original = stream._copy_snapshot

        def changed(value):
            if value is source:
                object.__setattr__(value, "binding", wrong)
            return original(value)

        stream._copy_snapshot = changed
        with pytest.raises(PositionBootstrapError, match="SNAPSHOT_BINDING_INVALID"):
            stream.install_snapshot(source)


def test_snapshot_growth_between_outer_check_and_copy_is_bounded():
    binding, verifier, _, stream = _build(max_rows=1)
    source = _snapshot(binding, verifier, rows=[_row()])
    original = stream._copy_snapshot

    def changed(value):
        if value is source:
            object.__setattr__(value, "positions", (_row(), _row(instrument="RB2701")))
        return original(value)

    stream._copy_snapshot = changed
    with pytest.raises(PositionBootstrapError, match="ROW_LIMIT_EXCEEDED"):
        stream.install_snapshot(source)


def test_expiry_verifier_revocation_and_clock_reversal_poison_projection():
    binding, verifier, now, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier))
    verifier.live = False
    with pytest.raises(PositionBootstrapError, match="VERIFIER_REJECTED"):
        stream.projection()
    binding, verifier, now, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier))
    now[0] = 200
    with pytest.raises(PositionBootstrapError, match="LEASE_EXPIRED"):
        stream.projection()
    binding, verifier, now, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier))
    now[0] = 99
    with pytest.raises(PositionBootstrapError, match="CLOCK_REVERSED"):
        stream.projection()


def test_lease_expiring_during_liveness_verification_cannot_be_published():
    binding, verifier, now, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier))
    original = verifier.verify_live

    def slow_verify(*args):
        accepted = original(*args)
        now[0] = 200
        return accepted

    verifier.verify_live = slow_verify
    with pytest.raises(PositionBootstrapError, match="LEASE_EXPIRED"):
        stream.projection()


def test_disconnect_during_inflight_snapshot_prevents_publication():
    binding, verifier, _, stream = _build()
    entered = threading.Event()
    release = threading.Event()
    snapshot = _snapshot(binding, verifier)
    original = verifier.verify_snapshot

    def blocking_verify(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    verifier.verify_snapshot = blocking_verify
    errors = []

    def install():
        try:
            stream.install_snapshot(snapshot)
        except PositionBootstrapError as error:
            errors.append(str(error))

    worker = threading.Thread(target=install)
    worker.start()
    assert entered.wait(2)
    closer = threading.Thread(target=stream.interrupt)
    closer.start()
    assert stream._cancelled.wait(2)
    release.set()
    worker.join(2)
    closer.join(2)
    assert not worker.is_alive() and not closer.is_alive()
    assert errors == ["STREAM_INTERRUPTED"]
    with pytest.raises(PositionBootstrapError):
        stream.projection()


def test_disconnect_during_projection_copy_prevents_publication():
    binding, verifier, _, stream = _build()
    stream.install_snapshot(_snapshot(binding, verifier, rows=[_row("1")]))
    entered = threading.Event()
    release = threading.Event()
    original = stream._row

    def blocking_row(row):
        entered.set()
        assert release.wait(2)
        return original(row)

    stream._row = blocking_row
    results = []
    errors = []

    def read():
        try:
            results.append(stream.projection())
        except PositionBootstrapError as error:
            errors.append(str(error))

    worker = threading.Thread(target=read)
    worker.start()
    assert entered.wait(2)
    closer = threading.Thread(target=stream.interrupt)
    closer.start()
    assert stream._cancelled.wait(2)
    release.set()
    worker.join(2)
    closer.join(2)
    assert not worker.is_alive() and not closer.is_alive()
    assert results == []
    assert errors == ["STREAM_INTERRUPTED"]


def test_concurrent_event_cannot_be_hidden_by_snapshot_publication():
    binding, verifier, _, stream = _build()
    entered = threading.Event()
    release = threading.Event()
    original = stream._push

    def delayed(event):
        entered.set()
        assert release.wait(2)
        return original(event)

    stream._push = delayed
    worker = threading.Thread(target=stream.push, args=(_event(binding, verifier, 11, "2"),))
    worker.start()
    assert entered.wait(2)
    stream.install_snapshot(_snapshot(binding, verifier, cut=10, rows=[_row("1")]))
    with pytest.raises(PositionBootstrapError, match="EVENTS_PENDING"):
        stream.projection()
    release.set()
    worker.join(2)
    assert not worker.is_alive()
    assert stream.projection().positions == (_row("2"),)


def _isolated_reconstruction(result):
    binding, verifier, _, stream = _build()
    stream.push(_event(binding, verifier, 11, "2"))
    stream.install_snapshot(_snapshot(binding, verifier, cut=10, rows=[_row("1")]))
    projection = stream.projection()
    result.put(
        (projection.binding.account_ref, projection.sequence, projection.positions[0].quantity)
    )


def test_independent_process_reconstructs_only_its_own_source_assertion():
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    child = context.Process(target=_isolated_reconstruction, args=(result,))
    child.start()
    try:
        assert result.get(timeout=10) == ("account-a", 11, "2")
        child.join(10)
        assert child.exitcode == 0
    finally:
        if child.is_alive():
            child.terminate()
            child.join(2)
        result.close()
