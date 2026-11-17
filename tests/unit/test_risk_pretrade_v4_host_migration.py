"""Synthetic Risk host pretrade migration wiring; no external service."""
from dataclasses import replace
import multiprocessing
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from bt_api_py import risk_service_config as config_module
from bt_api_py import risk_service_host as host_module
from bt_api_py.risk_service_config import RiskPretradeUpgrade, RiskServiceConfigError
from bt_api_py.risk_service_host import RiskServiceHost, RiskServiceStartError
from tests.unit.test_iter64_risk_service_config import document
from tests.unit.test_iter64_risk_service_host import setup, _isolate_process_cleanup_state


def parse(document, monkeypatch):
    monkeypatch.setattr(config_module, 'read_key_file', lambda path, *, secret: path.name.encode())
    return config_module.parse_risk_service_config(document, path=Path('/synthetic/config.yaml'))


def identities(config):
    from bt_api_risk.core.admission import AccountScope
    from bt_api_risk.risk_order_journal import RiskOrderJournal
    scope = AccountScope(config.provider, config.account_scope, config.environment)
    journal = RiskOrderJournal(config.risk_database, writer_id='synthetic-identity', _defer_initialization=True)
    db = os.stat(config.risk_database, follow_symlinks=False)
    lock = os.stat(journal._lock_path(scope), follow_symlinks=False)
    return (db.st_dev, db.st_ino), (lock.st_dev, lock.st_ino)


def upgrade_config(config):
    database, lock = identities(config)
    return replace(config, pretrade_schema_version=4,
                   pretrade_upgrade_from_v3=RiskPretradeUpgrade(database, lock, True))


def seed_v3(tmp_path):
    from bt_api_py.risk_service_config import load_risk_service_config
    config = load_risk_service_config(setup(tmp_path))
    old = RiskServiceHost(config)
    old.open_storage()
    assert old.ledger._schema_version == 3
    assert old.close() is True
    return config


def test_parser_defaults_to_v3_and_accepts_explicit_v4(monkeypatch):
    config = parse(document(), monkeypatch)
    assert config.pretrade_schema_version == 3 and config.pretrade_upgrade_from_v3 is None
    doc = document()
    doc['pretrade'] = {'schema_version': 4, 'upgrade_from_v3': {
        'expected_database_identity': [1, 2], 'expected_lock_identity': [1, 3], 'maintenance_window': True}}
    config = parse(doc, monkeypatch)
    assert config.pretrade_schema_version == 4
    assert config.pretrade_upgrade_from_v3 == RiskPretradeUpgrade((1, 2), (1, 3), True)
    doc['pretrade'] = {'schema_version': 4}
    assert parse(doc, monkeypatch).pretrade_upgrade_from_v3 is None


@pytest.mark.parametrize('raw', [
    {}, {'schema_version': True}, {'schema_version': 5},
    {'schema_version': 3, 'upgrade_from_v3': {'expected_database_identity': [1,2],
         'expected_lock_identity': [1,3], 'maintenance_window': True}},
    {'schema_version': 4, 'upgrade_from_v3': {'expected_database_identity': [True,2],
         'expected_lock_identity': [1,3], 'maintenance_window': True}},
    {'schema_version': 4, 'upgrade_from_v3': {'expected_database_identity': [1,2],
         'expected_lock_identity': [1,3], 'maintenance_window': 1}},
    {'schema_version': 4, 'unknown': True},
])
def test_bad_pretrade_configuration_is_rejected_before_key_access(monkeypatch, raw):
    doc = document()
    doc['pretrade'] = raw
    monkeypatch.setattr(config_module, 'read_key_file', lambda *a, **k: pytest.fail('unexpected key read'))
    with pytest.raises(RiskServiceConfigError):
        config_module.parse_risk_service_config(doc, path=Path('/synthetic/config.yaml'))


def test_upgrade_uses_original_fence_and_reopens_v4_only(tmp_path, monkeypatch):
    from bt_api_risk.pretrade import ledger as ledger_module
    config = seed_v3(tmp_path)
    upgrade = upgrade_config(config)
    actual = ledger_module.migrate_v3_to_v4
    calls = []
    def checked(journal, fence, **kwargs):
        manager = kwargs['fence_manager']
        assert manager._owner.borrow_binding[1] is journal
        assert manager._owner.borrow_binding[2] is fence
        assert kwargs['expected_database_identity'] == upgrade.pretrade_upgrade_from_v3.expected_database_identity
        assert kwargs['expected_lock_identity'] == upgrade.pretrade_upgrade_from_v3.expected_lock_identity
        calls.append(True)
        return actual(journal, fence, **kwargs)
    monkeypatch.setattr(ledger_module, 'migrate_v3_to_v4', checked)
    host = RiskServiceHost(upgrade)
    host.open_storage()
    assert host.ledger._schema_version == 4 and calls == [True]
    assert host.service_host is None and host._runtimes == []
    assert host.close() is True
    again = RiskServiceHost(upgrade)
    with pytest.raises(RiskServiceStartError, match='RISK_SERVICE_STORAGE_SCHEMA_INVALID'):
        again.open_storage()
    assert again.close() is True and calls == [True]
    reopened = RiskServiceHost(replace(upgrade, pretrade_upgrade_from_v3=None))
    reopened.open_storage()
    assert reopened.ledger._schema_version == 4
    assert reopened.close() is True
    from bt_api_risk.core.admission import AccountScope
    from bt_api_risk.risk_order_journal import RiskOrderJournal
    scope = AccountScope(config.provider, config.account_scope, config.environment)
    probe = RiskOrderJournal(config.risk_database, writer_id='synthetic-probe', _defer_initialization=True)
    lock_path = probe._lock_path(scope)
    lock_path.unlink()
    missing_lock = RiskServiceHost(replace(upgrade, pretrade_upgrade_from_v3=None))
    with pytest.raises(RiskServiceStartError, match='RISK_SERVICE_STORAGE_SCHEMA_INVALID'):
        missing_lock.open_storage()
    assert not lock_path.exists() and missing_lock.close() is True


