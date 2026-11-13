"""Unproven CTP cuts stay unusable, using only fake sessions and a local journal."""

import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from bt_api_base.direct_cancel_protocol import PreparedCancelRequest
from bt_api_base.direct_write_protocol import NativeOrderIdentity, PreDispatchRecord, WriteOperation
from bt_api_gateway.direct_cancel_dispatch import GatewayDirectCancelDispatcher
from bt_api_gateway.direct_cancel_journal import GatewayDirectCancelJournal
from bt_api_gateway.direct_command_journal import (
    DirectCommandJournalError,
    GatewayDirectCommandJournal,
)
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome, _identity
from bt_api_gateway.strategy_position_ledger import (
    ExternalBaselinePosition,
    StrategyBaselineEvidence,
)

from tests.unit.test_iter63_ctp_gateway_strategy_ledger import (
    D1,
    RB,
    ROUTE,
    baseline,
    build,
    order,
    trade_before_ledger,
)
from tests.unit.test_iter63_ctp_gateway_strategy_ledger import env as legacy_env
from tests.unit.test_iter63_e130b_strategy_ledger_reads import HeldSession

env = legacy_env


def _legacy_baseline(values):
    """Create a legacy row with an explicitly synthetic historical verifier."""
    now = time.time_ns()
    evidence = StrategyBaselineEvidence(
        "acct", values.journal.journal_id, D1, ROUTE, values.journal.generation, 3,
        "ctp.td.account-query", "p", "o", "ctp-return-buffer:3:7",
        ("open_orders", "positions"), now, now + 10_000_000_000,
        True, True, True, True,
    )
    values.journal.initialize_empty_strategy_ledger(
        evidence, guard=values.guard, baseline_fence=nullcontext,
        verify_current_empty=lambda item: item is evidence,
    )
    return evidence.to_mapping()


def test_delayed_callback_cannot_initialize_or_reserve(env):
    env.session = HeldSession()
    env.journal.enable_strategy_ledger(env.guard)
    ledger = build(env)
    ledger.maintain()
    assert (ledger.state, ledger.ready, env.session.begins) == ("SOURCE_CUT_UNPROVEN", False, 0)
    assert baseline(env) is None

    # Even if an already-started query returns an old empty snapshot while a
    # venue callback is still in flight, direct completion cannot mint a cut.
    before = env.session.return_buffer.status()
    observation = SimpleNamespace(
        observation_id="old-snapshot", positions=(), open_orders=(),
        positions_complete=True, open_orders_complete=True,
    )
    current = env.journal.read_strategy_ledger_state(guard=env.guard)
    assert ledger._baseline_from_read(current, before, 3, D1, observation) == "SOURCE_CUT_UNPROVEN"
    # Emulate a request already in progress from an older owner tick: the
    # reply is ready, but the later venue callback is not in the local buffer.
    ledger._begin_account_read("baseline", before, 3, D1, current)
    env.session.answer_now = True
    ledger.maintain()
    assert env.session.cancels == 1 and env.session.reads == 0 and not ledger.read_pending
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_LEDGER_UNAVAILABLE"):
        env.journal.reserve("s", "new", "p", order(),
                            reservation_fence=lambda: ledger.reservation_fence(None, None))
    assert baseline(env) is None
    env.session.return_buffer.last_sequence += 1  # the delayed callback arrives later
    ledger.maintain()
    assert not ledger.ready and baseline(env) is None


def test_legacy_baseline_reopen_remains_untrusted_and_unchanged(env, tmp_path):
    env.journal.enable_strategy_ledger(env.guard)
    old = _legacy_baseline(env)
    # This row represents an existing reservation made under the legacy gate.
    existing = env.journal.reserve("s", "old", "p", order())
    journal_id, generation = env.journal.journal_id, env.journal.generation
    reopened = GatewayDirectCommandJournal(
        tmp_path / "db", "acct", generation=generation, expected_journal_id=journal_id,
        minimum_revision=0, controls_enabled=True, strategy_ledger_enabled=True,
    )
    env.journal = reopened
    ledger = build(env)
    ledger.maintain()
    assert ledger.state == "SOURCE_CUT_UNPROVEN" and not ledger.ready
    assert baseline(env) == old
    assert reopened.observe("s", "old", "p").record.reservation_id == existing.reservation_id
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_LEDGER_UNAVAILABLE"):
        reopened.reserve("s", "new", "p", order(),
                         reservation_fence=lambda: ledger.reservation_fence(None, None))
    close = order("sell", "close_today")
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_LEDGER_UNAVAILABLE"):
        reopened.reserve("s", "new-close", "p", close,
                         reservation_fence=lambda: ledger.reservation_fence(None, None))
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_LEDGER_UNAVAILABLE"):
        reopened.read_strategy_position("s", order(), expected_instrument=RB)
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_LEDGER_UNAVAILABLE"):
        reopened.read_strategy_close_capacity("s", close, expected_instrument=RB)
    released = reopened.release("s", "old", existing.reservation_id, existing.digest,
                                "p", "release-old", existing.version)
    assert released.status == "RELEASED"
    assert baseline(env) == old


