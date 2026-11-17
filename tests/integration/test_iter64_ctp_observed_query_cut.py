"""Real local provider query cut and journal, with a synthetic native SPI."""
import sqlite3
import threading
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

import pytest

from bt_api_py.ctp_gateway_observed_order_source import _QueryCutBusyError
from tests.integration.test_iter63_ctp_gateway_return_ingestion import _order
from tests.integration.test_iter64_ctp_observed_order_source import selected

pytest_plugins = ('tests.integration.test_iter63_ctp_gateway_return_maintenance',)


def query_cut(session, native, *, rows, callback=None):
    native.client._query_interval = 0
    def submit(_field, request_id):
        if callback is not None:
            callback()
        for row in rows:
            native.client._handle_query_callback('orders', row, None, request_id, False)
        native.client._handle_query_callback('orders', None, None, request_id, True)
        return 0
    native.ReqQryOrder = submit
    handle = session.begin_order_query_cut(timeout=1)
    result = session.poll_order_query_cut(handle)
    assert result is not None and result.complete
    assert session.verify_order_query_cut(handle, result) is not None
    return handle, result


def test_empty_query_cut_is_candidate_without_erasing_earlier_fact(real_owner):
    value, session, native, journal, helper = selected(real_owner)
    native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY)))
    value.poll()
    handle, result = query_cut(session, native, rows=[])
    candidate = value._observed_order_source.record_query_cut(handle=handle, result=result, fact_digests=())
    assert candidate.status == 'CANDIDATE' and candidate.query_fact_digests == ()
    with journal._transaction() as db:
        assert db.execute('SELECT count(*) FROM direct_execution_facts').fetchone()[0] == 1
    assert session.close_order_query_cut(handle)


def test_query_rows_and_pinned_receipts_require_contiguous_prefix(real_owner):
    value, session, native, _, helper = selected(real_owner)
    query_row = _order(TradingDay=helper.TODAY, OrderRef='000000000002')
    callback_row = _order(TradingDay=helper.TODAY, OrderRef='000000000003')
    handle, result = query_cut(session, native, rows=[query_row],
        callback=lambda: native.client._spi.OnRtnOrder(SimpleNamespace(**callback_row)))
    source = value._observed_order_source
    digest = source.ingest_query_order_row(value._ingestor, handle=handle, result=result, row_index=0)
    assert source.record_query_cut(handle=handle, result=result, fact_digests=(digest,)) is None
    assert session._order_query_cut is not None
    value.poll()
    assert session.return_buffer.status().record_count == 0
    assert source.verify_next_query_receipt(handle=handle, result=result) is True
    candidate = source.record_query_cut(handle=handle, result=result, fact_digests=(digest,))
    assert candidate.status == 'CANDIDATE' and candidate.query_fact_digests == (digest,)
    assert (candidate.begin_sequence, candidate.end_sequence) == (0, 1)
    assert session.close_order_query_cut(handle)


def test_forged_result_and_wrong_arguments_do_not_invalidate(real_owner):
    value, session, native, journal, helper = selected(real_owner)
    handle, result = query_cut(session, native, rows=[_order(TradingDay=helper.TODAY)])
    source = value._observed_order_source
    with pytest.raises(ValueError, match='ARGUMENT_INVALID'):
        source.ingest_query_order_row(value._ingestor, handle=handle, result=replace(result), row_index=0)
    with pytest.raises(ValueError, match='ARGUMENT_INVALID'):
        source.ingest_query_order_row(value._ingestor, handle=handle, result=result, row_index=1)
    assert source.status().status == 'UNVERIFIED'
    assert session.close_order_query_cut(handle)


def test_unprojectable_query_row_invalidates_without_closing_pin(real_owner):
    value, session, native, _, helper = selected(real_owner)
    handle, result = query_cut(session, native, rows=[_order(TradingDay=helper.TODAY, ExchangeID='UNKNOWN')])
    source = value._observed_order_source
    with pytest.raises(ValueError, match='PROJECTION_FAILED'):
        source.ingest_query_order_row(value._ingestor, handle=handle, result=result, row_index=0)
    assert source.status().status == 'GAP' and session._order_query_cut is not None
    assert session.close_order_query_cut(handle)


