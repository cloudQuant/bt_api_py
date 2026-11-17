"""Synthetic CTP SPI callbacks through the original Gateway SQLite journal."""
from types import SimpleNamespace

import pytest

from bt_api_py.ctp_gateway_return_maintenance import CtpGatewayReturnMaintenance
from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order, _trade

pytest_plugins = ("tests.integration.test_iter63_ctp_gateway_return_maintenance",)


def selected(real_owner):
    old, session, native, journal, instrument, helper = real_owner
    guard = old._ledger._guard
    journal.enable_controls(guard)
    journal.enable_strategy_ledger(guard)
    journal.enable_account_extensions(guard)
    journal.enable_parent_authority(guard)
    journal.enable_account_facts_financial(guard)
    value = CtpGatewayReturnMaintenance(session, session.direct_provider, journal, guard,
        old._account_key, old._settings, observed_order_source=True)
    return value, session, native, journal, helper


def test_source_records_unattributed_order_and_duplicate_before_ack(real_owner):
    value, session, native, journal, helper = selected(real_owner)
    order = _order(TradingDay=helper.TODAY)
    native.client._spi.OnRtnOrder(SimpleNamespace(**order))
    native.client._spi.OnRtnOrder(SimpleNamespace(**order))
    value.poll()
    assert session.return_buffer.status().record_count == 0
    progress = value.observed_order_source_status()
    assert progress.status == 'UNVERIFIED' and progress.source_prefix == 2
    with journal._transaction() as db:
        facts = db.execute('SELECT disposition FROM direct_execution_facts').fetchall()
        receipts = db.execute('SELECT source_sequence,fact_digest,gap_reason FROM observed_order_source_receipts ORDER BY source_sequence').fetchall()
    assert len(facts) == 1 and facts[0][0] == 'UNATTRIBUTED'
    assert [row[0] for row in receipts] == [1, 2]
    assert receipts[0][1] == receipts[1][1] and receipts[0][1] is not None
    assert all(row[2] is None for row in receipts)


def test_unknown_venue_is_durable_gap_not_silent_skip(real_owner):
    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY, ExchangeID='UNKNOWN')))
    value.poll()
    assert session.return_buffer.status().record_count == 1
    progress = value.observed_order_source_status()
    assert progress.status == 'GAP' and progress.source_prefix == 0 and progress.last_seen == 1
    with journal._transaction() as db:
        receipt = db.execute('SELECT fact_digest,gap_reason FROM observed_order_source_receipts').fetchone()
    assert tuple(receipt) == (None, 'PROJECTION_UNAVAILABLE')


def test_fact_commit_then_receipt_failure_retains_ticket_and_replays(real_owner, monkeypatch):
    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    ticket = session.return_buffer.offer()
    original = journal.record_observed_order_result
    monkeypatch.setattr(journal, 'record_observed_order_result', lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError()))
    value.poll()
    assert session.return_buffer.status().record_count == 1
    assert session.return_buffer.offer() is ticket
    with journal._transaction() as db:
        assert db.execute('SELECT count(*) FROM direct_execution_facts').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM observed_order_source_receipts').fetchone()[0] == 0
    monkeypatch.setattr(journal, 'record_observed_order_result', original)
    value.poll()
    assert session.return_buffer.status().record_count == 0
    assert value.observed_order_source_status().source_prefix == 1


def test_gap_write_failure_retains_unprojectable_ticket(real_owner, monkeypatch):
    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY, ExchangeID='UNKNOWN')))
    ticket = session.return_buffer.offer()
    monkeypatch.setattr(journal, 'record_observed_order_result', lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError()))
    value.poll()
    assert session.return_buffer.offer() is ticket
    assert session.return_buffer.status().record_count == 1


def test_source_receipts_include_trade_and_bad_error_callback(real_owner):
    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnTrade(SimpleNamespace(**_trade(TradingDay=helper.TODAY)))
    native.client._spi.OnRspOrderInsert(SimpleNamespace(**_order(TradingDay=helper.TODAY)),
        SimpleNamespace(ErrorID=1, ErrorMsg=''), 7, True)
    value.poll()
    assert session.return_buffer.status().record_count == 2
    progress = value.observed_order_source_status()
    assert (progress.status, progress.source_prefix, progress.last_seen) == ('GAP', 1, 2)
    with journal._transaction() as db:
        receipts = db.execute('SELECT fact_digest,gap_reason FROM observed_order_source_receipts ORDER BY source_sequence').fetchall()
    assert receipts[0][0] is not None and receipts[0][1] is None
    assert tuple(receipts[1]) == (None, 'PROJECTION_UNAVAILABLE')


