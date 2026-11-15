"""Operator-provided Registry grant for the new Execution parent port.

This grant is installed by trusted local composition, never decoded from a
client request.  It is not a provider/account fact or an Execution service.
"""
from dataclasses import dataclass

from bt_api_base.market_protocol import MarketInstrumentKey
from bt_api_base.service_protocol import WireIdentity, WireRole

PORT = "service.execution_parent"
SUBMIT = "execution.parent.submit"
QUERY = "execution.parent.query"
OPERATIONS = frozenset({SUBMIT, QUERY})


def _ref(value):
    try:
        return (type(value) is str and bool(value) and value == value.strip()
                and value.isprintable() and len(value.encode('utf-8', 'strict')) <= 128)
    except UnicodeError:
        return False


@dataclass(frozen=True, slots=True)
class ExecutionParentGrant:
    instance_id: str
    scope_ref: str
    identity: WireIdentity
    account_ref: str
    strategy_ref: str
    execution_domain_ref: str
    revision: str
    instruments: frozenset[MarketInstrumentKey]
    operations: frozenset[str]

    def __post_init__(self):
        if (any(not _ref(v) for v in (self.instance_id, self.scope_ref, self.account_ref,
                                      self.strategy_ref, self.execution_domain_ref, self.revision))
                or type(self.identity) is not WireIdentity
                or self.identity.role is not WireRole.STRATEGY_CLIENT
                or self.identity.delegate_ref is not None
                or type(self.instruments) is not frozenset or not self.instruments
                or any(type(i) is not MarketInstrumentKey for i in self.instruments)
                or type(self.operations) is not frozenset or not self.operations
                or not self.operations <= OPERATIONS):
            raise ValueError('INVALID_EXECUTION_PARENT_GRANT')

    def allows_instrument(self, instrument):
        """The service must call this for submit *and* the retrieved query row."""
        return type(instrument) is MarketInstrumentKey and instrument in self.instruments
