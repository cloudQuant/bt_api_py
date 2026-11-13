"""SDK actor operation declarations and synthetic registration selection."""

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import (
    _GATEWAY_CONTROL_WRITE_OPERATIONS,
    _GATEWAY_DELEGATE_ACTOR_READ_OPERATIONS,
    _GATEWAY_DELEGATE_ACTOR_WRITE_OPERATIONS,
    _GATEWAY_DELEGATED_WRITE_OPERATIONS,
    _GATEWAY_PARENT_WRITE_OPERATIONS,
)
from bt_api_py.service_runtime import PORT_OPERATIONS, ServiceClientRuntime, ServiceRuntimeError


V2 = frozenset({'direct.delegate_actor_status_v2', 'direct.delegate_actor_release_status_v2'})
LEGACY = frozenset({'direct.delegate_actor_status', 'direct.delegate_actor_release_status'})
MOUNT = 'upstream.direct_write'
BINDING = {
    'mount': MOUNT, 'service_id': 'synthetic-service', 'instance_id': 'synthetic-instance',
    'scope_ref': 'synthetic-account', 'account_ref': 'synthetic-account', 'transport_ref': 'gateway',
}
SCOPE = WireScope('synthetic-domain', account_ref='synthetic-account')


def make_runtime(requested=None):
    config = parse_service_config({
        'service_config_schema_version': 1, 'process_role': 'service',
        'service_id': 'synthetic-execution', 'scope_ref': 'execution-root',
        'transport_ref': 'unused', 'registration_policy_ref': 'policy',
        'service_requirements_ref': 'requirements',
        'components': {'monitor': {'config_schema_version': 2, 'settings': {
            'instance_id': 'unused-monitor', 'bind_ports': ['service.telemetry_ingress'],
            'options': {'role': 'gateway_heartbeat'}}}},
        'clients': {'gateway': {'config_schema_version': 1, 'settings': {'bindings': [BINDING]}}},
        'communication': {'config_schema_version': 1, 'profiles': {
            'unused': {'backend': 'zmq', 'config_schema_version': 1,
                       'endpoints': {'rpc_bind': 'tcp://127.0.0.1:1'}, 'security_profile_ref': 'unused'},
            'gateway': {'backend': 'zmq', 'config_schema_version': 1,
                        'endpoints': {'rpc_connect': 'tcp://127.0.0.1:1'}, 'security_profile_ref': 'unused'},
        }},
    })
    return ServiceClientRuntime(
        config, identity=WireIdentity(WireRole.SERVICE, 'synthetic-execution', 'synthetic-actor'),
        scope_resolver=lambda _: SCOPE, peer_resolver=lambda _: None,
        transport_factory=lambda *_args: None,
        requested_operations=None if requested is None else {MOUNT: requested},
    )


class CapturedRegistration(BaseException):
    pass


def registration_operations(runtime, monkeypatch, remote):
    seen = []

    def capture(_service, _transport, _scope, operation, payload, **_kwargs):
        assert operation == 'service.register'
        seen.append(frozenset(payload['operations']))
        raise CapturedRegistration()

    monkeypatch.setattr(runtime, '_rpc', capture)
    capability = {'instance_id': BINDING['instance_id'], 'port': 'service.direct_write',
                  'operations': sorted(remote)}
    with pytest.raises(CapturedRegistration):
        runtime._register_port(BINDING, SCOPE, 'service.direct_write', [capability],
                               timeout=1, required=True, heartbeat_interval_seconds=None)
    return seen[0]


def test_v2_operations_are_read_only_and_legacy_names_remain():
    assert V2 | LEGACY <= PORT_OPERATIONS['service.direct_write']
    assert V2 | LEGACY <= _GATEWAY_DELEGATE_ACTOR_READ_OPERATIONS
    assert V2.isdisjoint(_GATEWAY_DELEGATE_ACTOR_WRITE_OPERATIONS |
                         _GATEWAY_DELEGATED_WRITE_OPERATIONS | _GATEWAY_PARENT_WRITE_OPERATIONS |
                         _GATEWAY_CONTROL_WRITE_OPERATIONS)


@pytest.mark.parametrize('requested', [V2, LEGACY, V2 | LEGACY])
def test_explicit_read_request_negotiates_advertised_operations(monkeypatch, requested):
    runtime = make_runtime(requested)
    assert registration_operations(runtime, monkeypatch, V2 | LEGACY) == requested


def test_old_gateway_without_v2_still_registers_legacy_defaults(monkeypatch):
    runtime = make_runtime()
    assert registration_operations(runtime, monkeypatch, LEGACY | {'direct.reserve'}) == {'direct.reserve'}


def test_default_selection_includes_advertised_v2_read_operations(monkeypatch):
    runtime = make_runtime()
    assert registration_operations(runtime, monkeypatch, V2 | LEGACY | {'direct.reserve'}) == V2 | {'direct.reserve'}


def test_explicit_v2_request_refuses_legacy_only_gateway(monkeypatch):
    runtime = make_runtime(V2)
    called = []
    monkeypatch.setattr(runtime, '_rpc', lambda *_args, **_kwargs: called.append(True))
    capability = {'instance_id': BINDING['instance_id'], 'port': 'service.direct_write',
                  'operations': sorted(LEGACY)}
    with pytest.raises(ServiceRuntimeError, match='CAPABILITY_UNAVAILABLE'):
        runtime._register_port(BINDING, SCOPE, 'service.direct_write', [capability],
                               timeout=1, required=True, heartbeat_interval_seconds=None)
    assert not called
