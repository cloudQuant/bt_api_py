"""Synthetic listener, account journal, and fault injection; no network."""
from dataclasses import replace
import os
import queue
import sqlite3
import threading
import time

import pytest
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_execution.account_journal_host import AccountJournalHostError
from bt_api_execution.isolated_local_parent_events import IsolatedLocalParentEventReader
from bt_api_py.execution_management_audit import (
    AuditStartupCleanupError, ExecutionManagementAuditError, ExecutionManagementReadAudit,
)
from bt_api_py.execution_management_read_grant import ExecutionManagementReadGrant, PARENT_READ_V1
from bt_api_py.execution_management_read_host import (
    ExecutionManagementReadHost, ManagementReadHostError, TrustedManagementReadListener,
)
from bt_api_py.execution_management_read_service import (
    ExecutionManagementReadService, ManagementServiceStartupCleanupError, PORT,
)
from bt_api_py.execution_parent_grant import SUBMIT
from bt_api_transport_zmq.server import CurveServerCredentials
from tests.unit.test_service_registry import _next_request, _register
from test_iter65_atomic_submission_intake import atomic
from test_iter65_fresh_submission_intake import KEY


class _FakeServer:
    """An explicit test leaf, not evidence of CURVE or a network listener."""

    instances = []
    close_failures = 0
    block = None

    def __init__(self, endpoint, identity, principals, handler, *, credentials):
        self._context = object()
        self.endpoint = endpoint
        self.handler = handler
        self.identity = identity
        self.principals = principals
        self.credentials = credentials
        self.thread = threading.current_thread()
        self.calls = queue.Queue()
        self.closed = False
        self.close_threads = []
        self.__class__.instances.append(self)

    def serve_once(self, timeout_ms):
        assert threading.current_thread() is self.thread
        if self.block is not None:
            self.block[0].set()
            self.block[1].wait(5)
        try:
            principal, request, done = self.calls.get(timeout=timeout_ms / 1000)
        except queue.Empty:
            return False
        done.put(self.handler(principal, request))
        return True

    def close(self):
        assert threading.current_thread() is self.thread
        self.close_threads.append(threading.current_thread())
        if type(self).close_failures:
            type(self).close_failures -= 1
            raise RuntimeError('synthetic close failure')
        self.closed = True


