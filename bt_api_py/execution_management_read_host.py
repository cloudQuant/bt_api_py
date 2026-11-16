"""Optional, account-process listener for audited local parent observations.

The transport, not the wire identity, supplies the authenticated principal.
The caller provides the already owned account Host and trusted CURVE binding.
No account writer, trading readiness, order command or strategy is started.

Start/stop timeouts limit only the caller's wait. A blocked transport or I/O
is not interrupted. Uncertain cleanup keeps the owner thread and journal hold
alive for same-thread retries; it is never reported as a completed shutdown.
"""
from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_execution.account_journal_host import AccountJournalHost
from bt_api_execution.contracts import ExecutionScope

from .execution_management_read_grant import ExecutionManagementReadGrant
from .execution_management_read_service import ExecutionManagementReadService


class ManagementReadHostError(RuntimeError):
    """Finite local configuration or ownership error."""


@dataclass(frozen=True, slots=True, repr=False)
class TrustedManagementReadListener:
    """Trusted operator composition; public keys map to authenticated principals."""

    host: AccountJournalHost
    local_scope: ExecutionScope
    grant: ExecutionManagementReadGrant
    scope_ref: str
    service_id: str
    service_identity: WireIdentity
    audit_root: str | Path
    endpoint: str
    principals: object = field(repr=False)
    credentials: object = field(repr=False)
    lease_seconds: float = 30.0

    def __post_init__(self):
        from bt_api_transport_zmq.server import CurveServerCredentials
        from bt_api_transport_zmq.service_v2 import validate_service_server_configuration

        if (type(self.host) is not AccountJournalHost or type(self.local_scope) is not ExecutionScope
                or type(self.grant) is not ExecutionManagementReadGrant
                or type(self.service_identity) is not WireIdentity
                or self.service_identity.role is not WireRole.SERVICE
                or type(self.scope_ref) is not str or not self.scope_ref or self.scope_ref.strip() != self.scope_ref
                or type(self.service_id) is not str or not self.service_id or self.service_id.strip() != self.service_id
                or type(self.credentials) is not CurveServerCredentials
                or not isinstance(self.audit_root, (str, Path)) or not str(self.audit_root)
                or type(self.principals) not in (dict, MappingProxyType)
                or not self.principals
                or any(value != self.grant.identity.principal_ref for value in self.principals.values())
                or type(self.lease_seconds) not in (int, float)
                or not math.isfinite(self.lease_seconds) or self.lease_seconds <= 0):
            raise ManagementReadHostError('INVALID_CONFIGURATION')
        try:
            principals = MappingProxyType(dict(self.principals))
            validate_service_server_configuration(self.endpoint, self.service_identity, principals,
                                                  credentials=self.credentials)
        except Exception:
            raise ManagementReadHostError('INVALID_CONFIGURATION') from None
        object.__setattr__(self, 'principals', principals)


@dataclass(frozen=True, slots=True)
class ManagementReadHostStatus:
    outcome: str
    phase: str
    cleanup: str
    endpoint: str | None = None
    reason: str | None = None


def _timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 60:
        raise ManagementReadHostError('INVALID_TIMEOUT')
    return float(value)


