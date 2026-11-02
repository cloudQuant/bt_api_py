"""Public provider-neutral execution-journal integration facade.

This module is the supported boundary for framework packages that need typed
journal ports and journal construction.  It keeps the execution package's
current port wiring behind one SDK-owned entry point and accepts provider
authority as an injected capability.  It never discovers or imports a venue.
The execution contracts and store primitives exported here are aliases to
their canonical ``bt_api_execution`` classes; this module does not wrap them.

The execution package currently has a legacy provider-attestation slot.  Its
mapping is intentionally private here so application frameworks do not bind to
that slot name or to its historical provider-specific vocabulary.  Replacing
that backend contract with a fully generic schema is a separate SDK migration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol, Tuple

from bt_api_py.managed_execution import (
    ExecutionManagedProviderPort,
    ManagedExecutionFacade,
    ManagedExecutionOperation,
    ManagedExecutionOutcomeUnknown,
    ManagedExecutionUnavailable,
)
from bt_api_execution import (
    CancelDispatchClaimReceiptV1,
    ExecutionScope,
    SqliteExecutionStore,
)
from bt_api_execution.journal import (
    ExecutionDecisionJournal as _ExecutionDecisionJournal,
    ExecutionDecisionKey,
    ExecutionDecisionJournalError as ExecutionJournalError,
    configure_journal_ports as _configure_journal_ports,
)
from bt_api_execution.journal_catalog import is_execution_journal_catalog_object
from bt_api_execution.journal_ports import (
    JOURNAL_POSITION_ATTRIBUTION_PORT_SCHEMA_VERSION,
    JOURNAL_PORT_SCHEMA_VERSION,
    JournalFeePairFactsPort,
    JournalPorts as _SdkJournalPorts,
    JournalPositionAttributionPort,
    JournalPositionCapabilityUnavailable,
    JournalPositionCapacityRow,
    JournalPositionCurrent,
    JournalPositionFill,
    JournalPositionGridIdentity,
    JournalPositionIssue,
    JournalRegisteredSimAttestationPort as _ProviderAuthorityPort,
)

__all__ = [
    "CancelDispatchClaimReceiptV1",
    "ExecutionScope",
    "ExecutionDecisionKey",
    "ExecutionJournalError",
    "ExecutionJournalPorts",
    "ExecutionJournalProviderAuthority",
    "ExecutionManagedProviderPort",
    "ManagedExecutionFacade",
    "ManagedExecutionOperation",
    "ManagedExecutionOutcomeUnknown",
    "ManagedExecutionUnavailable",
    "JOURNAL_POSITION_ATTRIBUTION_PORT_SCHEMA_VERSION",
    "JOURNAL_PORT_SCHEMA_VERSION",
    "JournalFeePairFactsPort",
    "JournalPositionAttributionPort",
    "JournalPositionCapabilityUnavailable",
    "JournalPositionCapacityRow",
    "JournalPositionCurrent",
    "JournalPositionFill",
    "JournalPositionGridIdentity",
    "JournalPositionIssue",
    "is_execution_journal_catalog_object",
    "SqliteExecutionStore",
    "bind_execution_journal_ports",
    "build_execution_journal_ports",
    "configure_execution_journal_ports",
    "new_execution_journal",
    "neutral_query_set_identifier",
    "provider_fee_pair_query_set_identifier",
    "validate_provider_fee_pair_source_lease",
]












class ExecutionJournalProviderAuthority(Protocol):
    """Provider-owned facts and authorization supplied by SDK composition.

    Implementations are selected and bound by the SDK's unified provider
    facade.  Backtrader receives this capability as an opaque object and does
    not select a provider implementation itself.
    """

    def query_set_identifier(
        self,
        account_fingerprint: str,
        generation: int,
        trading_day: str,
        started_at: datetime,
        completed_at: datetime,
        query_ids: Tuple[Tuple[str, int], ...],
    ) -> str:
        """Return the deterministic identifier for one complete source query set."""

    def validate_source_lease(self, lease: Any, facts: Any, *, operation: str) -> bool:
        """Return whether the exact provider source lease remains current."""

    def attest_order_identity_query(self, receipt: Any, *, source: Any) -> Any:
        """Return an SDK-accepted identity projection for the supplied evidence."""


def _require_provider_authority(authority: Any) -> ExecutionJournalProviderAuthority:
    required = (
        "query_set_identifier",
        "validate_source_lease",
        "attest_order_identity_query",
    )
    if authority is None or any(not callable(getattr(authority, name, None)) for name in required):
        raise ExecutionJournalError("EXECUTION_PROVIDER_AUTHORITY_INVALID")
    return authority


class _ProviderAuthorityAdapter(_ProviderAuthorityPort):
    """Private adapter from the SDK-neutral provider capability to the journal."""

    port_version = JOURNAL_PORT_SCHEMA_VERSION

    def __init__(self, authority: ExecutionJournalProviderAuthority) -> None:
        self._authority = authority

    def attest_order_identity_query(self, receipt: Any, *, source_store: Any) -> Any:
        try:
            return self._authority.attest_order_identity_query(receipt, source=source_store)
        except ExecutionJournalError:
            raise
        except Exception as error:  # noqa: BLE001 - keep provider detail behind the SDK boundary
            raise ExecutionJournalError("EXECUTION_PROVIDER_AUTHORITY_REJECTED") from error


@dataclass(frozen=True)
class ExecutionJournalPorts:
    """Neutral, immutable port set accepted by this public facade.

    ``provider_authority`` is injected by SDK composition.  When it is absent,
    provider-authorized journal operations remain unavailable and fail closed;
    the facade never picks a default provider.
    """

    position_attribution: Optional[JournalPositionAttributionPort] = None
    fee_pair_facts: Optional[JournalFeePairFactsPort] = None
    provider_authority: Optional[ExecutionJournalProviderAuthority] = None
    _sdk_ports: _SdkJournalPorts = field(init=False, repr=False, compare=False)
    _provider_port: Optional[_ProviderAuthorityAdapter] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        provider_port = None
        if self.provider_authority is not None:
            authority = _require_provider_authority(self.provider_authority)
            provider_port = _ProviderAuthorityAdapter(authority)
        sdk_ports = _SdkJournalPorts(
            position_attribution=self.position_attribution,
            fee_pair_facts=self.fee_pair_facts,
            registered_sim_attestation=provider_port,
        )
        object.__setattr__(self, "_provider_port", provider_port)
        object.__setattr__(self, "_sdk_ports", sdk_ports)


def build_execution_journal_ports(
    *,
    position_attribution: Optional[JournalPositionAttributionPort] = None,
    fee_pair_facts: Optional[JournalFeePairFactsPort] = None,
    provider_authority: Optional[ExecutionJournalProviderAuthority] = None,
) -> ExecutionJournalPorts:
    """Build one validated port set without selecting a provider."""

    return ExecutionJournalPorts(
        position_attribution=position_attribution,
        fee_pair_facts=fee_pair_facts,
        provider_authority=provider_authority,
    )


def _require_ports(ports: Any) -> ExecutionJournalPorts:
    if type(ports) is not ExecutionJournalPorts:
        raise ExecutionJournalError("EXECUTION_JOURNAL_PORT_SET_INVALID")
    return ports


def configure_execution_journal_ports(ports: ExecutionJournalPorts) -> None:
    """Register a facade-built port set for journals using process defaults.

    The execution journal permits identical repeated registration but rejects a
    different instance for an occupied slot.  This function preserves that
    fail-closed behavior and does not replace an existing provider authority.
    """

    selected = _require_ports(ports)
    sdk_ports = selected._sdk_ports
    _configure_journal_ports(
        position_attribution=sdk_ports.position_attribution,
        fee_pair_facts=sdk_ports.fee_pair_facts,
        registered_sim_attestation=sdk_ports.registered_sim_attestation,
    )


def bind_execution_journal_ports(
    journal: Any, ports: ExecutionJournalPorts
) -> ExecutionJournalPorts:
    """Require *journal* to hold this exact facade-built port set."""

    selected = _require_ports(ports)
    current = getattr(journal, "_ports", None)
    if type(current) is not _SdkJournalPorts:
        raise ExecutionJournalError("EXECUTION_JOURNAL_PORT_SET_INVALID")
    for name in (
        "position_attribution",
        "fee_pair_facts",
        "registered_sim_attestation",
    ):
        if getattr(current, name) is not getattr(selected._sdk_ports, name):
            raise ExecutionJournalError("EXECUTION_JOURNAL_PORT_SET_FOREIGN:" + name)
    return selected


def new_execution_journal(
    database_path: Any,
    *,
    ports: ExecutionJournalPorts,
    **options: Any,
) -> Any:
    """Construct an execution journal with an explicit facade-built port set."""

    selected = _require_ports(ports)
    if "journal_ports" in options:
        raise ExecutionJournalError("EXECUTION_JOURNAL_PORTS_ALREADY_SUPPLIED")
    return _ExecutionDecisionJournal(
        database_path, journal_ports=selected._sdk_ports, **options
    )


def neutral_query_set_identifier(receipt: Any) -> str:
    """Return the digest of a complete neutral query receipt, without inferring authority."""
    try:
        from bt_api_base.query_protocol import QueryBatchReceipt
    except Exception as error:
        raise ExecutionJournalError("NEUTRAL_QUERY_RECEIPT_SCHEMA_UNAVAILABLE") from error
    if type(receipt) is not QueryBatchReceipt or receipt.complete is not True:
        raise ExecutionJournalError("NEUTRAL_QUERY_RECEIPT_REQUIRED")
    return receipt.receipt_digest


def provider_fee_pair_query_set_identifier(
    authority: Optional[ExecutionJournalProviderAuthority],
    account_fingerprint: str,
    generation: int,
    trading_day: str,
    started_at: datetime,
    completed_at: datetime,
    query_ids: Tuple[Tuple[str, int], ...],
) -> str:
    """Ask the injected provider authority for its deterministic query-set ID."""

    selected = _require_provider_authority(authority)
    try:
        result = selected.query_set_identifier(
            account_fingerprint,
            generation,
            trading_day,
            started_at,
            completed_at,
            query_ids,
        )
    except ExecutionJournalError:
        raise
    except Exception as error:  # noqa: BLE001 - do not expose provider internals here
        raise ExecutionJournalError("FEE_PAIR_FACTS_UNAVAILABLE") from error
    if type(result) is not str or not result:
        raise ExecutionJournalError("FEE_PAIR_QUERY_SET_IDENTIFIER_INVALID")
    return result


def validate_provider_fee_pair_source_lease(
    authority: Optional[ExecutionJournalProviderAuthority],
    lease: Any,
    facts: Any,
    *,
    operation: str,
) -> bool:
    """Validate provider source currency; missing or failing authority rejects."""

    if authority is None:
        return False
    try:
        selected = _require_provider_authority(authority)
        return selected.validate_source_lease(lease, facts, operation=operation) is True
    except Exception:  # noqa: BLE001 - source mismatch is a closed rejection
        return False