def test_post_commit_cut_invalidation_retains_pin_and_gap(real_owner, monkeypatch):
    value, session, native, _, helper = selected(real_owner)
    handle, result = query_cut(session, native, rows=[_order(TradingDay=helper.TODAY)])
    source = value._observed_order_source
    original = source._ledger.ingest
    def commit_then_change(fact):
        value = original(fact)
        monkeypatch.setattr(session, 'verify_order_query_cut', lambda *_: None)
        return value
    monkeypatch.setattr(source._ledger, 'ingest', commit_then_change)
    with pytest.raises(ValueError, match='QUERY_UNAVAILABLE'):
        source.ingest_query_order_row(value._ingestor, handle=handle, result=result, row_index=0)
    assert source.status().status == 'GAP' and session._order_query_cut is not None
    assert session.close_order_query_cut(handle)


def test_pinned_record_must_match_original_durable_receipt(real_owner):
    value, session, native, _, helper = selected(real_owner)
    callback_row = _order(TradingDay=helper.TODAY)
    handle, result = query_cut(session, native, rows=[],
        callback=lambda: native.client._spi.OnRtnOrder(SimpleNamespace(**callback_row)))
    value.poll()
    pinned = session.verify_order_query_cut(handle, result).records[0]
    object.__setattr__(pinned, 'row', MappingProxyType(dict(pinned.row, UserProductInfo='changed')))
    source = value._observed_order_source
    with pytest.raises(ValueError, match='RECEIPT_UNAVAILABLE'):
        source.verify_next_query_receipt(handle=handle, result=result)
    assert source.status().status == 'GAP' and session._order_query_cut is not None
    assert session.close_order_query_cut(handle)


def test_busy_query_lock_before_writes_is_pending(real_owner):
    value, session, native, journal, helper = selected(real_owner)
    handle, result = query_cut(session, native, rows=[_order(TradingDay=helper.TODAY)])
    source = value._observed_order_source
    acquired, release = threading.Event(), threading.Event()
    def hold_lock():
        with session._connection_owner.query_lock:
            acquired.set()
            release.wait(5)
    thread = threading.Thread(target=hold_lock)
    thread.start()
    try:
        assert acquired.wait(5)
        assert source.ingest_query_order_row(value._ingestor, handle=handle, result=result, row_index=0) is None
        assert source.record_query_cut(handle=handle, result=result, fact_digests=()) is None
        assert source.status().status == 'UNVERIFIED'
        with journal._transaction() as db:
            assert db.execute('SELECT count(*) FROM direct_execution_facts').fetchone()[0] == 0
    finally:
        release.set()
        thread.join(5)
    digest = source.ingest_query_order_row(value._ingestor, handle=handle, result=result, row_index=0)
    assert source.record_query_cut(handle=handle, result=result, fact_digests=(digest,)).status == 'CANDIDATE'
    assert session.close_order_query_cut(handle)


def test_busy_after_fact_commit_invalidates_and_retains_pin(real_owner, monkeypatch):
    value, session, native, _, helper = selected(real_owner)
    handle, result = query_cut(session, native, rows=[_order(TradingDay=helper.TODAY)])
    source = value._observed_order_source
    acquired, release = threading.Event(), threading.Event()
    def hold_lock():
        with session._connection_owner.query_lock:
            acquired.set()
            release.wait(5)
    thread = threading.Thread(target=hold_lock)
    original = source._ledger.ingest
    def commit_then_hold(fact):
        answer = original(fact)
        thread.start()
        assert acquired.wait(5)
        return answer
    monkeypatch.setattr(source._ledger, 'ingest', commit_then_hold)
    try:
        with pytest.raises(_QueryCutBusyError):
            source.ingest_query_order_row(value._ingestor, handle=handle, result=result, row_index=0)
        assert source.status().status == 'GAP' and session._order_query_cut is not None
    finally:
        release.set()
        thread.join(5)
    assert session.close_order_query_cut(handle)


def test_closed_staged_pin_is_not_reinterpreted_as_foreign_argument(real_owner):
    value, session, native, _, helper = selected(real_owner)
    handle, result = query_cut(session, native, rows=[_order(TradingDay=helper.TODAY)])
    source = value._observed_order_source
    digest = source.ingest_query_order_row(value._ingestor, handle=handle, result=result, row_index=0)
    assert session.close_order_query_cut(handle)
    with pytest.raises(ValueError):
        source.record_query_cut(handle=handle, result=result, fact_digests=(digest,))
    assert source.status().status == 'GAP'


