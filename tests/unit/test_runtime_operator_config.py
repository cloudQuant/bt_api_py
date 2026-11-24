import json
import os
import queue
import socket
import threading
from pathlib import Path

import pytest

from bt_api_py import runtime_operator_cli as cli
from bt_api_py.runtime_operator_config import (
    TOKEN_SCHEMA,
    RuntimeOperatorError,
    load_authenticator,
    load_config,
)


def _deployment(tmp_path):
    runtime = tmp_path / 'runtime.json'
    runtime.write_text(json.dumps({'schema': 'bt_api_runtime/v1', 'home': str(tmp_path / 'home'),
        'api': {'transport': 'http_json', 'bind': '127.0.0.1', 'port': 23450,
                'identity_mechanism': 'subject_token'},
        'operators': [{'name': 'alice', 'role': 'operator', 'units': ['*']}],
        'monitor_events': {'enabled': False}, 'units': []}))
    envelope = {'schema': 'bt_api_py.runtime_operator.v1', 'runtime_config_file': str(runtime),
                'subject_token_file': str(tmp_path / 'secret.json'), 'gateway': None, 'monitor': None}
    config = tmp_path / 'operator.json'
    config.write_text(json.dumps(envelope))
    return config, envelope


def test_validate_never_reads_secrets_or_creates_home(tmp_path, capsys):
    path, envelope = _deployment(tmp_path)
    assert cli.main(['validate', '--config', str(path)]) == 0
    assert capsys.readouterr().out.strip() == 'runtime_configuration_valid'
    assert not (tmp_path / 'secret.json').exists()
    assert not (tmp_path / 'home').exists()
    assert repr(load_config(path)).find(envelope['subject_token_file']) < 0


@pytest.mark.parametrize('code', ['listener_bind_failed', 'non_loopback_bind', 'invalid_port'])
def test_start_failure_reports_only_exact_transport_allowlist(code, capsys):
    cli._emit_start_failure(cli.ControlTransportError(code))
    assert capsys.readouterr().err.splitlines() == [
        'runtime_start_failed', f'runtime_start_code={code}']


def test_start_failure_hides_untrusted_code_and_other_exception_types(capsys):
    class DerivedTransportError(cli.ControlTransportError):
        pass
    for error in (cli.ControlTransportError('private-error'), RuntimeError('listener_bind_failed'),
                  DerivedTransportError('listener_bind_failed')):
        cli._emit_start_failure(error)
        assert capsys.readouterr().err.splitlines() == ['runtime_start_failed']


def test_real_port_conflict_reports_bind_code_and_confirms_cleanup(tmp_path, monkeypatch, capsys):
    from bt_api_runtime.events import EventStore
    from bt_api_runtime.supervision.persistence import SingleInstanceLock

    path, envelope = _deployment(tmp_path)
    secret = Path(envelope['subject_token_file'])
    secret.write_text(json.dumps({'schema': TOKEN_SCHEMA,
        'tokens': [{'token': 'synthetic-token-' + 'x' * 32, 'operator': 'alice'}]}))
    secret.chmod(0o600)
    stores = []
    def create_store(*args):
        store = EventStore(*args)
        stores.append(store)
        return store
    monkeypatch.setattr(cli, 'EventStore', create_store)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        runtime_path = Path(envelope['runtime_config_file'])
        runtime = json.loads(runtime_path.read_text())
        runtime['api']['port'] = listener.getsockname()[1]
        runtime_path.write_text(json.dumps(runtime))
        assert cli.main(['run', '--config', str(path)]) == 1
    output = capsys.readouterr()
    assert output.err.splitlines() == ['runtime_start_failed', 'runtime_start_code=listener_bind_failed']
    assert 'runtime_control_listening' not in output.out and 'READY' not in output.out + output.err
    assert len(stores) == 1 and stores[0].close_confirmed
    lock = SingleInstanceLock(str(tmp_path / 'home' / 'runtime.lock'))
    lock.acquire()
    lock.release()


