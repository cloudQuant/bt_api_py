"""Local operator authority for Execution actions, independent of client grants.

These values are supplied by trusted composition, not by wire deserialization.
They do not establish account, Gateway, Risk or cross-process authority.
"""
from dataclasses import dataclass

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole

# These names are explicit: no action implies another action, including reads,
# cancellation and reconciliation. Names used by ParentAuthority are retained.
ACTIONS = frozenset({
    'accept', 'register', 'owner_dispatch', 'child', 'rollover',
    'dispatch_child', 'cancel', 'query', 'reconcile', 'release_proof', 'release_child',
    'child_observation', 'reserve_parent',
})


def _ref(value):
    try:
        return (type(value) is str and bool(value) and value == value.strip()
                and value.isprintable() and len(value.encode('utf-8', 'strict')) <= 128)
    except UnicodeError:
        return False


def _identity(value):
    return (type(value) is WireIdentity and value.role is WireRole.SERVICE
            and value.delegate_ref is None)


@dataclass(frozen=True, slots=True)
class ExecutionActionGrant:
    """One exact service identity and account/strategy/domain policy."""
    instance_id: str
    identity: WireIdentity
    account_ref: str
    strategy_ref: str
    execution_domain_ref: str
    revision: str
    instruments: frozenset[MarketInstrumentKey]
    plan_versions: frozenset[str]
    actions: frozenset[str]

    def __post_init__(self):
        if (not all(_ref(v) for v in (self.instance_id, self.account_ref, self.strategy_ref,
                                      self.execution_domain_ref, self.revision))
                or not _identity(self.identity)
                or type(self.instruments) is not frozenset or not self.instruments
                or any(type(v) is not MarketInstrumentKey for v in self.instruments)
                or type(self.plan_versions) is not frozenset or not self.plan_versions
                or any(not _ref(v) for v in self.plan_versions)
                or type(self.actions) is not frozenset or not self.actions
                or not self.actions <= ACTIONS):
            raise ValueError('INVALID_EXECUTION_ACTION_GRANT')


@dataclass(frozen=True, slots=True)
class ExecutionActionRequest:
    """Exact facts to bind to a local action; these alone confer no authority."""
    instance_id: str
    identity: WireIdentity
    account_ref: str
    strategy_ref: str
    execution_domain_ref: str
    grant_revision: str
    instrument: MarketInstrumentKey
    plan_version: str
    action: str

    def __post_init__(self):
        if (not all(_ref(v) for v in (self.instance_id, self.account_ref, self.strategy_ref,
                                      self.execution_domain_ref, self.grant_revision,
                                      self.plan_version))
                or not _identity(self.identity)
                or type(self.instrument) is not MarketInstrumentKey
                or type(self.action) is not str or self.action not in ACTIONS):
            raise ValueError('INVALID_EXECUTION_ACTION_REQUEST')


def grant_key(value):
    return (value.instance_id, value.identity.principal_ref, value.account_ref,
            value.strategy_ref, value.execution_domain_ref)


def allows(grant, request):
    return (type(grant) is ExecutionActionGrant and type(request) is ExecutionActionRequest
            and grant_key(grant) == grant_key(request) and grant.identity == request.identity
            and grant.revision == request.grant_revision and request.instrument in grant.instruments
            and request.plan_version in grant.plan_versions and request.action in grant.actions)
