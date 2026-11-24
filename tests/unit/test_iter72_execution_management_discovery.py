"""Fixed-grant discovery is bounded, source-local and fail-closed."""
import json
from dataclasses import fields
from pathlib import Path

import pytest

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.local_parent_snapshot import LocalParentDiscoverySnapshot
from bt_api_py.execution_management_read_client import _parse_discovery_snapshot
from bt_api_py.execution_management_read_grant import (
    PARENT_DISCOVER_RECENT_V1,
    ExecutionManagementDiscoveryReadRequest,
    ExecutionManagementReadGrant,
    allows_request,
)
from bt_api_py.execution_management_read_service import _valid_discovery_snapshot
from bt_api_py.execution_read_service_host import OPERATIONS, ReadServiceError, parse_config
from bt_api_py.service_runtime import PORT_OPERATIONS


IDENTITY = WireIdentity(WireRole.APPLICATION_CLIENT, 'management-client', 'operator-client')
SCOPE = WireScope('example-domain', account_ref='account-example', strategy_ref='strategy-example')
INSTRUMENT = MarketInstrumentKey('example', 'EXAMPLE', 'paper', 'future', 'EXAMPLE:TEST', 'TEST')
INCARNATION = 'a' * 32
EXAMPLE = Path(__file__).resolve().parents[2] / 'examples/services/execution-read-service.example.json'


def grant(operations=frozenset({PARENT_DISCOVER_RECENT_V1})):
    return ExecutionManagementReadGrant(
        'execution-read-instance', IDENTITY, SCOPE, 'policy-v1', frozenset({INSTRUMENT}), operations)


def request(**changes):
    values = dict(instance_id='execution-read-instance', identity=IDENTITY, scope=SCOPE,
                  grant_revision='policy-v1', operation=PARENT_DISCOVER_RECENT_V1, query_id='query-1')
    values.update(changes)
    return ExecutionManagementDiscoveryReadRequest(**values)


def test_discovery_request_has_no_caller_selected_target_or_filter():
    query = request()
    assert {field.name for field in fields(query)} == {
        'instance_id', 'identity', 'scope', 'grant_revision', 'operation', 'query_id'}
    assert allows_request(grant(), query)
    assert not allows_request(grant(frozenset({'execution.management.parent.read.v1'})), query)
    assert not allows_request(grant(), request(grant_revision='other-revision'))
    assert not allows_request(grant(), request(scope=WireScope(
        'example-domain', account_ref='other-account', strategy_ref='strategy-example')))
    assert PARENT_DISCOVER_RECENT_V1 in PORT_OPERATIONS['service.execution_management_read']


@pytest.mark.parametrize('changes', [
    {'operation': 'execution.management.parent.read.v1'},
    {'query_id': ''},
    {'query_id': 'query-1\n'},
])
def test_invalid_discovery_claims_are_rejected(changes):
    with pytest.raises(ValueError, match='INVALID_EXECUTION_MANAGEMENT_READ_REQUEST'):
        request(**changes)


def test_discovery_response_accepts_only_complete_empty_or_two_valid_refs():
    assert _parse_discovery_snapshot({
        'status': 'OBSERVED', 'source': 'LOCAL_JOURNAL',
        'journal_incarnation': INCARNATION, 'parent_refs': [],
    }) == (INCARNATION, ())
    assert _parse_discovery_snapshot({
        'status': 'OBSERVED', 'source': 'LOCAL_JOURNAL',
        'journal_incarnation': INCARNATION, 'parent_refs': ['parent-a', 'parent-b'],
    }) == (INCARNATION, ('parent-a', 'parent-b'))
    assert _parse_discovery_snapshot({
        'status': 'UNKNOWN', 'source': 'LOCAL_JOURNAL',
        'journal_incarnation': None, 'parent_refs': [],
    }) is None


