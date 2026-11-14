"""Explicit trusted Gateway ownership classification; no routing authority."""
from dataclasses import dataclass, field
from types import MappingProxyType
from collections.abc import Mapping

from bt_api_base.service_protocol import WireScope


@dataclass(frozen=True, slots=True)
class GatewayExecutionSubjectScopes:
    protected_accounts: frozenset[str]
    aliases: Mapping

    def __post_init__(self):
        from bt_api_execution.contracts import ExecutionScope
        if (type(self.protected_accounts) is not frozenset or not self.protected_accounts
                or any(type(a) is not str or not a or a != a.strip() for a in self.protected_accounts)
                or not isinstance(self.aliases, Mapping) or not self.aliases):
            raise ValueError('INVALID_EXECUTION_SUBJECT_SCOPES')
        aliases = dict(self.aliases)
        for wire, target in aliases.items():
            if (type(wire) is not WireScope or wire.account_ref not in self.protected_accounts
                    or not wire.strategy_ref or not wire.execution_domain_ref or wire.run_id is not None
                    or wire.account_refs or wire.manual_book_ref is not None or wire.portfolio_ref is not None
                    or wire.group_id is not None or wire.leg_id is not None
                    or (target is not None and (type(target) is not ExecutionScope or target.trading_day is not None))):
                raise ValueError('INVALID_EXECUTION_SUBJECT_SCOPES')
        if {wire.account_ref for wire in aliases} != set(self.protected_accounts):
            raise ValueError('INVALID_EXECUTION_SUBJECT_SCOPES')
        object.__setattr__(self, 'aliases', MappingProxyType(aliases))


@dataclass(frozen=True, slots=True)
class GatewayExecutionSubjectBinding:
    declaration: GatewayExecutionSubjectScopes
    journal: object = field(repr=False, compare=False)

    def __post_init__(self):
        from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
        if (type(self.declaration) is not GatewayExecutionSubjectScopes
                or type(self.journal) is not GatewayDirectCommandJournal
                or self.declaration.protected_accounts != frozenset({self.journal.account_scope})):
            raise ValueError('INVALID_EXECUTION_SUBJECT_BINDING')