def test_cached_ready_and_comparison_cut_are_never_accepted(env):
    env.journal.enable_strategy_ledger(env.guard)
    env.journal.enable_account_extensions(env.guard)
    ledger = build(env, comparison_refresh_seconds=1.0)
    ledger.install_comparison()
    ledger._ready = True  # represent a stale cached success in an existing owner
    ledger._state = "PRESENT"
    ledger._comparison_results = {"rb2701": "MATCH", "SA701": "MISMATCH:1"}
    ledger._account_cut = {"sequence": 7, "generation": 3, "day": D1,
                           "taken_at": time.monotonic(), "ref": "old", "nets": {}}
    assert not ledger.ready and ledger.state == "SOURCE_CUT_UNPROVEN" and not ledger._cut_current()
    assert ledger.comparison_state == {"rb2701": "UNAVAILABLE", "SA701": "UNAVAILABLE"}
    assert not ledger.verify_current_continuity(None, "acct", ledger.resolve_binding(order()), D1, None)
    assert not ledger.verify_external_cut_current(None, "acct", RB, D1, "old")
    with (pytest.raises(DirectCommandJournalError, match="STRATEGY_LEDGER_UNAVAILABLE"),
          ledger.reservation_fence(None, None)):
        pass
    assert env.journal.read_account_net_comparison(RB).status == "UNAVAILABLE"
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_LEDGER_UNAVAILABLE"):
        env.journal.read_external_net(RB)
    ledger.maintain()
    assert not ledger.ready and ledger._account_cut is None
    assert ledger.comparison_state == {"rb2701": "UNAVAILABLE", "SA701": "UNAVAILABLE"}


@pytest.mark.parametrize("kind", ["external", "replay"])
def test_other_legacy_baselines_are_preserved_but_not_trusted(env, tmp_path, kind):
    """Construct old fixture rows with explicit fake verifiers, not source proof."""
    if kind == "replay":
        trade_before_ledger(env)
    env.journal.enable_strategy_ledger(env.guard)
    position = ExternalBaselinePosition(RB, "long", "0", "1")
    now = time.time_ns()
    fields = {
        "account_scope": "acct", "journal_id": env.journal.journal_id, "trading_day": D1,
        "route": ROUTE, "owner_generation": env.journal.generation, "session_generation": 3,
        "source_ref": "ctp.td.account-query", "position_snapshot_ref": "p", "open_orders_snapshot_ref": "o",
        "cut_ref": "ctp-return-buffer:3:7", "coverage_refs": ("open_orders", "positions"),
        "observed_at_ns": now, "valid_until_ns": now + 10_000_000_000,
        "positions_complete": True, "open_orders_complete": True, "positions_empty": False, "open_orders_empty": True,
    }
    if kind == "external":
        evidence = StrategyBaselineEvidence(**fields, external_positions=(position,),
                                            operator_ref="operator", operation_id="operation")
        env.journal.initialize_external_strategy_ledger(
            evidence, guard=env.guard, baseline_fence=nullcontext,
            verify_authorized_snapshot=lambda actual: actual is evidence,
        )
    else:
        binding = build(env)
        env.journal.install_strategy_ledger_ports(
            guard=env.guard, resolve_binding=binding.resolve_binding,
            current_trading_day=lambda: D1, verify_day_transition=lambda *_: True,
            verify_current_continuity=lambda *_: True,
        )
        evidence = StrategyBaselineEvidence(**fields, schema_version=2,
                                            history_start_trading_day="20261110",
                                            account_positions=(position,))
        env.journal.initialize_replayed_strategy_ledger(
            evidence, guard=env.guard, baseline_fence=nullcontext,
            verify_current_snapshot=lambda actual: actual is evidence,
        )
    stored = baseline(env)
    reopened = GatewayDirectCommandJournal(
        tmp_path / "db", "acct", generation=env.journal.generation,
        expected_journal_id=env.journal.journal_id, minimum_revision=0,
        controls_enabled=True, strategy_ledger_enabled=True,
    )
    env.journal = reopened
    ledger = build(env)
    ledger.maintain()
    assert (ledger.ready, ledger.state) == (False, "SOURCE_CUT_UNPROVEN")
    assert baseline(env) == stored
    with pytest.raises(DirectCommandJournalError, match="STRATEGY_LEDGER_UNAVAILABLE"):
        reopened.read_strategy_position("s", order(), expected_instrument=RB)


