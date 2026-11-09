"""Typed Gateway delegate host over an explicitly account-fenced Risk journal."""
from __future__ import annotations

import json
from dataclasses import asdict

from bt_api_base.gateway_delegation_protocol import GatewayDelegatedCommand
from bt_api_base.gateway_direct_protocol import canonical_gateway_payload, gateway_payload_digest
from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.gateway_delegate_service_client import (GatewayDelegateActorCancelIntent, GatewayDelegateActorReleaseIntent, GatewayDelegateIntent,
    GatewayDelegateObservation, GatewayDelegateRoute)
from bt_api_py.gateway_delegated_cancel import GatewayDelegatedCancelObservation
from bt_api_py.gateway_order_contract import decode_gateway_order_request


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _route_value(route):
    scope, identity = route.scope, route.identity
    return {'mount': route.mount, 'service_id': route.service_id, 'instance_id': route.instance_id,
            'scope_ref': route.scope_ref, 'scope': asdict(scope),
            'identity': {'role': identity.role.value, 'principal_ref': identity.principal_ref,
                         'actor_ref': identity.actor_ref, 'delegate_ref': identity.delegate_ref}}


def _route_from(value):
    if type(value) is not dict or set(value) != {'mount', 'service_id', 'instance_id', 'scope_ref', 'scope', 'identity'}:
        raise ValueError('INVALID_DELEGATE_ROUTE')
    scope, identity = dict(value['scope']), dict(value['identity'])
    scope['account_refs'] = tuple(scope['account_refs'])
    identity['role'] = WireRole(identity['role'])
    return GatewayDelegateRoute(value['mount'], value['service_id'], value['instance_id'], value['scope_ref'],
                                WireScope(**scope), WireIdentity(**identity))


