"""Explicit local parent preparation and one authenticated Gateway SOURCE.

This opt-in records only a local source observation. It does not register a
parent, contact Risk, dispatch an order, or establish account authority.
"""
from __future__ import annotations

import uuid
import math
import threading

from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_execution.local_gateway_parent_preparation import LocalGatewayParentPreparation

from .execution_parent_service_host import ParentServiceHost
from .service_runtime import BoundServiceTarget
from .service_runtime_thread import ThreadedServiceClientRuntime


class GatewaySourcePreparationError(ValueError):
    pass


class GatewaySourcePreparation:
    """Borrow the exact running parent and an explicitly selected Gateway route."""

    def __init__(self, *, owner, runtime, target, strategy_identity):
        if (type(owner) is not ParentServiceHost or type(runtime) is not ThreadedServiceClientRuntime
                or type(target) is not BoundServiceTarget or type(strategy_identity) is not WireIdentity
                or strategy_identity.role is not WireRole.STRATEGY_CLIENT):
            raise GatewaySourcePreparationError('INVALID_CONFIGURATION')
        self._owner, self._runtime, self._target, self._identity = owner, runtime, target, strategy_identity
        self._binding()

    def _binding(self):
        owner, target, identity = self._owner, self._target, self._identity
        try:
            if (owner.state != 'running' or owner.host is None or owner.journal is None
                    or not owner.host.healthy or target.identity != owner.config.identity
                    or target.scope != owner.config.wire_scope
                    or not any(grant.identity == identity for grant in owner.config.parent_grants)):
                raise ValueError
            service, = owner.host._atomic_intake_services
            intake = service._intake
            route = intake._routes[target.scope]
            if (intake._host is not owner.journal or intake._store is not owner.journal._store
                    or route.scope != owner.config.local_scope or route.lease is not intake._lease
                    or service.instance_id != owner.config.action_grant.instance_id or not service.lease_ready):
                raise ValueError
            return owner.journal, intake._lease, service.instance_id
        except Exception:
            raise GatewaySourcePreparationError('BINDING_UNAVAILABLE') from None

    def observe(self, parent_ref, *, request_id, request_sha256, timeout_seconds=5.0):
        """Attempt SOURCE once; return OBSERVED, HISTORICAL, INVALID_REQUEST or UNKNOWN."""
        try:
            if (any(type(value) is not str or not value or value != value.strip() or not value.isprintable()
                    or len(value.encode('utf-8', 'strict')) > 256 for value in (parent_ref, request_id))
                    or type(request_sha256) is not str or len(request_sha256) != 64
                    or any(c not in '0123456789abcdef' for c in request_sha256)
                    or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
                    or not 0 < timeout_seconds <= threading.TIMEOUT_MAX):
                return 'INVALID_REQUEST'
        except (UnicodeError, OverflowError):
            return 'INVALID_REQUEST'
        try:
            journal, lease, instance = self._binding()
            preparation = LocalGatewayParentPreparation(host=journal, scope=self._owner.config.local_scope,
                lease=lease, wire_scope=self._owner.config.wire_scope, identity=self._identity)
        except Exception:
            return 'UNKNOWN'
        try:
            result = self._runtime.record_prepared_parent_source(self._target, preparation, parent_ref,
                request_id=request_id, request_sha256=request_sha256, instance_id=instance,
                attempt_id=str(uuid.uuid4()), timeout_seconds=timeout_seconds)
            if self._binding() != (journal, lease, instance) or type(result) is not dict:
                return 'UNKNOWN'
            return result['status'] if result.get('status') in ('OBSERVED', 'HISTORICAL') else 'UNKNOWN'
        except Exception:
            return 'UNKNOWN'
