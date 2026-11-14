"""Independent, audited read-only view of one local Execution parent.

The caller of ``handle`` must supply a transport-authenticated principal; wire
identity fields are claims and are checked by the privately owned Registry.
This service owns a separate in-memory Registry, a local read-only account
snapshot and a durable audit journal. It never changes an Atomic Host's
readiness, dispatches an order, proves a venue fact, or supplies cancellation.
The lifetime, authentication transport and single-process trusted composition
remain the embedding host's responsibility; no network listener is created.
"""
from __future__ import annotations

import os
import threading
from dataclasses import asdict
from pathlib import Path
from types import MappingProxyType

from bt_api_base.service_protocol import WireEnvelope
from bt_api_execution.contracts import ExecutionScope
from bt_api_execution.local_parent_snapshot import (
    LocalParentSnapshot, LocalParentPlanSnapshot, LocalParentSnapshotReader, SnapshotReaderStartupCleanupError,
)

from .execution_management_audit import (
    AuditStartupCleanupError, ExecutionManagementReadAudit, ManagementReadIdentity,
)
from .execution_management_read_grant import (
    PARENT_READ_V1, PARENT_PLAN_READ_V1, ExecutionManagementReadGrant, ExecutionManagementReadRequest,
    allows_instrument, allows_request,
)
from .service_registry import (PrincipalGrant, ResourceGrant, ServiceCapability, ServiceRegistry,
                               ServiceRegistryStartupCleanupError)


PORT = 'service.execution_management_read'
_UNKNOWN = LocalParentSnapshot('UNKNOWN')
_UNKNOWN_PLAN = LocalParentPlanSnapshot('UNKNOWN')


class ExecutionManagementReadServiceError(RuntimeError):
    """Finite configuration or lifecycle failure; no account values are exposed."""


class ManagementServiceStartupCleanupError(ExecutionManagementReadServiceError):
    """The service must remain on its owner thread until cleanup is confirmed."""

    def __init__(self, service):
        self.service = service
        super().__init__('MANAGEMENT_STARTUP_CLEANUP_UNKNOWN')


