"""Opt-in synthetic localhost integration; run under a network-deny sandbox."""
import hashlib
import os
from dataclasses import replace

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_transport_zmq.server import CurveServerCredentials

from bt_api_py.execution_management_read_client import ExecutionManagementReadClient
from bt_api_py.execution_management_read_grant import ExecutionManagementReadGrant, PARENT_READ_V1
from bt_api_py.execution_management_read_host import ExecutionManagementReadHost, TrustedManagementReadListener
from bt_api_py.execution_parent_grant import SUBMIT
from bt_api_py.service_config import parse_client_config
from bt_api_py.service_runtime import (BoundExecutionManagementReadTarget, ClientCurveKeys,
                                       ServiceClientRuntime, ZmqTransportFactory)
from bt_api_py.service_runtime_thread import ThreadedServiceClientRuntime
from test_iter65_atomic_submission_intake import atomic
from test_iter65_fresh_submission_intake import KEY, OTHER

pytestmark = pytest.mark.skipif(
    os.environ.get('BT_API_SYNTHETIC_CURVE_LOOPBACK') != '1' or not zmq.has('curve'),
    reason='requires explicit synthetic localhost CURVE sandbox opt-in',
)


def _config(endpoint, account):
    return parse_client_config({
        'client_config_schema_version': 1, 'process_role': 'application_client',
        'registration': {'application_ref': 'synthetic-management'},
        'clients': {'execution': {'config_schema_version': 1, 'settings': {
            'bindings': [{'mount': 'application.execution_management', 'service_id': 'management-service',
                'instance_id': 'management-instance', 'scope_ref': 'management-scope',
                'account_ref': account, 'transport_ref': 'management-zmq'}],
            'options': {'request_timeout_ms': 2000, 'heartbeat_interval_seconds': 5}}}},
        'communication': {'config_schema_version': 1, 'profiles': {
            'management-zmq': {'backend': 'zmq', 'config_schema_version': 1,
                'endpoints': {'rpc_connect': endpoint}, 'security_profile_ref': 'synthetic-key'}}},
    })


def test_real_sdk_client_over_isolated_localhost_curve(tmp_path, atomic):
    rig, _, _ = atomic
    original = rig.parent()
    assert rig.call(SUBMIT, original)['ok']
    rig.registry.set_ready(False)
    app = WireIdentity(WireRole.APPLICATION_CLIENT, 'synthetic-management', 'synthetic-actor')
    scope = replace(rig.scope, run_id=None)
    grant = ExecutionManagementReadGrant('management-instance', app, scope, 'policy-v1',
        frozenset({KEY}), frozenset({PARENT_READ_V1}))
    audit_root = tmp_path / 'management-audit'
    audit_root.mkdir(mode=0o700)
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    peer = WireIdentity(WireRole.SERVICE, 'synthetic-server', 'synthetic-service')
    selection = TrustedManagementReadListener(host=rig.host, local_scope=rig.target, grant=grant,
        scope_ref='management-scope', service_id='management-service', service_identity=peer,
        audit_root=audit_root, endpoint='tcp://127.0.0.1:*', principals={client_public: app.principal_ref},
        credentials=CurveServerCredentials(server_public, server_secret), lease_seconds=15)
    owner = ExecutionManagementReadHost(selection)
    wrapper = None
    try:
        started = owner.start(timeout=5)
        assert started.outcome == 'RUNNING' and started.endpoint.startswith('tcp://127.0.0.1:')
        config = _config(started.endpoint, scope.account_ref)
        keys = ClientCurveKeys(server_public, client_public, client_secret)
        def factory():
            return ServiceClientRuntime(config, identity=app, scope_resolver=lambda _: scope,
                peer_resolver=lambda _: peer,
                transport_factory=ZmqTransportFactory({'synthetic-key': keys}, strict_single_connection=True),
                run_id='local-runtime', requested_operations={'application.execution_management':
                                                             frozenset({PARENT_READ_V1})})
        wrapper = ThreadedServiceClientRuntime(factory)
        started_client = wrapper.start(timeout_seconds=8)
        binding, = started_client.bindings
        target = BoundExecutionManagementReadTarget('application.execution_management',
            'service.execution_management_read', 'management-service', grant.instance_id, 'management-scope',
            scope, app, 'local-runtime', 'management-zmq', 'synthetic-key', started.endpoint, peer,
            hashlib.sha256(server_public).hexdigest(), binding.registration_id, binding.service_epoch)
        client = ExecutionManagementReadClient(wrapper, target=target, grant_revision=grant.revision)
        parent = original['spec']['parent_ref']
        first = client.query(parent, query_id='query-1')
        assert first.status == 'OBSERVED' and first.source == 'LOCAL_JOURNAL'
        assert first.local_result_time_ns > 0 and first.snapshot.parent_ref == parent
        assert client.query(parent, query_id='query-1').status == 'OBSERVED'
        assert client.query('different-parent', query_id='query-1').status == 'UNKNOWN'
        missing = client.query('missing-parent', query_id='missing')
        assert missing.status == 'UNKNOWN' and missing.snapshot is None
        assert ExecutionManagementReadClient(wrapper, target=replace(target, server_key_fingerprint='0' * 64),
            grant_revision=grant.revision).query(parent, query_id='bad-key').status == 'UNKNOWN'
        restricted = replace(grant, revision='policy-v2', instruments=frozenset({OTHER}))
        assert owner._service.replace_grant(grant, restricted)
        restricted_client = ExecutionManagementReadClient(wrapper, target=target, grant_revision=restricted.revision)
        assert restricted_client.query(parent, query_id='excluded').status == 'UNKNOWN'
        assert restricted_client.query('missing-parent', query_id='absent').status == 'UNKNOWN'
        assert rig.registry._ready is False
    finally:
        if wrapper is not None:
            assert wrapper.close(timeout_seconds=5).state == 'closed'
        status = owner.stop(timeout=5)
        assert status.outcome == 'STOPPED' and status.cleanup == 'CONFIRMED'
    assert not rig.host._observer_holds
