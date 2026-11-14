"""Explicit local Gateway original-run selection; no native provider is started."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py import ctp_gateway_trading as trading
from bt_api_py.ctp_gateway_local_profile import load_simnow_trading_resources, simnow_trading_profile_factory
from bt_api_py.ctp_gateway_operator_profile import create_ctp_gateway_profile_factory
from bt_api_py.gateway_direct_service import validate_service_options
from bt_api_py.service_components import ServiceResourceConstructionError
from bt_api_py.service_config import parse_service_config
from tests.integration.test_iter63_gateway_direct_service import _service_config
from tests.unit.test_iter63_ctp_gateway_local_profile import (
    _composing_config, _composing_doc, _execution_delegate, _fixture,
)
import tests.test_iter63_ctp_gateway_trading as fixtures


BASE = {'role': 'trading_owner', 'dispatch_enabled': True, 'control_enabled': True,
        'strategy_ledger_enabled': True, 'account_extensions_enabled': True,
        'parent_authority_enabled': True}


def parse_options(options):
    raw = _service_config('tcp://127.0.0.1:19999').to_public_dict()
    raw['components']['gateway']['settings']['options'] = options
    return parse_service_config(raw).components['gateway']['settings']['options']


def test_option_is_explicit_strict_and_requires_parent_authority():
    assert 'parent_original_runs_enabled' not in parse_options(BASE)
    assert 'parent_original_runs_enabled' not in validate_service_options(BASE, ('service.direct_write',))
    for value in (False, True):
        options = dict(BASE, parent_original_runs_enabled=value)
        assert parse_options(options)['parent_original_runs_enabled'] is value
        assert validate_service_options(options, ('service.direct_write',))['parent_original_runs_enabled'] is value
    for value in (None, 0, 1, 'true'):
        options = dict(BASE, parent_original_runs_enabled=value)
        with pytest.raises(ValueError):
            parse_options(options)
        with pytest.raises(ValueError):
            validate_service_options(options, ('service.direct_write',))
    options = dict(BASE, parent_authority_enabled=False, parent_original_runs_enabled=True)
    with pytest.raises(ValueError):
        parse_options(options)
    with pytest.raises(ValueError):
        validate_service_options(options, ('service.direct_write',))


def profile(tmp_path, monkeypatch, *, selected=True, facts_enabled=False, upgrade_facts=False, upgrade_audit=False):
    monkeypatch.setattr(fixtures, '_free_endpoint', lambda: 'tcp://127.0.0.1:23456')
    doc, file, config = _fixture(tmp_path, monkeypatch)
    _, config = _composing_config(config, delegation_enabled=True, parent_authority_enabled=True,
                                  parent_original_runs_enabled=selected,
                                  account_facts_financial_enabled=facts_enabled)
    _composing_doc(doc)
    strategy = doc['clients'][0]['identity']['actor_ref']
    instrument = doc['returns']['instruments'][0]
    doc['delegation'] = {'delegates': [_execution_delegate(doc, strategy, instrument)],
                         'upgrade_parent_authority_from_v12': True}
    file.write_text(json.dumps(doc), encoding='utf-8')
    resources = load_simnow_trading_resources(config)
    if upgrade_facts or upgrade_audit:
        resources = replace(resources, upgrade_account_facts_financial_from_v13=upgrade_facts,
                            control_release_audit_enabled=upgrade_audit,
                            upgrade_control_release_audit=upgrade_audit)
        factory = create_ctp_gateway_profile_factory('original-run-test', resource_loader=lambda _: resources)
    else:
        factory = simnow_trading_profile_factory
    return factory.create_deployment(config), config, resources


def provision(resources, *, version=13, original_runs=False):
    settings = resources.settings
    guard = LocalAccountSessionGuard(settings.guard_root, settings.account_scope, 'provision-test')
    try:
        generation = guard.acquire()
        journal = GatewayDirectCommandJournal(settings.journal_path, settings.account_scope,
            generation=generation, expected_journal_id=settings.expected_journal_id, minimum_revision=0)
        journal.enable_controls(guard)
        journal.enable_strategy_ledger(guard)
        journal.enable_account_extensions(guard)
        if version >= 13:
            journal.enable_parent_authority(guard)
        if version >= 14:
            journal.enable_account_facts_financial(guard)
        if original_runs:
            journal.enable_parent_original_runs(guard)
    finally:
        guard.release()


def dump(path):
    with sqlite3.connect(path) as db:
        return tuple(db.iterdump())


def run_until_journal_open(deployment, config, resources, monkeypatch, *, options=None):
    calls = []
    def stop_before_provider():
        calls.append('journal-opened')
        raise RuntimeError('synthetic stop before loading provider')
    monkeypatch.setattr(trading, '_load_ctp_types', stop_before_provider)
    # Normal startup must neither create nor migrate a journal for this selection.
    def unexpected(*_args, **_kwargs):
        pytest.fail('startup attempted creation or migration')
    for name in ('create', 'enable_controls', 'enable_strategy_ledger', 'enable_account_extensions',
                 'enable_parent_authority', 'enable_parent_original_runs', 'enable_control_release_audit',
                 'enable_account_facts_financial', 'enable_actor_stream', 'enable_strategy_origin',
                 'enable_control_cancel', 'enable_account_fact_recovery', 'enable_account_event_recovery'):
        monkeypatch.setattr(GatewayDirectCommandJournal, name, unexpected)
    actual = dict(config.components['gateway']['settings']['options']) if options is None else options
    try:
        result = deployment._provide_resources('gateway', 'gateway-a', actual, resources.authority.scope)
    except ServiceResourceConstructionError as error:
        error.resources.close()
    else:
        result.close()
        pytest.fail('provider sentinel was not reached')
    return calls


@pytest.mark.parametrize(('selected', 'version', 'installed', 'reaches_open'), [
    (True, 13, True, True),
    (True, 13, False, False),
    (False, 13, False, True),
    (False, 13, True, False),
    (True, 12, False, False),
])
def test_profile_exact_schema_without_implicit_migration(tmp_path, monkeypatch, selected, version, installed, reaches_open):
    deployment, config, resources = profile(tmp_path, monkeypatch, selected=selected)
    provision(resources, version=version, original_runs=installed)
    before = dump(resources.settings.journal_path)
    calls = run_until_journal_open(deployment, config, resources, monkeypatch)
    assert bool(calls) is reaches_open
    assert dump(resources.settings.journal_path) == before


def test_profile_refuses_missing_selected_flag_before_owner(tmp_path, monkeypatch):
    deployment, config, resources = profile(tmp_path, monkeypatch)
    options = dict(config.components['gateway']['settings']['options'])
    del options['parent_original_runs_enabled']
    with pytest.raises(trading.CtpGatewayTradingError, match='INVALID_TRADING_CONFIGURATION'):
        deployment._provide_resources('gateway', 'gateway-a', options, resources.authority.scope)
    options['parent_original_runs_enabled'] = 1  # True-like values are not explicit booleans.
    with pytest.raises(trading.CtpGatewayTradingError, match='INVALID_TRADING_CONFIGURATION'):
        deployment._provide_resources('gateway', 'gateway-a', options, resources.authority.scope)


@pytest.mark.parametrize('damage', ['missing_sidecar', 'wrong_id', 'missing_file'])
def test_profile_refuses_damaged_or_missing_journal_without_mutation(tmp_path, monkeypatch, damage):
    deployment, config, resources = profile(tmp_path, monkeypatch)
    provision(resources, original_runs=True)
    path = Path(resources.settings.journal_path)
    if damage == 'missing_file':
        path.unlink()
        before = None
    else:
        with sqlite3.connect(path) as db:
            if damage == 'missing_sidecar':
                db.execute('DROP TABLE direct_parent_original_runs')
            else:
                db.execute("UPDATE direct_journal_meta SET journal_id='wrong-journal'")
        before = dump(path)
    assert run_until_journal_open(deployment, config, resources, monkeypatch) == []
    assert (dump(path) if path.exists() else None) == before


def test_original_run_selection_disables_later_schema_fallback(tmp_path, monkeypatch):
    deployment, config, resources = profile(tmp_path, monkeypatch, facts_enabled=True, upgrade_facts=True)
    provision(resources, version=13, original_runs=True)
    before = dump(resources.settings.journal_path)
    assert run_until_journal_open(deployment, config, resources, monkeypatch) == []
    assert dump(resources.settings.journal_path) == before


def test_original_run_selection_opens_matching_later_schema(tmp_path, monkeypatch):
    deployment, config, resources = profile(tmp_path, monkeypatch, facts_enabled=True)
    provision(resources, version=14, original_runs=True)
    before = dump(resources.settings.journal_path)
    assert run_until_journal_open(deployment, config, resources, monkeypatch) == ['journal-opened']
    assert dump(resources.settings.journal_path) == before


def test_original_run_selection_refuses_control_audit_auto_upgrade(tmp_path, monkeypatch):
    deployment, config, resources = profile(tmp_path, monkeypatch, upgrade_audit=True)
    provision(resources, version=13, original_runs=True)
    before = dump(resources.settings.journal_path)
    assert run_until_journal_open(deployment, config, resources, monkeypatch) == []
    assert dump(resources.settings.journal_path) == before
