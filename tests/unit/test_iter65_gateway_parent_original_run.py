"""Registry-issued strategy run is captured in the Gateway registration transaction."""
import sqlite3
from contextlib import nullcontext

import pytest

from bt_api_py.gateway_parent_service import CANCEL, OPERATION, OPERATION_V2, QUERY, REGISTER, REGISTER_V2
from tests.unit.test_iter65_gateway_parent_bound_v2 import service, _spec_wire, _expected, _key


def enable(env):
    journal = env['journal']
    authority = env['parent_service'].authority
    call = env['owner'].submit_service_call(lambda: journal.enable_parent_original_runs(authority._guard))
    call.result(timeout_seconds=2)


@pytest.mark.parametrize('operation', [REGISTER, REGISTER_V2])
def test_run_is_issued_by_real_registry_not_rpc_input(service, operation):
    enable(service)
    args = {'spec': _spec_wire()}
    if operation == REGISTER_V2:
        args['expected_source'] = _expected(service)
    reply = service['request'](operation, args)
    assert reply['ok'] and reply['result']['status'] == 'OBSERVED'
    with sqlite3.connect(service['db']) as db:
        row = db.execute('SELECT account,strategy,parent_ref,original_run_id FROM direct_parent_original_runs').fetchone()
    assert row == ('account', 'strategy', 'parent', service['scope'].run_id)
    bad = dict(args, spec=dict(args['spec'], original_run_id='untrusted'))
    rejected = service['request'](operation, bad)
    assert not rejected['ok'] or rejected['result']['status'] != 'OBSERVED'
    with sqlite3.connect(service['db']) as db:
        assert db.execute('SELECT original_run_id FROM direct_parent_original_runs').fetchone()[0] == 'run'


@pytest.mark.parametrize('history', ['legacy', 'other-run'])
@pytest.mark.parametrize('operation', [OPERATION, OPERATION_V2])
def test_old_registration_receipt_is_unknown_but_query_and_cancel_remain(service, history, operation):
    if history == 'legacy':
        reply = service['request'](REGISTER, {'spec': _spec_wire()})
        assert reply['ok'] and reply['result']['status'] == 'OBSERVED'
        enable(service)
    else:
        enable(service)
        # Establish a synthetic older run under the owner thread; the query
        # below is issued through an actual Registry admission for run B.
        authority = service['parent_service'].authority
        identity = service['identity']
        call = service['owner'].submit_service_call(lambda: authority.register_parent(
            _spec_wire(), principal=identity.principal_ref, actor_ref=identity.actor_ref,
            fence=nullcontext, recheck=lambda: True, original_run=lambda: 'old-run'))
        call.result(timeout_seconds=2)
    args = dict(_key(), operation_id='register-1')
    if operation == OPERATION_V2:
        args['expected_source'] = _expected(service)
    reply = service['request'](operation, args)
    assert reply['ok'] and reply['result']['status'] == 'UNKNOWN'
    query = service['request'](QUERY, _key())
    assert query['ok'] and query['result']['status'] == 'OBSERVED'
    cancel = service['request'](CANCEL, dict(_key(), cancel_operation_id='cancel-1', expected_version=1))
    assert cancel['ok'] and cancel['result']['status'] == 'OBSERVED'
    cancel_op = service['request'](OPERATION, dict(_key(), operation_id='cancel-1'))
    assert cancel_op['ok'] and cancel_op['result']['status'] == 'OBSERVED'