def _wait(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        threading.Event().wait(.01)
    return False


@pytest.fixture
def listener(tmp_path, atomic, monkeypatch):
    import bt_api_transport_zmq.service_v2 as transport
    rig, store, _ = atomic
    original = rig.parent()
    assert rig.call(SUBMIT, original)['ok']
    app = WireIdentity(WireRole.APPLICATION_CLIENT, 'management-principal', 'management-actor')
    scope = replace(rig.scope, run_id=None)
    grant = ExecutionManagementReadGrant('management-instance', app, scope, 'policy-v1',
                                          frozenset({KEY}), frozenset({PARENT_READ_V1}))
    audit_root = tmp_path / 'management-audit'
    audit_root.mkdir(mode=0o700)
    public, secret = zmq.curve_keypair()
    client_public, _ = zmq.curve_keypair()
    selected = TrustedManagementReadListener(
        host=rig.host, local_scope=rig.target, grant=grant, scope_ref='management-scope',
        service_id='management-service', service_identity=WireIdentity(WireRole.SERVICE, 'server', 'actor'),
        audit_root=audit_root, endpoint='tcp://127.0.0.1:48383',
        principals={client_public: app.principal_ref}, credentials=CurveServerCredentials(public, secret),
        lease_seconds=10)
    _FakeServer.instances = []
    _FakeServer.close_failures = 0
    _FakeServer.block = None
    monkeypatch.setattr(transport, 'ZmqServiceServer', _FakeServer)
    owner = ExecutionManagementReadHost(selected)
    yield owner, rig, store, grant, original, audit_root
    _FakeServer.block = None
    if owner._thread is not None and owner._thread.is_alive():
        owner.stop(timeout=2)


class _FakeClient:
    def __init__(self, server):
        self.server = server

    def handle(self, principal, request):
        done = queue.Queue()
        self.server.calls.put((principal, request, done))
        return done.get(timeout=3)


def test_server_passes_authenticated_principal_to_real_local_service(listener):
    owner, rig, _, grant, original, audit = listener
    assert owner.start(timeout=2).outcome == 'RUNNING'
    server = _FakeServer.instances[0]
    client = _FakeClient(server)
    assert server.thread is owner._thread and server.handler.__self__ is owner._service
    registered = _register(client, identity=grant.identity, scope=grant.scope,
        principal=grant.identity.principal_ref, instance_id=grant.instance_id, port=PORT,
        scope_ref='management-scope', operations=(PARENT_READ_V1,))
    assert registered['ok'], registered
    bound = registered['result']
    request = _next_request(PARENT_READ_V1, {'registration_id': bound['registration_id'],
        'service_epoch': bound['service_epoch'], 'instance_id': grant.instance_id,
        'arguments': {'schema_version': 1, 'parent_ref': original['spec']['parent_ref'],
                      'query_id': 'query-1', 'grant_revision': grant.revision}},
        identity=grant.identity, scope=grant.scope)
    denied = client.handle('different-transport-principal', request)
    assert not denied['ok']
    observed = client.handle(grant.identity.principal_ref, request)
    assert observed['ok'] and observed['result']['snapshot']['status'] == 'OBSERVED'
    assert observed['result']['snapshot']['source'] == 'LOCAL_JOURNAL'
    with sqlite3.connect(str(audit / 'execution-management-read.sqlite3')) as db:
        assert db.execute('SELECT count(*) FROM audit_attempts').fetchone()[0] == 1
    result = owner.stop(timeout=2)
    assert result.outcome == 'STOPPED' and result.cleanup == 'CONFIRMED'
    assert server.closed and not rig.host._observer_holds


def test_blocked_listener_retains_account_until_same_thread_cleanup(listener):
    owner, rig, *_ = listener
    entered, release = threading.Event(), threading.Event()
    _FakeServer.block = (entered, release)
    try:
        assert owner.start(timeout=2).outcome == 'RUNNING'
        assert entered.wait(2)
        assert owner.stop(timeout=.01).outcome == 'STILL_RUNNING'
        with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
            rig.host.close()
    finally:
        release.set()
        assert _wait(lambda: owner.status().outcome == 'STOPPED')
    assert not rig.host._observer_holds


def test_blocked_server_start_keeps_owner_and_hold_until_it_returns(listener, monkeypatch):
    import bt_api_transport_zmq.service_v2 as transport
    owner, rig, *_ = listener
    entered, release = threading.Event(), threading.Event()
    class SlowServer(_FakeServer):
        def __init__(self, *args, **kwargs):
            self._context = object()
            entered.set()
            assert release.wait(5)
            super().__init__(*args, **kwargs)
    monkeypatch.setattr(transport, 'ZmqServiceServer', SlowServer)
    try:
        assert owner.start(timeout=.01).outcome == 'STILL_RUNNING'
        assert entered.wait(2)
        assert owner.stop(timeout=.01).outcome == 'STILL_RUNNING'
        with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
            rig.host.close()
    finally:
        release.set()
        assert _wait(lambda: owner.status().outcome == 'STOPPED')


def test_server_failure_before_resource_allocation_closes_service(listener, monkeypatch):
    import bt_api_transport_zmq.service_v2 as transport
    owner, rig, *_ = listener
    class InvalidServer:
        def __init__(self, *_args, **_kwargs):
            raise ValueError('validation before context')
    monkeypatch.setattr(transport, 'ZmqServiceServer', InvalidServer)
    owner.start(timeout=2)
    assert _wait(lambda: not owner._thread.is_alive())
    assert owner.status().outcome == 'FAILED' and owner.status().cleanup == 'CONFIRMED'
    assert not rig.host._observer_holds


def test_server_start_exception_after_native_open_remains_unknown(listener, monkeypatch):
    import bt_api_transport_zmq.service_v2 as transport
    owner, rig, *_ = listener
    class UncertainServer:
        def __init__(self, *_args, **_kwargs):
            self._context = None
            raise RuntimeError('native allocation did not return')
        def close(self):
            pass
    monkeypatch.setattr(transport, 'ZmqServiceServer', UncertainServer)
    owner.start(timeout=2)
    assert _wait(lambda: owner.status().phase == 'CLEANUP_UNKNOWN')
    assert owner.stop(timeout=.01).outcome == 'STILL_RUNNING'
    with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
        rig.host.close()
    # Only this fake proves it allocated no native resources.
    owner._server_startup_uncertain = False
    assert owner.stop(timeout=2).cleanup == 'CONFIRMED'


def test_service_early_validation_failure_has_no_owned_reader(listener):
    owner, rig, _, _, _, audit = listener
    object.__setattr__(owner._selection, 'audit_root', audit / 'missing')
    owner.start(timeout=2)
    assert _wait(lambda: not owner._thread.is_alive())
    assert owner.status().outcome == 'FAILED' and owner.status().cleanup == 'CONFIRMED'
    assert not rig.host._observer_holds


def test_listener_close_failure_does_not_close_service_and_retries_on_owner(listener):
    owner, rig, *_ = listener
    assert owner.start(timeout=2).outcome == 'RUNNING'
    server = _FakeServer.instances[0]
    _FakeServer.close_failures = 1
    assert owner.stop(timeout=.1).outcome == 'STILL_RUNNING'
    assert _wait(lambda: owner.status().phase == 'CLEANUP_UNKNOWN')
    assert owner._service is not None and rig.host._observer_holds
    assert owner.stop(timeout=2).outcome == 'STOPPED'
    assert len(server.close_threads) == 2 and all(item is owner._thread for item in server.close_threads)
    assert not rig.host._observer_holds


def test_thread_start_and_hold_cleanup_failure_remain_retryable(listener, monkeypatch):
    owner, rig, *_ = listener
    original_close = type(rig.host.retain_observer()).close
    # Remove the probe hold before injecting the error.
    next(iter(rig.host._observer_holds)).close()
    calls = []
    def close_once(self, *args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError('synthetic hold busy')
        return original_close(self, *args, **kwargs)
    monkeypatch.setattr('bt_api_execution.account_journal_host._ObserverHold.close', close_once)
    monkeypatch.setattr(threading.Thread, 'start', lambda self: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(ManagementReadHostError, match='CLEANUP_UNKNOWN'):
        owner.start(timeout=.1)
    assert owner.status().outcome == 'CLEANUP_UNKNOWN' and rig.host._observer_holds
    assert owner.stop(timeout=.1).cleanup == 'CONFIRMED'
    assert not rig.host._observer_holds


def test_activation_interruption_keeps_listener_hold_for_retry(listener):
    owner, rig, *_ = listener
    class InterruptedHolds(set):
        def add(self, hold):
            super().add(hold)
            raise KeyboardInterrupt()
        def discard(self, hold):
            raise RuntimeError('synthetic release failure')
    holds = InterruptedHolds()
    rig.host._observer_holds = holds
    with pytest.raises(ManagementReadHostError, match='CLEANUP_UNKNOWN'):
        owner.start(timeout=.1)
    assert owner._hold in holds and owner._thread is None
    with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
        rig.host.close()
    rig.host._observer_holds = set(holds)
    assert owner.stop(timeout=.1).cleanup == 'CONFIRMED'
    assert not rig.host._observer_holds


def test_reader_unreturned_connection_never_releases_host_hold(listener, monkeypatch):
    owner, rig, *_ = listener
    import bt_api_execution.isolated_local_parent_events as isolated
    original = isolated.sqlite3.connect
    def failed(*args, **kwargs):
        if kwargs.get('uri') is True:
            raise sqlite3.Error('synthetic open did not return')
        return original(*args, **kwargs)
    monkeypatch.setattr(isolated.sqlite3, 'connect', failed)
    status = owner.start(timeout=2)
    assert _wait(lambda: owner.status().phase == 'CLEANUP_UNKNOWN')
    assert owner.stop(timeout=.01).outcome == 'STILL_RUNNING'
    with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
        rig.host.close()
    # Only the synthetic stub proves no native connection was ever opened.
    reader = owner._service._ExecutionManagementReadService__reader._reader
    reader._open_pending = False
    assert owner.stop(timeout=2).cleanup == 'CONFIRMED'


def test_audit_close_never_reuses_a_failed_os_descriptor(tmp_path, monkeypatch):
    audit = ExecutionManagementReadAudit(tmp_path)
    file_fd, root_fd = audit._file_fd, audit._root_fd
    original = os.close
    calls = []
    def close(fd):
        calls.append(fd)
        if fd == root_fd:
            raise OSError('synthetic unknown close')
        return original(fd)
    monkeypatch.setattr(os, 'close', close)
    with pytest.raises(OSError):
        audit.close()
    assert audit._file_fd is None and audit._root_fd is None
    with pytest.raises(ExecutionManagementAuditError, match='AUDIT_CLEANUP_UNKNOWN'):
        audit.close()
    assert calls.count(file_fd) == calls.count(root_fd) == 1
    # Synthetic stub proves root_fd was not closed; clean up only that test fd.
    original(root_fd)


def test_audit_open_without_returned_fd_is_unknown(tmp_path, monkeypatch):
    original = os.open
    def fail_root(path, *args, **kwargs):
        if path == str(tmp_path):
            raise OSError('synthetic unreturned descriptor')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', fail_root)
    with pytest.raises(AuditStartupCleanupError) as error:
        ExecutionManagementReadAudit(tmp_path)
    audit = error.value.audit
    with pytest.raises(ExecutionManagementAuditError, match='AUDIT_CLEANUP_UNKNOWN'):
        audit.close()
    # The test stub proves it returned before performing an OS open.
    audit._fd_open_pending = False
    audit.close()


def test_audit_connect_without_returned_handle_is_unknown(tmp_path, monkeypatch):
    import bt_api_py.execution_management_audit as module
    monkeypatch.setattr(module.sqlite3, 'connect', lambda *a, **k: (_ for _ in ()).throw(sqlite3.Error()))
    with pytest.raises(AuditStartupCleanupError) as error:
        ExecutionManagementReadAudit(tmp_path)
    audit = error.value.audit
    with pytest.raises(ExecutionManagementAuditError, match='AUDIT_CLEANUP_UNKNOWN'):
        audit.close()
    # This stub proves it did not open a connection; no real open has that proof.
    audit._sql_open_pending = False
    audit.close()


def test_audit_connection_close_failure_exposes_owner_retry(tmp_path, monkeypatch):
    import bt_api_py.execution_management_audit as module
    original = module.sqlite3.connect
    class FailingConnection:
        def __init__(self, actual):
            self.actual = actual
            self.close_calls = 0
        def execute(self, *_args, **_kwargs):
            raise sqlite3.Error('initialization')
        def close(self):
            self.close_calls += 1
            if self.close_calls <= 2:
                raise sqlite3.Error('close')
            self.actual.close()
    monkeypatch.setattr(module.sqlite3, 'connect',
                        lambda *args, **kwargs: FailingConnection(original(*args, **kwargs)))
    with pytest.raises(AuditStartupCleanupError) as error:
        ExecutionManagementReadAudit(tmp_path)
    audit = error.value.audit
    assert audit._active_connection is not None and audit._file_fd is not None
    audit.close()
    assert audit._closed and audit._active_connection is None


def test_service_exposes_audit_cleanup_handle_on_failed_initialization(listener, monkeypatch):
    owner, rig, _, grant, _, audit = listener
    import bt_api_py.execution_management_read_service as module
    class AuditFail:
        def __init__(self, _path):
            self.attempts = 0
            raise AuditStartupCleanupError(self)
        def close(self):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError()
    monkeypatch.setattr(module, 'ExecutionManagementReadAudit', AuditFail)
    with pytest.raises(ManagementServiceStartupCleanupError) as error:
        ExecutionManagementReadService(host=rig.host, local_scope=rig.target, grant=grant,
            scope_ref='scope', service_id='management-service',
            authenticated_principal=grant.identity.principal_ref, audit_root=audit)
    service = error.value.service
    assert service._ExecutionManagementReadService__audit is not None
    service.close()
    assert not rig.host._observer_holds


def test_service_releases_reader_after_unsupported_audit_platform(listener, monkeypatch):
    _, rig, _, grant, _, audit = listener
    original_init = ExecutionManagementReadAudit.__init__
    def unsupported_platform(instance, root):
        with monkeypatch.context() as scoped:
            scoped.delattr(os, 'geteuid')
            original_init(instance, root)
    monkeypatch.setattr(ExecutionManagementReadAudit, '__init__', unsupported_platform)
    with pytest.raises(ExecutionManagementAuditError, match='INVALID_AUDIT_CONFIGURATION'):
        ExecutionManagementReadService(host=rig.host, local_scope=rig.target, grant=grant,
            scope_ref='scope', service_id='management-service',
            authenticated_principal=grant.identity.principal_ref, audit_root=audit)
    assert not rig.host._observer_holds


def test_registry_unreturned_handle_retains_reader_and_host(listener, monkeypatch):
    _, rig, _, grant, _, audit = listener
    import bt_api_py.execution_management_read_service as module
    def fail(*_args, **_kwargs):
        raise sqlite3.Error('synthetic unreturned registry connection')
    monkeypatch.setattr(module, 'ServiceRegistry', fail)
    with pytest.raises(ManagementServiceStartupCleanupError) as error:
        ExecutionManagementReadService(host=rig.host, local_scope=rig.target, grant=grant,
            scope_ref='scope', service_id='management-service',
            authenticated_principal=grant.identity.principal_ref, audit_root=audit)
    service = error.value.service
    assert rig.host._observer_holds
    with pytest.raises(module.ExecutionManagementReadServiceError, match='REGISTRY_OPEN_UNCERTAIN'):
        service.close()
    # The stub did not allocate a connection; production cannot infer this.
    service._ExecutionManagementReadService__registry_open_pending = False
    service.close()
    assert not rig.host._observer_holds


def test_registry_failed_cleanup_exposes_retry_handle(listener, monkeypatch):
    _, rig, _, grant, _, audit = listener
    import bt_api_py.execution_management_read_service as module
    class FailedRegistry:
        def __init__(self):
            self._db = object()
            self.attempts = 0
        def close(self):
            self.attempts += 1
            if self.attempts == 1:
                raise sqlite3.Error('synthetic close failure')
            self._db = None
    registry = FailedRegistry()
    def fail(*_args, **_kwargs):
        raise module.ServiceRegistryStartupCleanupError(registry)
    monkeypatch.setattr(module, 'ServiceRegistry', fail)
    with pytest.raises(ManagementServiceStartupCleanupError) as error:
        ExecutionManagementReadService(host=rig.host, local_scope=rig.target, grant=grant,
            scope_ref='scope', service_id='management-service',
            authenticated_principal=grant.identity.principal_ref, audit_root=audit)
    assert rig.host._observer_holds and registry.attempts == 1
    error.value.service.close()
    assert registry.attempts == 2 and not rig.host._observer_holds
