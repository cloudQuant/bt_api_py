"""Account facts keep a distinct cursor and expose no provider secrets."""
from dataclasses import replace

import pytest
from bt_api_base.direct_write_protocol import DirectExecutionEventKind
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.gateway_execution_stream_protocol import project_gateway_execution_observation
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.gateway_account_facts import account_facts_consumer_ref
from bt_api_py.gateway_account_facts_client import GatewayAccountFactsClient
from bt_api_py.gateway_account_observer_client import GatewayAccountObserverRoute
from bt_api_py.service_runtime_thread import BindingSnapshot, RuntimeSnapshot, ThreadedServiceClientRuntime


def _client(monkeypatch):
    identity = WireIdentity(WireRole.APPLICATION_CLIENT, 'operator', 'actor')
    scope = WireScope('domain', account_ref='account')
    route = GatewayAccountObserverRoute('application.account', 'service', 'gateway', 'scope', scope, identity)
    runtime = ThreadedServiceClientRuntime(lambda: None)
    ops = frozenset({'account.facts.read_after', 'account.facts.progress', 'account.facts.ack'})
    binding = BindingSnapshot('registration', 'epoch', True, route.mount, route.service_id,
                              route.instance_id, route.scope_ref, scope, ops, True)
    snap = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0, identity, 'run')
    monkeypatch.setattr(runtime, 'status', lambda: snap)
    client = GatewayAccountFactsClient(runtime, route=route)
    consumer = account_facts_consumer_ref(principal_ref='operator', identity=identity, scope=scope)
    instrument = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb2601', 'rb2601')
    fact = GatewayReturnFact('account', 'CTP', instrument, '20300101', DirectExecutionEventKind.TRADE,
        'secret-provider-source', trade_key=('SHFE', 'secret-native-id'), side='buy', offset='open',
        trade_quantity='1', trade_price='10')
    observation = project_gateway_execution_observation(fact).to_mapping()
    event = {'sequence': 1, 'fact_digest': 'b' * 64, 'observation': observation,
             'disposition': 'UNATTRIBUTED', 'reason': 'NO_MATCH', 'strategy_scope': None,
             'execution_sequence': None, 'reservation_id': None, 'origin': None,
             'parent_ref': None, 'child_id': None, 'external_receipt_digest': None, 'phase': 'BOOTSTRAP',
             'event_digest': 'c' * 64}
    base = {'schema_version': 1, 'account_scope': 'account', 'consumer_ref': consumer,
            'epoch': 'a' * 64, 'offered_sequence': 1, 'acknowledged_sequence': 0, 'bootstrap_count': 1}
    return client, runtime, snap, binding, base, event


def test_distinct_fact_cursor_snapshot_classification_and_explicit_ack(monkeypatch):
    client, runtime, _snap, _binding, base, event = _client(monkeypatch)
    calls = []
    def call(_mount, operation, arguments, **_kwargs):
        calls.append(operation)
        if operation == 'account.facts.read_after':
            return {'status': 'OK', 'page': {**base, 'events': [event], 'has_more': False}}
        return {'status': 'OK', 'progress': {**base, 'acknowledged_sequence': arguments['sequence']}}
    monkeypatch.setattr(runtime, 'call', call)
    result = client.read_after()
    assert result.outcome == 'OBSERVED'
    item, = result.page.events
    assert item.disposition == 'UNATTRIBUTED' and item.strategy_scope is None and item.phase == 'BOOTSTRAP'
    assert item.observation.trade_quantity == '1'
    assert 'secret-provider-source' not in repr(item) and 'secret-native-id' not in repr(item)
    assert calls == ['account.facts.read_after']
    assert client.acknowledge(1, expected_epoch='a' * 64).outcome == 'OBSERVED'
    assert calls == ['account.facts.read_after', 'account.facts.ack']
    with pytest.raises(ValueError):
        client.read_after(after_sequence=1)


