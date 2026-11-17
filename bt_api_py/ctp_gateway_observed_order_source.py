"""Owner-local receipts for CTP callback observations in the Gateway journal.

These receipts do not prove venue completeness, source continuity before this
session, an atomic exchange cut, or authorization to trade.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from contextlib import suppress
from dataclasses import fields
from types import MappingProxyType

from bt_api_ctp.gateway_return_buffer import CtpGatewayReturnRecord
from bt_api_ctp.gateway_return_query import CtpGatewayReturnQueryReader
from bt_api_gateway.direct_execution_journal import GatewayDirectExecutionLedger


class _QueryCutBusyError(Exception):
    pass


class CtpGatewayObservedOrderSource:
    """Record each source sequence only after its fact or finite gap is durable."""

    def __init__(self, session, reader: CtpGatewayReturnQueryReader, ledger: GatewayDirectExecutionLedger):
        from bt_api_ctp.gateway_session import CtpGatewaySession

        if type(reader) is not CtpGatewayReturnQueryReader or type(ledger) is not GatewayDirectExecutionLedger:
            raise ValueError('INVALID_OBSERVED_ORDER_SOURCE')
        if type(session) is not CtpGatewaySession or session._trader is not reader._trader:
            raise ValueError('INVALID_OBSERVED_ORDER_SOURCE')
        self._session = session
        self._reader, self._ledger = reader, ledger
        self._journal, self._guard = ledger.journal, ledger._guard
        self._pid, self._thread = os.getpid(), threading.current_thread()
        self._binding = None
        self._blocked = threading.Event()
        self._begin_attempted = False
        self._invalidation_pending = False
        self._query_stage = None

    @property
    def blocked(self):
        return self._blocked.is_set()

    def _check_owner(self):
        if os.getpid() != self._pid or threading.current_thread() is not self._thread:
            raise RuntimeError('OBSERVED_ORDER_SOURCE_WRONG_OWNER')

    def start(self):
        self._check_owner()
        self._reader.assert_session_current()
        binding = self._reader._binding
        if not self._journal._observed_order_source_enabled:
            self._journal.enable_observed_order_source(self._guard)
        self._reader.assert_session_current()
        if self._session.connection_status() != 'ready' or self._session._trader is not self._reader._trader:
            raise ValueError('OBSERVED_ORDER_SOURCE_CHANGED')
        status = self._session.return_buffer.status()
        # Pending records must not be skipped by a freshly selected base.
        base = 0 if status.record_count else status.last_sequence
        self._reader.assert_session_current()
        # Retain this exact binding before the first potentially committed call.
        # A failed post-check or uncertain commit must invalidate this source,
        # not silently construct a new one.
        self._binding = binding
        self._begin_attempted = True
        try:
            progress = self._journal.begin_observed_order_source(self._guard, source_ref=binding.source_ref,
                session_ref=binding.generation, trading_day=binding.trading_day, base_sequence=base)
            self._reader.assert_session_current()
            if status.gap or progress.status == 'GAP':
                self.invalidate()
        except Exception:
            self._invalidate_safely()
            raise

    def matches(self, reader, ledger):
        return self._reader is reader and self._ledger is ledger

    def note_validation_failure(self, record):
        """Recheck a held ticket without treating a foreign record as evidence."""
        with suppress(Exception):
            self._check_record(record)

    def _invalidate_safely(self):
        # Never replace the original failure with an invalidation failure.
        with suppress(Exception):
            self.invalidate()

    def _check_record(self, record):
        self._check_owner()
        # A bad caller argument is not evidence against this source. Only an
        # exact record still held in this session's active ticket can revoke it.
        if (type(record) is not CtpGatewayReturnRecord
                or not self._session.return_buffer._contains_record(record)):
            raise ValueError('OBSERVED_ORDER_RECORD_INVALID')
        binding = self._binding
        try:
            self._reader.assert_session_current()
            self._session.assert_return_record_current(record)
            if (binding is None or self._reader._binding is not binding
                    or record.source_ref != binding.source_ref or record.source_tags != binding._source_tags
                    or record.connection_generation != binding.connection_generation
                    or record.reception_trading_day != binding.trading_day):
                raise ValueError('OBSERVED_ORDER_SOURCE_CHANGED')
        except Exception:
            self._invalidate_safely()
            raise
        return binding

    def record_digest(self, record):
        self._check_record(record)
        try:
            return self._raw_record_digest(record)
        except Exception:
            self._invalidate_safely()
            raise

    @staticmethod
    def _raw_record_digest(record):
        if type(record) is not CtpGatewayReturnRecord or type(record.row) is not MappingProxyType:
            raise ValueError('OBSERVED_ORDER_RECORD_INVALID')
        # The buffer admits only bounded immutable text and finite scalars.
        # Encode every record field; the digest is correlation, not authority.
        values = {field.name: (dict(record.row) if field.name == 'row' else getattr(record, field.name))
                  for field in fields(CtpGatewayReturnRecord)}
        encoded = json.dumps(values, ensure_ascii=True, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('ascii')
        return hashlib.sha256(encoded).hexdigest()

    def record(self, record, *, source_record_digest, fact=None):
        binding = self._check_record(record)
        if self.record_digest(record) != source_record_digest:
            raise ValueError('OBSERVED_ORDER_RECORD_CHANGED')
        # Historical-day rows cannot be represented by the current session's
        # journal source contract. Retain a gap instead of presenting them as
        # a current-day fact.
        digest = None if fact is None or fact.trading_day != binding.trading_day else fact.fact_digest()
        try:
            result = self._journal.record_observed_order_result(self._guard,
                source_ref=binding.source_ref, session_ref=binding.generation,
                trading_day=binding.trading_day, source_sequence=record.sequence,
                source_record_digest=source_record_digest, fact_digest=digest,
                gap_reason=None if digest is not None else 'PROJECTION_UNAVAILABLE')
        except Exception as error:
            if getattr(error, 'code', None) == 'OBSERVED_SOURCE_RECORD_CONFLICT':
                self._invalidate_safely()
            raise
        if result.status == 'GAP':
            self._blocked.set()
        self._check_record(record)
        if self.record_digest(record) != source_record_digest:
            raise ValueError('OBSERVED_ORDER_RECORD_CHANGED')
        return result

    def invalidate(self):
        self._check_owner()
        if self._binding is not None:
            self._blocked.set()
            self._invalidation_pending = True
            self._journal.invalidate_observed_order_source(self._guard,
                source_ref=self._binding.source_ref, session_ref=self._binding.generation)
            self._invalidation_pending = False

    def status(self):
        self._check_owner()
        if self._binding is None:
            raise ValueError('OBSERVED_ORDER_SOURCE_UNAVAILABLE')
        return self._journal.read_observed_order_source(self._guard,
            source_ref=self._binding.source_ref, session_ref=self._binding.generation)

    def _verify_query_cut(self, handle, result):
        """Recheck an exact provider-owned query and its pinned return window."""
        from bt_api_ctp.gateway_order_query_cut import CtpGatewayOrderQueryCutView
        from bt_api_ctp.query import QueryResult

        self._check_owner()
        state = self._session._order_query_cut
        # A foreign handle/result is a caller error, not evidence of a gap.
        if state is None or state.handle is not handle or state.result is not result or type(result) is not QueryResult:
            stage = self._query_stage
            if stage is not None and not stage['complete'] and stage['handle'] is handle and stage['result'] is result:
                self._invalidate_safely()
            raise ValueError('OBSERVED_ORDER_QUERY_ARGUMENT_INVALID')
        owner = state.owner
        if not self._session._connection_owner_matches(owner):
            self._invalidate_safely()
            raise ValueError('OBSERVED_ORDER_QUERY_UNAVAILABLE')
        if not owner.query_lock.acquire(blocking=False):
            raise _QueryCutBusyError
        try:
            try:
                if self.blocked or self._binding is None or self._reader._binding is not self._binding:
                    raise ValueError('OBSERVED_ORDER_QUERY_UNAVAILABLE')
                self._reader.assert_session_current()
                cut = self._session.verify_order_query_cut(handle, result)
                actual = state.binding
                expected = self._binding
                if (type(cut) is not CtpGatewayOrderQueryCutView or actual is None
                        or actual._issuer is not expected._issuer or actual._source_tags != expected._source_tags
                        or actual.source_ref != expected.source_ref or actual.generation != expected.generation
                        or actual.connection_generation != expected.connection_generation
                        or actual.trading_day != expected.trading_day or state.result is not result
                        or self._session._order_query_cut is not state or type(result.records) is not tuple
                        or len(result.records) > 10000):
                    raise ValueError('OBSERVED_ORDER_QUERY_UNAVAILABLE')
                self._reader.assert_session_current()
                return cut
            finally:
                owner.query_lock.release()
        except Exception:
            # Durable invalidation can perform SQLite I/O. Do it only after
            # releasing the native query lock so callbacks remain unblocked.
            self._invalidate_safely()
            raise

    def _query_stage_for(self, handle, result):
        cut = self._verify_query_cut(handle, result)
        stage = self._query_stage
        if stage is not None and (stage['handle'] is not handle or stage['result'] is not result):
            if not stage['complete']:
                self._invalidate_safely()
                raise ValueError('OBSERVED_ORDER_QUERY_IN_PROGRESS')
            stage = None
        if stage is None:
            stage = {'handle': handle, 'result': result, 'digests': [], 'complete': False,
                     'query_digest': result.query_source.records_sha256,
                     'begin': cut.begin_sequence, 'end': cut.end_sequence,
                     'receipts': [], 'receipt_history_gap': False}
            self._query_stage = stage
        if (stage['query_digest'] != result.query_source.records_sha256
                or stage['begin'] != cut.begin_sequence or stage['end'] != cut.end_sequence):
            self._invalidate_safely()
            raise ValueError('OBSERVED_ORDER_QUERY_CHANGED')
        return stage, cut

    def ingest_query_order_row(self, ingestor, *, handle, result, row_index):
        """Persist one row of the original verified query; never issue a query."""
        from bt_api_py.ctp_gateway_return_ingestion import CtpGatewayReturnIngestor

        if (type(ingestor) is not CtpGatewayReturnIngestor or not self.matches(ingestor._reader, ingestor._ledger)
                or ingestor._observed_order_source is not self):
            raise ValueError('OBSERVED_ORDER_QUERY_ARGUMENT_INVALID')
        try:
            stage, _ = self._query_stage_for(handle, result)
        except _QueryCutBusyError:
            return None
        if type(row_index) is not int or not 0 <= row_index < len(result.records):
            raise ValueError('OBSERVED_ORDER_QUERY_ARGUMENT_INVALID')
        if stage['complete'] or row_index > len(stage['digests']):
            raise ValueError('OBSERVED_ORDER_QUERY_ORDER_INVALID')
        if row_index < len(stage['digests']):
            return stage['digests'][row_index]
        committing = False
        try:
            fact = ingestor._project('order', result.records[row_index], source_ref=self._binding.source_ref,
                trading_day=self._binding.trading_day, connection_generation=self._binding.connection_generation)
            if fact is None:
                raise ValueError('OBSERVED_ORDER_QUERY_PROJECTION_FAILED')
            self._verify_query_cut(handle, result)
            committing = True
            self._ledger.ingest(fact)
            self._verify_query_cut(handle, result)
            digest = fact.fact_digest()
            stage['digests'].append(digest)
            return digest
        except _QueryCutBusyError:
            if not committing:
                return None
            self._invalidate_safely()
            raise
        except Exception:
            self._invalidate_safely()
            raise

    def _query_progress(self, cut):
        from bt_api_gateway.observed_order_source import ObservedOrderSourceProgress

        progress = self.status()
        if (type(progress) is not ObservedOrderSourceProgress
                or progress.account != self._ledger.account_scope
                or progress.journal_id != self._journal.journal_id
                or progress.owner_generation != self._journal.generation
                or progress.source_ref != self._binding.source_ref
                or progress.session_ref != self._binding.generation
                or progress.trading_day != self._binding.trading_day
                or progress.status == 'GAP' or type(progress.source_history_has_gap) is not bool
                or cut.begin_sequence < progress.base_sequence):
            raise ValueError('OBSERVED_ORDER_QUERY_UNAVAILABLE')
        return progress

    def verify_next_query_receipt(self, *, handle, result):
        """Verify at most one pinned receipt; None means no progress was made."""
        from bt_api_gateway.observed_order_source import ObservedOrderReceiptVerification

        try:
            stage, cut = self._query_stage_for(handle, result)
            progress = self._query_progress(cut)
            if len(stage['digests']) != len(result.records) or progress.source_prefix < cut.end_sequence:
                return None
            if len(cut.records) != cut.end_sequence - cut.begin_sequence:
                raise ValueError('OBSERVED_ORDER_QUERY_RECEIPT_UNAVAILABLE')
            index = len(stage['receipts'])
            if index > len(cut.records):
                raise ValueError('OBSERVED_ORDER_QUERY_RECEIPT_UNAVAILABLE')
            if index == len(cut.records):
                return True
            record = cut.records[index]
            if record.sequence != cut.begin_sequence + index + 1:
                raise ValueError('OBSERVED_ORDER_QUERY_RECEIPT_UNAVAILABLE')
            digest = self._raw_record_digest(record)
            check = self._journal.verify_observed_order_receipt(self._guard,
                source_ref=self._binding.source_ref, session_ref=self._binding.generation,
                source_sequence=record.sequence, source_record_digest=digest)
            if (type(check) is not ObservedOrderReceiptVerification or check.status != 'MATCH'
                    or type(check.source_history_has_gap) is not bool):
                raise ValueError('OBSERVED_ORDER_QUERY_RECEIPT_UNAVAILABLE')
            self._verify_query_cut(handle, result)
            stage['receipts'].append((record.sequence, digest))
            stage['receipt_history_gap'] |= progress.source_history_has_gap or check.source_history_has_gap
            return len(stage['receipts']) == len(cut.records)
        except _QueryCutBusyError:
            return None
        except Exception:
            self._invalidate_safely()
            raise

    def record_query_cut(self, *, handle, result, fact_digests):
        """Persist a local candidate after stepwise receipt verification.

        None means the verified prefix is not ready. This is not a venue
        snapshot or a trading authorization.
        """
        from bt_api_gateway.observed_order_source import ObservedOrderQueryCut

        try:
            stage, cut = self._query_stage_for(handle, result)
        except _QueryCutBusyError:
            return None
        if type(fact_digests) is not tuple or fact_digests != tuple(stage['digests']):
            raise ValueError('OBSERVED_ORDER_QUERY_ARGUMENT_INVALID')
        committing = False
        try:
            progress = self._query_progress(cut)
            if (len(stage['digests']) != len(result.records) or progress.source_prefix < cut.end_sequence
                    or len(stage['receipts']) != len(cut.records)):
                return None
            if tuple((record.sequence, self._raw_record_digest(record)) for record in cut.records) != tuple(stage['receipts']):
                raise ValueError('OBSERVED_ORDER_QUERY_RECEIPT_UNAVAILABLE')
            history_has_gap = progress.source_history_has_gap or stage['receipt_history_gap']
            self._verify_query_cut(handle, result)
            committing = True
            candidate = self._journal.record_observed_order_query_cut(self._guard,
                source_ref=self._binding.source_ref, session_ref=self._binding.generation,
                trading_day=self._binding.trading_day, begin_sequence=cut.begin_sequence,
                end_sequence=cut.end_sequence, query_digest=stage['query_digest'],
                query_scope='ctp.futures.account_orders.v1', query_row_count=len(result.records),
                query_fact_digests=tuple(stage['digests']))
            self._verify_query_cut(handle, result)
            if (type(candidate) is not ObservedOrderQueryCut or candidate.status != 'CANDIDATE'
                    or type(candidate.source_history_has_gap) is not bool
                    or (history_has_gap and not candidate.source_history_has_gap)
                    or candidate.begin_sequence != cut.begin_sequence
                    or candidate.end_sequence != cut.end_sequence or candidate.query_digest != stage['query_digest']
                    or candidate.query_scope != 'ctp.futures.account_orders.v1'
                    or candidate.query_fact_digests != tuple(stage['digests'])
                    or candidate.fact_epoch != progress.fact_epoch or candidate.source_prefix < cut.end_sequence
                    or candidate.base_sequence != progress.base_sequence):
                raise ValueError('OBSERVED_ORDER_QUERY_UNAVAILABLE')
            stage['complete'] = True
            return candidate
        except _QueryCutBusyError:
            if not committing:
                return None
            self._invalidate_safely()
            raise
        except Exception:
            self._invalidate_safely()
            raise