def test_unknown_receipt_commit_replays_without_double_advance(real_owner, monkeypatch):
    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    original = journal.record_observed_order_result
    def commit_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('synthetic uncertain commit')
    monkeypatch.setattr(journal, 'record_observed_order_result', commit_then_fail)
    value.poll()
    assert session.return_buffer.status().record_count == 1
    monkeypatch.setattr(journal, 'record_observed_order_result', original)
    value.poll()
    assert session.return_buffer.status().record_count == 0
    assert value.observed_order_source_status().source_prefix == 1
    with journal._transaction() as db:
        assert db.execute('SELECT count(*) FROM observed_order_source_receipts').fetchone()[0] == 1


def test_prior_drained_tail_is_explicit_unverified_base(real_owner):
    old, session, native, journal, _, helper = real_owner
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    old.poll()
    assert session.return_buffer.status().record_count == 0
    value, _, _, _, _ = selected(real_owner)
    start = value.observed_order_source_status()
    assert (start.base_sequence, start.source_prefix, start.status) == (1, 1, 'UNVERIFIED')
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    value.poll()
    assert (value.observed_order_source_status().base_sequence,
            value.observed_order_source_status().source_prefix) == (1, 2)


def test_pending_before_selection_is_not_skipped(real_owner):
    _, session, native, _, _, helper = real_owner
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    value, _, _, journal, _ = selected(real_owner)
    assert value.observed_order_source_status().base_sequence == 0
    value.poll()
    assert session.return_buffer.status().record_count == 0
    with journal._transaction() as db:
        assert db.execute('SELECT source_sequence FROM observed_order_source_receipts').fetchone()[0] == 1


def test_gap_seen_before_ack_cannot_be_lost_on_storage_failure(real_owner, monkeypatch):
    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    ticket = session.return_buffer.offer()
    original = value._ingestor.ingest_callbacks
    def mark_gap_after_ingestion(*args, **kwargs):
        result = original(*args, **kwargs)
        session.return_buffer._mark_gap('capacity_overflow')
        return result
    monkeypatch.setattr(value._ingestor, 'ingest_callbacks', mark_gap_after_ingestion)
    monkeypatch.setattr(journal, 'invalidate_observed_order_source', lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError()))
    value.poll()
    assert session.return_buffer.offer() is ticket
    assert session.return_buffer.status().record_count == 1


def test_relogin_invalidates_old_source_and_starts_new_unverified(real_owner):
    from tests.integration.test_iter63_ctp_gateway_return_maintenance import _relogin_same_spi
    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    value.poll()
    old = value.observed_order_source_status()
    _relogin_same_spi(native, helper.TODAY, 20)
    session.refresh_session()
    value.poll()
    new = value.observed_order_source_status()
    assert new.session_ref != old.session_ref
    assert (new.status, new.base_sequence, new.source_prefix) == ('UNVERIFIED', 1, 1)
    prior = journal.read_observed_order_source(value._ledger._guard,
        source_ref=old.source_ref, session_ref=old.session_ref)
    assert prior.status == 'GAP'


