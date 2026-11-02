"""SDK fee-pair journal projection with an injected facts-type owner.

The facts owner supplies the exact type; the provider authority remains an
explicit SDK capability. A missing fee projection is not execution evidence.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional, Tuple

from .execution import (
    ExecutionJournalError,
    ExecutionJournalProviderAuthority,
    JOURNAL_PORT_SCHEMA_VERSION,
    JournalFeePairFactsPort,
    neutral_query_set_identifier as _neutral_query_set_identifier,
    provider_fee_pair_query_set_identifier,
    validate_provider_fee_pair_source_lease,
)


class FeePairFactsJournalPort(JournalFeePairFactsPort):
    """Validate caller-selected fact types and delegate provider evidence checks."""

    port_version = JOURNAL_PORT_SCHEMA_VERSION

    def __init__(
        self, provider_authority: Optional[ExecutionJournalProviderAuthority] = None,
        *, facts_module_loader: Callable[[], Any],
    ) -> None:
        self._provider_authority = provider_authority
        self._facts_module_loader = facts_module_loader

    @property
    def unavailable_capabilities(self) -> Tuple[str, ...]:
        """Exact neutral inputs missing from the current four-family query schema."""

        return (
            "normalized provider trade/fill records and client-order correlation",
            "neutral commission/fee-rate records with currency, unit, scope, and validity",
            "route eligibility and open-risk fee projection for each target instrument",
            "fee-pair source lease bound to operation-specific query families",
        )

    def is_factor_execution_facts(self, facts: Any) -> bool:
        try:
            module = self._facts_module_loader()
            facts_class = module.FactorExecutionFacts
        except ImportError as error:  # pragma: no cover - package integrity guard
            raise ExecutionJournalError("FEE_PAIR_FACTS_UNAVAILABLE") from error
        if type(facts) is not facts_class:
            return False
        # The neutral QueryFacade facts do not contain the old journal's fee
        # policy projection.  Keep the SDK port registered, but refuse to label
        # account/order snapshots as fee-pair evidence.
        required = (
            "account_scope",
            "strategy_scope",
            "commission_required_for_open",
            "open_risk_ready",
            "open_risk_blockers",
            "account",
            "available_at",
            "route_eligibilities",
            "instruments",
            "positions",
            "quotes",
            "query_provenance",
            "working_orders",
            "execution_intents",
            "_authorized_target_id_set",
            "_full_target_ids",
        )
        try:
            return all(getattr(facts, name, None) is not None for name in required)
        except Exception:
            return False

    def query_set_identifier(
        self,
        account_fingerprint: str,
        generation: int,
        trading_day: str,
        started_at: Any,
        completed_at: Any,
        query_ids: Tuple[Tuple[str, int], ...],
    ) -> str:
        """Retain the SDK-owned deterministic identifier for the legacy journal port."""

        return provider_fee_pair_query_set_identifier(
            self._provider_authority,
            account_fingerprint,
            generation,
            trading_day,
            started_at,
            completed_at,
            query_ids,
        )

    def validate_source_lease(self, lease: Any, facts: Any, *, operation: str) -> bool:
        """Revalidate only a complete fee projection; neutral snapshots alone fail closed."""

        if not self.is_factor_execution_facts(facts):
            return False
        return validate_provider_fee_pair_source_lease(
            self._provider_authority, lease, facts, operation=operation
        )

    def neutral_query_set_identifier(self, receipt: Any) -> str:
        """Return a neutral QueryBatchReceipt digest for the next SDK port version."""

        return _neutral_query_set_identifier(receipt)


__all__ = ("FeePairFactsJournalPort",)
