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
SOURCE = 'direct.parent.source'
REGISTER_V2 = 'direct.parent.register_v2'
OPERATION_V2 = 'direct.parent.operation_v2'
CANCEL_V2 = 'direct.parent.cancel_v2'
V2_OPERATIONS = frozenset({REGISTER_V2, OPERATION_V2, CANCEL_V2})
_SUPPORTED_OPERATIONS = OPERATIONS | {SOURCE} | V2_OPERATIONS


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
                or type(self.operations) is not frozenset or not self.operations or not self.operations <= _SUPPORTED_OPERATIONS
                or (self.actor_kind == 'strategy' and self.operations & ({REGISTER, SOURCE} | V2_OPERATIONS))):
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
        if (operation not in _SUPPORTED_OPERATIONS or type(context) is not ServiceCallContext
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
            SOURCE: {'account_scope', 'strategy_scope', 'execution_domain_ref'},
            REGISTER_V2: {'spec', 'expected_source'},
            OPERATION_V2: {'account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id', 'operation_id', 'expected_source'},
            CANCEL_V2: {'account_scope', 'strategy_scope', 'parent_ref', 'registration_operation_id', 'cancel_operation_id', 'expected_version', 'expected_source'},
        }[operation]
        try:
            readiness = operation == SOURCE and 'readiness_version' in arguments
            subject_observation = operation == SOURCE and 'subject_version' in arguments
            if readiness and subject_observation:
                raise ValueError
            required = (expected | {'readiness_version'} if readiness else
                        expected | {'subject_version'} if subject_observation else expected)
            if set(arguments) != required:
                raise ValueError
            payload = json.loads(canonical_gateway_payload(_detach_wire(arguments)))
            if readiness and (type(payload['readiness_version']) is not int or payload['readiness_version'] != 2):
                raise ValueError
            if subject_observation and (type(payload['subject_version']) is not int or payload['subject_version'] != 1):
                raise ValueError
            expected_source = payload.get('expected_source') if operation in V2_OPERATIONS else None
            data = payload['spec'] if operation in {REGISTER, REGISTER_V2} else payload
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
                    or data.get('account_scope') != grant.account_scope or data.get('strategy_scope') != grant.strategy_scope):
                raise ValueError
            if operation in V2_OPERATIONS:
                if (grant.actor_kind != 'execution' or identity.role is not WireRole.SERVICE
                        or type(expected_source) is not dict
                        or set(expected_source) != {'account_scope', 'journal_id', 'gateway_generation', 'minimum_revision'}
                        or expected_source['account_scope'] != grant.account_scope
                        or not _ref(expected_source['journal_id'])
                        or not _ref(expected_source['gateway_generation'])
                        or type(expected_source['minimum_revision']) is not int
                        or expected_source['minimum_revision'] < 0):
                    raise ValueError
            if operation == SOURCE:
                if (grant.actor_kind != 'execution' or identity.role is not WireRole.SERVICE
                        or data.get('execution_domain_ref') != grant.execution_domain_ref):
                    raise ValueError
            elif not _ref(data.get('parent_ref')) or not _ref(data.get('registration_operation_id')):
                raise ValueError
            if operation in {REGISTER, REGISTER_V2}:
                self._check_spec(data, grant, identity, registration=True)
            if operation in {OPERATION, OPERATION_V2} and not _ref(arguments['operation_id']):
                raise ValueError
            if operation in {CANCEL, CANCEL_V2} and (not _ref(arguments['cancel_operation_id'])
                    or type(arguments['expected_version']) is not int or arguments['expected_version'] < 1):
                raise ValueError
        except Exception:
            admission.close()
            return {'status': 'REQUEST_REJECTED', 'code': 'INVALID_REQUEST'}

        active = threading.local()
        observed_subject = []
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
            if readiness:
                admission.check_gateway_parent_readiness()
            if subject_observation:
                current_subject = admission.observe_gateway_parent_strategy_subject()
                if not observed_subject:
                    observed_subject.append(current_subject)
                elif current_subject != observed_subject[0]:
                    return False
            return True

        def original_run():
            # A run is read only while the Registry-issued admission is held;
            # the RPC payload cannot choose or override this value.
            active.check()
            return scope.run_id

        params = {'principal': identity.principal_ref, 'actor_ref': identity.actor_ref,
                  'fence': fence, 'recheck': recheck}
        def run():
            try:
                if operation == REGISTER:
                    result = self.authority.register_parent(data, original_run=original_run, **params)
                elif operation == SOURCE:
                    options = {'require_original_runs': True} if readiness else {}
                    result = self.authority.describe_source(data['account_scope'], data['strategy_scope'],
                                                            data['execution_domain_ref'], **params, **options)
                elif operation == REGISTER_V2:
                    result = self.authority.register_parent_v2(data, expected_source, original_run=original_run, **params)
                elif operation in {OPERATION_V2, CANCEL_V2}:
                    key = (data['account_scope'], data['strategy_scope'], data['parent_ref'], data['registration_operation_id'])

                    def authorize_parent(parent):
                        self._check_spec(parent['spec'], grant, identity, registration=False, parent=parent)
                        return True

                    if operation == OPERATION_V2:
                        result = self.authority.query_operation_v2(*key, data['operation_id'], expected_source,
                                                                   authorize_parent=authorize_parent,
                                                                   original_run=original_run, **params)
                    else:
                        result = self.authority.cancel_parent_v2(*key, data['cancel_operation_id'],
                            data['expected_version'], expected_source, authorize_parent=authorize_parent, **params)
                else:
                    key = (data['account_scope'], data['strategy_scope'], data['parent_ref'], data['registration_operation_id'])
                    parent = self.authority.query_parent(*key, **params)
                    self._check_spec(parent['spec'], grant, identity, registration=False, parent=parent)
                    if operation == QUERY:
                        result = parent
                    elif operation == CANCEL:
                        result = self.authority.cancel_parent(*key, data['cancel_operation_id'], data['expected_version'], **params)
                    else:
                        result = self.authority.query_operation(*key[:3], data['operation_id'],
                                                                original_run=original_run, **params)
                if readiness:
                    return {'status': 'OBSERVED', 'schema_version': 2, 'operation': operation,
                            'result': result, 'checked_operations': [SOURCE, REGISTER_V2, OPERATION_V2, CANCEL_V2]}
                if subject_observation:
                    if len(observed_subject) != 1:
                        return {'status': 'UNKNOWN'}
                    return {'status': 'OBSERVED', 'schema_version': 3, 'operation': operation,
                            'result': result, 'strategy_subject': observed_subject[0]}
                if operation in V2_OPERATIONS:
                    return {'status': 'OBSERVED', 'schema_version': 2, 'operation': operation,
                            'result': result['result'], 'source': result['source']}
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
