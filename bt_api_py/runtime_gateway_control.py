"""Explicit Runtime-to-Gateway control transport composition.

The inputs are trusted deployment declarations, not grants. Gateway's registry
and its separately installed runtime delegation remain the authority. This module
does not load credentials, create proofs, retry calls, or fall back to direct.
"""
from __future__ import annotations

import math
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope
from bt_api_py.service_config import ClientConfig
from bt_api_py.service_runtime import ClientCurveKeys, ServiceClientRuntime, ZmqTransportFactory
from bt_api_py.service_runtime_thread import BoundCallRoute, ThreadedServiceClientRuntime

_OPERATIONS = frozenset({'direct.control.apply', 'direct.control.status', 'direct.control.resume'})
_FIELDS = {
    'direct.control.apply': frozenset({'cause_id', 'mode'}),
    'direct.control.status': frozenset({'cause_id'}),
    'direct.control.resume': frozenset({'cause_id', 'expected_version', 'operation_id'}),
}


def _ref(value):
    return type(value) is str and 0 < len(value) <= 128 and value == value.strip() and value.isprintable()


@dataclass(frozen=True)
class RuntimeGatewayControlRoute:
    service_id: str
    instance_id: str
    scope_ref: str
    scope: WireScope

    def __post_init__(self):
        scope = self.scope
        if (not all(_ref(v) for v in (self.service_id, self.instance_id, self.scope_ref))
                or type(scope) is not WireScope or not scope.account_ref or not scope.strategy_ref
                or scope.run_id is not None or scope.account_refs or scope.portfolio_ref is not None
                or scope.manual_book_ref is not None or scope.group_id is not None or scope.leg_id is not None):
            raise ValueError('INVALID_RUNTIME_GATEWAY_ROUTE')


@dataclass(frozen=True, repr=False)
class TrustedRuntimeControlBinding:
    """Externally installed declaration binding a proof to the exact deployment.

    The caller must obtain this independently of any Runtime request. Matching
    it locally does not prove that the server installed or still grants it.
    """
    route: RuntimeGatewayControlRoute
    client_identity: WireIdentity
    expected_gateway: WireIdentity
    client_config_fingerprint: str
    proof: object = field(repr=False)


