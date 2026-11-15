"""Exact, read-only proof for replaying one already received parent request.

A proof is produced only by a composition-installed prover (for the fresh
submission intake, ``bt_api_execution.submission_intake``). It says one thing:
the exact namespace/request_id pair already has a local receipt whose request
bytes and digest are identical, and the *current* operator grant still covers
that request's instrument. It is not a grant, a lease, a wire flag, or
permission to insert a new row. Without a prover installed the Registry keeps
refusing a stale write lease exactly as before.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from bt_api_base.service_protocol import WireIdentity, WireRole, WireScope

from .execution_parent_grant import ExecutionParentGrant

_HEX_DIGITS = frozenset('0123456789abcdef')


def execution_parent_replay_namespace(grant: object) -> str | None:
    """Return the intake-local namespace of the exact grant, or ``None``.

    This mirrors ``FreshSubmissionIntake`` and is deliberately derived only
    from an exact, already-installed grant; it never takes a wire namespace.
    """

    if type(grant) is not ExecutionParentGrant:
        return None
    try:
        # Imported lazily: Execution is an optional package at SDK import time.
        from bt_api_execution.contracts import canonical_json
    except Exception:
        return None
    try:
        return canonical_json({
            'domain': grant.execution_domain_ref,
            'account': grant.account_ref,
            'strategy': grant.strategy_ref,
        })
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class ExecutionParentReplayProof:
    """One exact existing-receipt proof; construction is never a wire path."""

    registration_id: str
    service_epoch: str
    instance_id: str
    principal: str
    identity: WireIdentity
    scope: WireScope
    grant: ExecutionParentGrant = field(repr=False, compare=False)
    generation: int
    namespace: str
    request_id: str
    request_bytes: bytes = field(repr=False)
    request_sha256: str

    def __post_init__(self) -> None:
        if (type(self.registration_id) is not str or not self.registration_id
                or type(self.service_epoch) is not str or not self.service_epoch
                or type(self.instance_id) is not str or not self.instance_id
                or type(self.principal) is not str or not self.principal
                or type(self.identity) is not WireIdentity
                or self.identity.role is not WireRole.STRATEGY_CLIENT
                or self.identity.delegate_ref is not None
                or type(self.scope) is not WireScope
                or type(self.grant) is not ExecutionParentGrant
                or self.grant.identity != self.identity
                or self.grant.instance_id != self.instance_id
                or self.principal != self.identity.principal_ref
                or self.scope.account_ref != self.grant.account_ref
                or self.scope.strategy_ref != self.grant.strategy_ref
                or self.scope.execution_domain_ref != self.grant.execution_domain_ref
                or type(self.generation) is not int or self.generation < 0
                or type(self.namespace) is not str or not self.namespace
                or type(self.request_id) is not str or not self.request_id
                or type(self.request_bytes) is not bytes or not self.request_bytes
                or type(self.request_sha256) is not str or len(self.request_sha256) != 64
                or any(character not in _HEX_DIGITS for character in self.request_sha256)
                or hashlib.sha256(self.request_bytes).hexdigest() != self.request_sha256
                or self.namespace != execution_parent_replay_namespace(self.grant)):
            raise ValueError('INVALID_EXECUTION_PARENT_REPLAY_PROOF')
