"""Authenticated parent RPCs on the account's existing Gateway owner."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from collections.abc import Mapping
import json
import threading

from bt_api_base.gateway_direct_protocol import canonical_gateway_payload
from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole
from bt_api_gateway.direct_parent_authority import GatewayParentAuthority
from bt_api_py.gateway_direct_owner import GatewayDirectOwner
from bt_api_py.gateway_execution_stream_client import _detach_wire
from bt_api_py.service_registry import ServiceCallContext, ServiceDispatchAdmission, ServiceRegistry

REGISTER = 'direct.parent.register'
QUERY = 'direct.parent.query'
CANCEL = 'direct.parent.cancel'
OPERATION = 'direct.parent.operation'
OPERATIONS = frozenset({REGISTER, QUERY, CANCEL, OPERATION})


def _ref(value):
    try:
        return (type(value) is str and bool(value) and value == value.strip()
                and value.isprintable() and len(value.encode('utf-8', 'strict')) <= 256)
    except UnicodeError:
        return False


@dataclass(frozen=True, slots=True)
class GatewayParentGrant:
    identity: WireIdentity
    account_scope: str
    strategy_scope: str
    execution_domain_ref: str
    actor_kind: str
    revision: str
    instruments: frozenset[MarketInstrumentKey]
    operations: frozenset[str]

    def __post_init__(self):
        if (type(self.identity) is not WireIdentity or self.identity.delegate_ref is not None
                or self.actor_kind not in ('execution', 'strategy')
                or self.identity.role is not (WireRole.SERVICE if self.actor_kind == 'execution' else WireRole.STRATEGY_CLIENT)
                or any(not _ref(v) for v in (self.account_scope, self.strategy_scope, self.execution_domain_ref, self.revision))
                or type(self.instruments) is not frozenset or not self.instruments
                or any(type(v) is not MarketInstrumentKey for v in self.instruments)
                or type(self.operations) is not frozenset or not self.operations or not self.operations <= OPERATIONS
                or (self.actor_kind == 'strategy' and REGISTER in self.operations)):
            raise ValueError('INVALID_PARENT_GRANT')


@dataclass(frozen=True, slots=True)
class GatewayParentSettings:
    grants: tuple[GatewayParentGrant, ...]

    def __post_init__(self):
        if (type(self.grants) is not tuple or not self.grants
                or any(type(g) is not GatewayParentGrant for g in self.grants)
                or len({(g.identity, g.account_scope, g.strategy_scope, g.execution_domain_ref)
                        for g in self.grants}) != len(self.grants)):
            raise ValueError('INVALID_PARENT_SETTINGS')


class GatewayParentService:
    """Only Registry-issued admissions can enter the journal transaction."""

    def __init__(self, settings, *, owner, journal, authority, instance_id, scope_ref):
        if (type(settings) is not GatewayParentSettings or type(owner) is not GatewayDirectOwner
                or not owner.is_bound_to(journal) or type(authority) is not GatewayParentAuthority
                or authority._journal is not journal or not _ref(instance_id) or not _ref(scope_ref)
                or any(g.account_scope != journal.account_scope for g in settings.grants)):
            raise ValueError('INVALID_PARENT_SERVICE')
        self.settings, self.owner, self.journal, self.authority = settings, owner, journal, authority
        self.instance_id, self.scope_ref = instance_id, scope_ref
        self._registry = None

    def configure(self, registry):
        if type(registry) is not ServiceRegistry or self._registry is not None:
            raise ValueError('PARENT_SERVICE_ALREADY_CONFIGURED')
        registry.configure_gateway_parents(instance_id=self.instance_id, scope_ref=self.scope_ref,
                                           grants=self.settings.grants)
        self._registry = registry

    def handle(self, operation, context, arguments):
        if (operation not in OPERATIONS or type(context) is not ServiceCallContext
                or not isinstance(arguments, Mapping)):
            return {'status': 'REQUEST_REJECTED', 'code': 'SCOPE_DENIED'}
        admission = context.dispatch_admission
        if (type(admission) is not ServiceDispatchAdmission or admission._registry is not self._registry
                or not admission.matches_context(context)
                or not admission.matches_operation(operation) or not admission.matches(arguments)):
            return {'status': 'REQUEST_REJECTED', 'code': 'ADMISSION_DENIED'}
        expected = {
            REGISTER: {'spec'},
            QUERY: {'account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id'},
            OPERATION: {'account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id', 'operation_id'},
            CANCEL: {'account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id', 'cancel_operation_id', 'expected_version'},
        }[operation]
        try:
            if set(arguments) != expected:
                raise ValueError
            data = json.loads(canonical_gateway_payload(_detach_wire(arguments)))
            data = data['spec'] if operation == REGISTER else data
            if type(data) is not dict:
                raise ValueError
            scope, identity = context.scope, context.identity
            grant = next(g for g in self.settings.grants if g.identity == identity
                         and g.account_scope == scope.account_ref and g.strategy_scope == scope.strategy_ref
                         and g.execution_domain_ref == scope.execution_domain_ref and operation in g.operations)
            if (not admission.matches_parent_grant(grant)
                    or context.principal_ref != identity.principal_ref or scope.run_id is None
                    or scope.account_refs or scope.manual_book_ref is not None or scope.portfolio_ref is not None
                    or scope.group_id is not None or scope.leg_id is not None
                    or data.get('account_scope') != grant.account_scope or data.get('strategy_scope') != grant.strategy_scope
                    or not _ref(data.get('parent_ref')) or not _ref(data.get('registration_operation_id'))):
                raise ValueError
            if operation == REGISTER:
                self._check_spec(data, grant, identity, registration=True)
            if operation == OPERATION and not _ref(arguments['operation_id']):
                raise ValueError
            if operation == CANCEL and (not _ref(arguments['cancel_operation_id'])
                    or type(arguments['expected_version']) is not int or arguments['expected_version'] < 1):
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
            if (self._registry is None or self._registry.gateway_parent_grant(
                    instance_id=self.instance_id, scope_ref=self.scope_ref, identity=identity,
                    scope=scope, operation=operation) != grant):
                return False
            return True

        params = {'principal': identity.principal_ref, 'actor_ref': identity.actor_ref,
                  'fence': fence, 'recheck': recheck}
        def run():
            try:
                if operation == REGISTER:
                    result = self.authority.register_parent(data, **params)
                else:
                    key = (data['account_scope'], data['strategy_scope'], data['parent_ref'], data['registration_operation_id'])
                    parent = self.authority.query_parent(*key, **params)
                    self._check_spec(parent['spec'], grant, identity, registration=False, parent=parent)
                    if operation == QUERY:
                        result = parent
                    elif operation == CANCEL:
                        result = self.authority.cancel_parent(*key, data['cancel_operation_id'], data['expected_version'], **params)
                    else:
                        result = self.authority.query_operation(*key[:3], data['operation_id'], **params)
                return {'status': 'OBSERVED', 'schema_version': 1, 'operation': operation, 'result': result}
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

    @staticmethod
    def _check_spec(spec, grant, identity, *, registration, parent=None):
        instrument = MarketInstrumentKey.from_dict(spec['instrument'])
        if (spec['account_scope'] != grant.account_scope or spec['strategy_scope'] != grant.strategy_scope
                or spec['execution_domain_ref'] != grant.execution_domain_ref or instrument not in grant.instruments
                or (registration and (grant.actor_kind != 'execution' or spec['grant_revision'] != grant.revision))
                or (not registration and grant.actor_kind == 'execution'
                    and (parent['execution_principal'] != identity.principal_ref or parent['execution_actor_ref'] != identity.actor_ref))):
            raise ValueError('PARENT_GRANT_DENIED')
