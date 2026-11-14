"""Explicit, optional background observation of one Atomic parent stream.

The selected scopes and clients are trusted operator composition, not proof of
account authority. Monitor observes local receipts only and never gates trading.
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path

from bt_api_base.service_protocol import WireScope
from bt_api_py.service_config import ServiceConfig


class AtomicMonitorAssemblyError(RuntimeError):
    """Finite local assembly error; no account or order facts are exposed."""


@dataclass(frozen=True, slots=True)
class TrustedAtomicMonitor:
    """Explicit source, destination and independently resolved Monitor client."""

    instance_id: str
    source_scope: object
    wire_scope: WireScope
    scope_ref: str
    client_config: ServiceConfig
    trusted_clients: object
    checkpoint_path: str | Path
    poll_interval: float = 1.0
    stop_timeout: float = 0.25

    def __post_init__(self):
        from bt_api_execution.contracts import ExecutionScope
        from bt_api_py.service_host import TrustedServiceClients
        from bt_api_py.service_local_parent_monitor_owner import _duration

        if (type(self.instance_id) is not str or not self.instance_id or self.instance_id.strip() != self.instance_id
                or type(self.source_scope) is not ExecutionScope or type(self.wire_scope) is not WireScope
                or self.wire_scope.account_ref != self.source_scope.account_ref
                or self.wire_scope.strategy_ref != self.source_scope.strategy_id
                or self.wire_scope.account_refs or self.wire_scope.manual_book_ref is not None
                or self.wire_scope.portfolio_ref is not None or self.wire_scope.group_id is not None
                or self.wire_scope.leg_id is not None or type(self.scope_ref) is not str or not self.scope_ref
                or type(self.client_config) is not ServiceConfig
                or type(self.trusted_clients) is not TrustedServiceClients
                or not isinstance(self.checkpoint_path, (str, Path)) or not str(self.checkpoint_path)):
            raise AtomicMonitorAssemblyError('INVALID_MONITOR_CONFIGURATION')
        try:
            _duration(self.poll_interval, positive=True)
            timeout = _duration(self.stop_timeout)
            if timeout > 1.0:
                raise ValueError
            public = self.client_config.to_public_dict()
            clients = public.get('clients', {})
            if set(clients) != {'monitor'}:
                raise ValueError
            bindings = clients['monitor']['settings']['bindings']
            if (type(bindings) is not list or len(bindings) != 1
                    or bindings[0]['mount'] != 'upstream.telemetry'
                    or bindings[0]['scope_ref'] != self.scope_ref):
                raise ValueError
            requested = self.trusted_clients.requested_operations
            if requested is not None and dict(requested) != {'upstream.telemetry': frozenset({'events.append'})}:
                raise ValueError
        except Exception:
            raise AtomicMonitorAssemblyError('INVALID_MONITOR_CONFIGURATION') from None


class _MonitorResource:
    """Retain partially started runtime for the owner and Host through cleanup."""

    def __init__(self, selection, identity, journal):
        self.selection, self.identity, self.journal = selection, identity, journal
        self.runtime = None
        self.relay = None
        self.reader = None
        self.fault_code = None
        self._abandoned_reader = False
        self._source_corrupt = False
        self.initialized = False
        self._relay_closed = False
        self._retry_cleanup = threading.Event()
        self.cleanup_uncertain = threading.Event()

    def maintain(self):
        try:
            self._maintain()
        except BaseException as error:
            self._record_fault(error)
            raise

    def _record_fault(self, error):
        from bt_api_execution.local_parent_events import LocalParentEventError
        from bt_api_py.service_local_parent_monitor import LocalParentRelayError

        # Transport and factory exceptions may contain arbitrary sensitive
        # strings. Only the relay's known finite codes are publishable.
        relay_codes = {
            'GAP', 'STREAM_CHANGED', 'GENERATION_CHANGED', 'SOURCE_CHANGED',
            'SOURCE_UNAVAILABLE', 'CHECKPOINT_CORRUPT', 'CHECKPOINT_CONFLICT',
            'CHECKPOINT_UNAVAILABLE', 'REMOTE_DELIVERY_UNKNOWN',
            'CONFIGURATION_INVALID', 'RELAY_UNAVAILABLE',
        }
        self.fault_code = 'MONITOR_UNAVAILABLE'
        if type(error) is LocalParentRelayError:
            code = error.code
            if type(code) is str and code in relay_codes:
                self.fault_code = code
        # The relay redacts its underlying reader exception using ``from None``;
        # the original remains in __context__. A malformed durable event is
        # not a transient SQLite busy result and must never be auto-restarted.
        cause = error
        seen = set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            if (type(cause) is LocalParentEventError and len(cause.args) == 1
                    and type(cause.args[0]) is str and cause.args[0] == 'JOURNAL_INVALID'):
                self._source_corrupt = True
                break
            cause = cause.__context__
        if self._source_corrupt or (self.reader is not None and getattr(self.reader, '_broken', False)):
            self.fault_code = 'SOURCE_CORRUPT'

    def _maintain(self):
        if not self.initialized:
            # Construct and use every runtime/reader object only on this owner.
            from bt_api_execution.isolated_local_parent_events import (
                IsolatedLocalParentEventReader, _ABANDONED_READERS,
            )
            from bt_api_py.service_local_parent_monitor_sink import (
                LOCAL_PARENT_OUTBOX_SCOPE, LocalParentMonitorBinding, create_local_parent_service_relay,
            )
            from bt_api_py.service_monitor import ServiceMonitorOutboxSink
            from bt_api_py.service_runtime import ServiceClientRuntime

            selection = self.selection
            clients = selection.trusted_clients
            self.runtime = ServiceClientRuntime(
                selection.client_config, identity=self.identity,
                scope_resolver=clients.scope_resolver, peer_resolver=clients.peer_resolver,
                transport_factory=clients.transport_factory,
                requested_operations={'upstream.telemetry': frozenset({'events.append'})},
                run_id=selection.wire_scope.run_id,
            )
            self.runtime.start()
            reader = object.__new__(IsolatedLocalParentEventReader)
            self.reader = reader
            try:
                IsolatedLocalParentEventReader.__init__(reader, host=self.journal, scope=selection.source_scope)
            except BaseException:
                self._source_corrupt = getattr(reader, '_broken', False) is True
                hold = getattr(reader, '_hold', None)
                self._abandoned_reader = hold is not None and any(
                    item[0] is hold for item in _ABANDONED_READERS)
                if not self._abandoned_reader:
                    self.reader = None
                raise
            sink = ServiceMonitorOutboxSink(self.runtime, scope_ref=selection.scope_ref,
                                            outbox_scope=LOCAL_PARENT_OUTBOX_SCOPE)
            self.relay = create_local_parent_service_relay(
                sink=sink, reader=reader,
                binding=LocalParentMonitorBinding(selection.source_scope, selection.wire_scope),
                checkpoint_path=selection.checkpoint_path,
            )
            self.initialized = True
        self.runtime.maintain()

    def poll(self):
        if not self.initialized or self.relay is None:
            raise AtomicMonitorAssemblyError('MONITOR_UNAVAILABLE')
        try:
            return self.relay.poll()
        except BaseException as error:
            self._record_fault(error)
            raise

    def close(self):
        from bt_api_py.service_local_parent_monitor_owner import LocalParentMonitorCleanup
        while True:
            failed = False
            if self.relay is not None and not self._relay_closed:
                try:
                    self.relay.close()
                except BaseException:
                    failed = True
                else:
                    self._relay_closed = True
                    self.reader = None
            elif self.relay is None and self.reader is not None and not self._abandoned_reader:
                try:
                    self.reader.close()
                except BaseException:
                    failed = True
                else:
                    self.reader = None
            if self._abandoned_reader:
                failed = True
            if self.runtime is not None:
                try:
                    self.runtime.close()
                    counters = (self.runtime.cleanup_failures, self.runtime.unregister_uncertainties,
                                self.runtime.unknown_registration_count)
                    if (self.runtime.state != 'closed'
                            or any(type(v) is not int or v != 0 for v in counters)):
                        failed = True
                except BaseException:
                    failed = True
            if not failed:
                self.cleanup_uncertain.clear()
                return LocalParentMonitorCleanup.CONFIRMED
            # Stay on the original runtime thread; only an explicit Host retry
            # attempts cleanup again. Remote UNKNOWN is never inferred closed.
            self.cleanup_uncertain.set()
            self._retry_cleanup.wait()
            self._retry_cleanup.clear()

    def retry_cleanup(self):
        self._retry_cleanup.set()


class AtomicMonitorAssembly:
    """Pin one account journal until its separate Monitor owner is confirmed closed."""

    def __init__(self, selection, service, identity, service_id):
        from bt_api_execution.service_atomic_intake import AtomicIntakeService
        from bt_api_py.service_local_parent_monitor_owner import LocalParentMonitorOwner

        if (type(selection) is not TrustedAtomicMonitor or type(service) is not AtomicIntakeService
                or selection.instance_id != service.instance_id or service.service_identity != identity
                or selection.client_config.service_id != service_id):
            raise AtomicMonitorAssemblyError('MONITOR_SOURCE_INVALID')
        candidates = [wire for wire, scope in service.trusted_strategy_targets.items()
                      if scope == selection.source_scope
                      and wire.execution_domain_ref == selection.wire_scope.execution_domain_ref]
        if len(candidates) != 1:
            raise AtomicMonitorAssemblyError('MONITOR_SOURCE_INVALID')
        journal = service._intake._host
        self.selection = selection
        self.resource = None
        raw_key = '|'.join((journal._account_hash, selection.source_scope.account_key,
                            selection.source_scope.strategy_id))
        self._key = 'atomic-monitor:' + hashlib.sha256(raw_key.encode('utf-8')).hexdigest()
        self._identity, self._journal = identity, journal
        self.owner = self._new_owner()
        # This is the last potentially failing operation: no partially-created
        # observer hold can be lost if building the owner fails.
        self._hold = journal.retain_observer()

    def _new_owner(self):
        from bt_api_py.service_local_parent_monitor_owner import LocalParentMonitorOwner
        def factory():
            # The retained assembly keeps this shell even if initialization
            # or cleanup fails on the worker thread.
            self.resource = _MonitorResource(self.selection, self._identity, self._journal)
            return self.resource
        return LocalParentMonitorOwner(owner_key=self._key, factory=factory,
                                       poll_interval=self.selection.poll_interval)

    def start(self):
        return self.owner.start(timeout=0)

    def restart_if_safe(self):
        status = self.owner.status()
        if status.outcome == 'NOT_STARTED':
            return self.owner.start(timeout=0)
        terminal = {'GAP', 'STREAM_CHANGED', 'GENERATION_CHANGED', 'SOURCE_CHANGED',
                    'SOURCE_CORRUPT', 'CHECKPOINT_CORRUPT', 'CHECKPOINT_CONFLICT'}
        if (status.outcome == 'FAILED' and status.cleanup == 'CONFIRMED'
                and (self.resource is None or self.resource.fault_code not in terminal)):
            # Acknowledged events remain in the durable checkpoint; uncertain
            # deliveries replay with their original event identifier.
            self.owner = self._new_owner()
            return self.owner.start(timeout=0)
        if status.phase == 'CLOSING' and self.resource is not None and self.resource.cleanup_uncertain.is_set():
            self.resource.retry_cleanup()
        return status

    def close(self):
        if self.resource is not None:
            self.resource.retry_cleanup()
        status = self.owner.stop(timeout=self.selection.stop_timeout)
        if status.outcome == 'NOT_STARTED' and status.cleanup == 'NOT_STARTED':
            return self._release_hold()
        if status.outcome in ('STOPPED', 'FAILED') and status.cleanup == 'CONFIRMED':
            return self._release_hold()
        return False

    def _release_hold(self):
        from bt_api_execution.account_journal_host import AccountJournalHostError

        # The observer lifetime mutex is independent from the intake Host lock.
        # A busy mutex is a retryable close, never a reason to lose the hold.
        try:
            self._hold.close(nonblocking=True)
        except AccountJournalHostError:
            return False
        return True