def test_public_example_validates_offline_and_binding_mismatches_fail(tmp_path):
    folder = Path(__file__).resolve().parents[2] / 'examples/services/runtime_operator'
    envelope = json.loads((folder / 'runtime_operator.example.json').read_text())
    envelope['runtime_config_file'] = str(folder / 'runtime.example.yaml')
    path = tmp_path / 'operator.json'
    path.write_text(json.dumps(envelope))
    assert load_config(path).unit_strategies == {'example': '11111111-1111-4111-8111-111111111111'}
    entry = envelope['gateway']['routes'][0]
    entry['client_config_fingerprint'] = '0' * 64
    path.write_text(json.dumps(envelope))
    with pytest.raises(RuntimeOperatorError, match='INVALID_CONFIGURATION'):
        load_config(path)
    assert not (tmp_path / 'home').exists()


@pytest.mark.parametrize('change', ['extra', 'duplicate', 'nonfinite', 'relative', 'missing_operator', 'mismatch'])
def test_rejects_invalid_deployment_without_creating_state(tmp_path, change):
    path, envelope = _deployment(tmp_path)
    if change == 'extra':
        envelope['extra'] = True
    elif change == 'relative':
        envelope['subject_token_file'] = 'relative'
    elif change == 'mismatch':
        envelope['monitor'] = {}
    elif change == 'missing_operator':
        runtime = Path(envelope['runtime_config_file'])
        data = json.loads(runtime.read_text())
        data['operators'] = []
        runtime.write_text(json.dumps(data))
    if change == 'duplicate':
        path.write_text('{"schema":"a","schema":"b"}')
    elif change == 'nonfinite':
        path.write_text('{"schema":NaN}')
    else:
        path.write_text(json.dumps(envelope))
    with pytest.raises(RuntimeOperatorError, match='INVALID_CONFIGURATION'):
        load_config(path)
    assert not (tmp_path / 'home').exists()


def test_tokens_are_protected_mapped_and_not_disclosed(tmp_path):
    path, envelope = _deployment(tmp_path)
    secret = Path(envelope['subject_token_file'])
    token = 'private-token-' + 'x' * 32
    secret.write_text(json.dumps({'schema': TOKEN_SCHEMA, 'tokens': [{'token': token, 'operator': 'alice'}]}))
    secret.chmod(0o600)
    config = load_config(path)
    auth = load_authenticator(config)
    assert auth.authenticate(token).identity.name == 'alice'
    assert not auth.authenticate('wrong-token').authenticated
    assert token not in repr(auth) and token not in repr(config)
    secret.chmod(0o640)
    with pytest.raises(RuntimeOperatorError, match='SUBJECT_TOKENS_INVALID') as caught:
        load_authenticator(config)
    assert token not in str(caught.value)
    secret.chmod(0o600)
    secret.rename(secret.with_suffix('.real'))
    secret.symlink_to(secret.with_suffix('.real'))
    with pytest.raises(RuntimeOperatorError, match='SUBJECT_TOKENS_INVALID'):
        load_authenticator(config)


def test_uncertain_close_waits_for_another_signal(capsys):
    event = threading.Event()
    class Host:
        state = 'running'
        calls = 0
        cleanup_components = ('gateway',)
        def close(self):
            self.calls += 1
            if self.calls == 1:
                self.state = 'close_uncertain'
                raise RuntimeError('private error')
            self.state = 'closed'
    host = Host()
    thread = threading.Thread(target=cli._wait_close, args=(host, event))
    thread.start()
    event.set()
    for _ in range(200):
        if host.calls:
            break
        threading.Event().wait(.005)
    assert host.calls == 1 and thread.is_alive()
    output = capsys.readouterr().err
    assert 'private error' not in output
    assert 'runtime_cleanup_components=gateway' in output
    event.set()
    thread.join(2)
    assert not thread.is_alive() and host.state == 'closed' and host.calls == 2