class GatewayRiskDelegateHost:
    """Store immutable intents and markers on the original Risk account fence."""
    def __init__(self, storage, *, route: GatewayDelegateRoute):
        if (type(route) is not GatewayDelegateRoute or not callable(getattr(storage, 'save', None))
                or not callable(getattr(storage, 'load', None))):
            raise ValueError('INVALID_RISK_DELEGATE_HOST')
        self._storage, self._route = storage, route
        self._scope = storage.account_scope
        if self._scope.account_id != route.scope.account_ref:
            raise ValueError('ACCOUNT_SCOPE_MISMATCH')

    def _check(self):
        if self._storage.account_scope != self._scope or self._scope.account_id != self._route.scope.account_ref:
            raise ValueError('ACCOUNT_SCOPE_MISMATCH')

    def _intent(self, intent, key):
        self._check()
        if type(intent) is not GatewayDelegateIntent or intent.route != self._route:
            raise ValueError('INVALID_INTENT')
        cmd, route = intent.command, self._route
        if (type(cmd) is not GatewayDelegatedCommand or cmd.request_key != key
                or cmd.actor_kind != 'risk'
                or cmd.account_scope != route.scope.account_ref or cmd.strategy_scope != route.scope.strategy_ref
                or cmd.actor_principal != route.identity.principal_ref or cmd.actor_ref != route.identity.actor_ref
                or cmd.execution_domain_ref != route.scope.execution_domain_ref or cmd.run_id != route.scope.run_id
                or cmd.instrument.provider != self._scope.provider
                or type(intent.ttl_seconds) not in (int, float) or not 0 < intent.ttl_seconds <= 30):
            raise ValueError('INVALID_INTENT')
        payload = json.loads(intent.canonical_payload)
        request = decode_gateway_order_request(payload, cmd.account_scope)
        if (canonical_gateway_payload(payload) != intent.canonical_payload or gateway_payload_digest(payload) != cmd.payload_digest
                or request.idempotency_key != key or request.symbol != cmd.instrument.native_symbol
                or request.exchange_id != cmd.instrument.venue):
            raise ValueError('INVALID_INTENT')
        return intent

    def save_delegate_intent(self, intent):
        self._intent(intent, intent.command.request_key)
        data = {'schema_version': 1, 'route': _route_value(intent.route), 'command': intent.command.to_mapping(),
                'canonical_payload': intent.canonical_payload, 'ttl_seconds': intent.ttl_seconds}
        self._storage.save('intent', intent.command.request_key, _json(data))
        return self.load_delegate_intent(intent.command.request_key)

    def load_delegate_intent(self, request_key):
        self._check()
        raw = self._storage.load('intent', request_key)
        if raw is None:
            return None
        value = json.loads(raw)
        if (type(value) is not dict or set(value) != {'schema_version', 'route', 'command', 'canonical_payload', 'ttl_seconds'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1):
            raise ValueError('INVALID_INTENT')
        result = GatewayDelegateIntent(_route_from(value['route']), GatewayDelegatedCommand.from_mapping(value['command']),
                                       value['canonical_payload'], value['ttl_seconds'])
        return self._intent(result, request_key)

    def _observation(self, request_key, observation):
        intent = self.load_delegate_intent(request_key)
        if intent is None or type(observation) is not GatewayDelegateObservation:
            raise ValueError('INVALID_OBSERVATION')
        cmd = intent.command
        if (observation.account_scope != cmd.account_scope or observation.strategy_scope != cmd.strategy_scope
                or observation.actor_principal != cmd.actor_principal or observation.request_key != request_key
                or observation.digest != cmd.payload_digest or type(observation.version) is not int
                or type(observation.state) is not str
                or {'RESERVED': 1, 'DISPATCHING': 2, 'SENT': 3, 'NOT_SENT': 3, 'UNKNOWN': 3,
                    'EXPIRED': 2, 'RELEASED': 2}.get(observation.state) != observation.version
                or any(type(v) is not str or not v or v != v.strip() for v in
                       (observation.reservation_id, observation.owner_generation, observation.reservation_generation))):
            raise ValueError('INVALID_OBSERVATION')
        return observation

    def save_delegate_observation(self, request_key, observation):
        self._observation(request_key, observation)
        prior = self.load_delegate_observation(request_key)
        if prior is not None and (prior.reservation_id != observation.reservation_id
                or prior.reservation_generation != observation.reservation_generation or prior.version > observation.version
                or (prior.version == observation.version and prior.state != observation.state)
                or (prior.version < observation.version and observation.state not in {
                    'RESERVED': {'DISPATCHING', 'SENT', 'NOT_SENT', 'UNKNOWN', 'RELEASED', 'EXPIRED'},
                    'DISPATCHING': {'SENT', 'NOT_SENT', 'UNKNOWN'},
                    'SENT': set(), 'NOT_SENT': set(), 'UNKNOWN': set(), 'RELEASED': set(), 'EXPIRED': set(),
                }[prior.state])):
            raise ValueError('OBSERVATION_CONFLICT')
        self._storage.save('observation', request_key, _json(asdict(observation)), version=observation.version)
        return self.load_delegate_observation(request_key)

    def load_delegate_observation(self, request_key):
        self._check()
        raw = self._storage.load('observation', request_key)
        if raw is None:
            return None
        return self._observation(request_key, GatewayDelegateObservation(**json.loads(raw)))

    def save_delegate_dispatch_marker(self, request_key, marker):
        intent = self.load_delegate_intent(request_key)
        observation = self.load_delegate_observation(request_key)
        if (intent is None or observation is None or observation.state != 'RESERVED'
                or marker != (observation.reservation_id, observation.digest, observation.version, intent.command.digest)):
            raise ValueError('INVALID_DISPATCH_MARKER')
        self._storage.save('dispatch_marker', request_key, _json(list(marker)))
        return self.load_delegate_dispatch_marker(request_key)

    def load_delegate_dispatch_marker(self, request_key):
        self._check()
        raw = self._storage.load('dispatch_marker', request_key)
        if raw is None:
            return None
        marker = json.loads(raw)
        intent = self.load_delegate_intent(request_key)
        observation = self.load_delegate_observation(request_key)
        if (type(marker) is not list or len(marker) != 4 or intent is None or observation is None
                or marker != [observation.reservation_id, intent.command.payload_digest, 1, intent.command.digest]):
            raise ValueError('INVALID_DISPATCH_MARKER')
        return tuple(marker)

    def _cancel_intent(self, plan, operation_id):
        if type(plan) is not GatewayDelegateActorCancelIntent or plan.operation_id != operation_id:
            raise ValueError('INVALID_CANCEL_INTENT')
        intent = self.load_delegate_intent(plan.request_key)
        observation = self.load_delegate_observation(plan.request_key)
        if (intent is None or observation is None or intent.command.digest != plan.command_digest
                or intent.command.actor_principal != plan.actor_principal or intent.command.payload_digest != plan.payload_digest
                or observation.reservation_id != plan.reservation_id or type(plan.operation_id) is not str
                or not plan.operation_id or plan.operation_id != plan.operation_id.strip()
                or type(plan.native_identity_digest) is not str or len(plan.native_identity_digest) != 64
                or any(c not in '0123456789abcdef' for c in plan.native_identity_digest)):
            raise ValueError('INVALID_CANCEL_INTENT')
        return plan

    def save_delegate_cancel_intent(self, plan):
        self._cancel_intent(plan, plan.operation_id)
        self._storage.save('cancel_intent', plan.operation_id, _json(asdict(plan)))
        return self.load_delegate_cancel_intent(plan.operation_id)

    def load_delegate_cancel_intent(self, operation_id):
        self._check()
        raw = self._storage.load('cancel_intent', operation_id)
        if raw is None:
            return None
        return self._cancel_intent(GatewayDelegateActorCancelIntent(**json.loads(raw)), operation_id)

    def _cancel_observation(self, operation_id, value):
        plan = self.load_delegate_cancel_intent(operation_id)
        if plan is None or type(value) is not GatewayDelegatedCancelObservation:
            raise ValueError('INVALID_CANCEL_OBSERVATION')
        intent = self.load_delegate_intent(plan.request_key)
        if (value.account_scope != intent.command.account_scope or value.strategy_scope != intent.command.strategy_scope
                or value.requester_principal != plan.actor_principal or value.target_actor_principal != plan.actor_principal
                or value.request_key != plan.request_key or value.operation_id != operation_id
                or value.target_reservation_id != plan.reservation_id or value.target_digest != plan.payload_digest
                or type(value.version) is not int or type(value.state) is not str
                or {'REQUESTED': 1, 'DISPATCHING': 2, 'SENT': 3, 'NOT_SENT': 3, 'UNKNOWN': 3}.get(value.state) != value.version
                or any(type(v) is not str or not v for v in (value.attempt_id, value.owner_generation, value.attempt_generation))):
            raise ValueError('INVALID_CANCEL_OBSERVATION')
        return value

    def save_delegate_cancel_observation(self, operation_id, value):
        self._cancel_observation(operation_id, value)
        prior = self.load_delegate_cancel_observation(operation_id)
        if prior is not None and (prior.attempt_id != value.attempt_id or prior.attempt_generation != value.attempt_generation
                or prior.version > value.version or (prior.version == value.version and prior.state != value.state)
                or (prior.version < value.version and value.state not in {
                    'REQUESTED': {'DISPATCHING', 'SENT', 'NOT_SENT', 'UNKNOWN'},
                    'DISPATCHING': {'SENT', 'NOT_SENT', 'UNKNOWN'},
                    'SENT': set(), 'NOT_SENT': set(), 'UNKNOWN': set(),
                }[prior.state])):
            raise ValueError('CANCEL_OBSERVATION_CONFLICT')
        self._storage.save('cancel_observation', operation_id, _json(asdict(value)), version=value.version)
        return self.load_delegate_cancel_observation(operation_id)

    def load_delegate_cancel_observation(self, operation_id):
        self._check()
        raw = self._storage.load('cancel_observation', operation_id)
        if raw is None:
            return None
        return self._cancel_observation(operation_id, GatewayDelegatedCancelObservation(**json.loads(raw)))

    def save_delegate_cancel_marker(self, operation_id, marker):
        plan = self.load_delegate_cancel_intent(operation_id)
        expected = None if plan is None else (plan.request_key, plan.reservation_id, plan.payload_digest,
                                              plan.command_digest, plan.native_identity_digest)
        if marker != expected:
            raise ValueError('INVALID_CANCEL_MARKER')
        self._storage.save('cancel_dispatch_marker', operation_id, _json(list(marker)))
        return self.load_delegate_cancel_marker(operation_id)

    def load_delegate_cancel_marker(self, operation_id):
        self._check()
        raw = self._storage.load('cancel_dispatch_marker', operation_id)
        if raw is None:
            return None
        plan = self.load_delegate_cancel_intent(operation_id)
        value = json.loads(raw)
        if plan is None or value != [plan.request_key, plan.reservation_id, plan.payload_digest,
                                     plan.command_digest, plan.native_identity_digest]:
            raise ValueError('INVALID_CANCEL_MARKER')
        return tuple(value)

    def _release_intent(self, plan, operation_id):
        if type(plan) is not GatewayDelegateActorReleaseIntent or plan.operation_id != operation_id:
            raise ValueError('INVALID_RELEASE_INTENT')
        intent = self.load_delegate_intent(plan.request_key)
        observation = self.load_delegate_observation(plan.request_key)
        if (intent is None or observation is None or intent.command.digest != plan.command_digest
                or intent.command.actor_principal != plan.actor_principal or intent.command.payload_digest != plan.payload_digest
                or observation.reservation_id != plan.reservation_id or type(plan.expected_version) is not int
                or plan.expected_version != 1 or type(operation_id) is not str or not operation_id
                or operation_id != operation_id.strip()):
            raise ValueError('INVALID_RELEASE_INTENT')
        return plan

    def save_delegate_release_intent(self, plan):
        self._release_intent(plan, plan.operation_id)
        self._storage.save('release_intent', plan.operation_id, _json(asdict(plan)))
        return self.load_delegate_release_intent(plan.operation_id)

    def load_delegate_release_intent(self, operation_id):
        self._check()
        raw = self._storage.load('release_intent', operation_id)
        if raw is None:
            return None
        return self._release_intent(GatewayDelegateActorReleaseIntent(**json.loads(raw)), operation_id)

    def save_delegate_release_marker(self, operation_id, marker):
        plan = self.load_delegate_release_intent(operation_id)
        expected = None if plan is None else (plan.request_key, plan.reservation_id, plan.payload_digest,
                                              plan.command_digest, plan.expected_version)
        if marker != expected:
            raise ValueError('INVALID_RELEASE_MARKER')
        self._storage.save('release_marker', operation_id, _json(list(marker)))
        return self.load_delegate_release_marker(operation_id)

    def load_delegate_release_marker(self, operation_id):
        self._check()
        raw = self._storage.load('release_marker', operation_id)
        if raw is None:
            return None
        plan = self.load_delegate_release_intent(operation_id)
        value = json.loads(raw)
        if plan is None or value != [plan.request_key, plan.reservation_id, plan.payload_digest,
                                     plan.command_digest, plan.expected_version]:
            raise ValueError('INVALID_RELEASE_MARKER')
        return tuple(value)