def test_v4_without_existing_database_never_creates(tmp_path):
    from bt_api_py.risk_service_config import load_risk_service_config
    config = replace(load_risk_service_config(setup(tmp_path)), pretrade_schema_version=4)
    host = RiskServiceHost(config)
    with pytest.raises(RiskServiceStartError, match='RISK_SERVICE_STORAGE_SCHEMA_INVALID'):
        host.open_storage()
    assert not config.risk_database.exists()
    assert host.close() is True


def test_wrong_identity_and_v3_without_upgrade_refuse_before_entry(tmp_path, monkeypatch):
    config = seed_v3(tmp_path)
    upgrade = upgrade_config(config)
    wrong = replace(upgrade.pretrade_upgrade_from_v3, expected_database_identity=(1, 1))
    entered = []
    monkeypatch.setattr(host_module, '_open_fenced_journal', lambda *_args: entered.append(True))
    for selected in (replace(upgrade, pretrade_upgrade_from_v3=wrong),
                     replace(upgrade, pretrade_upgrade_from_v3=None)):
        host = RiskServiceHost(selected)
        with pytest.raises(RiskServiceStartError, match='RISK_SERVICE_STORAGE_SCHEMA_INVALID'):
            host.open_storage()
        assert host.close() is True
    assert entered == []


def test_database_replaced_during_entry_is_detected_before_migration(tmp_path, monkeypatch):
    config = upgrade_config(seed_v3(tmp_path))
    original = host_module._open_fenced_journal
    saved = tmp_path / 'saved-risk.sqlite'
    def replace_before_entry(path, scope):
        manager = original(path, scope)
        os.replace(path, saved)
        shutil.copy2(saved, path)
        return manager
    monkeypatch.setattr(host_module, '_open_fenced_journal', replace_before_entry)
    host = RiskServiceHost(config)
    try:
        with pytest.raises(RiskServiceStartError, match='RISK_SERVICE_STORAGE_SCHEMA_INVALID'):
            host.open_storage()
        assert host.ledger is None and host.service_host is None
        assert host.close() is True
    finally:
        if saved.exists():
            config.risk_database.unlink()
            os.replace(saved, config.risk_database)


def test_status_does_not_perform_explicit_upgrade(tmp_path, monkeypatch, capsys):
    config = upgrade_config(seed_v3(tmp_path))
    monkeypatch.setattr(host_module, 'load_risk_service_config', lambda _: config)
    assert host_module._command_status(SimpleNamespace(config='synthetic')) == 2
    assert 'RISK_SERVICE_MIGRATION_RUN_REQUIRED' in capsys.readouterr().out


def _uncertain_migration_child(path):
    from bt_api_risk.pretrade import ledger as ledger_module
    monkeypatch = pytest.MonkeyPatch()
    config = upgrade_config(seed_v3(Path(path)))
    original = ledger_module._split_statements
    def fail_after_first_ddl(script):
        statements = original(script)
        yield statements[0]
        raise OSError('synthetic migration uncertainty')
    monkeypatch.setattr(ledger_module, '_split_statements', fail_after_first_ddl)
    host = RiskServiceHost(config)
    started = []
    monkeypatch.setattr(host, 'start_gateway', lambda: started.append('gateway'))
    monkeypatch.setattr(host, 'start_service_host', lambda: started.append('server'))
    with pytest.raises(RiskServiceStartError, match='RISK_SERVICE_STORAGE_INIT_COMMIT_UNCERTAIN'):
        host.start()
    assert started == [] and host in host_module._RETAINED_HOSTS
    root = host_module._STORAGE_INIT_ROOTS[id(host)]
    assert root[0] is host and root[2]._binding[1:] == host._uncertain_storage
    assert root[2]._binding[0] is root[2]._inner_manager
    assert host.close() is False
    with pytest.raises(RiskServiceStartError):
        host.open_storage()
    class WaitReached(Exception): pass
    with pytest.raises(WaitReached):
        host_module._wait_for_cleanup(host, _wait=lambda: (_ for _ in ()).throw(WaitReached()))
    assert host in host_module._RETAINED_HOSTS and started == []


def test_uncertain_migration_keeps_original_stack_and_never_starts_gateway(tmp_path):
    # An isolated synthetic process may exit after observing retention; the
    # production CLI remains in its cleanup wait on this outcome.
    process = multiprocessing.get_context('spawn').Process(target=_uncertain_migration_child, args=(str(tmp_path),))
    process.start()
    process.join(15)
    assert not process.is_alive() and process.exitcode == 0