def test_source_digest_contains_complete_raw_record_and_rejects_changed_replay(real_owner, monkeypatch):
    import hashlib
    import json
    from dataclasses import fields
    from types import MappingProxyType

    from bt_api_ctp.gateway_return_buffer import CtpGatewayReturnRecord

    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    ticket = session.return_buffer.offer()
    record, = ticket.records
    source = value._observed_order_source
    wire = {f.name: dict(record.row) if f.name == 'row' else getattr(record, f.name)
            for f in fields(CtpGatewayReturnRecord)}
    expected = hashlib.sha256(json.dumps(wire, ensure_ascii=True, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('ascii')).hexdigest()
    assert source.record_digest(record) == expected
    original = journal.record_observed_order_result
    def commit_then_fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('uncertain')
    monkeypatch.setattr(journal, 'record_observed_order_result', commit_then_fail)
    value.poll()
    assert session.return_buffer.offer() is ticket
    assert value.observed_order_source_status().status == 'UNVERIFIED'
    monkeypatch.setattr(journal, 'record_observed_order_result', original)
    # A wrong fact/gap argument does not establish a source-record conflict.
    from bt_api_gateway.direct_command_journal import DirectCommandJournalError
    with pytest.raises(DirectCommandJournalError, match='OBSERVED_SOURCE_CONFLICT'):
        source.record(record, source_record_digest=expected)
    assert value.observed_order_source_status().status == 'UNVERIFIED'
    object.__setattr__(record, 'row', MappingProxyType(dict(record.row, UserProductInfo='changed')))
    value.poll()
    assert session.return_buffer.offer() is ticket
    assert value.observed_order_source_status().status == 'GAP'
    assert _reopened_source_status(value, journal).status == 'GAP'
    with journal._transaction() as db:
        assert db.execute('SELECT count(*) FROM observed_order_source_receipts').fetchone()[0] == 1


def test_unencodable_record_does_not_ack_or_write_receipt(real_owner):
    from types import MappingProxyType
    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    ticket = session.return_buffer.offer()
    record, = ticket.records
    object.__setattr__(record, 'row', MappingProxyType(dict(record.row, corrupt=float('nan'))))
    value.poll()
    assert session.return_buffer.offer() is ticket
    assert value.observed_order_source_status().status == 'GAP'
    assert _reopened_source_status(value, journal).status == 'GAP'
    with journal._transaction() as db:
        assert db.execute('SELECT count(*) FROM observed_order_source_receipts').fetchone()[0] == 0


def _reopened_source_status(value, journal):
    from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal

    source = value._observed_order_source
    binding = source._binding
    reopened = GatewayDirectCommandJournal(journal._identity.path, journal.account_scope,
        generation=journal.generation, expected_journal_id=journal.journal_id, minimum_revision=0,
        controls_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True,
        parent_authority_enabled=True, account_facts_financial_enabled=True,
        observed_order_source_enabled=True)
    return reopened.read_observed_order_source(value._ledger._guard,
        source_ref=binding.source_ref, session_ref=binding.generation)


def test_invalid_foreign_argument_does_not_invalidate_source(real_owner):
    value, session, native, journal, helper = selected(real_owner)
    with pytest.raises(ValueError, match='OBSERVED_ORDER_RECORD_INVALID'):
        value._observed_order_source.record_digest(object())
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    record, = session.return_buffer.offer().records
    with pytest.raises(ValueError, match='OBSERVED_ORDER_RECORD_CHANGED'):
        value._observed_order_source.record(record, source_record_digest='0' * 64)
    assert value.observed_order_source_status().status == 'UNVERIFIED'
    assert _reopened_source_status(value, journal).status == 'UNVERIFIED'


def test_held_record_source_drift_marks_original_source_gap(real_owner):
    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    ticket = session.return_buffer.offer()
    record, = ticket.records
    object.__setattr__(record, 'source_ref', 'foreign-source')
    value.poll()
    assert session.return_buffer.offer() is ticket
    assert value.observed_order_source_status().status == 'GAP'
    assert _reopened_source_status(value, journal).status == 'GAP'


def test_session_drift_before_offer_invalidates_original_source(real_owner, monkeypatch):
    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    monkeypatch.setattr(value._ingestor, 'assert_session_current',
                        lambda: (_ for _ in ()).throw(RuntimeError('synthetic source loss')))
    value.poll()
    assert session.return_buffer.status().record_count == 1
    assert value.observed_order_source_status().status == 'GAP'
    assert _reopened_source_status(value, journal).status == 'GAP'


def test_invalidation_failure_does_not_replace_receipt_conflict(real_owner, monkeypatch):
    from bt_api_gateway.direct_command_journal import DirectCommandJournalError

    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    ticket = session.return_buffer.offer()
    record, = ticket.records
    source = value._observed_order_source
    digest = source.record_digest(record)
    def conflict(*_args, **_kwargs):
        raise DirectCommandJournalError('OBSERVED_SOURCE_RECORD_CONFLICT')
    def invalidation_failed(*_args, **_kwargs):
        raise RuntimeError('synthetic invalidation failure')
    monkeypatch.setattr(journal, 'record_observed_order_result', conflict)
    monkeypatch.setattr(journal, 'invalidate_observed_order_source', invalidation_failed)
    with pytest.raises(DirectCommandJournalError, match='OBSERVED_SOURCE_RECORD_CONFLICT'):
        source.record(record, source_record_digest=digest)
    value.poll()
    assert session.return_buffer.offer() is ticket


def test_unconfigured_valid_future_is_retained(real_owner):
    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY, InstrumentID='ag2701')))
    value.poll()
    assert session.return_buffer.status().record_count == 0
    assert value.observed_order_source_status().source_prefix == 1
    with journal._transaction() as db:
        assert db.execute("SELECT count(*) FROM direct_execution_facts WHERE disposition='UNATTRIBUTED'").fetchone()[0] == 1
        assert db.execute('SELECT gap_reason FROM observed_order_source_receipts').fetchone()[0] is None