class ExecutionManagementReadHost:
    """Run one CURVE listener and its read service on the same owner thread.

    The source AccountJournalHost remains in the existing account process.
    Service and server constructors are deliberately owned before their first
    initialization call so incomplete construction cannot lose a cleanup
    handle. This is a trusted same-process composition, not a Python sandbox.
    """

    def __init__(self, selection: TrustedManagementReadListener):
        if type(selection) is not TrustedManagementReadListener:
            raise ManagementReadHostError('INVALID_CONFIGURATION')
        self._selection = selection
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._retry = threading.Event()
        self._started = threading.Event()
        self._thread = self._hold = self._service = self._server = None
        self._attempted = False
        self._server_startup_uncertain = False
        self._phase = 'NEW'
        self._reason = None
        self._cleanup = 'NOT_STARTED'
        self._endpoint = None

    def status(self):
        with self._lock:
            thread, phase, reason, cleanup, endpoint = (
                self._thread, self._phase, self._reason, self._cleanup, self._endpoint)
        if thread is None:
            outcome = 'CLEANUP_UNKNOWN' if cleanup == 'UNKNOWN' else ('FAILED' if reason else 'NOT_STARTED')
            return ManagementReadHostStatus(outcome, phase, cleanup, endpoint, reason)
        if thread.is_alive():
            state = 'RUNNING' if phase == 'RUNNING' and not self._stop.is_set() else 'STILL_RUNNING'
            return ManagementReadHostStatus(state, phase, cleanup, endpoint, reason)
        outcome = 'CLEANUP_UNKNOWN' if cleanup != 'CONFIRMED' else ('FAILED' if reason else 'STOPPED')
        return ManagementReadHostStatus(outcome, phase, cleanup, endpoint, reason)

    def start(self, *, timeout):
        timeout = _timeout(timeout)
        if os.getpid() != self._pid:
            raise ManagementReadHostError('WRONG_PROCESS')
        with self._lock:
            if self._attempted:
                raise ManagementReadHostError('ALREADY_STARTED')
            try:
                self._hold = self._selection.host.prepare_observer()
            except Exception:
                raise ManagementReadHostError('HOST_UNAVAILABLE') from None
            self._attempted = True
            self._phase, self._cleanup = 'STARTING', 'UNKNOWN'
            thread = None
            try:
                self._selection.host.activate_observer(self._hold)
                thread = threading.Thread(target=self._run, name='execution-management-read', daemon=True)
                self._thread = thread
                thread.start()
            except BaseException as error:
                # KeyboardInterrupt/SystemExit can arrive while Thread.start
                # waits after launching its native thread. Keep the hold until
                # that owner actually finishes; it may not have an ident yet.
                if thread is not None and (not isinstance(error, Exception)
                                           or thread.is_alive() or thread.ident is not None):
                    self._reason, self._phase = 'START_FAILED', 'STOPPING'
                    self._stop.set()
                    self._retry.set()
                    raise ManagementReadHostError('START_UNKNOWN') from None
                try:
                    self._hold.close()
                    self._hold = None
                except BaseException:
                    self._thread = None
                    self._reason, self._phase = 'START_FAILED', 'CLEANUP_UNKNOWN'
                    raise ManagementReadHostError('CLEANUP_UNKNOWN') from None
                self._thread = None
                self._reason, self._phase, self._cleanup = 'START_FAILED', 'STOPPED', 'CONFIRMED'
                raise ManagementReadHostError('START_FAILED') from None
        self._started.wait(timeout)
        return self.status()

    def stop(self, *, timeout):
        timeout = _timeout(timeout)
        if os.getpid() != self._pid:
            raise ManagementReadHostError('WRONG_PROCESS')
        with self._lock:
            thread = self._thread
            if thread is None:
                if self._hold is not None:
                    try:
                        self._hold.close(nonblocking=True)
                    except BaseException:
                        return ManagementReadHostStatus('CLEANUP_UNKNOWN', 'CLEANUP_UNKNOWN', 'UNKNOWN',
                                                        reason=self._reason)
                    self._hold = None
                    self._phase, self._cleanup = 'STOPPED', 'CONFIRMED'
                return ManagementReadHostStatus('FAILED' if self._reason else 'NOT_STARTED',
                                                self._phase, self._cleanup, reason=self._reason)
            if thread is threading.current_thread():
                raise ManagementReadHostError('OWNER_THREAD_CANNOT_JOIN')
            retry_unknown = self._phase == 'CLEANUP_UNKNOWN'
            self._stop.set()
            if self._phase in ('STARTING', 'RUNNING'):
                self._phase = 'STOPPING'
        # Explicit stop also retries any previous uncertain same-thread close.
        if retry_unknown:
            self._retry.set()
        try:
            thread.join(timeout)
        except RuntimeError:
            # A thread whose start was interrupted before it became joinable
            # does not prove that no native owner will still begin executing.
            with self._lock:
                self._phase = 'CLEANUP_UNKNOWN'
        return self.status()

    def _run(self):
        import zmq
        from bt_api_transport_zmq.service_v2 import ZmqServiceServer

        selected = self._selection
        try:
            # Retain partially initialized objects even when __init__ raises.
            self._service = object.__new__(ExecutionManagementReadService)
            try:
                ExecutionManagementReadService.__init__(
                    self._service, host=selected.host, local_scope=selected.local_scope,
                    grant=selected.grant, scope_ref=selected.scope_ref, service_id=selected.service_id,
                    authenticated_principal=selected.grant.identity.principal_ref,
                    audit_root=selected.audit_root, lease_seconds=selected.lease_seconds)
            except BaseException:
                # Exact service constructor performs only static checks before
                # installing its gate; no resources exist on that early path.
                if not hasattr(self._service, '_ExecutionManagementReadService__gate'):
                    self._service = None
                raise
            if not self._stop.is_set():
                self._server = object.__new__(ZmqServiceServer)
                # If native allocation raises before returning its handle,
                # closing known fields cannot prove that nothing remains.
                self._server_startup_uncertain = True
                try:
                    ZmqServiceServer.__init__(self._server, selected.endpoint, selected.service_identity,
                                              selected.principals, self._service.handle,
                                              credentials=selected.credentials)
                except BaseException as error:
                    # Before the transport's context field exists, its exact
                    # constructor has done validation only, with no resources.
                    if not hasattr(self._server, '_context'):
                        self._server = None
                        self._server_startup_uncertain = False
                    elif (type(error) is zmq.ZMQError and error.errno == zmq.EADDRINUSE
                          and type(self._server) is ZmqServiceServer
                          and ZmqServiceServer.__module__ == 'bt_api_transport_zmq.service_v2'
                          and ZmqServiceServer.__qualname__ == 'ZmqServiceServer'
                          and getattr(self._server, '_closed', None) is True
                          and getattr(self._server, '_close_pending', None) is False
                          and self._server._context is None
                          and getattr(self._server, '_socket', object()) is None
                          and getattr(self._server, '_authenticator', object()) is None):
                        # Bind happens after allocation. The exact constructor
                        # returned its EADDRINUSE only after its owner-thread
                        # close completed; arbitrary startup failures remain
                        # uncertain even if some fields happen to be empty.
                        self._server_startup_uncertain = False
                    raise
                self._server_startup_uncertain = False
                with self._lock:
                    self._endpoint = self._server.endpoint
                    if not self._stop.is_set():
                        self._phase = 'RUNNING'
                self._started.set()
                while not self._stop.is_set():
                    self._server.serve_once(100)
        except BaseException:
            with self._lock:
                self._reason = 'START_FAILED' if not self._started.is_set() else 'SERVE_FAILED'
        finally:
            self._started.set()
            self._cleanup_on_owner()

    def _cleanup_on_owner(self):
        """Keep all unresolved handles on the original thread until confirmed."""
        with self._lock:
            self._phase = 'CLOSING'
        while True:
            try:
                if self._server is not None:
                    self._server.close()
                    self._server = None
                if self._server_startup_uncertain:
                    raise ManagementReadHostError('SERVER_STARTUP_UNCERTAIN')
                if self._service is not None:
                    self._service.close()
                    self._service = None
                if self._hold is not None:
                    self._hold.close(nonblocking=True)
                    self._hold = None
            except BaseException:
                with self._lock:
                    self._phase, self._cleanup = 'CLEANUP_UNKNOWN', 'UNKNOWN'
                self._retry.wait()
                self._retry.clear()
                with self._lock:
                    self._phase = 'CLOSING'
                continue
            with self._lock:
                self._phase, self._cleanup = 'STOPPED', 'CONFIRMED'
            return


__all__ = ['TrustedManagementReadListener', 'ExecutionManagementReadHost',
           'ManagementReadHostError', 'ManagementReadHostStatus']
