"""Neutral provider-session revocation evidence (iteration 60, step 60.10, G1).

An account handoff may grant a new owner only after the old owner's provider
session is proven unable to write. That proof comes from the provider side,
not from local storage: a logout receipt, a provider-issued session generation
that superseded the old session, or an exchange-side session revocation.

This module defines only the neutral shape of that evidence and the verifier
protocol that a deployment supplies. It performs no provider I/O. A verifier
must check the evidence against a trusted provider source; tests use fakes.
The evidence carries opaque references only, never credentials or raw provider
payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = [
    "REVOCATION_EVIDENCE_KINDS",
    "ProviderSessionRevocationEvidence",
    "ProviderSessionRevocationVerifier",
    "RevocationEvidenceError",
]

#: Accepted evidence kinds. A storage lease, timeout, or local flag is never
#: provider-session revocation evidence (plan INV-08).
REVOCATION_EVIDENCE_KINDS = frozenset(
    {
        "PROVIDER_LOGOUT_RECEIPT",
        "PROVIDER_SESSION_GENERATION_ADVANCED",
        "EXCHANGE_SESSION_REVOKED",
    }
)


class RevocationEvidenceError(ValueError):
    """Finite, value-free evidence construction failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _text(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


@dataclass(frozen=True)
class ProviderSessionRevocationEvidence:
    """Provider-side evidence that one owner generation's session is revoked.

    ``revoked_session_ref`` is the opaque session reference the old owner
    registered when it took the host owner lock; ``evidence_ref`` is an opaque
    receipt reference (for example a logout response id). ``observed_at_ns``
    is when the provider-side fact was observed; a handoff refuses evidence
    observed before its QUIESCE.
    """

    account_ref: str
    provider_route: str
    revoked_owner_generation: int
    revoked_session_ref: str
    kind: str
    observed_at_ns: int
    evidence_ref: str

    def __post_init__(self) -> None:
        if self.kind not in REVOCATION_EVIDENCE_KINDS:
            raise RevocationEvidenceError("REVOCATION_EVIDENCE_KIND_INVALID")
        for value in (
            self.account_ref,
            self.provider_route,
            self.revoked_session_ref,
            self.evidence_ref,
        ):
            if not _text(value):
                raise RevocationEvidenceError("REVOCATION_EVIDENCE_REFERENCE_INVALID")
        generation = self.revoked_owner_generation
        if type(generation) is not int or generation < 1:
            raise RevocationEvidenceError("REVOCATION_EVIDENCE_GENERATION_INVALID")
        if type(self.observed_at_ns) is not int or self.observed_at_ns <= 0:
            raise RevocationEvidenceError("REVOCATION_EVIDENCE_TIME_INVALID")


@runtime_checkable
class ProviderSessionRevocationVerifier(Protocol):
    """Deployment-supplied check against a trusted provider source.

    Return ``True`` only when the provider confirms that the referenced
    session can no longer place or cancel orders for the account. Any other
    result, or an exception, is treated as not confirmed.
    """

    def verify_session_revocation(self, evidence: ProviderSessionRevocationEvidence) -> bool:
        """Return whether the provider confirms the session revocation."""
