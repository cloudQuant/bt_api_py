"""Network-free synthetic account-owner tracing and socket fault tests."""
from __future__ import annotations

import json
import multiprocessing
import queue
import sqlite3
import threading
from types import SimpleNamespace

import pytest
import zmq
from bt_api_execution.contracts import CancelIntent
from bt_api_base.service_protocol import (
    WIRE_PROTOCOL_VERSION, WireEnvelope, WireHeader, WireIdentity, WireMessageKind,
    WireRole, WireScope,
)
from bt_api_transport_zmq.service_v2 import RequestOutcomeUnknownError

from tests.integration._arb04_group_reply_loss_support import (
    _AccountDispatchTrace, _CloseAfterProviderDispatchSocket,
    _ReplyLossTransport, _WireFaultState,
)
from tests.integration.test_arb03_dim05_group_partial_fill_service import (
    _InstrumentMappedV2SyntheticFeed, _SyntheticGroupCancelDispatch, _execution_scope,
)

ACCOUNT = 'synthetic-account'
SUBMIT = 'orders.submit_group_leg'
CANCEL = 'orders.cancel_group_leg'


def _request(operation=SUBMIT, message_id='request-b', arguments=None):
    if arguments is None:
        arguments = {'origin_run_id': 'run', 'group_id': 'group', 'leg_id': 'leg'}
    return WireEnvelope(
        WireHeader(protocol_version=WIRE_PROTOCOL_VERSION, kind=WireMessageKind.REQUEST,
                   message_id=message_id, correlation_id=None,
                   identity=WireIdentity(WireRole.SERVICE, 'principal', 'service'),
                   scope=WireScope('domain', account_ref=ACCOUNT), operation=operation,
                   schema_id='sdk.service.call', schema_version=1,
                   sent_at_utc='2026-01-01T00:00:00Z'),
        {'arguments': arguments})


def _dispatch_in_child(trace_path, ledger_path, operation, ready):
    """Exercise the real test provider leaf under an account handler context."""
    trace = _AccountDispatchTrace(trace_path, ledger_path, ACCOUNT)
    request = _request(operation, 'request-a')
    if operation == SUBMIT:
        client_id = 'grp-' + 'a' * 40
        provider = _InstrumentMappedV2SyntheticFeed(
            ledger_path, ('open',), 'provider-account', (('TEST', '1', '2', 'buy-limit'),))
        def handler(_principal, _request):
            return trace.submit(
                _InstrumentMappedV2SyntheticFeed.make_order, provider,
                'TEST', 1.0, 2.0, 'buy-limit', client_order_id=client_id,
                offset='open', post_only=False, reduce_only=False, time_in_force='IOC')
    else:
        provider = _SyntheticGroupCancelDispatch(ledger_path)
        intent = CancelIntent('cancel-a', _execution_scope(ACCOUNT), 'intent-a', 'provider-a')
        with sqlite3.connect(ledger_path) as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS provider_orders '
                               '(client_order_id TEXT PRIMARY KEY, order_json TEXT NOT NULL, fills_json TEXT NOT NULL)')
            connection.execute('INSERT INTO provider_orders VALUES (?, ?, ?)',
                               ('client-a', json.dumps({'order_id': 'provider-a', 'status': 'open',
                                                        'order_revision': 1}), '[]'))
        def handler(_principal, _request):
            return trace.cancel(_SyntheticGroupCancelDispatch.cancel, provider, intent)
    try:
        trace.handle(handler, 'principal', request)
    except TimeoutError:
        if operation != CANCEL:
            raise
    ready.send('committed')
    ready.close()


@pytest.fixture
def paths(tmp_path):
    trace_path = str(tmp_path / 'trace.sqlite')
    ledger_path = str(tmp_path / 'provider.sqlite')
    _AccountDispatchTrace(trace_path, ledger_path, ACCOUNT)
    return trace_path, ledger_path


@pytest.fixture
def owned_socket():
    context = zmq.Context()
    socket = context.socket(zmq.DEALER)
    socket.setsockopt(zmq.IDENTITY, b'synthetic-owned-socket')
    try:
        yield socket
    finally:
        socket.close(linger=0)
        context.term()


def _fault(socket, state, path, request):
    return _CloseAfterProviderDispatchSocket(
        socket, state=state, account_ref=ACCOUNT, operation=request.header.operation,
        message_id=request.header.message_id, provider_ledger_path=path, request=request)


def _seed_trace(path, request):
    from tests.integration._arb04_group_reply_loss_support import _request_trace_key
    message_id, operation, arguments = _request_trace_key(request)
    with sqlite3.connect(path) as connection:
        connection.execute('INSERT INTO dispatch_trace(message_id,operation,arguments_json,account_ref,provider_id) '
                           'VALUES (?,?,?,?,?)', (message_id, operation, arguments, ACCOUNT, 'old-provider'))