class ExecutionManagementReadService:
    """One separately scoped management Registry and owner-thread reader.

    Every Registry call and policy mutation must go through this object's gate.
    The private Registry is not exported. The process and trusted Python owner
    are a cooperative boundary, not a sandbox against reflection or raw DB
    access. A successful read is linearized at the final authorization check;
    a later revocation cannot retract an already authorized response. The
    Registry and audit journal are independent of the trading Registry.

    Lease tracking is process/epoch-local, requires the Registry's verified
    sleep-inclusive clock and fails closed when that clock is unavailable.
    Currently that clock is available only on the supported Darwin path. A
    crash or uncertain registration/renewal never reconstructs a lease from
    wall-clock expiry. Actual machine sleep and real transport are unverified.
    """

    def __init__(self, *, host, local_scope: ExecutionScope, grant: ExecutionManagementReadGrant,
                 scope_ref: str, service_id: str, authenticated_principal: str,
                 audit_root: str | Path, lease_seconds: float = 30.0):
        from bt_api_execution.account_journal_host import AccountJournalHost

        if (type(host) is not AccountJournalHost or type(local_scope) is not ExecutionScope
                or type(grant) is not ExecutionManagementReadGrant
                or type(scope_ref) is not str or not scope_ref or scope_ref.strip() != scope_ref
                or type(service_id) is not str or not service_id or service_id.strip() != service_id
                or authenticated_principal != grant.identity.principal_ref):
            raise ExecutionManagementReadServiceError('INVALID_CONFIGURATION')
        try:
            root = Path(host._root).resolve(strict=True)
            audit_dir = Path(audit_root).resolve(strict=True)
            if audit_dir == root or root in audit_dir.parents:
                raise ValueError('audit root cannot be inside the account journal')
        except Exception:
            raise ExecutionManagementReadServiceError('INVALID_CONFIGURATION') from None

        self.__gate = threading.RLock()
        self.__pid = os.getpid()
        self.__thread = threading.current_thread()
        self.__closed = False
        self.__closing = False
        self.__grant = grant
        self.__operations = grant.operations
        self.__generation = 1
        self.__principal = authenticated_principal
        self.__scope_ref = scope_ref
        self.__reader = self.__audit = self.__registry = None
        self.__registry_open_pending = False
        self.__leases = {}
        try:
            try:
                self.__reader = object.__new__(LocalParentSnapshotReader)
                LocalParentSnapshotReader.__init__(self.__reader,
                    host=host, scope=local_scope, expected_wire_scope=grant.scope)
            except SnapshotReaderStartupCleanupError as error:
                self.__reader = error.reader
                raise
            try:
                self.__audit = object.__new__(ExecutionManagementReadAudit)
                ExecutionManagementReadAudit.__init__(self.__audit, audit_root)
            except AuditStartupCleanupError as error:
                self.__audit = error.audit
                raise
            operations = grant.operations
            capability = ServiceCapability(grant.instance_id, PORT, operations)
            resource = ResourceGrant(grant.instance_id, PORT, scope_ref, grant.scope,
                                     operations)
            self.__registry_open_pending = True
            try:
                self.__registry = ServiceRegistry(':memory:', service_id=service_id,
                    capabilities=(capability,), grants={authenticated_principal: (PrincipalGrant(grant.identity, (resource,)),)},
                    policy_version='execution-management-read-v1', requirements_version='execution-management-read-v1',
                    lease_seconds=lease_seconds)
            except ServiceRegistryStartupCleanupError as error:
                self.__registry = error.registry
                # A retained database proves connect returned a handle.  A
                # failed open without one has no comparable cleanup proof.
                if error.registry._db is not None:
                    self.__registry_open_pending = False
                raise
            self.__registry_open_pending = False
            if PARENT_READ_V1 in operations:
                self.__registry.register_handler(grant.instance_id, PARENT_READ_V1, self.__read_parent)
            if PARENT_PLAN_READ_V1 in operations:
                self.__registry.register_handler(grant.instance_id, PARENT_PLAN_READ_V1, self.__read_plan)
        except BaseException:
            # The caller is on the reader owner thread. Never discard a hold on
            # an uncertain close; retain this partially constructed object.
            try:
                self.close()
            except BaseException:
                _UNCERTAIN_CONSTRUCTIONS.append(self)
                raise ManagementServiceStartupCleanupError(self) from None
            raise

    def __owner(self):
        if os.getpid() != self.__pid or threading.current_thread() is not self.__thread:
            raise ExecutionManagementReadServiceError('WRONG_OWNER')

    def __now(self):
        return self.__registry._execution_release_mono_now()

    def revoke_grant(self, expected: ExecutionManagementReadGrant) -> bool:
        """Operator-only revocation; serialized with reads and disclosure."""
        with self.__gate:
            if self.__closed or self.__closing or self.__grant is not expected:
                return False
            self.__grant = None
            self.__generation += 1
            return True

    def replace_grant(self, expected: ExecutionManagementReadGrant,
                      replacement: ExecutionManagementReadGrant) -> bool:
        """Replace the policy only within the original identity and source scope."""
        if (type(expected) is not ExecutionManagementReadGrant
                or type(replacement) is not ExecutionManagementReadGrant
                or replacement.operations != self.__operations
                or (expected.instance_id, expected.identity, expected.scope)
                != (replacement.instance_id, replacement.identity, replacement.scope)):
            raise ExecutionManagementReadServiceError('INVALID_CONFIGURATION')
        with self.__gate:
            if self.__closed or self.__closing or self.__grant is not expected:
                return False
            self.__grant = replacement
            self.__generation += 1
            return True

    def __active(self, context, grant, generation, operation):
        registry = self.__registry
        row = registry._db.execute('SELECT * FROM sessions WHERE registration_id=? AND principal=? '
                                   'AND service_epoch=?',
                                   (context.registration_id, context.principal_ref, registry._epoch)).fetchone()
        lease = self.__leases.get(context.registration_id)
        if (self.__closed or self.__closing or self.__grant is not grant or self.__generation != generation
                or row is None or lease is None or lease[0] != registry._epoch
                or lease[1] != row['expires_at'] or self.__now() >= lease[2]
                or row['expires_at'] <= registry._now() or not registry._ready
                or row['instance_id'] != grant.instance_id or row['port'] != PORT
                or row['scope_ref'] != self.__scope_ref or context.identity != grant.identity
                or context.scope != grant.scope or context.principal_ref != self.__principal
                or registry._stored_identity(row['identity_json']) != context.identity
                or registry._stored_scope(row['scope_json']) != context.scope
                or not registry._registration_policy_matches(row)
                or not registry._identity_is_trusted(context.principal_ref, context.identity)
                or not registry._current_resource_allows(context.principal_ref, context.identity,
                    instance_id=grant.instance_id, port=PORT, scope_ref=self.__scope_ref,
                    scope=context.scope, operation=operation)):
            raise ExecutionManagementReadServiceError('UNAVAILABLE')

    def __read_parent(self, context, arguments):
        return self.__read(context, arguments, PARENT_READ_V1)

    def __read_plan(self, context, arguments):
        return self.__read(context, arguments, PARENT_PLAN_READ_V1)

    def __read(self, context, arguments, operation):
        # Called only by the private Registry while handle() owns the service
        # gate. The context/arguments must be its exact decoded wire objects.
        if not context._is_registry_wire_arguments(arguments):
            raise ExecutionManagementReadServiceError('UNAVAILABLE')
        if (type(arguments) not in (dict, MappingProxyType)
                or set(arguments) != {'schema_version', 'parent_ref', 'query_id', 'grant_revision'}
                or type(arguments['schema_version']) is not int or arguments['schema_version'] != 1):
            raise ExecutionManagementReadServiceError('INVALID_REQUEST')
        grant, generation = self.__grant, self.__generation
        if grant is None:
            raise ExecutionManagementReadServiceError('UNAVAILABLE')
        try:
            query = ExecutionManagementReadRequest(grant.instance_id, context.identity, context.scope,
                arguments['grant_revision'], operation, arguments['parent_ref'], arguments['query_id'])
            if not allows_request(grant, query):
                raise ValueError
            with self.__registry._read_snapshot():
                self.__active(context, grant, generation, operation)
                snapshot = (self.__reader.query(query.parent_ref) if operation == PARENT_READ_V1
                            else self.__reader.query_plan(query.parent_ref))
                if snapshot.status != 'OBSERVED' or not allows_instrument(grant, query, snapshot.instrument):
                    snapshot = _UNKNOWN if operation == PARENT_READ_V1 else _UNKNOWN_PLAN
                if operation == PARENT_PLAN_READ_V1 and snapshot.status == 'OBSERVED':
                    from .execution_management_plan_contract import plan_snapshot_payload
                    try:
                        plan_snapshot_payload(snapshot, parent_ref=query.parent_ref)
                    except Exception:
                        snapshot = _UNKNOWN_PLAN
                self.__active(context, grant, generation, operation)
                identity = ManagementReadIdentity(grant.instance_id, self.__registry._epoch,
                    context.identity.role.value, context.principal_ref, context.identity.actor_ref,
                    context.scope.account_ref, context.scope.strategy_ref, context.scope.execution_domain_ref,
                    grant.revision)
                snapshot = self.__audit.disclose_after_audit(identity=identity, query_id=query.query_id,
                    parent_ref=query.parent_ref, operation_version=1 if operation == PARENT_READ_V1 else 2,
                    snapshot=snapshot)
                self.__active(context, grant, generation, operation)
                if operation == PARENT_READ_V1:
                    payload = asdict(snapshot)
                else:
                    from .execution_management_plan_contract import plan_snapshot_payload
                    payload = plan_snapshot_payload(snapshot, parent_ref=query.parent_ref)
                return {'schema_version': 1, 'snapshot': payload}
        except Exception:
            # Registry returns a finite HANDLER_FAILED; no snapshot is disclosed.
            raise ExecutionManagementReadServiceError('UNAVAILABLE') from None

    def handle(self, authenticated_principal: str, request: WireEnvelope):
        """Handle a request from an authenticated transport, never from wire identity alone."""
        try:
            self.__owner()
            with self.__gate:
                if self.__closed or self.__closing or self.__registry is None:
                    return {'ok': False, 'code': 'UNAVAILABLE', 'result': {}}
                if type(request) is not WireEnvelope or authenticated_principal != self.__principal:
                    return {'ok': False, 'code': 'DENIED', 'result': {}}
                operation = request.header.operation
                if operation not in {'service.discover', 'service.register', 'service.renew',
                                     'service.unregister', PARENT_READ_V1, PARENT_PLAN_READ_V1}:
                    return {'ok': False, 'code': 'UNKNOWN_OPERATION', 'result': {}}
                if self.__grant is None:
                    return {'ok': False, 'code': 'SCOPE_DENIED', 'result': {}}
                registry = self.__registry
                payload = request.payload
                prior = None
                started = None
                if operation in {'service.register', 'service.renew'}:
                    started = self.__now()
                    if operation == 'service.register':
                        # The Registry bounds live sessions, but expired local
                        # lease entries must not accumulate across fresh keys.
                        self.__leases = {key: value for key, value in self.__leases.items()
                                         if value[2] > started}
                        key = payload.get('registration_key')
                        if type(key) is str:
                            with registry._lock:
                                prior = registry._db.execute(
                                    'SELECT registration_id FROM sessions WHERE principal=? AND registration_key=? AND service_epoch=?',
                                    (authenticated_principal, key, registry._epoch)).fetchone()
                    else:
                        prior = self.__leases.get(payload.get('registration_id'))
                        if prior is None or started >= prior[2]:
                            return {'ok': False, 'code': 'NOT_REGISTERED', 'result': {}}
                if operation in (PARENT_READ_V1, PARENT_PLAN_READ_V1):
                    lease = self.__leases.get(payload.get('registration_id'))
                    if lease is None or self.__now() >= lease[2]:
                        return {'ok': False, 'code': 'NOT_REGISTERED', 'result': {}}
                result = registry.handle(authenticated_principal, request)
                if operation == 'service.register' and result.get('ok'):
                    entry = result['result']
                    registration_id = entry['registration_id']
                    old = self.__leases.get(registration_id)
                    if old is None:
                        if prior is not None and prior['registration_id'] == registration_id:
                            return {'ok': False, 'code': 'NOT_REGISTERED', 'result': {}}
                        deadline = started + registry.lease_seconds
                        if self.__now() >= deadline:
                            return {'ok': False, 'code': 'NOT_REGISTERED', 'result': {}}
                        self.__leases[registration_id] = (registry._epoch, entry['expires_at'], deadline)
                    elif self.__now() >= old[2] or old[1] != entry['expires_at']:
                        return {'ok': False, 'code': 'NOT_REGISTERED', 'result': {}}
                elif operation == 'service.renew' and result.get('ok'):
                    entry = result['result']
                    deadline = started + registry.lease_seconds
                    if self.__now() >= prior[2] or self.__now() >= deadline:
                        self.__leases.pop(payload.get('registration_id'), None)
                        return {'ok': False, 'code': 'NOT_REGISTERED', 'result': {}}
                    self.__leases[payload['registration_id']] = (registry._epoch, entry['expires_at'], deadline)
                elif operation == 'service.unregister' and result.get('ok'):
                    self.__leases.pop(payload.get('registration_id'), None)
                return result
        except Exception:
            return {'ok': False, 'code': 'UNAVAILABLE', 'result': {}}

    def close(self):
        """Close on the owner thread; a failed reader close retains its Host hold."""
        self.__owner()
        with self.__gate:
            if self.__closed:
                return
            self.__closing = True
            if self.__registry is not None:
                self.__registry.close()
                self.__registry = None
            if self.__registry_open_pending:
                raise ExecutionManagementReadServiceError('REGISTRY_OPEN_UNCERTAIN')
            if self.__reader is not None:
                self.__reader.close()
                self.__reader = None
            if self.__audit is not None:
                self.__audit.close()
                self.__audit = None
            self.__closed = True
            _UNCERTAIN_CONSTRUCTIONS[:] = [item for item in _UNCERTAIN_CONSTRUCTIONS if item is not self]


_UNCERTAIN_CONSTRUCTIONS: list[ExecutionManagementReadService] = []