def test_wrong_row_digests_do_not_invalidate_source(real_owner):
    value, session, native, _, helper = selected(real_owner)
    handle, result = query_cut(session, native, rows=[_order(TradingDay=helper.TODAY)])
    source = value._observed_order_source
    digest = source.ingest_query_order_row(value._ingestor, handle=handle, result=result, row_index=0)
    with pytest.raises(ValueError, match='ARGUMENT_INVALID'):
        source.record_query_cut(handle=handle, result=result, fact_digests=('0' * 64,))
    assert source.status().status == 'UNVERIFIED'
    assert source.record_query_cut(handle=handle, result=result, fact_digests=(digest,)).status == 'CANDIDATE'
    assert session.close_order_query_cut(handle)


def test_candidate_result_drift_after_commit_invalidates(real_owner, monkeypatch):
    value, session, native, _, _ = selected(real_owner)
    handle, result = query_cut(session, native, rows=[])
    source = value._observed_order_source
    original = source._journal.record_observed_order_query_cut
    def commit_then_misreport(*args, **kwargs):
        return replace(original(*args, **kwargs), query_digest='0' * 64)
    monkeypatch.setattr(source._journal, 'record_observed_order_query_cut', commit_then_misreport)
    with pytest.raises(ValueError, match='QUERY_UNAVAILABLE'):
        source.record_query_cut(handle=handle, result=result, fact_digests=())
    assert source.status().status == 'GAP' and session._order_query_cut is not None
    assert session.close_order_query_cut(handle)


def test_prior_session_gap_is_reported_without_blocking_local_candidate(real_owner):
    from bt_api_py.ctp_gateway_observed_order_source import CtpGatewayObservedOrderSource

    value, session, native, journal, helper = selected(real_owner)
    old_source = value._observed_order_source
    binding = old_source._binding
    # Synthetic earlier journal session with the same source reference. Real
    # CTP relogin currently changes source_ref and is not covered by this test.
    journal.begin_observed_order_source(old_source._guard, source_ref=binding.source_ref,
        session_ref='synthetic-prior-session', trading_day=binding.trading_day)
    journal.invalidate_observed_order_source(old_source._guard, source_ref=binding.source_ref,
        session_ref='synthetic-prior-session')
    source = CtpGatewayObservedOrderSource(session, value._ingestor._reader, value._ledger)
    source.start()
    value._observed_order_source = source
    value._ingestor._observed_order_source = source
    progress = source.status()
    assert source is not old_source and not source.blocked
    assert progress.status == 'UNVERIFIED' and progress.source_history_has_gap is True
    assert journal.read_observed_order_source(source._guard, source_ref=binding.source_ref,
        session_ref='synthetic-prior-session').status == 'GAP'
    callback_row = _order(TradingDay=helper.TODAY)
    handle, result = query_cut(session, native, rows=[],
        callback=lambda: native.client._spi.OnRtnOrder(SimpleNamespace(**callback_row)))
    value.poll()
    assert source.verify_next_query_receipt(handle=handle, result=result) is True
    candidate = source.record_query_cut(handle=handle, result=result, fact_digests=())
    assert candidate.status == 'CANDIDATE' and candidate.source_history_has_gap is True
    assert source.status().source_prefix == 1
    assert session.close_order_query_cut(handle)


def test_current_session_gap_refuses_candidate_and_closes_write_gate(real_owner):
    value, session, native, _, helper = selected(real_owner)
    handle, result = query_cut(session, native, rows=[])
    source = value._observed_order_source
    source.invalidate()
    with pytest.raises(ValueError, match='QUERY_UNAVAILABLE'):
        source.record_query_cut(handle=handle, result=result, fact_digests=())
    assert source.status().status == 'GAP'
    assert not value.dispatch_ready()
    assert session._order_query_cut is not None
    assert session.close_order_query_cut(handle)


def test_real_relogin_reports_old_source_gap_on_new_local_candidate(real_owner):
    from tests.integration.test_iter63_ctp_gateway_return_maintenance import _relogin_same_spi

    value, session, native, journal, helper = selected(real_owner)
    old_source = value._observed_order_source
    old_binding = old_source._binding
    old_source.invalidate()
    _relogin_same_spi(native, helper.TODAY, 20)
    session.refresh_session()
    value.poll()
    source = value._observed_order_source
    progress = source.status()
    assert source is not old_source and not source.blocked
    assert progress.source_ref != old_binding.source_ref
    assert progress.status == 'UNVERIFIED' and progress.source_history_has_gap is True
    assert journal.read_observed_order_source(source._guard, source_ref=old_binding.source_ref,
        session_ref=old_binding.generation).status == 'GAP'
    callback_row = _order(TradingDay=helper.TODAY)
    handle, result = query_cut(session, native, rows=[],
        callback=lambda: native.client._spi.OnRtnOrder(SimpleNamespace(**callback_row)))
    value.poll()
    assert source.verify_next_query_receipt(handle=handle, result=result) is True
    candidate = source.record_query_cut(handle=handle, result=result, fact_digests=())
    assert candidate.status == 'CANDIDATE' and candidate.source_history_has_gap is True
    assert source.status().source_prefix == 1
    assert session.close_order_query_cut(handle)


