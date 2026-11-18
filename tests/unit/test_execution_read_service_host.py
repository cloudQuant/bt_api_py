import copy
import json
from pathlib import Path

import pytest

from bt_api_py.execution_read_service_host import load_config, parse_config, ReadServiceHost, ReadServiceError

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples/services/execution-read-service.example.json'


def example():
    return json.loads(EXAMPLE.read_text())


def test_offline_validation_and_strict_fields(tmp_path):
    raw = example()
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(raw))
    assert load_config(config).storage_id == raw['storage']['storage_id']
    assert list(tmp_path.iterdir()) == [config]
    for change in (
        lambda v: v.update(unexpected=1),
        lambda v: v['service_identity'].update(role='application_client'),
        lambda v: v['client_identity'].update(role='service'),
        lambda v: v['wire_scope'].update(run_id='run'),
        lambda v: v['wire_scope'].update(account_ref='other'),
        lambda v: v['storage'].update(audit_root='/private/execution-journal/audit'),
        lambda v: v['storage'].update(journal_root='/private/execution-audit/journal'),
        lambda v: v['storage'].update(storage_id='C8BA2323-93DC-4A31-A0E5-E93098CD4544'),
        lambda v: v['grant']['operations'].append('execution.parent.submit'),
        lambda v: v['listener'].update(endpoint='tcp://0.0.0.0:17869'),
    ):
        mutated = copy.deepcopy(raw)
        change(mutated)
        with pytest.raises((ValueError, TypeError)):
            parse_config(mutated)
    config.write_text('{"schema":1,"schema":2}')
    with pytest.raises(ReadServiceError):
        load_config(config)


def test_partial_start_cleanup_closes_owned_journal(tmp_path, monkeypatch):
    raw = example()
    root = tmp_path.resolve() / 'journal'; root.mkdir(mode=0o700)
    audit = tmp_path.resolve() / 'audit'; audit.mkdir(mode=0o700)
    raw['storage'].update(journal_root=str(root), audit_root=str(audit))
    from bt_api_py import execution_read_service_host as module
    import zmq
    public, secret = zmq.curve_keypair()
    client, _ = zmq.curve_keypair()
    monkeypatch.setattr(module, '_keys', lambda _: (public, secret, client))
    class FailListener:
        def __init__(self, *_, **kwargs):
            raise RuntimeError('failed')
    monkeypatch.setattr(module, 'TrustedManagementReadListener', FailListener)
    host = ReadServiceHost(parse_config(raw))
    with pytest.raises(RuntimeError):
        host.start()
    assert host.journal is None
    # Host lock was released after the failure; the same storage can reopen.
    from bt_api_execution.account_journal_host import AccountJournalHost
    again = AccountJournalHost(root=str(root), scope=host.config.local_scope, storage_id=host.config.storage_id)
    again.close()


def test_old_schema_rejected_without_upgrade(tmp_path):
    import sqlite3
    from bt_api_execution.account_journal_host import AccountJournalHost
    raw = example()
    root = tmp_path.resolve() / 'journal'; root.mkdir(mode=0o700)
    audit = tmp_path.resolve() / 'audit'; audit.mkdir(mode=0o700)
    raw['storage'].update(journal_root=str(root), audit_root=str(audit))
    config = parse_config(raw)
    host = AccountJournalHost(root=str(root), scope=config.local_scope, storage_id=config.storage_id)
    host.close()
    database, = root.glob('*.sqlite3')
    with sqlite3.connect(database) as db:
        db.execute("UPDATE execution_meta SET value='27' WHERE key='schema_version'")
    from bt_api_py.execution_read_service_host import _check_existing
    with pytest.raises(ReadServiceError):
        _check_existing(config)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT value FROM execution_meta WHERE key='schema_version'").fetchone() == ('27',)




