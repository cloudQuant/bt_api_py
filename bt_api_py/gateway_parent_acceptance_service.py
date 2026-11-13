"""Explicit account-scoped, read-only parent acceptance observation."""
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import json
import threading

from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_gateway.direct_parent_authority import GatewayParentAuthority
from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission, ServiceRegistry

OPERATION = 'direct.parent.acceptance_order'


def _ref(value):
    try:
        return (type(value) is str and bool(value) and value == value.strip()
                and value.isprintable() and len(value.encode('utf-8', 'strict')) <= 256)
    except UnicodeError:
        return False


@dataclass(frozen=True, slots=True)
class GatewayParentAcceptanceGrant:
    instance_id: str
    scope_ref: str
    identity: WireIdentity
    account_scope: str
    execution_domain_ref: str
    revision: str
    strategies: frozenset[str]
    instruments: frozenset[MarketInstrumentKey]

    def __post_init__(self):
        if (any(not _ref(v) for v in (self.instance_id, self.scope_ref, self.account_scope,
                                      self.execution_domain_ref, self.revision))
                or type(self.identity) is not WireIdentity or self.identity.role is not WireRole.SERVICE
                or self.identity.delegate_ref is not None
                or type(self.strategies) is not frozenset or not self.strategies
                or any(not _ref(v) for v in self.strategies)
                or type(self.instruments) is not frozenset or not self.instruments
                or any(type(v) is not MarketInstrumentKey for v in self.instruments)):
            raise ValueError('INVALID_ACCEPTANCE_GRANT')


class GatewayParentAcceptanceService:
    def __init__(self, grants, *, owner, journal, authority, instance_id, scope_ref):
        if (type(grants) is not tuple or not grants
                or any(type(g) is not GatewayParentAcceptanceGrant for g in grants)
                or len({g.identity.principal_ref for g in grants}) != len(grants)
                or type(owner) is not GatewayDirectOwner or not owner.is_bound_to(journal)
                or type(authority) is not GatewayParentAuthority or authority._journal is not journal
                or owner._dispatcher._guard is not authority._guard
                or not journal._parent_acceptance_order_enabled
                or any(g.instance_id != instance_id or g.scope_ref != scope_ref
                       or g.account_scope != journal.account_scope for g in grants)):
            raise ValueError('INVALID_ACCEPTANCE_SERVICE')
        self.grants, self.owner, self.journal, self.authority = grants, owner, journal, authority
        self.instance_id, self.scope_ref = instance_id, scope_ref
        self._registry = None

    def configure(self, registry):
        if type(registry) is not ServiceRegistry or self._registry is not None:
            raise ValueError('ACCEPTANCE_SERVICE_ALREADY_CONFIGURED')
        registry.configure_gateway_parent_acceptance(grants=self.grants)
        self._registry = registry

    def handle(self, operation, context, arguments):
        if (operation != OPERATION or type(context) is not ServiceCallContext
                or not isinstance(arguments, Mapping)):
            return {'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}
        admission = context.dispatch_admission
        if (type(admission) is not ServiceDispatchAdmission or admission._registry is not self._registry
                or not admission.matches_context(context) or not admission.matches_operation(operation)
                or not admission.matches(arguments)):
            return {'status': 'REQUEST_REJECTED', 'code': 'ADMISSION_DENIED'}
        try:
            payload = json.loads(canonical_gateway_payload(_detach_wire(arguments)))
            if set(payload) != {'account_scope', 'strategy_scope', 'parent_ref',
                                'registration_operation_id', 'expected_source'}:
                raise ValueError
            grant = admission._binding.acceptance_grant
            if (type(grant) is not GatewayParentAcceptanceGrant
                    or grant.identity != context.identity or grant.account_scope != context.scope.account_ref
                    or grant.execution_domain_ref != context.scope.execution_domain_ref
                    or payload['account_scope'] != grant.account_scope
                    or payload['strategy_scope'] not in grant.strategies
                    or any(not _ref(payload[k]) for k in ('parent_ref', 'registration_operation_id'))):
                raise ValueError
        except Exception:
            admission.close()
            return {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}
        active = threading.local()
        @contextmanager
        def fence():
            with admission.scope() as check:
                active.check = check
                try:
                    yield
                finally:
                    active.check = None
        def recheck():
            active.check()
            return True
        def run():
            try:
                result = self.authority._query_parent_acceptance_order_v2(
                    payload['account_scope'], payload['strategy_scope'], payload['parent_ref'],
                    payload['registration_operation_id'], payload['expected_source'],
                    execution_domain_ref=grant.execution_domain_ref, allowed_strategies=grant.strategies,
                    allowed_instruments=grant.instruments, principal=grant.identity.principal_ref,
                    actor_ref=grant.identity.actor_ref, fence=fence, recheck=recheck)
                return {'status': 'OBSERVED', 'schema_version': 1, 'operation': OPERATION,
                        'result': {'sequence': result['sequence']}, 'source': result['source']}
            except Exception:
                return {'status': 'UNKNOWN'}
            finally:
                admission.close()
        try:
            handle = self.owner.submit_service_call(run)
        except Exception:
            admission.close()
            return {'status': 'UNKNOWN'}
        try:
            return handle.result(timeout_seconds=1.0)
        except Exception:
            if handle.cancel() or handle.done:
                admission.close()
            return {'status': 'UNKNOWN'}
