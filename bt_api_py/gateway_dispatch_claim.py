"""Gateway-specific identities atop the Base owner-local claim fence.

Normal construction is fail-closed. The test constructor is synthetic only;
source coverage, authorization, durable markers and recovery remain external.
"""
from __future__ import annotations

import os as os
import re
from dataclasses import dataclass

from bt_api_base.claim_fence import (
    ClaimFence,
    ClaimSchema,
    DispatchClaim as DispatchClaim,
    DispatchClaimError,
    _Candidate,
    _Change as _Change,
    _Snapshot,
)

_DOMAINS = ("registry", "market", "parent_facts", "control", "session_source", "risk")
_HEX = re.compile(r"[0-9a-f]{64}\Z")


def _text(value: object) -> bool:
    return type(value) is str and 0 < len(value) <= 512 and value == value.strip() and value.isprintable()


@dataclass(frozen=True, slots=True)
class DispatchCommandBinding:
    """Exact locally prepared command identity; values are not authority proof."""

    account: str
    owner_generation: str
    journal_id: str
    strategy: str
    principal: str
    run_id: str
    request_key: str
    reservation_id: str
    payload_digest: str
    native_identity_digest: str
    marker_ref: str
    parent_ref: str | None = None
    child_id: str | None = None

    def __post_init__(self) -> None:
        if (not all(_text(value) for value in (
                self.account, self.owner_generation, self.journal_id, self.strategy,
                self.principal, self.run_id, self.request_key, self.reservation_id,
                self.marker_ref))
                or type(self.payload_digest) is not str or _HEX.fullmatch(self.payload_digest) is None
                or type(self.native_identity_digest) is not str
                or _HEX.fullmatch(self.native_identity_digest) is None
                or (self.parent_ref is None) != (self.child_id is None)
                or (self.parent_ref is not None and
                    (not _text(self.parent_ref) or not _text(self.child_id)))):
            raise DispatchClaimError("INVALID_COMMAND_BINDING")

    def _values(self) -> tuple:
        # Exact primitive values are copied before entering the coordinator lock.
        return (self.account, self.owner_generation, self.journal_id, self.strategy,
                self.principal, self.run_id, self.request_key, self.reservation_id,
                self.payload_digest, self.native_identity_digest, self.marker_ref,
                self.parent_ref, self.child_id)


_SCHEMA = ClaimSchema(
    ("text", "text", "text", "text", "text", "text", "text", "text",
     "hex256", "hex256", "text", "optional_text", "optional_text"),
    account_index=0, owner_index=1, key_indices=(0, 3, 6), pairs=((11, 12),),
)


class GatewayDispatchClaimCoordinator:
    """Gateway identity adapter around one exact, final Base core."""

    def __init__(self, account: str, owner_generation: str, *, max_commands: int = 4096,
                 max_snapshots: int = 4096, max_validity_ns: int = 2_000_000_000) -> None:
        self._core = ClaimFence(account, owner_generation, domains=_DOMAINS, schema=_SCHEMA,
                                optional_domains=("risk",), max_commands=max_commands,
                                max_snapshots=max_snapshots, max_validity_ns=max_validity_ns)

    @classmethod
    def _for_test(cls, account: str, owner_generation: str, *, max_commands: int = 4096,
                  max_snapshots: int = 4096, max_validity_ns: int = 2_000_000_000):
        instance = cls.__new__(cls)
        instance._core = ClaimFence._for_test(account, owner_generation, domains=_DOMAINS,
            schema=_SCHEMA, optional_domains=("risk",), max_commands=max_commands,
            max_snapshots=max_snapshots, max_validity_ns=max_validity_ns)
        return instance

    @property
    def _pid(self):
        return self._core._pid

    @property
    def _lock(self):
        return self._core._lock

    @property
    def _domains(self):
        return self._core._domains

    @property
    def _test_only(self):
        return self._core._test_only

    def matches_owner(self, account, owner_generation):
        return self._core.matches_owner(account, owner_generation)

    def begin_change(self, domain):
        return self._core.begin_change(domain)

    def finish_change(self, token, status):
        return self._core.finish_change(token, status)

    def poison(self, token):
        return self._core.poison(token)

    def poison_domain(self, domain):
        return self._core.poison_domain(domain)

    def capture(self):
        return self._core.capture()

    def discard_snapshot(self, snapshot):
        return self._core.discard_snapshot(snapshot)

    def discard_pair(self, before, after):
        return self._core.discard_pair(before, after)

    def writer_handle(self, domain, *, account, owner_generation):
        return self._core.writer_handle(domain, account=account, owner_generation=owner_generation)

    def prepare(self, binding: DispatchCommandBinding, before: _Snapshot, after: _Snapshot,
                *, deadline_ns: int) -> _Candidate:
        """Consume tickets on validation failure; a retry must recapture."""
        ClaimFence._process(self._core)
        try:
            if (type(binding) is not DispatchCommandBinding or type(before) is not _Snapshot
                    or type(after) is not _Snapshot or type(deadline_ns) is not int):
                raise DispatchClaimError("INVALID_CANDIDATE")
            before_sequence, after_sequence = before.sequence, after.sequence
            if (type(before_sequence) is not int or type(after_sequence) is not int
                    or before._seal is not self._core._seal or after._seal is not self._core._seal):
                raise DispatchClaimError("INVALID_CANDIDATE")
            values = DispatchCommandBinding(*binding._values())._values()
            if values[0] != self._core._account or values[1] != self._core._generation:
                raise DispatchClaimError("OWNER_MISMATCH")
        except BaseException:
            self._core.discard_pair(before, after)
            raise
        return self._core._prepare_values(values, before, after, deadline_ns=deadline_ns)

    def try_claim(self, candidate):
        return self._core.try_claim(candidate)

    def capacity_status(self):
        return self._core.capacity_status()

    def close(self):
        return self._core.close()
