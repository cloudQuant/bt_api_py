"""Typed account RPC: strict map, cursor contract and local CURVE path."""
import copy
import multiprocessing
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
import zmq

from tests.unit import test_service_account as unit
from tests.unit import test_service_account_integration as integ
from bt_api_base.wire_codec_v1 import (
    WireCodecError,
    _decode_account,
    _encode_account,
    account_observation_from_typed_map,
    account_observation_to_typed_map,
)
from bt_api_py.service_account import ServiceAccountClient, ServiceAccountError
from bt_api_py.service_runtime import ServiceRuntimeError
from bt_api_base.service_protocol import WireIdentity, WireRole


def _typed_reply(observation=None, *, cursor=0):
    observation = observation or unit._observation()
    result = unit._reply(observation, cursor=cursor)
    payload = result['snapshot']['payload']
    payload.update(schema_id='sdk.account.state.typed', source_schema_version=payload['schema_version'], schema_version=1,
                   observation=account_observation_to_typed_map(observation))
    return result


def test_typed_map_exact_roundtrip_and_rejects_decimal_rewrite():
    observation = unit._observation()
    encoded = account_observation_to_typed_map(observation)
    assert account_observation_from_typed_map(encoded).to_dict() == observation.to_dict()
    noncanonical = replace(observation, balances=(replace(observation.balances[0], total='100.00'),) + observation.balances[1:])
    with pytest.raises(WireCodecError, match='WIRE_DECIMAL_NONCANONICAL'):
        account_observation_to_typed_map(noncanonical)
    for field, value in [('registry_digest', 'bad'), ('minor', 99), ('kind', 'bar'), ('extra', True)]:
        bad = copy.deepcopy(encoded)
        bad[field] = value
        with pytest.raises(WireCodecError):
            account_observation_from_typed_map(bad)
    bad = copy.deepcopy(encoded)
    bad['data']['balances'][0]['total'] = '100.00'
    with pytest.raises(WireCodecError):
        account_observation_from_typed_map(bad)
    too_many = copy.deepcopy(encoded)
    too_many['data']['balances'] *= 257
    with pytest.raises(WireCodecError):
        account_observation_from_typed_map(too_many)
    too_large = copy.deepcopy(encoded)
    row = copy.deepcopy(too_large['data']['balances'][0])
    row['currency'] = 'X' * 512
    too_large['data']['balances'] = [row] * 160
    with pytest.raises(WireCodecError, match='WIRE_MESSAGE_TOO_LARGE'):
        account_observation_from_typed_map(too_large)


def test_typed_cursor_zero_and_original_checks(tmp_path):
    client, runtime, registry, *_ = unit._make_client(tmp_path)
    try:
        client._snapshot_format = 'typed'
        client._snapshot_operation = client._initial_snapshot_operation = 'account.snapshot.typed'
        parsed = client._parse_reply(_typed_reply(cursor=0))
        assert parsed[1] == 0 and parsed[0].to_dict() == unit._observation().to_dict()
        for mutate in (
            lambda x: x['snapshot']['payload'].update(session_generation='other'),
            lambda x: x['snapshot']['payload']['observation'].update(registry_digest='bad'),
            lambda x: x.update(latest_cursor=1),
            lambda x: x['snapshot']['payload'].update(source_schema_version=2),
        ):
            malformed = _typed_reply(cursor=0)
            mutate(malformed)
            with pytest.raises(ServiceAccountError):
                client._parse_reply(malformed)
        client._snapshot_format = 'generic'
        with pytest.raises(ServiceAccountError, match='ACCOUNT_SNAPSHOT_FORMAT_CHANGED'):
            client.refresh()
    finally:
        unit._close_all(client, runtime, registry)


def _host(control, *args):
    # The source itself supplies canonical strings. The codec is not permitted
    # to silently rewrite the persisted snapshot.
    integ._REQUESTED_OPERATIONS = frozenset({'account.snapshot.typed'})
    old = integ._observation
    def canonical(*a, **kw):
        return _decode_account(_encode_account(old(*a, **kw)))
    integ._observation = canonical
    integ._service_host_process(control, *args)