@pytest.mark.parametrize('raw', [
    {'status': 'UNKNOWN', 'source': 'LOCAL_JOURNAL', 'journal_incarnation': None,
     'parent_refs': ['must-not-leak']},
    {'status': 'UNKNOWN', 'source': 'LOCAL_JOURNAL', 'journal_incarnation': INCARNATION,
     'parent_refs': []},
    {'status': 'OBSERVED', 'source': 'LOCAL_JOURNAL', 'journal_incarnation': 'bad',
     'parent_refs': []},
    {'status': 'OBSERVED', 'source': 'LOCAL_JOURNAL', 'journal_incarnation': INCARNATION,
     'parent_refs': ['same', 'same']},
    {'status': 'OBSERVED', 'source': 'LOCAL_JOURNAL', 'journal_incarnation': INCARNATION,
     'parent_refs': ['a', 'b', 'c']},
    {'status': 'OBSERVED', 'source': 'REMOTE', 'journal_incarnation': INCARNATION,
     'parent_refs': ['parent-a']},
    {'status': 'OBSERVED', 'source': 'LOCAL_JOURNAL', 'journal_incarnation': INCARNATION,
     'parent_refs': [' bad-ref']},
    {'status': 'OBSERVED', 'source': 'LOCAL_JOURNAL', 'journal_incarnation': INCARNATION,
     'parent_refs': ['parent-a'], 'count': 1},
])
def test_malformed_discovery_never_projects_observed(raw):
    with pytest.raises(ValueError):
        _parse_discovery_snapshot(raw)


def test_service_contract_preserves_unknown_and_caps_observed_refs():
    assert _valid_discovery_snapshot(LocalParentDiscoverySnapshot('UNKNOWN'))
    assert _valid_discovery_snapshot(LocalParentDiscoverySnapshot(
        'OBSERVED', journal_incarnation=INCARNATION, parent_refs=()))
    assert _valid_discovery_snapshot(LocalParentDiscoverySnapshot(
        'OBSERVED', journal_incarnation=INCARNATION, parent_refs=('parent-a', 'parent-b')))
    assert not _valid_discovery_snapshot(LocalParentDiscoverySnapshot(
        'UNKNOWN', journal_incarnation=INCARNATION))
    assert not _valid_discovery_snapshot(LocalParentDiscoverySnapshot(
        'OBSERVED', journal_incarnation=INCARNATION, parent_refs=('same', 'same')))
    assert not _valid_discovery_snapshot(LocalParentDiscoverySnapshot(
        'OBSERVED', journal_incarnation=INCARNATION, parent_refs=('a', 'b', 'c')))


def test_host_operations_are_an_explicit_subset_of_supported_grants(tmp_path):
    raw = json.loads(EXAMPLE.read_text(encoding='utf-8'))
    for name in ('journal', 'audit', 'keys'):
        (tmp_path / name).mkdir()
    raw['storage'].update(journal_root=str(tmp_path / 'journal'), audit_root=str(tmp_path / 'audit'))
    raw['listener'].update(
        endpoint='tcp://127.0.0.1:17869',
        server_public_key_file=str(tmp_path / 'keys/server.public'),
        server_secret_key_file=str(tmp_path / 'keys/server.secret'),
        client_public_key_file=str(tmp_path / 'keys/client.public'),
    )
    configured = parse_config(raw)
    assert configured.operations == OPERATIONS
    assert PARENT_DISCOVER_RECENT_V1 in configured.operations

    raw['grant']['operations'] = [operation for operation in raw['grant']['operations']
                                  if operation != PARENT_DISCOVER_RECENT_V1]
    assert PARENT_DISCOVER_RECENT_V1 not in parse_config(raw).operations

    raw['grant']['operations'] = ['execution.management.parent.cancel.v1']
    with pytest.raises(ReadServiceError, match='INVALID_CONFIGURATION'):
        parse_config(raw)
    raw['grant']['operations'] = [PARENT_DISCOVER_RECENT_V1, PARENT_DISCOVER_RECENT_V1]
    with pytest.raises(ReadServiceError, match='INVALID_CONFIGURATION'):
        parse_config(raw)
