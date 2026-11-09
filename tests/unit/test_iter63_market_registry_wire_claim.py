"""Real Registry wire freezing at the market invalidation boundary."""
from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType

import pytest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.market_hub import GatewayMarketHub, GatewayMarketHubError, GatewayMarketService
from bt_api_py.gateway_dispatch_claim import GatewayDispatchClaimCoordinator
from bt_api_py.gateway_market_dispatch_gate import GatewayMarketDispatchGate
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry
from tests.integration.test_iter63_gateway_dual_ports import _resources
from tests.integration.test_iter63_gateway_direct_service import ACCOUNT, DOMAIN, SERVICE
from tests.unit.test_iter60_market_runtime_revocation import _Provider
from tests.unit.test_iter63_trading_market_component import _build, _config, _prepared
from tests.unit.test_service_host import _market_key
from tests.unit.test_service_registry import _next_request, _register


def test_real_registry_bad_market_wire_does_not_poison_but_provider_failure_does(tmp_path):
    binding, _, _ = _resources(tmp_path)
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref='strategy', run_id='run')
    class Provider(_Provider):
        def subscribe(self, key, *, generation):
            raise RuntimeError('synthetic provider failure')
    hub = GatewayMarketHub(Provider(), key_universe=(_market_key(),),
        principal_allowed_keys={'principal': {_market_key()}}, service_identity=SERVICE,
        service_scope=WireScope(DOMAIN))
    hub.set_publisher(lambda _: None)
    coordinator = GatewayDispatchClaimCoordinator(ACCOUNT, binding._journal.generation)
    gate = GatewayMarketDispatchGate(hub, GatewayMarketService(hub), account_scope=ACCOUNT,
        service_generation=binding._journal.generation, claim_coordinator=coordinator)
    hub.install_market_dispatch_gate(gate)
    component = _build(_prepared(_config(account=False)), {'binding': binding, 'hub': hub})
    operations = frozenset({'market.subscribe'})
    registry = ServiceRegistry(':memory:', service_id='gateway',
        capabilities=(ServiceCapability('gateway-a', 'service.market_source', operations),),
        grants={'principal': (PrincipalGrant(identity, (ResourceGrant('gateway-a', 'service.market_source',
            'scope', replace(scope, run_id=None), operations),)),)}, policy_version='p', requirements_version='r')
    registry.register_handler('gateway-a', 'market.subscribe', component.handlers['market.subscribe'])
    try:
        registration = _register(registry, principal='principal', identity=identity, scope=scope,
            instance_id='gateway-a', port='service.market_source', scope_ref='scope',
            operations=tuple(operations))['result']
        def call(arguments):
            return registry.handle('principal', _next_request('market.subscribe', {
                'registration_id': registration['registration_id'], 'service_epoch': registration['service_epoch'],
                'instance_id': 'gateway-a', 'arguments': arguments}, identity=identity, scope=scope))
        invalid = call({'unexpected': True})
        assert invalid['ok'] is False
        assert coordinator._domains['market'][4] is False
        epoch = coordinator._domains['market'][0]
        failed = call({'key': _market_key().to_dict()})
        assert failed['ok'] is False
        # Provider failure: epoch moved and the domain is UNAVAILABLE (fail
        # closed) until a later successful change; it is not poisoned forever.
        assert coordinator._domains['market'][4] is False
        assert coordinator._domains['market'][0] > epoch
        assert coordinator._domains['market'][1] == 'UNAVAILABLE'
    finally:
        registry.close()
        # An injected provider error leaves subscription outcome uncertain;
        # cleanup must report that, even though claim state is unavailable.
        with pytest.raises(GatewayMarketHubError, match='PROVIDER_SUBSCRIBE_OUTCOME_UNKNOWN'):
            hub.close()


def test_untrusted_mapping_proxy_never_invokes_custom_mapping(tmp_path):
    binding, _, _ = _resources(tmp_path)
    identity = WireIdentity(WireRole.STRATEGY_CLIENT, 'principal', 'strategy')
    scope = WireScope(DOMAIN, account_ref=ACCOUNT, strategy_ref='strategy', run_id='run')
    hub = GatewayMarketHub(_Provider(), key_universe=(_market_key(),),
        principal_allowed_keys={'principal': {_market_key()}}, service_identity=SERVICE,
        service_scope=WireScope(DOMAIN))
    hub.set_publisher(lambda _: None)
    component = _build(_prepared(_config(account=False)), {'binding': binding, 'hub': hub})
    from bt_api_py.service_registry import ServiceCallContext
    context = ServiceCallContext(identity, scope, 'reg', 'principal')
    touched = []
    class Evil(Mapping):
        def __getitem__(self, key):
            touched.append('getitem')
            raise KeyError(key)
        def __iter__(self):
            touched.append('iter')
            return iter(())
        def __len__(self):
            touched.append('len')
            return 0
    try:
        with pytest.raises(ValueError, match='INVALID_MARKET_ARGUMENTS'):
            component.handlers['market.subscribe'](context, MappingProxyType(Evil()))
        assert touched == []
    finally:
        hub.close()
