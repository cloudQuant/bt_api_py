"""Explicit strategy lease expiry to Gateway DRAIN bridge.

The Registry persists expiry before deleting its session. A pending event fences
new strategy writes until this owner has durably stored the matching cause.
Reconnection alone never proves recovery and cannot clear the cause.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GatewayLeaseDrainSettings:
    strategies: frozenset[str]
    automatic_resume: bool = True
    recovery_source: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if (type(self.strategies) is not frozenset or not self.strategies
                or len(self.strategies) > 1024 or type(self.automatic_resume) is not bool
                or any(type(item) is not str or not item or item != item.strip() or len(item) > 256
                       for item in self.strategies)
                or (self.recovery_source is not None and
                    (not callable(getattr(self.recovery_source, 'hold_verified', None))
                     or not callable(getattr(self.recovery_source, 'recheck', None))))):
            raise ValueError("INVALID_GATEWAY_LEASE_DRAIN")


class GatewayLeaseDrain:
    def __init__(self, settings: GatewayLeaseDrainSettings, *, instance_id, account_ref, owner, control, binding=None, service_identity=None):
        from bt_api_gateway.direct_control import GatewayDirectControl
        from bt_api_base.service_protocol import WireIdentity, WireRole
        from bt_api_py.gateway_direct_owner import GatewayDirectOwner

        if (type(settings) is not GatewayLeaseDrainSettings or type(owner) is not GatewayDirectOwner
                or type(control) is not GatewayDirectControl or not owner.supports_cancel
                or owner.binding() is None or owner.binding().account_scope != account_ref
                or not owner.is_bound_to(control._journal) or owner.binding().generation != control._generation
                or control._journal.account_scope != account_ref
                or (service_identity is not None and (type(service_identity) is not WireIdentity
                    or service_identity.role is not WireRole.SERVICE or service_identity.delegate_ref is not None))
                or (binding is not None and (binding._owner is not owner or binding._control is not control))):
            raise ValueError("INVALID_GATEWAY_LEASE_DRAIN")
        self.settings, self.instance_id, self.account_ref = settings, instance_id, account_ref
        self.owner, self.control = owner, control
        self.binding, self.recovery = binding, None
        self.service_identity = service_identity
        self.origin = "system:lease:" + hashlib.sha256((instance_id + "\0" + account_ref).encode()).hexdigest()
        self._target_cursor = {}

    @property
    def recovery_state(self):
        """No installed provider currently proves the required recovery cut."""
        return "NOT_COMPARABLE"

    def configure(self, registry):
        registry.configure_gateway_lease_drain(instance_id=self.instance_id,
            account_ref=self.account_ref, strategies=self.settings.strategies)
        from bt_api_py.gateway_lease_recovery import GatewayLeaseRecovery
        self.recovery = GatewayLeaseRecovery(registry=registry, instance_id=self.instance_id,
            account_ref=self.account_ref, owner=self.owner, control=self.control,
            source=self.settings.recovery_source, clock=time.monotonic)
        if self.binding is not None:
            self.binding._lease_recovery = self.recovery

    def maintain(self, registry):
        events = registry.pending_gateway_lease_expirations(instance_id=self.instance_id,
                                                            account_ref=self.account_ref)
        for event in events:
            expected = 'lease:' + hashlib.sha256(json.dumps(
                [self.instance_id, event.get('registration_id'), event.get('service_epoch')],
                separators=(',', ':')).encode()).hexdigest()
            if (event.get('state') != 'PENDING' or event.get('instance_id') != self.instance_id
                    or event.get('account_ref') != self.account_ref
                    or event.get('strategy_ref') not in self.settings.strategies
                    or event.get('event_id') != expected
                    or any(type(event.get(name)) is not str or not event[name]
                           for name in ('registration_id', 'service_epoch', 'run_id', 'principal'))
                    or any(type(event.get(name)) not in (int, float) or not math.isfinite(event[name])
                           for name in ('expires_at', 'observed_at'))
                    or event['observed_at'] < event['expires_at']):
                raise ValueError('INVALID_GATEWAY_LEASE_EVENT')
            def persist(event=event):
                self.control.apply(event['event_id'], 'DRAIN', scope_kind='strategy',
                                   scope_value=event['strategy_ref'], origin=self.origin)
            self.owner.submit_service_call(persist).result(timeout_seconds=1.0)
            registry.acknowledge_gateway_lease_expiration(instance_id=self.instance_id,
                account_ref=self.account_ref, event_id=event['event_id'])

        def dispatch_one():
            # A sent/uncertain cancel is never retried; only durable PENDING
            # frozen targets are claimable. Reconciliation is observation only.
            causes = self.control.active_system_causes(self.origin)
            if self.control._journal._schema_version < 17 or self.service_identity is None:
                return
            active = {cause['cause_id'] for cause in causes}
            self._target_cursor = {key: value for key, value in self._target_cursor.items() if key in active}
            for cause in causes:
                self.control.reconcile_targets(cause['cause_id'])
                page = self.control.targets(cause['cause_id'], after=self._target_cursor.get(cause['cause_id'], ''), limit=256)
                for target in page:
                    self._target_cursor[cause['cause_id']] = target['reservation_id']
                    if target['state'] == 'PENDING':
                        from contextlib import contextmanager
                        with registry.gateway_lease_control_fence(instance_id=self.instance_id,
                                account_ref=self.account_ref, event_id=cause['cause_id']) as (event, check):
                            if event['strategy_ref'] != cause['scope_value']:
                                raise ValueError('INVALID_GATEWAY_LEASE_EVENT')
                            claimed, token = self.control.claim_target(cause['cause_id'], target['reservation_id'],
                                                                       before_commit=check)
                        if token is not None:
                            @contextmanager
                            def lease_fence(_record):
                                with registry.gateway_lease_control_fence(instance_id=self.instance_id,
                                        account_ref=self.account_ref, event_id=cause['cause_id']) as (current, recheck):
                                    if current != event:
                                        raise ValueError('INVALID_GATEWAY_LEASE_EVENT')
                                    yield recheck
                            self.control.dispatch_claimed_cancel(claimed, self.owner._cancel_dispatcher, token,
                                request_fence=lease_fence, requester=self.service_identity.principal_ref,
                                authority_source={'kind':'lease', 'actor':self.service_identity.actor_ref,
                                    'registration':event['registration_id'], 'epoch':event['service_epoch'],
                                    'event_id':event['event_id']})
                        return
                if len(page) < 256:
                    self._target_cursor.pop(cause['cause_id'], None)
        self.owner.submit_service_call(dispatch_one).result(timeout_seconds=1.0)
        if self.settings.automatic_resume and self.recovery is not None and self.settings.recovery_source is not None:
            causes = self.owner.submit_service_call(
                lambda: self.control.active_system_causes(self.origin)).result(timeout_seconds=1.0)
            for cause in causes:
                self.recovery.recover_auto(cause['cause_id'])