def test_typed_account_snapshot_over_local_curve(tmp_path):
    context = multiprocessing.get_context('spawn')
    endpoint = integ._free_endpoints(1)[0]
    server = zmq.curve_keypair()
    client_keys = zmq.curve_keypair()
    service = integ._account_service_info('account-a', endpoint, 'curve-account-a-v1')
    service['server_key'] = server[0]
    (tmp_path / 'guards').mkdir()
    record = integ._start_process(context, _host, (
        'account-a', integ._ACCOUNT_KEYS['account-a'],
        integ._account_instrument('account-a', integ._ACCOUNT_KEYS['account-a']),
        integ._service_config(service), str(tmp_path / 'registry.sqlite'),
        str(tmp_path / 'journal.sqlite'), str(tmp_path / 'guards'), server,
        ((client_keys[0], integ._PRINCIPAL_ONE, integ._STRATEGY_ONE, True),),
    ), 'typed-account-host')
    forced, failures = [], []
    runtime = facade = None
    saved = integ._REQUESTED_OPERATIONS
    try:
        assert integ._receive(record['pipe'])[0] == 'ready'
        integ._REQUESTED_OPERATIONS = frozenset({'account.snapshot.typed'})
        raw = integ._client_config(integ._STRATEGY_ONE, {'account-a': service}, ('account-a',))
        runtime = integ._direct_runtime(raw, WireIdentity(WireRole.STRATEGY_CLIENT, integ._PRINCIPAL_ONE, integ._STRATEGY_ONE), service, client_keys)
        runtime.start()
        facade = ServiceAccountClient(runtime, 'store.account', account_ref='account-a', expected_account=integ._ACCOUNT_KEYS['account-a'], required_coverage=integ._REQUIRED_COVERAGE, snapshot_format='typed')

        first = facade.refresh()
        assert first.observation.balances[0].total == '100'
        assert facade.refresh().observation.to_dict() == first.observation.to_dict()
        facade.close()
        runtime.close()
        facade = runtime = None
        # The host grants only typed; generic registration cannot inherit it.
        integ._REQUESTED_OPERATIONS = frozenset({'account.snapshot'})
        runtime = integ._direct_runtime(raw, WireIdentity(WireRole.STRATEGY_CLIENT, integ._PRINCIPAL_ONE, integ._STRATEGY_ONE), service, client_keys)
        with pytest.raises(ServiceRuntimeError):
            runtime.start()
        runtime.close()
        runtime = None
        integ._close_normally(record, expected_tag='closed')
    finally:
        integ._REQUESTED_OPERATIONS = saved
        if facade is not None:
            facade.close()
        if runtime is not None:
            runtime.close()
        integ._bounded_reap_spawned_process(record, forced, failures)
        integ._assert_no_fallback_cleanup(forced, failures)


def test_direct_account_module_does_not_import_gateway_or_codec():
    code = "import sys; import bt_api_py.service_account; assert 'bt_api_base.wire_codec_v1' not in sys.modules; assert 'bt_api_gateway' not in sys.modules"
    subprocess.run([sys.executable, '-c', code], check=True, capture_output=True, text=True)


def test_typed_requires_explicit_discovered_capability(tmp_path):
    generic, runtime, registry, *_ = unit._make_client(tmp_path)
    typed = ServiceAccountClient(
        runtime, 'store.account', account_ref=unit._ACCOUNT_REF,
        expected_account=unit._ACCOUNT, required_coverage=unit._COVERAGE,
        snapshot_format='typed',
    )
    try:
        assert generic.refresh().observation == unit._observation()
        with pytest.raises(ServiceAccountError, match='ACCOUNT_BINDING_UNAVAILABLE'):
            typed.refresh()
    finally:
        typed.close()
        unit._close_all(generic, runtime, registry)


def test_typed_same_cursor_preserves_conflict_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(unit, '_OPERATIONS', frozenset({'account.snapshot', 'account.snapshot.typed'}))
    generic, runtime, registry, source, *_ = unit._make_client(tmp_path)
    registry.register_handler('account-instance', 'account.snapshot.typed', source.snapshot)
    typed = ServiceAccountClient(
        runtime, 'store.account', account_ref=unit._ACCOUNT_REF,
        expected_account=unit._ACCOUNT, required_coverage=unit._COVERAGE,
        snapshot_format='typed',
    )
    try:
        source.response = _typed_reply(cursor=7)
        first = typed.refresh()
        assert typed.refresh().observation.to_dict() == first.observation.to_dict()
        changed = replace(unit._observation(), observation_id='changed')
        source.response = _typed_reply(changed, cursor=7)
        with pytest.raises(ServiceAccountError, match='ACCOUNT_CURSOR_CONFLICT'):
            typed.refresh()
    finally:
        typed.close()
        unit._close_all(generic, runtime, registry)


