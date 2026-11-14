"""Local policy values for an Execution management parent observation.

These values are supplied by trusted composition, never as proof from a wire
request. They do not authenticate the application, establish a registration or
lease, prove that a parent exists, or authorize a network reply. A future
service must independently bind and recheck those facts and audit the access.
"""
from dataclasses import dataclass

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import MAX_REFERENCE_BYTES, WireIdentity, WireRole, WireScope


# This local policy name does not register a service port or wire operation.
PARENT_READ_V1 = 'execution.management.parent.read.v1'
PARENT_PLAN_READ_V1 = 'execution.management.parent.plan.read.v1'
_OPERATIONS = frozenset({PARENT_READ_V1, PARENT_PLAN_READ_V1})
_MAX_INSTRUMENTS = 1024


def _reference(value):
    try:
        return (type(value) is str and bool(value) and value == value.strip()
                and value.isprintable() and len(value.encode('utf-8', 'strict')) <= MAX_REFERENCE_BYTES)
    except UnicodeError:
        return False


def _identity(value):
    return (type(value) is WireIdentity and value.role is WireRole.APPLICATION_CLIENT
            and value.delegate_ref is None)


def _scope(value):
    """One account and strategy, without a claimed current strategy run."""
    return (type(value) is WireScope and _reference(value.execution_domain_ref)
            and _reference(value.account_ref) and _reference(value.strategy_ref)
            and not value.account_refs and value.run_id is None
            and value.manual_book_ref is None and value.portfolio_ref is None
            and value.group_id is None and value.leg_id is None)


@dataclass(frozen=True, slots=True)
class ExecutionManagementReadGrant:
    """Trusted local policy for one application, service and single scope."""

    instance_id: str
    identity: WireIdentity
    scope: WireScope
    revision: str
    instruments: frozenset[MarketInstrumentKey]
    operations: frozenset[str]

    def __post_init__(self):
        if (not _reference(self.instance_id) or not _identity(self.identity)
                or not _scope(self.scope) or not _reference(self.revision)
                or type(self.instruments) is not frozenset or not self.instruments
                or len(self.instruments) > _MAX_INSTRUMENTS
                or any(type(item) is not MarketInstrumentKey for item in self.instruments)
                or type(self.operations) is not frozenset or not self.operations
                or any(type(operation) is not str for operation in self.operations)
                or not self.operations <= _OPERATIONS):
            raise ValueError('INVALID_EXECUTION_MANAGEMENT_READ_GRANT')


@dataclass(frozen=True, slots=True)
class ExecutionManagementReadRequest:
    """Request claims; query_id correlates an attempt and grants no permission."""

    instance_id: str
    identity: WireIdentity
    scope: WireScope
    grant_revision: str
    operation: str
    parent_ref: str
    query_id: str

    def __post_init__(self):
        if (not _reference(self.instance_id) or not _identity(self.identity)
                or not _scope(self.scope) or not _reference(self.grant_revision)
                or not _reference(self.operation) or not _reference(self.parent_ref)
                or not _reference(self.query_id)):
            raise ValueError('INVALID_EXECUTION_MANAGEMENT_READ_REQUEST')


def allows_request(grant, request):
    """Check local scope policy only; do not disclose whether a parent exists."""
    return (type(grant) is ExecutionManagementReadGrant
            and type(request) is ExecutionManagementReadRequest
            and grant.instance_id == request.instance_id and grant.identity == request.identity
            and grant.scope == request.scope and grant.revision == request.grant_revision
            and request.operation in grant.operations)


def allows_instrument(grant, request, instrument):
    """Check a snapshot's complete instrument after its local source is checked.

    A caller must perform both checks and enforce the same disclosure behavior
    for absent, corrupt and out-of-scope records. This is not a Registry fence.
    """
    return (allows_request(grant, request) and type(instrument) is MarketInstrumentKey
            and instrument in grant.instruments)


__all__ = ['PARENT_READ_V1', 'PARENT_PLAN_READ_V1', 'ExecutionManagementReadGrant',
           'ExecutionManagementReadRequest', 'allows_request', 'allows_instrument']
