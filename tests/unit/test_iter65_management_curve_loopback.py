"""Opt-in localhost CURVE integration over synthetic journals and identities.

Run only inside a sandbox that permits loopback and denies external networking.
The listener uses an OS-assigned port, ephemeral keys, and temporary databases;
no provider, real account, existing service, or user credentials are accessed.
"""

from dataclasses import replace
import os
import sqlite3

import pytest
import zmq

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_execution.account_journal_host import AccountJournalHostError
from bt_api_py.execution_management_read_grant import ExecutionManagementReadGrant, PARENT_READ_V1
from bt_api_py.execution_management_read_host import ExecutionManagementReadHost, TrustedManagementReadListener
from bt_api_py.execution_management_read_service import PORT
from bt_api_py.execution_parent_grant import SUBMIT
from bt_api_transport_zmq.server import CurveClientCredentials, CurveServerCredentials
from bt_api_transport_zmq.service_v2 import RequestNotSentError, RequestOutcomeUnknownError, ZmqServiceClient
from test_iter65_atomic_submission_intake import atomic
from test_iter65_fresh_submission_intake import KEY, OTHER
from tests.unit.test_service_registry import _next_request, _register


pytestmark = pytest.mark.skipif(
    os.environ.get('BT_API_SYNTHETIC_CURVE_LOOPBACK') != '1' or not zmq.has('curve'),
    reason='requires explicit synthetic localhost CURVE sandbox opt-in',
)


class _TransportClient:
    def __init__(self, client):
        self._client = client

    def handle(self, _principal, request):
        # The transport, not this helper or request header, supplies principal.
        return dict(self._client.request(request, timeout=3.0).payload)


def _read_request(grant, registration, parent, query_id):
    return _next_request(PARENT_READ_V1, {
        'registration_id': registration['registration_id'],
        'service_epoch': registration['service_epoch'],
        'instance_id': grant.instance_id,
        'arguments': {'schema_version': 1, 'parent_ref': parent,
                      'query_id': query_id, 'grant_revision': grant.revision},
    }, identity=grant.identity, scope=grant.scope)