@pytest.mark.parametrize('mutation', ['gap', 'invented_owner', 'wrong_epoch', 'missing_bootstrap', 'wrong_cursor'])
def test_invalid_or_incomplete_page_cannot_become_empty_success(monkeypatch, mutation):
    client, runtime, _snap, _binding, base, event = _client(monkeypatch)
    if mutation == 'gap':
        event['sequence'] = 2
        base['offered_sequence'] = 2
    elif mutation == 'invented_owner':
        event['strategy_scope'] = 'some-strategy'
    elif mutation == 'wrong_epoch':
        base['epoch'] = 'b' * 64
    elif mutation == 'wrong_cursor':
        base['consumer_ref'] = 'account-observer:' + '0' * 64
    events = [] if mutation == 'missing_bootstrap' else [event]
    monkeypatch.setattr(runtime, 'call', lambda *_a, **_k: {
        'status': 'OK', 'page': {**base, 'events': events, 'has_more': False}})
    result = client.read_after(expected_epoch='a' * 64)
    assert result.outcome == 'UNKNOWN'


def test_missing_facts_grant_never_calls_transport(monkeypatch):
    client, runtime, snap, binding, _base, _event = _client(monkeypatch)
    monkeypatch.setattr(runtime, 'status', lambda: replace(snap, bindings=(replace(binding, operations=frozenset()),)))
    monkeypatch.setattr(runtime, 'call', lambda *_a, **_k: pytest.fail('unauthorized RPC'))
    assert client.read_after().outcome == 'TRANSPORT_NOT_STARTED'


def test_fact_status_is_explicitly_unavailable_or_a_local_observation(monkeypatch):
    client, runtime, snap, binding, _base, _event = _client(monkeypatch)
    monkeypatch.setattr(runtime, 'status', lambda: replace(
        snap, bindings=(replace(binding, operations=binding.operations | {'account.facts.status'}),)))
    status = {'status': 'UNAVAILABLE', 'unattributed': None, 'conflict': None, 'verified_external': None}
    monkeypatch.setattr(runtime, 'call', lambda *_a, **_k: {'status': 'OK', 'fact_status': status})
    value = client.status()
    assert value.outcome == 'OBSERVED' and value.fact_status.status == 'UNAVAILABLE'
    status = {'status': 'NO_UNRESOLVED_OBSERVED', 'unattributed': 0, 'conflict': 0, 'verified_external': 2}
    value = client.status()
    assert value.outcome == 'OBSERVED' and value.fact_status.verified_external == 2
    status = {'status': 'NO_UNRESOLVED_OBSERVED', 'unattributed': 1, 'conflict': 0, 'verified_external': 0}
    assert client.status().outcome == 'UNKNOWN'