def test_unprojectable_callback_retains_ticket_and_closes_existing_write_fence(real_owner):
    from bt_api_py.gateway_direct_service import GatewayReservationAdmissionDenied
    from tests.integration.test_iter63_ctp_gateway_return_maintenance import _pair

    value, session, native, journal, helper = selected(real_owner)
    _pair(value)
    assert value.dispatch_ready() and value.ledger_continuous()
    native.client._spi.OnRspOrderInsert(SimpleNamespace(**_order(TradingDay=helper.TODAY)),
        SimpleNamespace(ErrorID=1, ErrorMsg=''), 7, True)
    ticket = session.return_buffer.offer()
    value.poll()
    assert session.return_buffer.offer() is ticket
    assert session.return_buffer.status().record_count == 1
    assert value.observed_order_source_status().status == 'GAP'
    assert _reopened_source_status(value, journal).status == 'GAP'
    assert value.status() == {'status': 'OBSERVED_SOURCE_GAP', 'reservation_open': False}
    assert value.ledger_continuous() is False
    assert value.ledger_discontinuity() == 'OBSERVED_SOURCE_GAP'
    assert not value.dispatch_ready()
    with pytest.raises(GatewayReservationAdmissionDenied), value.reservation_fence(None, None):
        pass
    value.poll()
    assert session.return_buffer.offer() is ticket


def test_begin_committed_then_session_check_failure_keeps_and_invalidates_owner(real_owner, monkeypatch):
    from bt_api_ctp.gateway_return_query import CtpGatewayReturnQueryReader

    _, _, _, journal, _, _ = real_owner
    original_begin = journal.begin_observed_order_source
    original_check = CtpGatewayReturnQueryReader.assert_session_current
    def committed_then_stale(*args, **kwargs):
        result = original_begin(*args, **kwargs)
        monkeypatch.setattr(CtpGatewayReturnQueryReader, 'assert_session_current',
                            lambda _: (_ for _ in ()).throw(RuntimeError('synthetic source loss')))
        return result
    monkeypatch.setattr(journal, 'begin_observed_order_source', committed_then_stale)
    value, session, _, _, _ = selected(real_owner)
    monkeypatch.setattr(CtpGatewayReturnQueryReader, 'assert_session_current', original_check)
    assert value._source_start_failed and value._observed_order_source is not None
    assert value.observed_order_source_status().status == 'GAP'
    assert _reopened_source_status(value, journal).status == 'GAP'
    assert value.status()['reservation_open'] is False
    assert session.return_buffer.status().record_count == 0


def test_uncertain_begin_and_invalidation_failure_retains_original_owner_for_retry(real_owner, monkeypatch):
    _, _, _, journal, _, _ = real_owner
    original_begin = journal.begin_observed_order_source
    original_invalidate = journal.invalidate_observed_order_source
    def committed_then_unknown(*args, **kwargs):
        original_begin(*args, **kwargs)
        raise RuntimeError('synthetic uncertain begin')
    def invalidate_failed(*_args, **_kwargs):
        raise RuntimeError('synthetic uncertain invalidation')
    monkeypatch.setattr(journal, 'begin_observed_order_source', committed_then_unknown)
    monkeypatch.setattr(journal, 'invalidate_observed_order_source', invalidate_failed)
    value, _, _, _, _ = selected(real_owner)
    source = value._observed_order_source
    assert value._source_start_failed and source._invalidation_pending
    assert value.close(timeout_seconds=0) is False
    assert value._observed_order_source is source and source._invalidation_pending
    monkeypatch.setattr(journal, 'invalidate_observed_order_source', original_invalidate)
    assert value.close(timeout_seconds=0) is True
    assert not source._invalidation_pending
    assert _reopened_source_status(value, journal).status == 'GAP'