@pytest.mark.parametrize('operation', [SUBMIT, CANCEL])
def test_history_and_same_target_other_request_do_not_trigger(operation, paths, owned_socket):
    trace_path, ledger_path = paths
    current = _request(operation, 'request-b')
    _seed_trace(trace_path, current)
    with sqlite3.connect(ledger_path) as connection:
        if operation == SUBMIT:
            connection.execute('CREATE TABLE provider_writes(sequence INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL)')
            connection.execute('INSERT INTO provider_writes(client_order_id) VALUES (?)', ('historical-order',))
        else:
            connection.execute('CREATE TABLE provider_cancel_dispatches(cancel_id TEXT PRIMARY KEY, provider_order_id TEXT NOT NULL, target_intent_id TEXT NOT NULL)')
            connection.execute('INSERT INTO provider_cancel_dispatches VALUES (?, ?, ?)',
                               ('historical-cancel', 'historical-order', 'historical-intent'))
    state = _WireFaultState(operation, 1, 'socket_disconnect_after_provider_dispatch')
    hook = _fault(owned_socket, state, trace_path, current)
    hook.install()
    try:
        assert owned_socket.poll(5, zmq.POLLIN) == 0
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        child = context.Process(target=_dispatch_in_child,
                                args=(trace_path, ledger_path, operation, sender))
        child.start()
        sender.close()
        assert receiver.poll(15) and receiver.recv() == 'committed'
        child.join(15)
        assert child.exitcode == 0
        assert owned_socket.poll(5, zmq.POLLIN) == 0
        assert not owned_socket.closed and state.socket_statistics() == ()
        # An A hook armed only after A committed treats that trace as history.
        request_a = _request(operation, 'request-a')
        context2 = zmq.Context()
        socket_a = context2.socket(zmq.DEALER)
        socket_a.setsockopt(zmq.IDENTITY, b'other-owner')
        try:
            # A different hook must have been armed before A's dispatch.
            hook_a = _fault(socket_a, _WireFaultState(operation, 1,
                            'socket_disconnect_after_provider_dispatch'), trace_path, request_a)
            assert socket_a.poll(1, zmq.POLLIN) == 0
            assert hook_a._provider_dispatch_count() == 0
        finally:
            socket_a.close(linger=0)
            context2.term()
    finally:
        hook.restore()


@pytest.mark.parametrize('operation', [SUBMIT, CANCEL])
def test_matching_request_dispatch_from_other_process_triggers(operation, paths, owned_socket):
    trace_path, ledger_path = paths
    request = _request(operation, 'request-a')
    state = _WireFaultState(operation, 1, 'socket_disconnect_after_provider_dispatch')
    hook = _fault(owned_socket, state, trace_path, request)
    hook.install()
    try:
        context = multiprocessing.get_context('spawn')
        receiver, sender = context.Pipe(duplex=False)
        child = context.Process(target=_dispatch_in_child,
                                args=(trace_path, ledger_path, operation, sender))
        child.start()
        sender.close()
        assert receiver.poll(15) and receiver.recv() == 'committed'
        child.join(15)
        assert child.exitcode == 0
        with pytest.raises(zmq.ZMQError):
            owned_socket.poll(10, zmq.POLLIN)
        events = state.socket_statistics()
        assert [event['phase'] for event in events] == [
            'socket_closed_before_reply_receive', 'native_poll_error_after_close']
        assert events[0]['provider_dispatch_count'] == 1
        assert events[0]['message_id'] == 'request-a'
    finally:
        hook.restore()


def test_wrong_thread_or_missing_handler_cannot_dispatch(paths):
    trace = _AccountDispatchTrace(paths[0], paths[1], ACCOUNT)
    provider = SimpleNamespace(_ledger_path=paths[1])
    called = []
    def leaf(*_args, **_kwargs):
        called.append(True)
    with pytest.raises(AssertionError, match='same-thread'):
        trace.submit(leaf, provider, client_order_id='id')
    errors = queue.Queue()
    def handler(_principal, _request):
        def worker():
            try:
                trace.submit(leaf, provider, client_order_id='id')
            except Exception as error:
                errors.put(error)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
    trace.handle(handler, 'principal', _request())
    assert isinstance(errors.get_nowait(), AssertionError)
    assert called == [] and trace.current is None


def test_existing_poll_override_and_owner_restored(owned_socket, paths):
    def prior(_timeout=-1, _flags=zmq.POLLIN):
        return 0
    owned_socket.poll = prior
    state = _WireFaultState(SUBMIT, 1, 'socket_disconnect_after_provider_dispatch')
    hook = _fault(owned_socket, state, paths[0], _request())
    hook.install()
    errors = queue.Queue()
    def restore():
        try:
            hook.restore()
        except Exception as error:
            errors.put(error)
    thread = threading.Thread(target=restore)
    thread.start()
    thread.join(2)
    assert isinstance(errors.get_nowait(), RuntimeError)
    hook.restore()
    assert vars(owned_socket)['poll'] is prior
    del owned_socket.poll


