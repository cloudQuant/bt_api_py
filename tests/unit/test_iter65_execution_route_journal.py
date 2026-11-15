"""Candidate routing records are transactional but confer no send authority."""
from dataclasses import replace
from decimal import Decimal
import sqlite3

import pytest
from bt_api_base.gateway_direct_protocol import GatewayCommandBinding
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py._contracts.models import OrderRequest, OrderType, Side
from bt_api_py._execution_route_journal import (
    RouteJournalError, install_route_table, load_route_candidate, record_route_candidate,
)
from bt_api_py._execution_route_match import _Outcome, _Rule, _RuleSet
from bt_api_py.gateway_direct_client import GatewayClientRoute, build_gateway_order_intent


@pytest.fixture
def context():
    binding = GatewayCommandBinding('journal', 'acct', 'strategy', 'principal')
    route = GatewayClientRoute('broker.direct_write', 'service', 'instance', 'scope',
        WireScope('paper', account_ref='acct', strategy_ref='strategy', run_id='run'),
        WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy'))
    request = OrderRequest(symbol='rb', side=Side.BUY, order_type=OrderType.LIMIT,
        quantity=Decimal('5'), account_id='acct', client_order_id='client',
        idempotency_key='request', price=Decimal('10'), quantity_unit='lots',
        exchange_id='SHFE', offset='open')
    intent = build_gateway_order_intent(binding, route, request)
    key = MarketInstrumentKey('ctp', 'SHFE', 'sim', 'future', 'rb', 'rb')
    return intent, key, _RuleSet([_Rule(side='buy')])


def _record(db, context, *, configuration_id='config-v1'):
    intent, key, rules = context
    return record_route_candidate(db, intent, configuration_id=configuration_id,
                                  instruments=(key,), rules=rules)


def _open(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.execute('BEGIN IMMEDIATE')
    install_route_table(db)
    db.commit()
    return db


def test_records_and_reloads_with_exact_intent_and_policy(tmp_path, context):
    path = tmp_path / 'routes.db'
    db = _open(path)
    db.execute('BEGIN IMMEDIATE')
    assert load_route_candidate(db, context[0]) is None
    candidate = _record(db, context)
    assert candidate.outcome is _Outcome.MATCH and candidate.rule_index == 0
    assert _record(db, context) == candidate
    db.commit()
    db.close()
    db = sqlite3.connect(path, isolation_level=None)
    db.execute('BEGIN')
    assert load_route_candidate(db, context[0]) == candidate
    db.rollback()
    db.close()


def test_rollback_does_not_leave_a_decision(tmp_path, context):
    db = _open(tmp_path / 'routes.db')
    db.execute('BEGIN IMMEDIATE')
    _record(db, context)
    db.rollback()
    db.execute('BEGIN')
    assert load_route_candidate(db, context[0]) is None
    db.rollback()
    db.close()


@pytest.mark.parametrize('changed', ['policy', 'intent', 'run', 'instrument'])
def test_first_candidate_cannot_be_overwritten(tmp_path, context, changed):
    db = _open(tmp_path / 'routes.db')
    intent, key, rules = context
    db.execute('BEGIN IMMEDIATE')
    original = _record(db, context)
    db.commit()
    db.execute('BEGIN IMMEDIATE')
    if changed == 'policy':
        action = lambda: _record(db, context, configuration_id='config-v2')
    elif changed == 'intent':
        request = OrderRequest.from_dict(__import__('json').loads(intent.canonical_payload))
        changed_intent = build_gateway_order_intent(intent.binding, intent.route,
                                                     replace(request, price=Decimal('11')))
        action = lambda: _record(db, (changed_intent, key, rules))
    elif changed == 'run':
        route = replace(intent.route, scope=replace(intent.route.scope, run_id='run-2'))
        request = OrderRequest.from_dict(__import__('json').loads(intent.canonical_payload))
        changed_intent = build_gateway_order_intent(intent.binding, route, request)
        action = lambda: _record(db, (changed_intent, key, rules))
    else:
        action = lambda: _record(db, (intent, replace(key, environment='other'), rules))
    with pytest.raises(RouteJournalError, match='CANDIDATE_CONFLICT'):
        action()
    assert load_route_candidate(db, intent) == original
    db.rollback()
    db.close()


def test_ambiguous_and_missing_instrument_do_not_become_no_match(tmp_path, context):
    db = _open(tmp_path / 'routes.db')
    db.execute('BEGIN IMMEDIATE')
    intent, key, rules = context
    for instruments in ((), (key, key), (replace(key, venue='DCE'),)):
        with pytest.raises(RouteJournalError, match='INVALID_CANDIDATE'):
            record_route_candidate(db, intent, configuration_id='v1',
                                   instruments=instruments, rules=rules)
    assert load_route_candidate(db, intent) is None
    db.rollback()
    db.close()


def test_no_match_is_preserved_as_candidate(tmp_path, context):
    db = _open(tmp_path / 'routes.db')
    db.execute('BEGIN IMMEDIATE')
    intent, key, _ = context
    no_match = _record(db, (intent, key, _RuleSet([_Rule(side='sell')])))
    assert no_match.outcome is _Outcome.NO_MATCH
    assert load_route_candidate(db, intent) == no_match
    db.rollback()
    db.close()


def test_malformed_stored_candidate_is_not_returned(tmp_path, context):
    db = _open(tmp_path / 'routes.db')
    db.execute('BEGIN IMMEDIATE')
    _record(db, context)
    db.execute("UPDATE execution_route_candidates_v1 SET snapshot='{}'")
    with pytest.raises(RouteJournalError, match='CANDIDATE_UNAVAILABLE'):
        load_route_candidate(db, context[0])
    db.rollback()
    db.close()


def test_transaction_and_schema_are_mandatory(tmp_path, context):
    db = sqlite3.connect(tmp_path / 'routes.db', isolation_level=None)
    with pytest.raises(RouteJournalError, match='TRANSACTION_REQUIRED'):
        install_route_table(db)
    db.execute('BEGIN IMMEDIATE')
    db.execute('CREATE TABLE execution_route_candidates_v1 (wrong TEXT)')
    with pytest.raises(RouteJournalError, match='SCHEMA_MISMATCH'):
        install_route_table(db)
    with pytest.raises(RouteJournalError, match='SCHEMA_MISMATCH'):
        load_route_candidate(db, context[0])
    db.rollback()
    db.close()


def test_rehashed_forged_outcome_is_recomputed(tmp_path, context):
    import hashlib
    import json

    db = _open(tmp_path / 'routes.db')
    db.execute('BEGIN IMMEDIATE')
    candidate = _record(db, context)
    data = json.loads(candidate.snapshot)
    data['outcome'], data['rule_index'] = 'NO_MATCH', None
    forged = json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    digest = hashlib.sha256(forged.encode('ascii')).hexdigest()
    db.execute('UPDATE execution_route_candidates_v1 SET snapshot=?, digest=?', (forged, digest))
    with pytest.raises(RouteJournalError, match='CANDIDATE_UNAVAILABLE'):
        load_route_candidate(db, context[0])
    db.rollback()
    db.close()


def test_conflicting_old_run_cannot_read_as_same_intent(tmp_path, context):
    import json

    db = _open(tmp_path / 'routes.db')
    db.execute('BEGIN IMMEDIATE')
    intent = context[0]
    _record(db, context)
    route = replace(intent.route, scope=replace(intent.route.scope, run_id='run-2'))
    request = OrderRequest.from_dict(json.loads(intent.canonical_payload))
    changed = build_gateway_order_intent(intent.binding, route, request)
    with pytest.raises(RouteJournalError, match='CANDIDATE_UNAVAILABLE'):
        load_route_candidate(db, changed)
    db.rollback()
    db.close()


def test_two_connections_cannot_overwrite_committed_candidate(tmp_path, context):
    path = tmp_path / 'routes.db'
    first = _open(path)
    second = sqlite3.connect(path, isolation_level=None, timeout=0)
    first.execute('BEGIN IMMEDIATE')
    original = _record(first, context)
    with pytest.raises(sqlite3.OperationalError, match='locked'):
        second.execute('BEGIN IMMEDIATE')
    first.commit()
    second.execute('BEGIN IMMEDIATE')
    with pytest.raises(RouteJournalError, match='CANDIDATE_CONFLICT'):
        _record(second, context, configuration_id='different')
    assert load_route_candidate(second, context[0]) == original
    second.rollback()
    first.close()
    second.close()


def test_temporary_table_cannot_shadow_persistent_records(tmp_path, context):
    db = _open(tmp_path / 'routes.db')
    db.execute('BEGIN IMMEDIATE')
    db.execute('CREATE TEMP TABLE execution_route_candidates_v1 (account TEXT, strategy TEXT, '
               'request_key TEXT, snapshot TEXT, digest TEXT)')
    original = _record(db, context)
    assert load_route_candidate(db, context[0]) == original
    assert db.execute('SELECT count(*) FROM temp.execution_route_candidates_v1').fetchone() == (0,)
    assert db.execute('SELECT count(*) FROM main.execution_route_candidates_v1').fetchone() == (1,)
    db.rollback()
    db.close()


def test_sqlite_row_factory_supports_install_reentry_and_record_load(tmp_path, context):
    path = tmp_path / 'routes.db'
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('BEGIN IMMEDIATE')
    install_route_table(db)
    install_route_table(db)
    assert load_route_candidate(db, context[0]) is None
    candidate = _record(db, context)
    assert _record(db, context) == candidate
    assert load_route_candidate(db, context[0]) == candidate
    db.commit()
    db.close()
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('BEGIN')
    install_route_table(db)
    assert load_route_candidate(db, context[0]) == candidate
    db.rollback()
    db.close()


def test_unknown_row_factory_fails_closed(tmp_path, context):
    db = _open(tmp_path / 'routes.db')
    db.row_factory = lambda cursor, row: {'sql': row[0]}
    db.execute('BEGIN IMMEDIATE')
    with pytest.raises(RouteJournalError, match='UNSUPPORTED_ROW_FACTORY'):
        install_route_table(db)
    with pytest.raises(RouteJournalError, match='UNSUPPORTED_ROW_FACTORY'):
        _record(db, context)
    db.rollback()
    db.close()
