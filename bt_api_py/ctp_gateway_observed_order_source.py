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
            if status.gap or progress.status == 'GAP' or progress.source_history_has_gap:
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
            if type(record.row) is not MappingProxyType:
                raise ValueError('OBSERVED_ORDER_RECORD_INVALID')
            # The buffer admits only bounded immutable text and finite scalars.
            # Encode every record field; the digest is correlation, not authority.
            values = {field.name: (dict(record.row) if field.name == 'row' else getattr(record, field.name))
                      for field in fields(CtpGatewayReturnRecord)}
            encoded = json.dumps(values, ensure_ascii=True, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False).encode('ascii')
        except Exception:
            self._invalidate_safely()
            raise
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