def test_wrapper_restores_hook_and_checks_owned_socket(owned_socket, paths):
    state = _WireFaultState(SUBMIT, 1, 'socket_disconnect_after_provider_dispatch')
    client = SimpleNamespace(_socket=owned_socket, _owned_sockets=[owned_socket],
                             _socket_binding=object(), _socket_binding_current=lambda _binding: True)
    class Inner:
        _transport = client
        def request(self, _request, *, timeout):
            assert 'poll' in vars(owned_socket)
            raise RuntimeError('pre-dispatch')
    wrapper = _ReplyLossTransport(Inner(), state, ACCOUNT, paths[0])
    with pytest.raises(RuntimeError, match='pre-dispatch'):
        wrapper.request(_request(), timeout=1)
    assert 'poll' not in vars(owned_socket) and state.remaining == 1
    client._owned_sockets.clear()
    with pytest.raises(AssertionError, match='current owned DEALER'):
        wrapper.request(_request(), timeout=1)

@pytest.mark.parametrize('operation', [SUBMIT, CANCEL])
def test_account_process_installs_handler_and_provider_hooks_then_restores(monkeypatch, paths, operation):
    import tests.integration._arb04_group_reply_loss_support as support
    trace_path, ledger_path = paths
    calls = []
    class Provider:
        _ledger_path = ledger_path
    provider = Provider()
    if operation == SUBMIT:
        with sqlite3.connect(ledger_path) as connection:
            connection.execute('CREATE TABLE provider_writes(sequence INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT)')
        def original_leaf(_provider, *args, **kwargs):
            with sqlite3.connect(ledger_path) as connection:
                connection.execute('INSERT INTO provider_writes(client_order_id) VALUES (?)',
                                   (kwargs['client_order_id'],))
            calls.append('committed')
        monkeypatch.setattr(support._InstrumentMappedV2SyntheticFeed, 'make_order', original_leaf)
        invoke = lambda: support._InstrumentMappedV2SyntheticFeed.make_order(provider, client_order_id='client-a')
    else:
        with sqlite3.connect(ledger_path) as connection:
            connection.execute('CREATE TABLE provider_cancel_dispatches(cancel_id TEXT PRIMARY KEY, '
                               'provider_order_id TEXT, target_intent_id TEXT)')
        intent = SimpleNamespace(cancel_id='cancel-a', provider_order_id='provider-a', target_intent_id='intent-a')
        def original_leaf(_provider, current_intent):
            with sqlite3.connect(ledger_path) as connection:
                connection.execute('INSERT INTO provider_cancel_dispatches VALUES (?, ?, ?)',
                                   (current_intent.cancel_id, current_intent.provider_order_id,
                                    current_intent.target_intent_id))
            calls.append('committed')
            raise TimeoutError('committed but reply lost')
        monkeypatch.setattr(support._SyntheticGroupCancelDispatch, 'cancel', original_leaf)
        invoke = lambda: support._SyntheticGroupCancelDispatch.cancel(provider, intent)
    host = SimpleNamespace(_server=SimpleNamespace(_handler=lambda _principal, _request: invoke()))
    original_start = lambda _host: None
    monkeypatch.setattr(support.ServiceHost, 'start', original_start)
    def account_process(*_args):
        support.ServiceHost.start(host)
        if operation == CANCEL:
            with pytest.raises(TimeoutError, match='reply lost'):
                host._server._handler('principal', _request(operation, 'request-a'))
        else:
            host._server._handler('principal', _request(operation, 'request-a'))
    monkeypatch.setattr(support, '_account_host_process', account_process)
    support._arb04_account_host_process(None, None, None,
        {'fault_trace': trace_path, 'provider': ledger_path}, ACCOUNT)
    assert calls == ['committed'] and support.ServiceHost.start is original_start
    current_leaf = (support._InstrumentMappedV2SyntheticFeed.make_order if operation == SUBMIT
                    else support._SyntheticGroupCancelDispatch.cancel)
    assert current_leaf is original_leaf
    with sqlite3.connect(trace_path) as connection:
        assert connection.execute('SELECT message_id,operation FROM dispatch_trace').fetchall() == [
            ('request-a', operation)]


def test_trace_failure_after_commit_preserves_provider_write(paths, monkeypatch):
    trace_path, ledger_path = paths
    trace = _AccountDispatchTrace(trace_path, ledger_path, ACCOUNT)
    provider = _InstrumentMappedV2SyntheticFeed(
        ledger_path, ('open',), 'provider-account', (('TEST', '1', '2', 'buy-limit'),))
    def fail_record(*_args):
        raise OSError('synthetic trace failure')
    monkeypatch.setattr(trace, '_record', fail_record)
    def handler(_principal, _request):
        return trace.submit(_InstrumentMappedV2SyntheticFeed.make_order, provider,
                            'TEST', 1.0, 2.0, 'buy-limit', client_order_id='grp-' + 'a' * 40,
                            offset='open', post_only=False, reduce_only=False, time_in_force='IOC')
    with pytest.raises(OSError, match='trace failure'):
        trace.handle(handler, 'principal', _request())
    with sqlite3.connect(ledger_path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM provider_writes').fetchone()[0] == 1
    assert trace.current is None
