"""Risk and Execution service principals on the Gateway market hub (63.13 base, D63-56).

A delegate SERVICE principal gets its own market keys and an account-level market
session, separate from any strategy run. Synthetic CTP composition and market provider;
no provider session, no account.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import replace

import pytest
import zmq
from bt_api_base.market_protocol import MarketInstrumentKey, MarketSubscriptionKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_gateway.direct_delegation import GatewayDelegateGrant

import bt_api_py.ctp_gateway_trading as module
from bt_api_py.gateway_delegation_service import GatewayDelegationSettings
from bt_api_py.gateway_direct_service import GatewayControlGrant
from bt_api_py.service_config import parse_service_config
from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
from tests.integration.test_service_account_instrument_metadata import _free_endpoint
from tests.test_iter63_ctp_gateway_trading import CLIENT_A, FakeSession, make
from tests.unit.test_service_registry import _next_request, _register

RISK = WireIdentity(WireRole.SERVICE, 'risk-principal', 'risk-actor')
EXECUTION = WireIdentity(WireRole.SERVICE, 'execution-principal', 'execution-actor')
MONITOR = WireIdentity(WireRole.SERVICE, 'monitor-principal', 'monitor-actor')
# The trading-owner hub bootstraps asynchronously; the clock observation qualifies a dispatch.
MARKET_OPS = frozenset({'market.subscribe', 'market.clock_observation', 'market.bootstrap_begin',
                        'market.bootstrap_status', 'market.bootstrap_cancel'})
RB = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb2612', 'rb2612')
HC = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'hc2612', 'hc2612')


def _key(instrument):
    return MarketSubscriptionKey('source', 'entitlement', instrument, 'tick')


class Market:
    def __init__(self):
        self.calls = []

    def subscribe(self, subscription, *, generation):
        self.calls.append(('subscribe', subscription, threading.get_ident()))
        return object()

    def unsubscribe(self, handle):
        return True

    def source_epoch_reset_required(self, handle):
        return False

    def retained_events_ready(self, handle):
        return False

    def poll(self, handle, *, limit):
        return ()


def _build(tmp_path, monkeypatch, *, services=(RISK, EXECUTION), delegates=(RISK, EXECUTION),
           acl=None):
    market = Market()
    monkeypatch.setattr(FakeSession, 'market_provider', property(lambda _session: market), raising=False)
    _, settings, config, authority, grants, _ = make(tmp_path, monkeypatch)
    raw = config.to_public_dict()
    gateway = raw['components']['gateway']['settings']
    gateway['bind_ports'].append('service.market_source')
    gateway['options'].update(control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True,
                              delegation_enabled=True)
    transport = raw['communication']['profiles']['server-transport']
    transport['endpoints']['market_bind'] = _free_endpoint()
    # Each principal sees only part of the key universe: a partitioned audience (wire v2).
    transport.setdefault('options', {})['market_wire_version'] = 2
    config = parse_service_config(raw)
    domain = authority.scope.execution_domain_ref
    account_scope = WireScope(domain, account_ref=settings.account_scope)
    template = WireScope(domain, account_ref=settings.account_scope, strategy_ref=CLIENT_A.actor_ref)
    operator = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'operator-actor')
    control_ops = frozenset({'direct.control.status'})
    principals = dict(authority.principals)
    principal_grants = dict(authority.grants)
    principals[zmq.curve_keypair()[0]] = 'operator'
    principal_grants['operator'] = (PrincipalGrant(operator, (ResourceGrant('gateway-a', 'service.direct_write',
        config.scope_ref, account_scope, control_ops),)),)
    delegate_grants = []
    for identity in services:
        resources = [ResourceGrant('gateway-a', 'service.market_source', config.scope_ref, account_scope,
                                   MARKET_OPS)]
        if identity in delegates:
            resources.append(ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, template,
                frozenset({'direct.delegated_reserve', 'direct.delegated_dispatch'})))
            delegate_grants.append(GatewayDelegateGrant(identity, settings.account_scope, CLIENT_A.actor_ref,
                domain, 'risk' if identity is RISK else 'execution', 'rev1',
                frozenset({MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')}),
                frozenset({'direct.reserve', 'direct.dispatch'})))
        principals[zmq.curve_keypair()[0]] = identity.principal_ref
        principal_grants[identity.principal_ref] = (PrincipalGrant(identity, tuple(resources)),)
    authority = replace(authority, principals=principals, grants=principal_grants)
    keys = frozenset({_key(RB), _key(HC)})
    acl = acl or {RISK.principal_ref: frozenset({_key(RB)}), EXECUTION.principal_ref: frozenset({_key(HC)})}
    profile = module.build_ctp_gateway_trading_profile(config, authority, tmp_path / 'registry.sqlite',
        profile_ref='service-market', settings=settings, grants=grants, request_feed_factory=lambda: None,
        market_stream_factory=lambda *_: None, assert_admitted=lambda _: True,
        market_settings=module.CtpGatewayMarketSettings(keys, acl),
        control_grants={'operator': GatewayControlGrant('operator-actor', True, frozenset(), control_ops)},
        upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True,
        upgrade_account_extensions_from_v11=True,
        delegation_settings=GatewayDelegationSettings(tuple(delegate_grants), lambda _: True)
        if delegate_grants else None)
    return profile, config, account_scope, market


def test_risk_and_execution_principals_subscribe_only_their_own_market_keys(tmp_path, monkeypatch):
    profile, config, account_scope, market = _build(tmp_path, monkeypatch)
    deployment = profile.deployment_factory()
    deployment.start()
    try:
        registry = deployment._host._registry
        for identity, own, other in ((RISK, RB, HC), (EXECUTION, HC, RB)):
            registered = _register(registry, principal=identity.principal_ref, identity=identity, scope=account_scope,
                key='market-' + identity.principal_ref, instance_id='gateway-a', port='service.market_source',
                scope_ref=config.scope_ref, operations=tuple(sorted(MARKET_OPS)))
            assert registered['ok'], registered
            rec = registered['result']

            def call(operation, arguments, identity=identity, rec=rec):
                response = registry.handle(identity.principal_ref, _next_request(operation, {
                    'registration_id': rec['registration_id'], 'service_epoch': rec['service_epoch'],
                    'instance_id': 'gateway-a', 'arguments': arguments}, identity=identity, scope=account_scope))
                assert response['ok'], response
                return response['result']

            def bootstrap(instrument):
                operation_id = uuid.uuid4().hex
                started = call('market.bootstrap_begin', {'operation_id': operation_id,
                                                          'subscriptions': [{'key': _key(instrument).to_dict()}]})
                deadline = time.monotonic() + 5
                while started['status'] == 'pending' and time.monotonic() < deadline:
                    time.sleep(0.01)
                    started = call('market.bootstrap_status', {'operation_id': operation_id})
                return started
            accepted = bootstrap(own)
            assert accepted['status'] == 'active', (identity, accepted)
            assert accepted['items'][0]['subscription']['already_subscribed'] is False
            denied = bootstrap(other)   # a key outside this principal's own grant
            assert (denied['status'], denied['code']) == ('rejected', 'MARKET_KEY_DENIED'), (identity, denied)
            # Its market grant is account-level: never a strategy-run market session.
            strategy_scope = replace(account_scope, strategy_ref=CLIENT_A.actor_ref, run_id='run')
            refused = _register(registry, principal=identity.principal_ref, identity=identity, scope=strategy_scope,
                key='market-strategy-' + identity.principal_ref, instance_id='gateway-a',
                port='service.market_source', scope_ref=config.scope_ref, operations=tuple(sorted(MARKET_OPS)))
            assert not refused['ok'] and refused['code'] == 'SCOPE_DENIED', refused
        # One native subscription per granted key, made on the account owner's thread.
        assert [entry[1].instrument for entry in market.calls] == [RB, HC]
        assert {entry[2] for entry in market.calls} == {FakeSession.instances[0].owner}
    finally:
        deployment.close()


def test_a_service_principal_without_delegation_gets_no_market_keys(tmp_path, monkeypatch):
    # A SERVICE principal with market keys must be a declared delegate of this Gateway.
    acl = {RISK.principal_ref: frozenset({_key(RB)}), MONITOR.principal_ref: frozenset({_key(RB)})}
    with pytest.raises(module.CtpGatewayTradingError, match='INVALID_TRADING_CONFIGURATION'):
        _build(tmp_path, monkeypatch, services=(RISK, MONITOR), delegates=(RISK,), acl=acl)
