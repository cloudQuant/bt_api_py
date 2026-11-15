"""Synthetic Gateway composition; no sockets, native account or credentials."""
from dataclasses import replace
import importlib
import json
import sqlite3

import pytest
import zmq
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_execution.contracts import ExecutionScope
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_py.ctp_gateway_local_profile import _execution_subject_scopes, CtpLocalProfileError
from bt_api_py.gateway_direct_service import GatewayDirectGrant, GatewayDirectServiceBinding
from bt_api_py.gateway_execution_subject_scopes import GatewayExecutionSubjectBinding, GatewayExecutionSubjectScopes
from bt_api_py.service_components import ComponentResources
from bt_api_py.service_host import ServiceHost, ServiceHostError, TrustedServiceAuthority
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceRegistry
from tests.integration.test_iter63_gateway_direct_service import _service_config, SERVICE, DOMAIN, ACCOUNT
from tests.unit.test_iter65_fresh_intake_host import PollingServer
from tests.unit.test_service_registry import _register


A = WireIdentity(WireRole.STRATEGY_CLIENT, 'subject-a', 'strategy-a')
B = WireIdentity(WireRole.STRATEGY_CLIENT, 'subject-b', 'strategy-a')
WIRE = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref='strategy-a')
TARGET = ExecutionScope('ctp', 'simnow', 'synthetic-execution-account', 'strategy-a')


def scope_declaration():
    return GatewayExecutionSubjectScopes(frozenset({ACCOUNT}), {WIRE: TARGET})


def make_host(tmp_path, monkeypatch, declaration, events, *, wrong_journal=False):
    server_key, server_secret = zmq.curve_keypair()
    key_a, _ = zmq.curve_keypair()
    key_b, _ = zmq.curve_keypair()
    config = _service_config('tcp://127.0.0.1:24987')
    resource = ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', WIRE,
                             frozenset({'direct.reserve'}))
    authority = TrustedServiceAuthority(identity=SERVICE, scope=WireScope(DOMAIN), scope_ref='scope-a',
        scope_version='scope-v1', security_ref='security-a', security_version='security-v1',
        registration_policy_ref='direct-policy-v1', registration_policy_version='direct-policy-v1',
        service_requirements_ref='direct-requirements-v1', service_requirements_version='direct-requirements-v1',
        curve_public_key=server_key, curve_secret_key=server_secret,
        principals={key_a: A.principal_ref, key_b: B.principal_ref},
        grants={identity.principal_ref: (PrincipalGrant(identity, (resource,)),) for identity in (A, B)})
    journal_path = tmp_path / 'journal.sqlite'
    if journal_path.exists():
        with sqlite3.connect(journal_path) as db:
            journal_id = db.execute('SELECT journal_id FROM direct_journal_meta').fetchone()[0]
        journal = GatewayDirectCommandJournal(journal_path, ACCOUNT, generation='g1',
                                              expected_journal_id=journal_id, minimum_revision=0)
    else:
        journal = GatewayDirectCommandJournal.create(journal_path, ACCOUNT, generation='g1')
    def resources(*_):
        binding = GatewayDirectServiceBinding(journal, {
            A.principal_ref: GatewayDirectGrant('strategy-a', frozenset({'rb'})),
            B.principal_ref: GatewayDirectGrant('strategy-a', frozenset({'rb'}))})
        values = {'binding': binding}
        if declaration is not None:
            other = (GatewayDirectCommandJournal.create(tmp_path / 'other-journal.sqlite', ACCOUNT, generation='g1')
                     if wrong_journal else journal)
            values['execution_subject_binding'] = GatewayExecutionSubjectBinding(declaration, other)
        return ComponentResources.owned(values, ready=lambda: True, close=lambda: events.append('closed'))
    class Server(PollingServer):
        def __init__(self, *args, **kwargs):
            events.append('listener')
            super().__init__(*args, **kwargs)
    monkeypatch.setattr(importlib.import_module('bt_api_transport_zmq.service_v2'), 'ZmqServiceServer', Server)
    return ServiceHost(config, authority, tmp_path / 'registry.sqlite', resources)


