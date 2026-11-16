import json
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
    assert 'private error' not in capsys.readouterr().err
    event.set()
    thread.join(2)
    assert not thread.is_alive() and host.state == 'closed' and host.calls == 2


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