def test_pinned_receipts_are_verified_one_per_step(real_owner, monkeypatch):
    value, session, native, journal, helper = selected(real_owner)
    def callbacks():
        for number in range(3):
            native.client._spi.OnRtnOrder(SimpleNamespace(**_order(
                TradingDay=helper.TODAY, OrderRef=f'{number + 1:012d}')))
    handle, result = query_cut(session, native, rows=[], callback=callbacks)
    source = value._observed_order_source
    assert source.verify_next_query_receipt(handle=handle, result=result) is None
    value.poll()
    original = journal.verify_observed_order_receipt
    calls = []
    def counted(*args, **kwargs):
        calls.append(kwargs['source_sequence'])
        return original(*args, **kwargs)
    monkeypatch.setattr(journal, 'verify_observed_order_receipt', counted)
    assert source.verify_next_query_receipt(handle=handle, result=result) is False
    assert calls == [1]
    assert source.record_query_cut(handle=handle, result=result, fact_digests=()) is None
    assert source.verify_next_query_receipt(handle=handle, result=result) is False
    assert calls == [1, 2]
    assert source.verify_next_query_receipt(handle=handle, result=result) is True
    assert calls == [1, 2, 3]
    assert source.record_query_cut(handle=handle, result=result, fact_digests=()).status == 'CANDIDATE'
    assert calls == [1, 2, 3]
    assert session.close_order_query_cut(handle)


def test_previously_verified_pin_drift_is_rejected_before_commit(real_owner):
    value, session, native, _, helper = selected(real_owner)
    handle, result = query_cut(session, native, rows=[], callback=lambda:
        native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY))))
    value.poll()
    source = value._observed_order_source
    assert source.verify_next_query_receipt(handle=handle, result=result) is True
    pinned = session.verify_order_query_cut(handle, result).records[0]
    object.__setattr__(pinned, 'row', MappingProxyType(dict(pinned.row, UserProductInfo='changed')))
    with pytest.raises(ValueError, match='RECEIPT_UNAVAILABLE'):
        source.record_query_cut(handle=handle, result=result, fact_digests=())
    assert source.status().status == 'GAP' and session._order_query_cut is not None
    assert session.close_order_query_cut(handle)


def test_previously_verified_durable_receipt_is_immutable(real_owner):
    value, session, native, journal, helper = selected(real_owner)
    handle, result = query_cut(session, native, rows=[], callback=lambda:
        native.client._spi.OnRtnOrder(SimpleNamespace(**_order(TradingDay=helper.TODAY))))
    value.poll()
    source = value._observed_order_source
    assert source.verify_next_query_receipt(handle=handle, result=result) is True
    with pytest.raises(sqlite3.IntegrityError, match='source history is immutable'), journal._transaction() as db:
        db.execute('DELETE FROM observed_order_source_receipts')
    with pytest.raises(sqlite3.IntegrityError, match='source history is immutable'), journal._transaction() as db:
        db.execute("UPDATE observed_order_source_receipts SET source_record_digest=?", ('0' * 64,))
    assert source.record_query_cut(handle=handle, result=result, fact_digests=()).status == 'CANDIDATE'
    assert session.close_order_query_cut(handle)


def test_failed_query_releases_native_lock_before_durable_invalidation(real_owner, monkeypatch):
    value, session, native, _, _ = selected(real_owner)
    handle, result = query_cut(session, native, rows=[])
    source = value._observed_order_source
    original_invalidate = source.invalidate
    acquired = []
    def probe_invalidation():
        def probe():
            lock = session._connection_owner.query_lock
            available = lock.acquire(blocking=False)
            acquired.append(available)
            if available:
                lock.release()
        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(5)
        assert not thread.is_alive()
        original_invalidate()
    monkeypatch.setattr(source, 'invalidate', probe_invalidation)
    monkeypatch.setattr(session, 'verify_order_query_cut', lambda *_: None)
    with pytest.raises(ValueError, match='QUERY_UNAVAILABLE'):
        source.verify_next_query_receipt(handle=handle, result=result)
    assert acquired and all(acquired)
    assert source.status().status == 'GAP' and session._order_query_cut is not None
    assert not value.dispatch_ready()
    assert session.close_order_query_cut(handle)