def test_source_gate_does_not_hide_a_real_install_failure(env, monkeypatch):
    env.journal.enable_strategy_ledger(env.guard)
    ledger = build(env)

    def fail_install():
        raise DirectCommandJournalError("INVALID_INPUT")

    monkeypatch.setattr(ledger, "install", fail_install)
    ledger.maintain()
    assert not ledger.ready and ledger.state == "REFUSED:INVALID_INPUT"


def test_partial_query_cancel_failure_latches_unknown_and_stops_new_reads(env, monkeypatch):
    env.session = HeldSession()
    env.journal.enable_strategy_ledger(env.guard)
    ledger = build(env)
    before = env.session.return_buffer.status()
    ledger._begin_account_read("baseline", before, 3, D1, {})
    request = env.session.request
    calls = []

    def fail(_request):
        calls.append(_request)
        # As with the provider, pending state may already be cleared before
        # native cancellation raises; a later no-op is not an acknowledgement.
        env.session.request = None
        raise RuntimeError("synthetic cancellation failure")

    monkeypatch.setattr(env.session, "cancel_read_account", fail)
    ledger.maintain()
    assert ledger.state == "FAILED:ACCOUNT_READ_CANCEL_UNCONFIRMED"
    assert ledger.read_pending and env.session.request is None and env.session.begins == 1
    monkeypatch.setattr(env.session, "cancel_read_account", lambda _request: None)
    ledger.maintain()
    assert ledger.state == "FAILED:ACCOUNT_READ_CANCEL_UNCONFIRMED" and ledger.read_pending
    assert calls == [request] and env.session.begins == 1


def test_existing_sent_order_status_and_cancel_remain_available(env):
    """The cut gate is not used as a substitute for cancel admission."""
    command = env.journal.reserve("s", "old", "p", order())
    identity = NativeOrderIdentity(ROUTE, "native-ref", {"trading_day": D1}, 1)
    native = _identity(identity, ROUTE)
    args = ("s", "old", command.reservation_id, command.digest, "p")
    started = env.journal.begin_dispatch(*args, command.version, native, ("native-key",))
    env.journal.finish(*args, started.record.version, native, ("native-key",), "SENT")
    env.journal.enable_strategy_ledger(env.guard)
    ledger = build(env)
    ledger.maintain()
    assert not ledger.ready
    assert env.journal.observe("s", "old", "p").record.status == "SENT"

    cancels = GatewayDirectCancelJournal(env.journal)
    row = cancels.request("s", "cancel-1", "p", "old", command.reservation_id,
                          command.digest, target_identity=identity, symbol="rb2701")
    calls = []

    def native_cancel(target, symbol, port, scope):
        prepared = PreparedCancelRequest("provider-reservation", target, symbol, D1, 3,
                                         {"request_id": 12}, ("cancel-key",))
        marker = port.commit(PreDispatchRecord(WriteOperation.CANCEL, scope, ROUTE, symbol, None,
                                               target, {}, D1, 3, "synthetic", prepared))
        calls.append(symbol)
        return NativeDispatchOutcome(True, target, marker)

    direct = GatewayDirectDispatcher(env.journal, ROUTE, env.guard, lambda *_: None,
                                     lambda _: (), lambda _: True)
    dispatcher = GatewayDirectCancelDispatcher(cancels, direct, env.guard, ROUTE,
                                               native_cancel, lambda _: True)
    assert dispatcher.dispatch("s", "cancel-1", "p", expected_version=row.version).status == "SENT"
    assert calls == ["rb2701"]