def test_host_single_use_and_retry_close(tmp_path, monkeypatch):
    import zmq
    from types import SimpleNamespace
    from bt_api_py import execution_read_service_host as module
    raw = example()
    root = tmp_path.resolve() / 'journal'; root.mkdir(mode=0o700)
    audit = tmp_path.resolve() / 'audit'; audit.mkdir(mode=0o700)
    raw['storage'].update(journal_root=str(root), audit_root=str(audit))
    public, secret = zmq.curve_keypair()
    client, _ = zmq.curve_keypair()
    monkeypatch.setattr(module, '_keys', lambda _: (public, secret, client))
    class Listener:
        def __init__(self, selection):
            self.stops = 0
        def start(self, **kwargs):
            return SimpleNamespace(outcome='RUNNING')
        def stop(self, **kwargs):
            self.stops += 1
            return SimpleNamespace(outcome='STILL_RUNNING' if self.stops == 1 else 'STOPPED',
                                   cleanup='UNKNOWN' if self.stops == 1 else 'CONFIRMED')
    monkeypatch.setattr(module, 'ExecutionManagementReadHost', Listener)
    host = ReadServiceHost(parse_config(raw))
    host.start()
    listener, journal = host.listener, host.journal
    assert host.state == 'running'
    with pytest.raises(ReadServiceError, match='INVALID_STATE'):
        host.start()
    assert host.listener is listener and host.journal is journal
    with pytest.raises(ReadServiceError, match='CLEANUP_UNKNOWN'):
        host.close()
    assert host.state == 'close_uncertain' and host.listener is listener and host.journal is journal
    with pytest.raises(ReadServiceError, match='INVALID_STATE'):
        host.start()
    host.close()
    assert host.state == 'closed' and host.listener is None and host.journal is None
    host.close()
    with pytest.raises(ReadServiceError, match='INVALID_STATE'):
        host.start()


def test_partial_start_uncertain_retains_owner_until_retry(tmp_path, monkeypatch):
    import zmq
    from types import SimpleNamespace
    from bt_api_py import execution_read_service_host as module
    raw = example()
    root = tmp_path.resolve() / 'journal'; root.mkdir(mode=0o700)
    audit = tmp_path.resolve() / 'audit'; audit.mkdir(mode=0o700)
    raw['storage'].update(journal_root=str(root), audit_root=str(audit))
    public, secret = zmq.curve_keypair()
    client, _ = zmq.curve_keypair()
    monkeypatch.setattr(module, '_keys', lambda _: (public, secret, client))
    class Listener:
        def __init__(self, selection):
            self.stops = 0
        def start(self, **kwargs):
            raise RuntimeError('injected start failure')
        def stop(self, **kwargs):
            self.stops += 1
            return SimpleNamespace(outcome='STILL_RUNNING' if self.stops == 1 else 'STOPPED',
                                   cleanup='UNKNOWN' if self.stops == 1 else 'CONFIRMED')
    monkeypatch.setattr(module, 'ExecutionManagementReadHost', Listener)
    host = ReadServiceHost(parse_config(raw))
    with pytest.raises(ReadServiceError, match='CLEANUP_UNKNOWN'):
        host.start()
    assert host.state == 'close_uncertain' and host.listener is not None and host.journal is not None
    host.close()
    assert host.state == 'closed' and host.listener is None and host.journal is None


def test_darwin_reboot_shifted_device_opens_readonly_journal(tmp_path, monkeypatch):
    import hashlib
    import sys
    from bt_api_execution.account_journal_host import AccountJournalHost
    from bt_api_execution.contracts import ExecutionScope
    from bt_api_py.execution_read_service_host import _check_existing

    monkeypatch.setattr(sys, 'platform', 'darwin')
    raw = example()
    root = tmp_path.resolve() / 'journal'
    root.mkdir(mode=0o700)
    audit = tmp_path.resolve() / 'audit'
    audit.mkdir(mode=0o700)
    raw['storage'].update(journal_root=str(root), audit_root=str(audit))
    cfg = parse_config(raw)
    with AccountJournalHost(root=str(root), scope=cfg.local_scope, storage_id=raw['storage']['storage_id']):
        pass
    account_hash = hashlib.sha256(cfg.local_scope.account_key.encode()).hexdigest()
    owner_path = root / f'.execution-intake-{account_hash}.owner'
    manifest = json.loads(owner_path.read_text())
    manifest['device'] = manifest['device'] + 1
    owner_path.chmod(0o600)
    owner_path.write_text(json.dumps(manifest))
    owner_path.chmod(0o400)
    _check_existing(cfg)
