"""Actor cursor reply validation uses no remote authority shortcuts."""
from dataclasses import replace

import pytest
from bt_api_base.direct_write_protocol import DirectExecutionEventKind, DirectOrderStatus, NativeOrderIdentity
from bt_api_base.gateway_execution_protocol import GatewayReturnFact
from bt_api_base.gateway_execution_stream_protocol import GatewayExecutionStreamEvent, project_gateway_execution_observation
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.gateway_delegate_actor_stream_client import GatewayDelegateActorStreamClient
from bt_api_py.gateway_delegate_actor_stream_service import ACTOR_STREAM_OPERATIONS, actor_stream_consumer
from bt_api_py.gateway_delegate_service_client import GatewayDelegateRoute
from bt_api_py.service_runtime_thread import BindingSnapshot, RuntimeSnapshot, ThreadedServiceClientRuntime


def test_actor_client_validates_scope_grant_sequence_and_never_autoacks(monkeypatch):
    identity = WireIdentity(WireRole.SERVICE, 'principal', 'actor')
    scope = WireScope('domain', account_ref='account', strategy_ref='strategy', run_id='run')
    route = GatewayDelegateRoute('upstream.direct_write', 'gateway', 'instance', 'scope', scope, identity)
    runtime = ThreadedServiceClientRuntime(lambda: None)
    binding = BindingSnapshot('registration', 'epoch', True, route.mount, route.service_id,
                              route.instance_id, route.scope_ref, scope, ACTOR_STREAM_OPERATIONS, True)
    snap = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0, identity, scope.run_id)
    monkeypatch.setattr(runtime, 'status', lambda: snap)
    grant = type('Grant', (), {'revision': 'v1'})()
    consumer = actor_stream_consumer(identity, scope, grant)
    key = MarketInstrumentKey('ctp', 'SHFE', 'simnow', 'future', 'rb', 'rb')
    fact = GatewayReturnFact('account', 'CTP', key, '20300101', DirectExecutionEventKind.ORDER,
        'source', identity=NativeOrderIdentity('CTP', 'key', {'trading_day': '20300101'}, 1),
        identity_keys=('anchor',), binding_keys=('anchor',), status=DirectOrderStatus.ACCEPTED)
    event = GatewayExecutionStreamEvent(3, 'request', 'reservation', 'b'*64, 'principal',
        fact.fact_digest(), project_gateway_execution_observation(fact)).to_mapping()
    event.update(origin='risk:principal', on_behalf_of='strategy', parent_ref=None, child_id=None)
    calls, bad = [], []
    def call(_mount, operation, args, **_kwargs):
        calls.append(operation)
        progress = {'schema_version': 1, 'account_scope': 'account', 'strategy_scope': 'strategy',
                    'actor_principal': 'principal', 'consumer_ref': consumer, 'epoch': 'a'*64,
                    'offered_sequence': 3, 'acknowledged_sequence': 0}
        if operation.endswith('read_after'):
            item = dict(event, **(bad[0] if bad else {}))
            return {'status': 'OK', 'page': {**progress, 'events': [item], 'has_more': False}}
        if operation.endswith('.ack'):
            progress['acknowledged_sequence'] = args['sequence']
        return {'status': 'OK', 'progress': progress}
    monkeypatch.setattr(runtime, 'call', call)
    client = GatewayDelegateActorStreamClient(runtime, route=route, grant_revision='v1')
    observed = client.read_after()
    assert observed.outcome == 'OBSERVED' and observed.events[0].event.sequence == 3
    assert calls == ['direct.delegate_events.read_after']
    assert client.acknowledge(3, expected_epoch='a'*64).outcome == 'OBSERVED'
    bad[:] = [{'origin': 'execution:principal', 'parent_ref': 'parent', 'child_id': 'child'}]
    observed = client.read_after()
    assert observed.outcome == 'OBSERVED'
    assert (observed.events[0].parent_ref, observed.events[0].child_id) == ('parent', 'child')
    for invalid in ({'on_behalf_of': 'other'}, {'origin': 'risk:other'}, {'parent_ref': 'parent'},
                    {'command_principal': 'other'}, {'sequence': 0}, {'origin': 'execution:principal'}):
        bad[:] = [invalid]
        assert client.read_after().outcome == 'UNKNOWN'
    monkeypatch.setattr(runtime, 'status', lambda: replace(snap, bindings=(replace(binding, operations=frozenset()),)))
    assert client.progress().outcome == 'TRANSPORT_NOT_STARTED'
    with pytest.raises(ValueError):
        client.read_after(after_sequence=1)