def test_scope_pin_is_only_from_accepted_snapshot_and_is_epoch_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(unit, '_OPERATIONS', frozenset({'account.snapshot', 'account.snapshot.typed'}))
    generic, runtime, registry, source, *_ = unit._make_client(tmp_path)
    registry.register_handler('account-instance', 'account.snapshot.typed', source.snapshot)
    typed = ServiceAccountClient(
        runtime, 'store.account', account_ref=unit._ACCOUNT_REF,
        expected_account=unit._ACCOUNT, required_coverage=unit._COVERAGE,
        snapshot_format='typed',
    )
    try:
        source.response = _typed_reply(cursor=7)
        source.response['snapshot']['account_scope'] = 'untrusted-invalid'
        source.response['snapshot']['payload']['observation']['registry_digest'] = 'bad'
        with pytest.raises(ServiceAccountError, match='INVALID_ACCOUNT_OBSERVATION'):
            typed.refresh()
        source.response = _typed_reply(cursor=7)
        typed.refresh()
        for field in ('account_scope', 'strategy_scope'):
            changed = _typed_reply(cursor=8)
            changed['snapshot'][field] = 'different'
            source.response = changed
            with pytest.raises(ServiceAccountError, match='ACCOUNT_SCOPE_CHANGED'):
                typed.refresh()
        source.response = _typed_reply(cursor=8)
        typed.refresh()
        later = _typed_reply(cursor=1)
        later['snapshot']['payload']['session_generation'] = 'generation-two'
        later['state_status']['session_generation'] = 'generation-two'
        later['snapshot']['account_scope'] = 'later-scope'
        source.response = later
        typed.refresh()
        rollback = _typed_reply(cursor=9)
        rollback['snapshot']['account_scope'] = 'changed-old-generation'
        source.response = rollback
        with pytest.raises(ServiceAccountError, match='ACCOUNT_SCOPE_CHANGED'):
            typed.refresh()
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(ServiceAccountError, match='WRONG_OWNER_THREAD'):
                executor.submit(typed.refresh).result()
        original_binding = typed._get_binding
        monkeypatch.setattr(typed, '_get_binding', lambda **kw: replace(original_binding(**kw), service_epoch='new-epoch'))
        new_epoch = _typed_reply(cursor=0)
        new_epoch['snapshot']['account_scope'] = 'new-scope'
        source.response = new_epoch
        assert typed.refresh().cursor == 0
    finally:
        typed.close()
        unit._close_all(generic, runtime, registry)


def test_scope_history_capacity_rejects_new_generation_without_forgetting(tmp_path, monkeypatch):
    from bt_api_py import service_account

    monkeypatch.setattr(service_account, '_MAX_ACCOUNT_SCOPE_GENERATIONS', 2)
    monkeypatch.setattr(unit, '_OPERATIONS', frozenset({'account.snapshot', 'account.snapshot.typed'}))
    generic, runtime, registry, source, *_ = unit._make_client(tmp_path)
    registry.register_handler('account-instance', 'account.snapshot.typed', source.snapshot)
    typed = ServiceAccountClient(
        runtime, 'store.account', account_ref=unit._ACCOUNT_REF,
        expected_account=unit._ACCOUNT, required_coverage=unit._COVERAGE,
        snapshot_format='typed',
    )
    def reply(generation, scope):
        value = _typed_reply(cursor=0)
        value['snapshot']['payload']['session_generation'] = generation
        value['state_status']['session_generation'] = generation
        value['snapshot']['account_scope'] = scope
        return value
    try:
        source.response = reply('generation-one', 'scope-a')
        typed.refresh()
        source.response = reply('generation-two', 'scope-b')
        typed.refresh()
        source.response = reply('generation-three', 'scope-c')
        with pytest.raises(ServiceAccountError, match='ACCOUNT_SCOPE_HISTORY_EXHAUSTED'):
            typed.refresh()
        # Exhaustion must not erase earlier pins or poison the next epoch.
        source.response = reply('generation-one', 'changed-a')
        with pytest.raises(ServiceAccountError, match='ACCOUNT_SCOPE_CHANGED'):
            typed.refresh()
        source.response = reply('generation-one', 'scope-a')
        typed.refresh()
        original_binding = typed._get_binding
        monkeypatch.setattr(typed, '_get_binding', lambda **kw: replace(original_binding(**kw), service_epoch='new-epoch'))
        source.response = reply('generation-three', 'scope-c')
        typed.refresh()
        assert list(typed._history_by_generation) == ['generation-three']
        assert typed._history_by_generation['generation-three'][:2] == ('scope-c', 'journal-account-strategy')
    finally:
        typed.close()
        unit._close_all(generic, runtime, registry)