class RuntimeGatewayControlBridge:
    """Own one authenticated client thread per strategy; calls are never retried."""

    def __init__(self, routes: Mapping[str, RuntimeGatewayControlRoute], *,
                 client_configs: Mapping[str, ClientConfig], client_identity: WireIdentity,
                 expected_gateway: WireIdentity, security_profiles: Mapping[str, ClientCurveKeys],
                 proofs: Mapping[str, TrustedRuntimeControlBinding], timeout_seconds: float = 5.0):
        # Runtime is optional: import only when this explicit integration is constructed.
        from bt_api_runtime.gateway_control import GatewayDelegationProof

        if (type(client_identity) is not WireIdentity or client_identity.role is not WireRole.APPLICATION_CLIENT
                or client_identity.delegate_ref is not None or type(expected_gateway) is not WireIdentity
                or expected_gateway.role is not WireRole.SERVICE or expected_gateway.delegate_ref is not None
                or type(timeout_seconds) not in (int, float) or isinstance(timeout_seconds, bool)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60
                or not isinstance(routes, Mapping) or not routes
                or not isinstance(client_configs, Mapping) or not isinstance(proofs, Mapping)
                or set(routes) != set(client_configs) or set(routes) != set(proofs)
                or not isinstance(security_profiles, Mapping)):
            raise ValueError('INVALID_RUNTIME_GATEWAY_CONFIG')
        self._entries = {}
        self._timeout = float(timeout_seconds)
        self._state = 'new'
        self._lifecycle_lock = threading.RLock()
        for strategy, route in routes.items():
            config, binding = client_configs[strategy], proofs[strategy]
            if (not _ref(strategy) or type(route) is not RuntimeGatewayControlRoute
                    or route.scope.strategy_ref != strategy or expected_gateway.actor_ref != route.service_id
                    or type(config) is not ClientConfig or type(binding) is not TrustedRuntimeControlBinding
                    or binding.route != route or binding.client_identity != client_identity
                    or binding.expected_gateway != expected_gateway
                    or binding.client_config_fingerprint != config.fingerprint()
                    or type(binding.proof) is not GatewayDelegationProof):
                raise ValueError('INVALID_RUNTIME_GATEWAY_BINDING')
            # Revalidate parsed input and exact one required registration; avoid
            # other mounts or another scope riding on this client session.
            if config.process_role != 'application_client' or config.registration.get('application_ref') != client_identity.actor_ref or set(config.clients) != {'gateway'}:
                raise ValueError('INVALID_RUNTIME_GATEWAY_CONFIG')
            bindings = config.clients['gateway']['settings']['bindings']
            if len(bindings) != 1:
                raise ValueError('INVALID_RUNTIME_GATEWAY_CONFIG')
            item = bindings[0]
            if (item['mount'] != 'application.direct_write' or item['required'] is not True
                    or item['service_id'] != route.service_id or item['instance_id'] != route.instance_id
                    or item['scope_ref'] != route.scope_ref or item.get('account_ref') != route.scope.account_ref
                    or item.get('portfolio_ref') is not None):
                raise ValueError('INVALID_RUNTIME_GATEWAY_CONFIG')
            profiles = config.communication['profiles']
            if set(profiles) != {item['transport_ref']}:
                raise ValueError('INVALID_RUNTIME_GATEWAY_CONFIG')
            profile = profiles[item['transport_ref']]
            key = security_profiles.get(profile['security_profile_ref'])
            if (profile['backend'] != 'zmq' or set(profile['endpoints']) != {'rpc_connect'}
                    or type(key) is not ClientCurveKeys):
                raise ValueError('INVALID_RUNTIME_GATEWAY_CONFIG')
            # Confirm CURVE keys without exposing them or attempting a connection.
            try:
                import zmq
                if (any(type(k) is not bytes or len(k) != 40 for k in (key.server_key, key.public_key, key.secret_key))
                        or zmq.curve_public(key.secret_key) != key.public_key):
                    raise ValueError
            except Exception:
                raise ValueError('INVALID_RUNTIME_GATEWAY_CONFIG') from None
            factory = ZmqTransportFactory({profile['security_profile_ref']: key})
            runtime = ThreadedServiceClientRuntime(
                lambda c=config, i=client_identity, s=route.scope, p=expected_gateway, f=factory:
                ServiceClientRuntime(c, identity=i, scope_resolver=lambda _: s, peer_resolver=lambda _: p,
                                     transport_factory=f, requested_operations={'application.direct_write': _OPERATIONS},
                                     require_unregister_ack=True),
                require_unregister_ack=True)
            bound = BoundCallRoute(client_identity, route.scope, route.scope_ref, route.service_id,
                                   route.instance_id, expected_gateway)
            self._entries[strategy] = (route, binding.proof, runtime, bound)
        self._entries = MappingProxyType(self._entries)

    @property
    def state(self):
        """Lifecycle state; ``close_uncertain`` requires a further cleanup attempt."""
        with self._lifecycle_lock:
            return self._state

    def start(self):
        with self._lifecycle_lock:
            if self._state != 'new':
                raise RuntimeError('RUNTIME_GATEWAY_LIFECYCLE_INVALID')
            self._state = 'starting'
            try:
                for _, proof, runtime, _ in self._entries.values():
                    if proof.revoked or time.time() >= proof.expires_at:
                        raise ValueError('RUNTIME_GATEWAY_PROOF_UNAVAILABLE')
                    if not runtime.start(timeout_seconds=self._timeout).healthy:
                        raise RuntimeError('RUNTIME_GATEWAY_UNAVAILABLE')
            except Exception:
                self.close()
                raise
            self._state = 'running'
            return self

    def close(self):
        with self._lifecycle_lock:
            self._state = 'closing'
            failed = False
            for _, _, runtime, _ in self._entries.values():
                try:
                    result = runtime.close(timeout_seconds=self._timeout)
                    counts = (result.cleanup_failures, result.unregister_uncertainties,
                              result.unknown_registration_count, result.owner_cleanup_uncertainties)
                    if (type(result.state) is not str or result.state != 'closed'
                            or result.thread_alive is not False
                            or any(type(value) is not int or value != 0 for value in counts)):
                        failed = True
                except Exception:
                    failed = True
            if failed:
                self._state = 'close_uncertain'
                raise RuntimeError('RUNTIME_GATEWAY_CLEANUP_UNCERTAIN')
            self._state = 'closed'

    def __call__(self, strategy_ref, operation, arguments):
        with self._lifecycle_lock:
            if self._state != 'running' or strategy_ref not in self._entries or operation not in _OPERATIONS:
                raise ValueError('RUNTIME_GATEWAY_CALL_DENIED')
        if not isinstance(arguments, Mapping) or set(arguments) != _FIELDS[operation]:
            raise ValueError('RUNTIME_GATEWAY_CALL_DENIED')
        _, proof, runtime, bound = self._entries[strategy_ref]
        if proof.revoked or time.time() >= proof.expires_at:
            raise ValueError('RUNTIME_GATEWAY_PROOF_UNAVAILABLE')
        # call_bound checks exact registration, authenticated peer, lease and
        # binding both sides of the one RPC. Exceptions mean UNKNOWN to Runtime.
        return runtime.call_bound('application.direct_write', operation, dict(arguments), route=bound,
                                  timeout_seconds=self._timeout).result

    def refresh_proof(self, strategy_ref, expires_at):
        """Renew one strategy's delegation proof on the operator's explicit act.

        D66-08 continuation: the proof's ``expires_at`` is absolute and finite;
        once it passes the bridge refuses to start and every call is refused.
        The only lawful renewal is this operator-triggered replacement, which
        preserves the delegation id and operator and only advances the wall-clock
        window.  An unknown strategy, a non-finite or non-future expiry, or a
        revoked proof is refused and the entry is left unchanged.  The proof is
        swapped under the lifecycle lock so an in-flight call never races it.
        """
        with self._lifecycle_lock:
            if self._state != 'running' or strategy_ref not in self._entries:
                raise ValueError('RUNTIME_GATEWAY_CALL_DENIED')
            route, proof, runtime, bound = self._entries[strategy_ref]
            if (type(expires_at) not in (int, float) or isinstance(expires_at, bool)
                    or not math.isfinite(expires_at)):
                raise ValueError('RUNTIME_GATEWAY_CALL_DENIED')
            if proof.revoked or float(expires_at) <= time.time():
                raise ValueError('RUNTIME_GATEWAY_PROOF_UNAVAILABLE')
            new_proof = replace(proof, expires_at=float(expires_at))
            entries = dict(self._entries)
            entries[strategy_ref] = (route, new_proof, runtime, bound)
            self._entries = MappingProxyType(entries)
        return new_proof.expires_at

    def client_for_strategy(self, config, strategy_ref):
        """Build Runtime's existing client with the supplied proof, never a request proof."""
        from bt_api_runtime.gateway_control import build_gateway_control_client
        if strategy_ref not in self._entries:
            raise ValueError('RUNTIME_GATEWAY_CALL_DENIED')
        proof = self._entries[strategy_ref][1]
        def call(selected, operation, arguments):
            if selected != strategy_ref:
                raise ValueError('RUNTIME_GATEWAY_CALL_DENIED')
            return self(selected, operation, arguments)
        return build_gateway_control_client(config, port=call, proof=proof)