def reg(host, identity, key):
    return _register(host._registry, identity=identity, principal=identity.principal_ref,
        scope=replace(WIRE, run_id='run-a'), key=key, client_session_id=key,
        instance_id='gateway-a', port='service.direct_write', scope_ref='scope-a', operations=('direct.reserve',))


def test_host_installs_before_listener_and_omission_never_downgrades(tmp_path, monkeypatch):
    events = []
    original = ServiceRegistry.configure_execution_subject_scopes
    def configure(registry, **kwargs):
        assert 'listener' not in events
        events.append('configured')
        return original(registry, **kwargs)
    monkeypatch.setattr(ServiceRegistry, 'configure_execution_subject_scopes', configure)
    host = make_host(tmp_path, monkeypatch, scope_declaration(), events)
    try:
        host.start()
        assert events[:2] == ['configured', 'listener']
        assert reg(host, A, 'a')['ok']
        assert not reg(host, B, 'b')['ok']
    finally:
        host.close()
    events.clear()
    host = make_host(tmp_path, monkeypatch, None, events)
    try:
        with pytest.raises(ServiceHostError, match='EXECUTION_SUBJECT_SCOPES_INVALID'):
            host.start()
        assert 'listener' not in events and 'closed' in events
    finally:
        host.close()
    events.clear()
    changed = GatewayExecutionSubjectScopes(frozenset({ACCOUNT}), {WIRE: replace(TARGET, strategy_id='changed')})
    host = make_host(tmp_path, monkeypatch, changed, events)
    try:
        with pytest.raises(ServiceHostError, match='EXECUTION_SUBJECT_SCOPES_INVALID'):
            host.start()
        assert 'listener' not in events and 'closed' in events
    finally:
        host.close()
    events.clear()
    host = make_host(tmp_path, monkeypatch, scope_declaration(), events)
    try:
        host.start()
        assert not reg(host, B, 'b-restart')['ok']
        assert reg(host, A, 'a-restart')['ok']
    finally:
        host.close()


def test_declaration_for_different_journal_fails_before_listener(tmp_path, monkeypatch):
    events = []
    host = make_host(tmp_path, monkeypatch, scope_declaration(), events, wrong_journal=True)
    try:
        with pytest.raises(ServiceHostError):
            host.start()
        assert 'listener' not in events and 'closed' in events
    finally:
        host.close()


def test_immutable_parser_and_wrong_journal(tmp_path):
    row = {'execution_domain_ref': DOMAIN, 'account_ref': ACCOUNT, 'strategy_ref': 'strategy-a',
           'target': {'provider': 'ctp', 'environment': 'simnow', 'account_ref': 'exec', 'strategy_id': 's'}}
    parsed = _execution_subject_scopes({'protected_accounts': [ACCOUNT], 'aliases': [row]})
    row['target']['account_ref'] = 'changed'
    assert next(iter(parsed.aliases.values())).account_ref == 'exec'
    with pytest.raises(TypeError):
        parsed.aliases[WIRE] = None
    with pytest.raises(CtpLocalProfileError):
        _execution_subject_scopes({'protected_accounts': [ACCOUNT], 'aliases': [row, row]})
    wrong = GatewayDirectCommandJournal.create(tmp_path / 'other.sqlite', 'other-account', generation='g1')
    with pytest.raises(ValueError, match='INVALID_EXECUTION_SUBJECT_BINDING'):
        GatewayExecutionSubjectBinding(parsed, wrong)


def test_public_example_is_synthetic_explicit_and_parseable():
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'examples/services/ctp-gateway-execution-subject-scopes.example.json'
    declaration = _execution_subject_scopes(json.loads(path.read_text())['execution_subject_scopes'])
    assert len(declaration.aliases) == 2
    assert sum(target is None for target in declaration.aliases.values()) == 1


from tests.unit.test_iter65_gateway_parent_bound_v2 import service as parent_environment


