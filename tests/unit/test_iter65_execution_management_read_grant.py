"""Local management policy is not a registration or a disclosure decision."""
from dataclasses import FrozenInstanceError, replace

import pytest

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import MAX_REFERENCE_BYTES, WireIdentity, WireRole, WireScope
from bt_api_py.execution_management_read_grant import (
    PARENT_READ_V1,
    ExecutionManagementReadGrant,
    ExecutionManagementReadRequest,
    allows_instrument,
    allows_request,
)


IDENTITY = WireIdentity(WireRole.APPLICATION_CLIENT, 'app-principal', 'app-actor')
SCOPE = WireScope('domain', account_ref='account', strategy_ref='strategy')
INSTRUMENT = MarketInstrumentKey('ctp', 'SHFE', 'simulation', 'future', 'rb2610', 'rb2610')
OTHER_INSTRUMENT = replace(INSTRUMENT, native_symbol='rb2611', instrument_id='rb2611')


def grant(**changes):
    values = dict(instance_id='execution-instance', identity=IDENTITY, scope=SCOPE,
                  revision='revision-1', instruments=frozenset({INSTRUMENT}),
                  operations=frozenset({PARENT_READ_V1}))
    values.update(changes)
    return ExecutionManagementReadGrant(**values)


def request(**changes):
    values = dict(instance_id='execution-instance', identity=IDENTITY, scope=SCOPE,
                  grant_revision='revision-1', operation=PARENT_READ_V1,
                  parent_ref='parent', query_id='query-1')
    values.update(changes)
    return ExecutionManagementReadRequest(**values)


def test_exact_local_policy_requires_complete_instrument_before_disclosure():
    policy, query = grant(), request()
    assert allows_request(policy, query)
    assert allows_instrument(policy, query, INSTRUMENT)
    assert not allows_instrument(policy, query, OTHER_INSTRUMENT)
    assert not allows_instrument(policy, query, None)
    # A new query ID or parent is a correlation claim, not a policy grant.
    assert allows_request(policy, replace(query, query_id='other', parent_ref='other'))
    assert not allows_instrument(None, query, INSTRUMENT)
    assert not allows_request(None, query)


@pytest.mark.parametrize('changes', [
    {'instance_id': 'other-instance'},
    {'identity': WireIdentity(WireRole.APPLICATION_CLIENT, 'other-principal', 'app-actor')},
    {'identity': WireIdentity(WireRole.APPLICATION_CLIENT, 'app-principal', 'other-actor')},
    {'scope': WireScope('other-domain', account_ref='account', strategy_ref='strategy')},
    {'scope': WireScope('domain', account_ref='other-account', strategy_ref='strategy')},
    {'scope': WireScope('domain', account_ref='account', strategy_ref='other-strategy')},
    {'grant_revision': 'revision-2'},
    {'operation': 'execution.management.parent.cancel.v1'},
])
def test_no_other_identity_scope_revision_or_operation_is_allowed(changes):
    query = request(**changes)
    assert not allows_request(grant(), query)
    assert not allows_instrument(grant(), query, INSTRUMENT)


@pytest.mark.parametrize('bad_identity', [
    WireIdentity(WireRole.STRATEGY_CLIENT, 'p', 'a'),
    WireIdentity(WireRole.SERVICE, 'p', 'a'),
    WireIdentity(WireRole.APPLICATION_CLIENT, 'p', 'a', delegate_ref='delegate'),
    None,
])
def test_only_direct_application_identity_is_a_valid_claim(bad_identity):
    with pytest.raises(ValueError, match='INVALID_EXECUTION_MANAGEMENT_READ_GRANT'):
        grant(identity=bad_identity)
    with pytest.raises(ValueError, match='INVALID_EXECUTION_MANAGEMENT_READ_REQUEST'):
        request(identity=bad_identity)


@pytest.mark.parametrize('bad_scope', [
    WireScope('domain', account_ref='account'),
    WireScope('domain', strategy_ref='strategy'),
    WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run'),
    WireScope('domain', account_ref='account', strategy_ref='strategy', portfolio_ref='portfolio'),
    WireScope('domain', account_refs=('account', 'account-2'), portfolio_ref='portfolio', group_id='group'),
    WireScope('domain', account_ref='account', portfolio_ref='portfolio', group_id='group', leg_id='leg'),
    None,
])
def test_composite_or_run_scopes_are_rejected(bad_scope):
    with pytest.raises(ValueError, match='INVALID_EXECUTION_MANAGEMENT_READ_GRANT'):
        grant(scope=bad_scope)
    with pytest.raises(ValueError, match='INVALID_EXECUTION_MANAGEMENT_READ_REQUEST'):
        request(scope=bad_scope)


@pytest.mark.parametrize('field,bad', [
    ('instance_id', ''), ('revision', ' revision'), ('revision', 'x\n'),
    ('revision', 'x' * (MAX_REFERENCE_BYTES + 1)), ('revision', '\ud800'),
    ('instruments', frozenset()), ('instruments', {INSTRUMENT}),
    ('instruments', frozenset({'not-a-key'})),
    ('operations', frozenset()), ('operations', frozenset({'execution.management.parent.cancel.v1'})),
    ('operations', {PARENT_READ_V1}),
])
def test_invalid_grant_fails_at_construction(field, bad):
    with pytest.raises(ValueError, match='INVALID_EXECUTION_MANAGEMENT_READ_GRANT'):
        grant(**{field: bad})


@pytest.mark.parametrize('field,bad', [
    ('query_id', ''), ('query_id', True), ('query_id', 'x' * (MAX_REFERENCE_BYTES + 1)),
    ('query_id', '\ud800'), ('parent_ref', ' parent'), ('operation', ''),
    ('grant_revision', None),
])
def test_invalid_request_fails_at_construction(field, bad):
    with pytest.raises(ValueError, match='INVALID_EXECUTION_MANAGEMENT_READ_REQUEST'):
        request(**{field: bad})


def test_policy_and_request_are_immutable_and_large_policy_is_rejected():
    policy, query = grant(), request()
    with pytest.raises(FrozenInstanceError):
        policy.revision = 'other'
    with pytest.raises(FrozenInstanceError):
        query.query_id = 'other'
    too_many = frozenset(replace(INSTRUMENT, native_symbol=f'contract-{i}') for i in range(1025))
    with pytest.raises(ValueError, match='INVALID_EXECUTION_MANAGEMENT_READ_GRANT'):
        grant(instruments=too_many)


def test_policy_rejects_string_subclasses_even_if_they_compare_equal():
    class Operation(str):
        pass

    with pytest.raises(ValueError, match='INVALID_EXECUTION_MANAGEMENT_READ_GRANT'):
        grant(operations=frozenset({Operation(PARENT_READ_V1)}))


def test_exact_types_only_and_query_id_does_not_bypass_scope():
    assert not allows_request(object(), request())
    assert not allows_request(grant(), object())
    query = request(scope=WireScope('domain', account_ref='other-account', strategy_ref='strategy'))
    assert not allows_instrument(grant(), replace(query, query_id='another-id'), INSTRUMENT)
