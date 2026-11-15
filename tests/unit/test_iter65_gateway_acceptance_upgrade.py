"""Migration remains disabled without an independent first-migration proof."""
from contextlib import nullcontext
import sqlite3

import pytest
from bt_api_gateway.direct_command_journal import DirectCommandJournalError, GatewayDirectCommandJournal
from bt_api_gateway.direct_parent_authority import GatewayParentAuthority
from bt_api_gateway.session_guard import LocalAccountSessionGuard
from bt_api_py.ctp_gateway_trading import CtpGatewayTradingError, _open_parent_acceptance_journal
from tests.unit.test_iter65_gateway_acceptance_account import parent_spec


@pytest.fixture
def owner(tmp_path):
    directory = tmp_path / 'guard'
    directory.mkdir()
    guard = LocalAccountSessionGuard(directory, 'account', 'owner')
    journal = GatewayDirectCommandJournal.create(tmp_path / 'journal', 'account', generation=guard.acquire())
    journal.enable_controls(guard)
    journal.enable_strategy_ledger(guard)
    journal.enable_account_extensions(guard)
    journal.enable_parent_authority(guard)
    authority = GatewayParentAuthority(journal, guard, now_ns=lambda: 100, trading_day=lambda: '20300101')
    authority.register_parent(parent_spec(), principal='execution', actor_ref='actor',
                              fence=nullcontext, recheck=lambda: True)
    yield tmp_path / 'journal', directory, guard, journal, authority
    guard.release()


def open_journal(path, guard, journal_id, *, upgrade=False, schema=None):
    if schema is None:
        schema = dict(controls_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True,
                      parent_authority_enabled=True, parent_acceptance_order_enabled=True)
    return _open_parent_acceptance_journal(path, 'account', guard, schema_options=schema,
        journal_options=dict(generation=guard.acquire(), expected_journal_id=journal_id, minimum_revision=0),
        release_audit=False, upgrade_acceptance=upgrade)


def dump(path):
    with sqlite3.connect(path) as db:
        return tuple(db.iterdump())


def test_old_schema_never_upgrades_implicitly_or_by_flag(owner):
    path, _, guard, journal, _ = owner
    before = dump(path)
    with pytest.raises(DirectCommandJournalError):
        open_journal(path, guard, journal.journal_id)
    with pytest.raises(CtpGatewayTradingError, match='PARENT_ACCEPTANCE_MIGRATION_PROOF_REQUIRED'):
        open_journal(path, guard, journal.journal_id, upgrade=True)
    assert dump(path) == before


def test_complete_extension_removal_is_not_mistaken_for_first_migration(owner):
    path, directory, guard, journal, authority = owner
    # Synthetic provisioning only, to create evidence of a formerly enabled log.
    journal.enable_parent_acceptance_order(guard)
    authority.register_parent(dict(parent_spec(), parent_ref='new', registration_operation_id='new'),
                              principal='execution', actor_ref='actor', fence=nullcontext, recheck=lambda: True)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT sequence FROM direct_parent_acceptance_order WHERE parent_ref=?', ('new',)).fetchone() == (1,)
    guard.release()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE direct_parent_acceptance_order')
        db.execute('DROP TABLE direct_parent_acceptance_meta')
    corrupted = dump(path)
    restarted = LocalAccountSessionGuard(directory, 'account', 'new-owner')
    try:
        restarted.acquire()
        with pytest.raises(CtpGatewayTradingError, match='PARENT_ACCEPTANCE_MIGRATION_PROOF_REQUIRED'):
            open_journal(path, restarted, journal.journal_id, upgrade=True)
        with pytest.raises(DirectCommandJournalError):
            open_journal(path, restarted, journal.journal_id)
        assert dump(path) == corrupted
    finally:
        restarted.release()


@pytest.mark.parametrize('version', [13, 14, 19])
def test_preexisting_extension_opens_with_exact_schema(owner, version):
    path, _, guard, journal, _ = owner
    upgrades = [journal.enable_account_facts_financial, journal.enable_actor_stream,
                journal.enable_strategy_origin, journal.enable_control_cancel,
                journal.enable_account_fact_recovery, journal.enable_account_event_recovery]
    for upgrade in upgrades[:version - 13]:
        upgrade(guard)
    journal.enable_parent_acceptance_order(guard)
    schema = dict(controls_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True,
                  parent_authority_enabled=True, parent_acceptance_order_enabled=True,
                  account_facts_financial_enabled=version >= 14, actor_stream_enabled=version >= 15,
                  strategy_origin_enabled=version >= 16, control_cancel_enabled=version >= 17,
                  account_fact_recovery_enabled=version >= 18, account_event_recovery_enabled=version >= 19)
    opened = open_journal(path, guard, journal.journal_id, schema=schema)
    assert opened._schema_version == version and opened._parent_acceptance_order_enabled
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT sequence FROM direct_parent_acceptance_order').fetchone() == (None,)