@pytest.mark.parametrize('classification', ['selected', 'direct', 'missing', 'wrong-alias'])
def test_parent_enabled_host_requires_explicit_journal_bound_classification(
        tmp_path, monkeypatch, parent_environment, classification):
    from bt_api_py.gateway_direct_service import GatewayControlGrant
    from bt_api_py.gateway_parent_service import GatewayParentService
    env = parent_environment
    scope = replace(env['scope'], run_id=None)
    strategy = WireIdentity(WireRole.STRATEGY_CLIENT, 'strategy-principal', 'strategy')
    parent = GatewayParentService(env['parent_service'].settings, owner=env['owner'], journal=env['journal'],
        authority=env['parent_service'].authority, instance_id='gateway-a', scope_ref='scope-a')
    raw = _service_config('tcp://127.0.0.1:24987').to_public_dict()
    raw['components']['gateway']['settings']['options'] = {
        'role': 'trading_owner', 'dispatch_enabled': True, 'control_enabled': True,
        'strategy_ledger_enabled': True, 'account_extensions_enabled': True, 'parent_authority_enabled': True}
    from bt_api_py.service_config import parse_service_config
    config = parse_service_config(raw)
    ops = frozenset({'direct.reserve'})
    resource = ResourceGrant('gateway-a', 'service.direct_write', 'scope-a', scope, ops)
    server_key, server_secret = zmq.curve_keypair()
    client_key, _ = zmq.curve_keypair()
    authority = TrustedServiceAuthority(identity=SERVICE, scope=WireScope('domain'), scope_ref='scope-a',
        scope_version='scope-v1', security_ref='security-a', security_version='security-v1',
        registration_policy_ref='direct-policy-v1', registration_policy_version='direct-policy-v1',
        service_requirements_ref='direct-requirements-v1', service_requirements_version='direct-requirements-v1',
        curve_public_key=server_key, curve_secret_key=server_secret, principals={client_key: strategy.principal_ref},
        grants={strategy.principal_ref: (PrincipalGrant(strategy, (resource,)),)})
    from bt_api_gateway.direct_control import GatewayDirectControl
    control = env['owner'].submit_service_call(
        lambda: GatewayDirectControl(env['journal'], parent.authority._guard)).result(timeout_seconds=2)
    binding = GatewayDirectServiceBinding(env['journal'], {
        strategy.principal_ref: GatewayDirectGrant('strategy', frozenset({'rb'}))},
        owner=env['owner'], control=control, control_grants={
            'operator': GatewayControlGrant('operator', True, frozenset(), frozenset({'direct.control.status'}))})
    events = []
    class Server(PollingServer):
        def __init__(self, *args, **kwargs):
            events.append('listener')
            super().__init__(*args, **kwargs)
    monkeypatch.setattr(importlib.import_module('bt_api_transport_zmq.service_v2'), 'ZmqServiceServer', Server)
    def resources(*_):
        values = {'binding': binding, 'parent_service': parent}
        if classification != 'missing':
            target = None if classification in ('direct', 'wrong-alias') else ExecutionScope('ctp', 'simnow', 'exec-account', 'strategy')
            classified_scope = replace(scope, strategy_ref='other') if classification == 'wrong-alias' else scope
            declaration = GatewayExecutionSubjectScopes(frozenset({'account'}), {classified_scope: target})
            values['execution_subject_binding'] = GatewayExecutionSubjectBinding(declaration, env['journal'])
        return ComponentResources.owned(values, ready=lambda: True, close=lambda: events.append('closed'))
    host = ServiceHost(config, authority, tmp_path / 'parent-host.sqlite', resources)
    try:
        if classification in ('missing', 'wrong-alias'):
            with pytest.raises(ServiceHostError):
                host.start()
            assert 'listener' not in events and 'closed' in events
        else:
            host.start()
            assert 'listener' in events
            row = host._registry._db.execute('SELECT canonical_account_key FROM execution_subject_aliases').fetchone()
            assert (row[0] is None) == (classification == 'direct')
    finally:
        host.close()
