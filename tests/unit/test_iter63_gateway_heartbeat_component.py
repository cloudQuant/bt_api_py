"""Gateway heartbeat service is an explicit trusted Monitor component."""
from types import MappingProxyType

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_monitor.service_gateway_heartbeat import GatewayHeartbeatService, GatewayHeartbeatError
from bt_api_py.service_components import ComponentResources, build_component, component_specs, validate_components
from bt_api_py.service_registry import ServiceCallContext
from tests.unit.test_service_monitor_ingress import _service_config


def test_heartbeat_component_passes_authenticated_context_and_request():
    scope = WireScope('test-domain', account_ref='account')
    identity = WireIdentity(WireRole.SERVICE, 'gateway-p', 'gateway-a')
    epochs = {(identity, scope): 'current-epoch'}
    service = GatewayHeartbeatService(monitor_principal='monitor-p', targets=frozenset({(identity, scope)}),
        validate_current_epoch=lambda who, target, epoch: epochs.get((who, target)) == epoch)
    config = _service_config(bind_ports=('service.telemetry_ingress',), options={'role': 'gateway_heartbeat'})
    prepared, = validate_components(component_specs(config.components))
    assert prepared.operations_by_port == {'service.telemetry_ingress': frozenset({'monitor.heartbeat'})}
    component = build_component(prepared, scope=scope,
        resource_provider=lambda *_: ComponentResources.borrowed({'service': service}, ready=lambda: True))
    try:
        ctx = ServiceCallContext(identity, scope, 'current-epoch', identity.principal_ref)
        request = MappingProxyType({'schema_version': 1, 'relay_epoch': 'current-epoch', 'heartbeat_id': 'hb-1', 'sent_at_ns': 12})
        assert component.handlers['monitor.heartbeat'](ctx, request)['heartbeat_id'] == 'hb-1'
        epochs[(identity, scope)] = 'new-epoch'
        with pytest.raises(GatewayHeartbeatError):
            component.handlers['monitor.heartbeat'](ctx, request)
    finally:
        component.close()