def test_localhost_curve_listener_binds_key_to_management_principal(tmp_path, atomic):
    rig, _, _ = atomic
    original = rig.parent()
    assert rig.call(SUBMIT, original)['ok']
    # The management listener has its own Registry; it cannot enable trading.
    rig.registry.set_ready(False)
    app = WireIdentity(WireRole.APPLICATION_CLIENT, 'synthetic-management', 'synthetic-actor')
    scope = replace(rig.scope, run_id=None)
    grant = ExecutionManagementReadGrant('management-instance', app, scope, 'policy-v1',
        frozenset({KEY}), frozenset({PARENT_READ_V1}))
    audit_root = tmp_path / 'management-audit'
    audit_root.mkdir(mode=0o700)
    server_public, server_secret = zmq.curve_keypair()
    client_public, client_secret = zmq.curve_keypair()
    server_identity = WireIdentity(WireRole.SERVICE, 'synthetic-server', 'synthetic-service')
    selection = TrustedManagementReadListener(
        host=rig.host, local_scope=rig.target, grant=grant, scope_ref='management-scope',
        service_id='management-service', service_identity=server_identity, audit_root=audit_root,
        endpoint='tcp://127.0.0.1:*', principals={client_public: app.principal_ref},
        credentials=CurveServerCredentials(server_public, server_secret), lease_seconds=15,
    )
    owner = ExecutionManagementReadHost(selection)
    clients = []
    try:
        started = owner.start(timeout=5)
        assert started.outcome == 'RUNNING'
        assert started.endpoint.startswith('tcp://127.0.0.1:')
        assert started.endpoint != selection.endpoint
        with pytest.raises(AccountJournalHostError, match='HOST_BUSY'):
            rig.host.close()

        valid = ZmqServiceClient(started.endpoint, server_identity,
            credentials=CurveClientCredentials(server_public, client_public, client_secret))
        clients.append(valid)
        client = _TransportClient(valid)
        response = _register(client, identity=app, scope=scope, principal=app.principal_ref,
            instance_id=grant.instance_id, port=PORT, scope_ref='management-scope',
            operations=(PARENT_READ_V1,))
        assert response['ok']
        registration = response['result']
        parent = original['spec']['parent_ref']
        observed = client.handle(app.principal_ref, _read_request(grant, registration, parent, 'query-1'))
        assert observed['ok']
        snapshot = observed['result']['snapshot']
        assert snapshot['status'] == 'OBSERVED' and snapshot['source'] == 'LOCAL_JOURNAL'
        assert snapshot['parent_ref'] == parent and snapshot['intake_local_state'] == 'RECEIVED'
        assert (snapshot['risk_local_state'], snapshot['gateway_local_state'],
                snapshot['cancel_local_state']) == ('UNKNOWN', 'UNKNOWN', 'UNKNOWN')
        assert rig.registry._ready is False

        # Even an authenticated key cannot substitute a different wire actor.
        forged_actor = WireIdentity(WireRole.APPLICATION_CLIENT, app.principal_ref, 'different-actor')
        forged = _next_request(PARENT_READ_V1, {
            'registration_id': registration['registration_id'],
            'service_epoch': registration['service_epoch'],
            'instance_id': grant.instance_id,
            'arguments': {'schema_version': 1, 'parent_ref': parent,
                          'query_id': 'wrong-actor', 'grant_revision': grant.revision},
        }, identity=forged_actor, scope=scope)
        assert not client.handle(app.principal_ref, forged)['ok']

        # Possession of a different CURVE key cannot be bypassed by copying
        # the valid wire identity and registration details. A timeout means no
        # valid response was received; by itself it cannot distinguish an ACL
        # rejection from a transport failure.
        unknown_public, unknown_secret = zmq.curve_keypair()
        unknown = ZmqServiceClient(started.endpoint, server_identity,
            credentials=CurveClientCredentials(server_public, unknown_public, unknown_secret))
        clients.append(unknown)
        with pytest.raises((RequestNotSentError, RequestOutcomeUnknownError)):
            unknown.request(_read_request(grant, registration, parent, 'unknown-key'), timeout=.5)

        wrong_public, _ = zmq.curve_keypair()
        wrong_server = ZmqServiceClient(started.endpoint, server_identity,
            credentials=CurveClientCredentials(wrong_public, client_public, client_secret))
        clients.append(wrong_server)
        with pytest.raises((RequestNotSentError, RequestOutcomeUnknownError)):
            wrong_server.request(_read_request(grant, registration, parent, 'wrong-server'), timeout=.5)

        # A source in the wrong contract's grant is indistinguishable from a
        # missing parent, even on an authenticated connection.
        restricted = replace(grant, revision='policy-v2', instruments=frozenset({OTHER}))
        assert owner._service.replace_grant(grant, restricted)
        denied = client.handle(app.principal_ref, _read_request(restricted, registration, parent, 'wrong-contract'))
        missing = client.handle(app.principal_ref, _read_request(restricted, registration, 'missing-parent', 'missing'))
        assert denied['ok'] and missing['ok']
        assert denied['result']['snapshot'] == missing['result']['snapshot']
        assert denied['result']['snapshot']['status'] == 'UNKNOWN'
        with sqlite3.connect(str(audit_root / 'execution-management-read.sqlite3')) as db:
            assert db.execute('SELECT count(*) FROM audit_attempts').fetchone()[0] == 3

        unregister = _next_request('service.unregister', {
            'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
        }, identity=app, scope=scope)
        assert client.handle(app.principal_ref, unregister)['ok']
    finally:
        for client in clients:
            client.close()
        status = owner.stop(timeout=5)
        assert status.outcome == 'STOPPED' and status.cleanup == 'CONFIRMED'
    assert not rig.host._observer_holds
    assert rig.registry._ready is False