def test_ctp_profile_requires_explicit_v18_upgrade_without_parent_service(tmp_path, monkeypatch):
    from dataclasses import replace
    import zmq
    from bt_api_py.ctp_gateway_trading import build_ctp_gateway_trading_profile, CtpGatewayReturnSettings
    from bt_api_py.service_components import ServiceResourceConstructionError
    from bt_api_py.gateway_account_facts import ACCOUNT_FACTS_OPERATIONS
    from bt_api_py.gateway_direct_service import GatewayControlGrant
    from bt_api_py.service_config import parse_service_config
    from bt_api_py.service_registry import PrincipalGrant, ResourceGrant
    from tests.test_iter63_ctp_gateway_trading import CLIENT_A, FakeReturnMaintenance, _read_profile
    import bt_api_py.ctp_gateway_return_maintenance as return_module
    import bt_api_py.gateway_instrument_service as instrument_module

    _, settings, config, authority, grants, read = _read_profile(tmp_path, monkeypatch)
    class Returns(FakeReturnMaintenance):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._ledger = self.ledger
    monkeypatch.setattr(return_module, 'CtpGatewayReturnMaintenance', Returns)
    class InstrumentStub:
        def __init__(self, owner, session, journal, read_service, *args):
            self.owner, self.journal, self.read_service = owner, journal, read_service
        def matches(self, binding, read_service):
            return (binding._owner is self.owner and binding._journal is self.journal
                    and read_service is self.read_service)
        def begin_close(self):
            pass
        def maintain_registrations(self, contexts):
            pass
        def cleanup_on_owner(self):
            return True
    monkeypatch.setattr(instrument_module, 'GatewayInstrumentService', InstrumentStub)
    raw = config.to_public_dict()
    opts = raw['components']['gateway']['settings']['options']
    opts.update(control_enabled=True, strategy_ledger_enabled=True, account_extensions_enabled=True,
                account_facts_financial_enabled=True, cancel_enabled=True, strategy_origin_enabled=True,
                control_cancel_enabled=True, account_fact_recovery_enabled=True)
    config = parse_service_config(raw)
    observer = WireIdentity(WireRole.APPLICATION_CLIENT, 'observer', 'actor')
    scope = WireScope(authority.scope.execution_domain_ref, account_ref=settings.account_scope)
    control_ops = frozenset({'direct.control.status'})
    direct = ResourceGrant('gateway-a', 'service.direct_write', config.scope_ref, scope, control_ops)
    read_grant = ResourceGrant('gateway-a', 'service.account_read', config.scope_ref, scope,
                               ACCOUNT_FACTS_OPERATIONS | {'account.strategy_net_comparison'})
    public, _ = zmq.curve_keypair()
    authority = replace(authority, principals={**authority.principals, public: 'observer'},
        grants={**authority.grants, 'observer': (PrincipalGrant(observer, (direct, read_grant)),)})
    kwargs = dict(profile_ref='facts-v18', settings=settings, grants=grants,
        request_feed_factory=lambda: None, market_stream_factory=lambda *_: None, assert_admitted=lambda _: True,
        return_settings=CtpGatewayReturnSettings(read.observation_instruments), account_read_settings=read,
        account_read_principals=frozenset({CLIENT_A.principal_ref, 'observer'}),
        control_grants={'observer': GatewayControlGrant('actor', True, frozenset(), control_ops)},
        upgrade_controls_from_v9=True, upgrade_strategy_ledger_from_v10=True,
        upgrade_account_extensions_from_v11=True)
    with pytest.raises(ServiceResourceConstructionError, match='RESOURCE_CONSTRUCTION_FAILED') as failure:
        # Neither undocumented implicit upgrade nor enabling a parent service can be assumed.
        deployment = build_ctp_gateway_trading_profile(config, authority, tmp_path/'registry-a', **kwargs).deployment_factory()
        resources = deployment._provide_resources('gateway', 'gateway-a', opts, authority.scope)
        resources.close()
    failure.value.resources.close()
    deployment = build_ctp_gateway_trading_profile(config, authority, tmp_path/'registry-b', **kwargs,
        upgrade_parent_authority_from_v12=True, upgrade_account_facts_financial_from_v13=True,
        upgrade_actor_stream_from_v14=True, upgrade_strategy_origin_from_v15=True,
        upgrade_control_cancel_from_v16=True, upgrade_account_fact_recovery_from_v17=True).deployment_factory()
    resources = deployment._provide_resources('gateway', 'gateway-a', opts, authority.scope)
    try:
        facts = resources.values['account_facts']
        assert facts.principals == frozenset({'observer'})
        assert facts.owner.is_bound_to(facts.journal) and facts.journal._schema_version == 18
        assert 'parent_service' not in resources.values
    finally:
        resources.close()


def test_parent_child_and_origin_are_explicit_attributed_facts_only(monkeypatch):
    client, runtime, _snap, _binding, base, event = _client(monkeypatch)
    event.update(disposition='ATTRIBUTED', strategy_scope='strategy', execution_sequence=15,
                 reservation_id='reservation', origin='execution:execution-principal',
                 parent_ref='parent', child_id='child')
    monkeypatch.setattr(runtime, 'call', lambda *_a, **_k: {
        'status': 'OK', 'page': {**base, 'events': [event], 'has_more': False}})
    item, = client.read_after().page.events
    assert (item.reservation_id, item.origin, item.parent_ref, item.child_id) == (
        'reservation', 'execution:execution-principal', 'parent', 'child')
    # An unassociated fact must not acquire an owner or a source from the reader.
    event.update(disposition='UNATTRIBUTED', strategy_scope=None, execution_sequence=None)
    assert client.read_after().outcome == 'UNKNOWN'
    event.update(disposition='ATTRIBUTED', strategy_scope='strategy', execution_sequence=15, child_id=None)
    assert client.read_after().outcome == 'UNKNOWN'