def test_uncertain_close_waits_for_a_new_owner_stop_file(tmp_path, monkeypatch):
    announced = queue.Queue()
    requested = threading.Event()
    closed = threading.Event()

    class Host:
        state = 'running'
        calls = 0

        def close(self):
            self.calls += 1
            if self.calls == 1:
                self.state = 'close_uncertain'
                raise RuntimeError('cleanup uncertain')
            self.state = 'closed'
            closed.set()

    def capture(message, *, error=False):
        if not error:
            announced.put(message.removeprefix('runtime_control_stop_file '))

    def publish(name):
        marker = tmp_path / name
        marker.touch(mode=0o600, exist_ok=False)
        if os.name == 'nt':
            from bt_api_py._win32_security import _set_owner_only_acl

            _set_owner_only_acl(marker)
        return marker

    monkeypatch.setattr(cli, '_emit', capture)
    host = Host()
    thread = threading.Thread(target=cli._wait_close, args=(host, requested, tmp_path), daemon=True)
    thread.start()
    try:
        first = publish(announced.get(timeout=2))
        next_name = announced.get(timeout=2)
        assert next_name != first.name and first.is_file()
        assert not closed.wait(0.4) and host.calls == 1
        publish(next_name)
        assert closed.wait(2) and host.calls == 2
    finally:
        requested.set()
        thread.join(2)
        if thread.is_alive():
            requested.set()
            thread.join(2)
    assert not thread.is_alive() and host.state == 'closed'


def test_cleanup_diagnostic_filters_untrusted_values(capsys):
    class Host:
        cleanup_components = ('monitor', 'private-token', 'gateway', object())

    cli._emit_cleanup_uncertain(Host())
    assert capsys.readouterr().err.splitlines() == [
        'runtime_cleanup_uncertain; signal to retry',
        'runtime_cleanup_components=monitor,gateway',
    ]
    Host.cleanup_components = ('private-path',)
    cli._emit_cleanup_uncertain(Host())
    assert capsys.readouterr().err.splitlines()[-1] == 'runtime_cleanup_components=unknown'
    cli._emit_cleanup_uncertain(event_store=True)
    assert capsys.readouterr().err.splitlines()[-1] == 'runtime_cleanup_components=event_store'


@pytest.mark.parametrize('fail_before_host', [True, False])
def test_event_store_close_failure_waits_for_signal_without_reclosing_host(
        tmp_path, monkeypatch, capsys, fail_before_host):
    path, _ = _deployment(tmp_path)
    config = load_config(path)
    signals = {}
    monkeypatch.setattr(cli.signal, 'getsignal', lambda sig: None)
    monkeypatch.setattr(cli.signal, 'signal', lambda sig, fn: signals.__setitem__(sig, fn))
    monkeypatch.setattr(cli, 'load_authenticator', lambda _: object())
    monkeypatch.setattr(cli, 'build_bridges', lambda *args: (None, None))
    first_failed = threading.Event()
    class Store:
        available = True
        close_confirmed = False
        calls = 0
        def __init__(self, *args):
            pass
        def close(self):
            self.calls += 1
            if self.calls == 1:
                first_failed.set()
                raise RuntimeError('private-store-path')
            self.available = False
            self.close_confirmed = True
    store = Store()
    monkeypatch.setattr(cli, 'EventStore', lambda *args: store)
    class Host:
        state = 'new'
        bound_address = ('127.0.0.1', 23450)
        closes = 0
        def start(self):
            self.state = 'running'
        def close(self):
            self.closes += 1
            self.state = 'closed'
    host = Host()
    def factory(*args, **kwargs):
        if fail_before_host:
            raise RuntimeError('private-construction-path')
        return host
    monkeypatch.setattr(cli, 'RuntimeServiceHost', factory)
    result = []
    thread = threading.Thread(target=lambda: result.append(cli._run(config)))
    thread.start()
    if not fail_before_host:
        for _ in range(400):
            if host.state == 'running':
                break
            threading.Event().wait(.005)
        signals[cli.signal.SIGTERM](cli.signal.SIGTERM, None)
    assert first_failed.wait(2)
    assert thread.is_alive() and store.calls == 1
    assert host.closes == (0 if fail_before_host else 1)
    signals[cli.signal.SIGTERM](cli.signal.SIGTERM, None)
    thread.join(2)
    assert not thread.is_alive() and store.calls == 2
    assert host.closes == (0 if fail_before_host else 1)
    assert result == [1 if fail_before_host else 0]
    output = capsys.readouterr()
    assert 'private-store-path' not in output.err + output.out
    assert 'private-construction-path' not in output.err + output.out
    assert 'runtime_cleanup_components=event_store' in output.err
