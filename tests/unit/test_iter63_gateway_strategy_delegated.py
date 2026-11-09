"""No network: strategy delegated status retains and validates the original actor."""
from dataclasses import replace

from tests.unit.test_iter63_gateway_strategy_position import ROUTE, setup
from tests.unit.test_iter63_gateway_strategy_client import parts
from bt_api_py.gateway_api import GatewayBtApi
from bt_api_py.gateway_strategy_client import GatewayStrategyClientSession
from bt_api_py.service_runtime_thread import BindingSnapshot, RuntimeSnapshot


OP = "direct.delegated_command_status"


def response():
    return {"status": "OBSERVED", "schema_version": 1, "account_scope": "account",
            "strategy_scope": "strategy", "actor_principal": "risk-principal", "request_key": "order-1",
            "reservation_id": "reservation", "digest": "a" * 64, "state": "UNKNOWN", "version": 3,
            "owner_generation": "owner", "reservation_generation": "original-owner"}


def test_status_retains_actor_and_scopes_request(monkeypatch):
    session, api, _, _ = setup(monkeypatch, frozenset({OP}))
    calls = []
    def call(*args, **kwargs):
        calls.append((args, kwargs))
        return response()
    monkeypatch.setattr(session.runtime, "call", call)
    result = api.delegated_command_status(ROUTE, "order-1")
    assert result.outcome == "OBSERVED" and result.observation.actor_principal == "risk-principal"
    assert result.observation.state == "UNKNOWN"
    assert calls[0][0][1:] == (OP, {"request_key": "order-1"})
    assert calls[0][1]["account_ref"] == "account"


def test_status_rejects_wrong_identity_changed_registration_and_missing_grant(monkeypatch):
    session, api, registration, snapshot = setup(monkeypatch, frozenset({OP}))
    for changes in ({"strategy_scope": "other"}, {"account_scope": "other"},
                    {"actor_principal": ""}, {"request_key": "other"},
                    {"digest": "bad"}, {"state": "SENT", "version": 1}, {"schema_version": True}):
        monkeypatch.setattr(session.runtime, "call", lambda *_a, _m=changes, **_k: {**response(), **_m})
        assert api.delegated_command_status(ROUTE, "order-1").outcome == "UNAVAILABLE"
    calls = []
    monkeypatch.setattr(session.runtime, "status", lambda: snapshot if not calls else replace(
        snapshot, bindings=(replace(registration, registration_id="different"),)))
    def call(*_a, **_k):
        calls.append(True)
        return response()
    monkeypatch.setattr(session.runtime, "call", call)
    assert api.delegated_command_status(ROUTE, "order-1").outcome == "UNAVAILABLE"
    monkeypatch.setattr(session.runtime, "status", lambda: replace(snapshot, bindings=(
        replace(registration, operations=frozenset()),)))
    assert api.delegated_command_status(ROUTE, "order-1").outcome == "UNAVAILABLE"


class Host:
    def __init__(self):
        self.plans, self.observations = {}, {}
    def save_delegated_cancel_plan(self, plan):
        self.plans[plan.operation_id] = plan
        return plan
    def load_delegated_cancel_plan(self, operation):
        return self.plans.get(operation)
    def save_delegated_cancel_observation(self, operation, observation):
        self.observations[operation] = observation
        return observation
    def load_delegated_cancel_observation(self, operation):
        return self.observations.get(operation)


def test_cancel_persists_before_send_and_unknown_only_uses_status(monkeypatch):
    host = Host()
    kwargs = parts()
    kwargs['requested_operations'] = frozenset({OP})
    session = GatewayStrategyClientSession(**kwargs, delegated_cancel_host=host)
    api = GatewayBtApi(session, exchange_name=ROUTE)
    route = session.route
    operations = frozenset({OP, 'direct.delegated_cancel', 'direct.delegated_cancel_status'})
    binding = BindingSnapshot('registration', 'service-epoch', True, route.mount, route.service_id,
                              route.instance_id, route.scope_ref, route.scope, operations, True)
    snapshot = RuntimeSnapshot('running', True, True, None, (binding,), 0, 0, route.identity, route.scope.run_id)
    monkeypatch.setattr(session.runtime, 'status', lambda: snapshot)
    calls = []
    def rpc(_mount, operation, _arguments, **_kwargs):
        calls.append(operation)
        if operation == OP:
            return response()
        assert host.plans['cancel-1'].target_actor_principal == 'risk-principal'
        if operation == 'direct.delegated_cancel':
            raise TimeoutError('lost reply')
        return {'status': 'OK', 'record': {
            'account_scope': 'account', 'strategy_scope': 'strategy', 'requester_principal': 'principal',
            'target_actor_principal': 'risk-principal', 'request_key': 'order-1', 'operation_id': 'cancel-1',
            'attempt_id': 'attempt', 'target_reservation_id': 'reservation', 'target_digest': 'a' * 64,
            'state': 'UNKNOWN', 'version': 3, 'owner_generation': 'owner', 'attempt_generation': 'owner'}}
    monkeypatch.setattr(session.runtime, 'call', rpc)
    observed = api.delegated_command_status(ROUTE, 'order-1').observation
    assert api.cancel_delegated_order(ROUTE, observed, operation_id='cancel-1').outcome == 'UNKNOWN'
    recovered = api.cancel_delegated_order(ROUTE, observed, operation_id='cancel-1')
    assert recovered.outcome == 'OBSERVED' and recovered.observation.state == 'UNKNOWN'
    assert recovered.observation.requester_principal == 'principal'
    assert calls == [OP, 'direct.delegated_cancel', 'direct.delegated_cancel_status']