def test_self_origin_requires_attributed_reservation_without_parent(monkeypatch):
    client, runtime, _snap, _binding, base, event = _client(monkeypatch)
    event.update(disposition='ATTRIBUTED', strategy_scope='strategy', execution_sequence=15,
                 reservation_id='reservation', origin='self')
    monkeypatch.setattr(runtime, 'call', lambda *_a, **_k: {
        'status': 'OK', 'page': {**base, 'events': [event], 'has_more': False}})
    result = client.read_after()
    assert result.outcome == 'OBSERVED'
    item, = result.page.events
    assert (item.reservation_id, item.origin, item.parent_ref, item.child_id) == (
        'reservation', 'self', None, None)
    event['reservation_id'] = None
    assert client.read_after().outcome == 'UNKNOWN'
    event.update(reservation_id='reservation', parent_ref='parent', child_id='child')
    assert client.read_after().outcome == 'UNKNOWN'
    event.update(parent_ref=None, child_id=None, disposition='UNATTRIBUTED', strategy_scope=None,
                 execution_sequence=None)
    assert client.read_after().outcome == 'UNKNOWN'
    event.update(disposition='ATTRIBUTED', strategy_scope='strategy', execution_sequence=15,
                 reservation_id='reservation', origin=None)
    assert client.read_after().outcome == 'UNKNOWN'


def test_snapshot_pages_are_distinct_and_never_implicitly_confirmed(monkeypatch):
    client, runtime, snap, binding, base, event = _client(monkeypatch)
    operations = binding.operations | {'account.facts.snapshot_info', 'account.facts.snapshot_read',
                                       'account.facts.snapshot_confirm'}
    monkeypatch.setattr(runtime, 'status', lambda: replace(snap, bindings=(replace(binding, operations=operations),)))
    snapshot_id, digest = 'd'*64, 'e'*64
    metadata = {'schema_version': 1, 'account_scope': base['account_scope'], 'consumer_ref': base['consumer_ref'],
                'epoch': base['epoch'], 'high_sequence': 2, 'retained_floor': 2, 'snapshot_id': snapshot_id,
                'snapshot_digest': digest, 'snapshot_count': 1}
    snapshot = {'schema_version': 1, 'account_scope': base['account_scope'], 'consumer_ref': base['consumer_ref'],
                'epoch': base['epoch'], 'snapshot_id': snapshot_id, 'snapshot_digest': digest,
                'watermark': 2, 'total': 1, 'after_ordinal': 0, 'events': [event], 'has_more': False}
    calls = []
    def call(_mount, operation, _arguments, **_kwargs):
        calls.append(operation)
        return ({'status': 'OK', 'snapshot': snapshot} if operation == 'account.facts.snapshot_read'
                else {'status': 'OK', 'snapshot_info': metadata})
    monkeypatch.setattr(runtime, 'call', call)
    assert client.snapshot_info().snapshot_info.snapshot_id == snapshot_id
    result = client.read_snapshot(snapshot_id)
    assert result.outcome == 'OBSERVED' and result.snapshot.events[0].fact_digest == event['fact_digest']
    assert calls == ['account.facts.snapshot_info', 'account.facts.snapshot_read']
    assert client.confirm_snapshot(snapshot_id, digest).snapshot_info.retained_floor == 2
    snapshot['after_ordinal'] = 1
    assert client.read_snapshot(snapshot_id).outcome == 'UNKNOWN'
    snapshot['after_ordinal'] = 0
    snapshot['has_more'] = True
    assert client.read_snapshot(snapshot_id).outcome == 'UNKNOWN'