def test_cursor_history_survives_generation_switch_and_failed_epoch_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(unit, '_OPERATIONS', frozenset({'account.snapshot', 'account.snapshot.typed'}))
    generic, runtime, registry, source, *_ = unit._make_client(tmp_path)
    registry.register_handler('account-instance', 'account.snapshot.typed', source.snapshot)
    typed = ServiceAccountClient(runtime, 'store.account', account_ref=unit._ACCOUNT_REF,
                                 expected_account=unit._ACCOUNT, required_coverage=unit._COVERAGE,
                                 snapshot_format='typed')
    def reply(generation, cursor, observation=None):
        value = _typed_reply(observation, cursor=cursor)
        value['snapshot']['payload']['session_generation'] = generation
        value['state_status']['session_generation'] = generation
        return value
    try:
        source.response = reply('A', 7)
        typed.refresh()
        source.response = reply('A', 8)
        typed.refresh()
        source.response = reply('B', 1)
        typed.refresh()
        source.response = reply('A', 3)
        with pytest.raises(ServiceAccountError, match='ACCOUNT_CURSOR_REGRESSION'):
            typed.refresh()
        source.response = reply('A', 8, replace(unit._observation(), observation_id='different'))
        with pytest.raises(ServiceAccountError, match='ACCOUNT_CURSOR_CONFLICT'):
            typed.refresh()
        invalid = reply('A', 100)
        invalid['snapshot']['payload']['observation']['registry_digest'] = 'invalid'
        source.response = invalid
        with pytest.raises(ServiceAccountError, match='INVALID_ACCOUNT_OBSERVATION'):
            typed.refresh()
        source.response = reply('A', 9)
        typed.refresh()
        original_binding = typed._get_binding
        epoch = ['new-epoch']
        monkeypatch.setattr(typed, '_get_binding', lambda **kw: replace(original_binding(**kw), service_epoch=epoch[0]))
        source.response = reply('A', 0)
        source.response['snapshot']['payload']['observation']['registry_digest'] = 'invalid'
        with pytest.raises(ServiceAccountError, match='INVALID_ACCOUNT_OBSERVATION'):
            typed.refresh()
        epoch[0] = typed._scope_epoch
        source.response = reply('A', 8)
        with pytest.raises(ServiceAccountError, match='ACCOUNT_CURSOR_REGRESSION'):
            typed.refresh()
        epoch[0] = 'new-epoch'
        source.response = reply('A', 0)
        assert typed.refresh().cursor == 0
    finally:
        typed.close()
        unit._close_all(generic, runtime, registry)


def test_history_byte_limit_rejects_without_overwriting_and_resets_on_epoch(tmp_path, monkeypatch):
    from bt_api_py import service_account

    monkeypatch.setattr(unit, '_OPERATIONS', frozenset({'account.snapshot', 'account.snapshot.typed'}))
    generic, runtime, registry, source, *_ = unit._make_client(tmp_path)
    registry.register_handler('account-instance', 'account.snapshot.typed', source.snapshot)
    typed = ServiceAccountClient(runtime, 'store.account', account_ref=unit._ACCOUNT_REF,
                                 expected_account=unit._ACCOUNT, required_coverage=unit._COVERAGE,
                                 snapshot_format='typed')
    try:
        source.response = _typed_reply(cursor=0)
        typed.refresh()
        monkeypatch.setattr(service_account, '_MAX_ACCOUNT_SCOPE_HISTORY_BYTES', typed._history_bytes)
        source.response = _typed_reply(cursor=1)
        source.response['snapshot']['snapshot_id'] = 'snapshot-id-too-large-for-the-bound'
        with pytest.raises(ServiceAccountError, match='ACCOUNT_SCOPE_HISTORY_EXHAUSTED'):
            typed.refresh()
        source.response = _typed_reply(cursor=0)
        typed.refresh()
        source.response = _typed_reply(cursor=1)
        typed.refresh()
        original_binding = typed._get_binding
        monkeypatch.setattr(typed, '_get_binding', lambda **kw: replace(original_binding(**kw), service_epoch='new-epoch'))
        source.response = _typed_reply(cursor=0)
        assert typed.refresh().cursor == 0
    finally:
        typed.close()
        unit._close_all(generic, runtime, registry)


def test_generic_account_history_limits_fail_closed(tmp_path, monkeypatch):
    from bt_api_py import service_account

    monkeypatch.setattr(service_account, '_MAX_ACCOUNT_SCOPE_GENERATIONS', 1)
    generic, runtime, registry, source, *_ = unit._make_client(tmp_path)
    try:
        source.response = unit._reply(cursor=1)
        generic.refresh()
        source.response = unit._reply(generation='generation-two', cursor=0)
        with pytest.raises(ServiceAccountError, match='ACCOUNT_SCOPE_HISTORY_EXHAUSTED'):
            generic.refresh()
        source.response = unit._reply(cursor=1)
        generic.refresh()
        monkeypatch.setattr(service_account, '_MAX_ACCOUNT_SCOPE_HISTORY_BYTES', generic._history_bytes)
        source.response = unit._reply(cursor=2, snapshot_id='snapshot-id-greater-than-bound')
        with pytest.raises(ServiceAccountError, match='ACCOUNT_SCOPE_HISTORY_EXHAUSTED'):
            generic.refresh()
        source.response = unit._reply(cursor=2)
        generic.refresh()
    finally:
        unit._close_all(generic, runtime, registry)
