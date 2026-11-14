"""Opt-in, explicit polling of one authorized local-parent event stream.

The caller supplies an already authorized reader and a trusted sink with an
explicit receipt contract. This module starts no thread, owns no execution
resources, and is not installed in ServiceHost or an order path. The caller
must run poll on its background owner thread and bound sink RPC duration.
Events describe local RECEIVED only; no Gateway or exchange acceptance is
implied. Source retention gaps, unknown ACKs and source changes do not advance
the checkpoint. Source and checkpoint remain subject to their own durability
and rollback assumptions.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from bt_api_py.service_execution_monitor import (
    _CheckpointFailureError, _CursorNamespace, _advance_checkpoint,
    _initialize_or_validate_checkpoint, _read_checkpoint, _reject_same_database,
)

_HEX32 = re.compile(r'[0-9a-f]{32}\Z')
_HEX64 = re.compile(r'[0-9a-f]{64}\Z')
_MAX_POLL = 128


class LocalParentRelayError(RuntimeError):
    """Finite diagnostics; the cursor never advances for an unconfirmed event."""

    def __init__(self, code: str, *, delivered: int = 0, checkpointed: int = 0):
        self.code, self.delivered, self.checkpointed = code, delivered, checkpointed
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LocalParentMonitorEvent:
    """Minimal observation, with no account, strategy, order or payload fields."""
    event_id: str
    fingerprint: str
    sequence: int
    occurred_at_ns: int
    event_type: str = 'LOCAL_RECEIVED'
    coverage: str = 'FROM_ACTIVATION'


@dataclass(frozen=True, slots=True)
class LocalParentMonitorAck:
    """Trusted sink's validated durable receipt for the exact observation."""
    consumer_id: str
    event_id: str
    fingerprint: str


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def _sha(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def _text(value):
    return type(value) is str and bool(value) and value == value.strip() and len(value.encode('utf-8')) <= 256


def _identity(path):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('source file unavailable')
    return info.st_dev, info.st_ino


class LocalParentMonitorRelay:
    """One trusted strategy stream to one sink; poll is synchronous and bounded by count.

    The sink must expose a stable ``consumer_id`` and return exactly a
    ``LocalParentMonitorAck`` only after validating durable remote receipt. It
    must de-duplicate by event_id when a reply or local checkpoint is lost.
    Authentication, source authorization, network timeouts and independent
    execution context are the caller's responsibility.
    """
    def __init__(self, *, reader, scope, sink, checkpoint_path):
        try:
            from bt_api_execution.contracts import ExecutionScope
            from bt_api_execution.local_parent_events import LocalParentEventReader
            if (type(reader) is not LocalParentEventReader or type(scope) is not ExecutionScope
                    or reader._scope != scope or not callable(sink) or not _text(sink.consumer_id)):
                raise ValueError('invalid trusted binding')
            if not isinstance(checkpoint_path, (str, Path)) or not str(checkpoint_path):
                raise ValueError('invalid checkpoint')
            source = Path(reader._store.path).resolve(strict=True)
            source_id = _identity(source)
            checkpoint = Path(checkpoint_path).expanduser().resolve(strict=False)
            _reject_same_database(source, source_id, checkpoint)
            _initialize_or_validate_checkpoint(checkpoint)
            scope_key = reader._scope_key
            if not _text(scope_key):
                raise ValueError('invalid scope')
            binding = _json({'schema': 'sdk.local-parent-monitor.v1', 'consumer_id': sink.consumer_id,
                             'scope_key': scope_key})
            namespace = _CursorNamespace(_sha(binding), binding)
            generation = reader.generation()
            if type(generation) is not str or _HEX32.fullmatch(generation) is None:
                raise ValueError('invalid generation')
        except _CheckpointFailureError as error:
            raise LocalParentRelayError(error.code) from None
        except Exception:
            raise LocalParentRelayError('CONFIGURATION_INVALID') from None
        self._reader, self._sink, self._scope_key = reader, sink, scope_key
        self._consumer_id, self._generation = sink.consumer_id, generation
        self._path, self._namespace = checkpoint, namespace
        self._source, self._source_id = source, source_id
        self._thread = threading.get_ident()
        self._closed = False

    def _check(self):
        if self._closed or threading.get_ident() != self._thread:
            raise LocalParentRelayError('RELAY_UNAVAILABLE')
        try:
            if self._sink.consumer_id != self._consumer_id or _identity(self._source) != self._source_id:
                raise ValueError
            if self._reader.generation() != self._generation:
                raise LocalParentRelayError('GENERATION_CHANGED')
        except LocalParentRelayError:
            raise
        except Exception:
            raise LocalParentRelayError('SOURCE_UNAVAILABLE') from None

    def _page(self, stream_id, after, limit):
        from bt_api_execution.local_parent_events import LocalParentEventError, LocalParentEventGap
        try:
            return self._reader.page(generation=self._generation, stream_id=stream_id,
                                     after_sequence=after, limit=limit)
        except LocalParentEventGap:
            raise LocalParentRelayError('GAP') from None
        except LocalParentEventError as error:
            if str(error) in ('GENERATION_CHANGED', 'STREAM_CHANGED'):
                raise LocalParentRelayError(str(error)) from None
            raise LocalParentRelayError('SOURCE_UNAVAILABLE') from None
        except Exception:
            raise LocalParentRelayError('SOURCE_UNAVAILABLE') from None

    def _project(self, event, stream):
        raw = _json(event)
        fingerprint = _sha(raw)
        identity = _json({'schema': 'sdk.local-parent-monitor.event.v1', 'generation': self._generation,
                          'stream_id': stream, 'scope_key': self._scope_key, 'sequence': event['sequence']})
        return LocalParentMonitorEvent('service-event:' + _sha(identity), fingerprint,
                                       event['sequence'], event['created_at_ns'])

    def checkpoint(self):
        self._check()
        try:
            return _read_checkpoint(self._path, self._namespace)
        except _CheckpointFailureError as error:
            raise LocalParentRelayError(error.code) from None

    def poll(self, limit=32):
        """Return confirmed delivery/checkpoint counts; never invokes itself automatically."""
        self._check()
        if type(limit) is not int or not 0 <= limit <= _MAX_POLL:
            raise LocalParentRelayError('INVALID_POLL_LIMIT')
        delivered = checkpointed = 0
        try:
            cursor = _read_checkpoint(self._path, self._namespace)
            stream = None
            if cursor.sequence:
                pieces = cursor.source_event_id.split(':')
                if (len(pieces) != 3 or _HEX32.fullmatch(pieces[0]) is None
                        or _HEX32.fullmatch(pieces[1]) is None or _HEX64.fullmatch(pieces[2]) is None):
                    raise LocalParentRelayError('CHECKPOINT_CORRUPT')
                generation, stream, digest = pieces
                if generation != self._generation:
                    raise LocalParentRelayError('GENERATION_CHANGED')
                try:
                    anchor = self._page(stream, cursor.sequence - 1, 1)
                except LocalParentRelayError as error:
                    if error.code != 'GAP':
                        raise
                    # The last confirmed row may have been retained away while
                    # the first *unconfirmed* row remains. The trusted reader
                    # checks both generation and stream identity in this page.
                    # This relies on the existing trusted-checkpoint/source
                    # assumptions and is not proof against storage rollback.
                    continuation = self._page(stream, cursor.sequence, 1)
                    if (continuation.first_available != cursor.sequence + 1
                            or continuation.stream_id != stream
                            or len(continuation.events) != 1
                            or continuation.events[0]['sequence'] != cursor.sequence + 1):
                        raise LocalParentRelayError('SOURCE_CHANGED')
                else:
                    if len(anchor.events) != 1:
                        raise LocalParentRelayError('SOURCE_UNAVAILABLE')
                    projected = self._project(anchor.events[0], stream)
                    if projected.event_id != 'service-event:' + digest or projected.fingerprint != cursor.fingerprint:
                        raise LocalParentRelayError('SOURCE_CHANGED')
            page = self._page(stream, cursor.sequence, max(1, limit))
            if not page.events or limit == 0:
                return (0, 0)
            stream = page.stream_id
            if type(stream) is not str or _HEX32.fullmatch(stream) is None:
                raise LocalParentRelayError('SOURCE_UNAVAILABLE')
            for source_event in page.events:
                event = self._project(source_event, stream)
                if event.sequence != cursor.sequence + 1:
                    raise LocalParentRelayError('SOURCE_UNAVAILABLE')
                try:
                    receipt = self._sink(event)
                except Exception:
                    raise LocalParentRelayError('REMOTE_DELIVERY_UNKNOWN') from None
                expected = LocalParentMonitorAck(self._consumer_id, event.event_id, event.fingerprint)
                if type(receipt) is not LocalParentMonitorAck or receipt != expected:
                    raise LocalParentRelayError('REMOTE_DELIVERY_UNKNOWN')
                delivered += 1
                self._check()
                anchor = self._page(stream, event.sequence - 1, 1)
                if len(anchor.events) != 1 or self._project(anchor.events[0], stream) != event:
                    raise LocalParentRelayError('SOURCE_CHANGED')
                source_id = self._generation + ':' + stream + ':' + event.event_id.removeprefix('service-event:')
                outcome = _advance_checkpoint(self._path, self._namespace,
                    expected_sequence=cursor.sequence, sequence=event.sequence,
                    source_event_id=source_id, fingerprint=event.fingerprint)
                if outcome not in ('advanced', 'duplicate'):
                    raise LocalParentRelayError('CHECKPOINT_CONFLICT')
                checkpointed += 1
                cursor = _read_checkpoint(self._path, self._namespace)
            return delivered, checkpointed
        except _CheckpointFailureError as error:
            raise LocalParentRelayError(error.code, delivered=delivered, checkpointed=checkpointed) from None
        except LocalParentRelayError as error:
            raise LocalParentRelayError(error.code, delivered=delivered, checkpointed=checkpointed) from None
        except Exception:
            raise LocalParentRelayError('SOURCE_UNAVAILABLE', delivered=delivered, checkpointed=checkpointed) from None

    def close(self):
        self._check()
        self._closed = True
