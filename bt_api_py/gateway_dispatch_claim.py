"""Gateway-specific identities atop the Base owner-local claim fence.

Normal construction is fail-closed. ``with_coverage_sealer`` returns the same
production coordinator plus the Base one-time sealer; only the trusted account
owner composition that attached every domain writer may seal it. The test
constructor is synthetic only; authorization, durable markers and recovery
remain external.
"""
from __future__ import annotations

import os as os
import re
from dataclasses import dataclass

from bt_api_base.claim_fence import (
    ClaimFence,
    ClaimSchema,
    CoverageSealer,
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
    def with_coverage_sealer(cls, account: str, owner_generation: str, *,
                             max_commands: int = 4096, max_snapshots: int = 4096,
                             max_validity_ns: int = 2_000_000_000):
        """Return ``(coordinator, sealer)``; keep the sealer in the composition root."""
        instance = cls.__new__(cls)
        instance._core, sealer = ClaimFence.with_sealer(
            account, owner_generation, domains=_DOMAINS, schema=_SCHEMA,
            optional_domains=("risk",), max_commands=max_commands,
            max_snapshots=max_snapshots, max_validity_ns=max_validity_ns)
        if type(sealer) is not CoverageSealer:
            raise DispatchClaimError("INVALID_SEALER")
        return instance, sealer

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

    def finish_change_current(self, token):
        """Finish READY once coverage is sealed, otherwise UNAVAILABLE.

        READY only means the domain has no change in progress; dispatch
        validation still reads the actual source state.
        """
        return self.finish_change(token, "READY" if self._core._sealed else "UNAVAILABLE")

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

    def attach_writer_handle(self, domain, *, account, owner_generation):
        return self._core.attach_writer_handle(domain, account=account,
                                               owner_generation=owner_generation)

    @property
    def sealed(self) -> bool:
        return self._core._sealed

    def coverage_status(self):
        return self._core.coverage_status()

    def retire(self, claim):
        return self._core.retire(claim)

    def abandon(self, candidate):
        return self._core.abandon(candidate)

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


_RETRYABLE_CLAIM_CODES = frozenset({"CANDIDATE_STALE", "DOMAIN_UNAVAILABLE"})


class GatewayNativeDispatchClaimPort:
    """Decision-B claim port used by the Gateway dispatcher on its owner thread.

    ``claim_native`` runs before the durable marker: it captures the version
    vector, runs the dispatcher's final authority/market/control validation
    (which takes Registry, market and journal locks only briefly), captures
    again, prepares and claims under the core's leaf lock. The returned claim
    means the original request is in flight; a revocation that began before
    the claim makes it stale. Stale or mid-change domains are retried within a
    short budget; afterwards ``DISPATCH_CLAIM_BUSY`` tells the owner to retry
    later instead of spinning here, because a covered writer may itself be
    waiting for this owner thread. ``native_check`` is a lock-free age check
    invoked by the provider under its own session lock right before the call.
    """

    def __init__(self, coordinator: GatewayDispatchClaimCoordinator, *, journal_id: str,
                 candidate_validity_ns: int = 250_000_000,
                 max_send_delay_ns: int = 2_000_000_000,
                 retry_budget_ns: int = 50_000_000, max_attempts: int = 16) -> None:
        if (type(coordinator) is not GatewayDispatchClaimCoordinator or not _text(journal_id)
                or any(type(v) is not int or v <= 0 for v in (
                    candidate_validity_ns, max_send_delay_ns, retry_budget_ns, max_attempts))
                or candidate_validity_ns > coordinator._core._max_validity_ns
                or max_attempts > 1000):
            raise DispatchClaimError("INVALID_CLAIM_PORT")
        self._coordinator = coordinator
        self._journal_id = journal_id
        self._validity_ns = candidate_validity_ns
        self._max_send_delay_ns = max_send_delay_ns
        self._retry_budget_ns = retry_budget_ns
        self._max_attempts = max_attempts

    def ready(self) -> bool:
        """Composition readiness only: sealed, open and unfaulted core."""
        core = self._coordinator._core
        return bool((core._sealed or core._test_only) and not core._closed and not core._faulted)

    def binding(self, record, identity) -> DispatchCommandBinding:
        import hashlib
        import json

        try:
            # Claims precede native preparation: bind the reservation itself.
            source = ("pre-native:" + record.reservation_id if identity is None
                      else json.dumps(identity, ensure_ascii=True, sort_keys=True,
                                      separators=(",", ":")))
            identity_digest = hashlib.sha256(source.encode("ascii")).hexdigest()
        except Exception:
            raise DispatchClaimError("INVALID_COMMAND_BINDING") from None
        return DispatchCommandBinding(
            record.account_scope, record.generation, self._journal_id, record.strategy_scope,
            record.principal, "gateway.direct", record.request_key, record.reservation_id,
            record.digest, identity_digest, record.reservation_id)

    def claim_native(self, record, identity, validate):
        import time as _time

        if not callable(validate):
            raise DispatchClaimError("INVALID_CANDIDATE")
        coordinator = self._coordinator
        binding = self.binding(record, identity)
        started = _time.monotonic_ns()
        last_code = "DISPATCH_CLAIM_STALE"
        for attempt in range(self._max_attempts):
            if attempt and _time.monotonic_ns() - started >= self._retry_budget_ns:
                break
            before = after = None
            try:
                before = coordinator.capture()
                validate(self._validity_ns)
                after = coordinator.capture()
            except DispatchClaimError as error:
                coordinator.discard_pair(before, after)
                if error.code not in _RETRYABLE_CLAIM_CODES:
                    raise
                last_code = error.code
                _time.sleep(min(0.001 * (attempt + 1), 0.01))
                continue
            except BaseException:
                coordinator.discard_pair(before, after)
                raise
            try:
                candidate = coordinator.prepare(
                    binding, before, after,
                    deadline_ns=_time.monotonic_ns() + self._validity_ns)
            except DispatchClaimError as error:
                if error.code not in _RETRYABLE_CLAIM_CODES:
                    raise
                last_code = error.code
                continue
            try:
                return coordinator.try_claim(candidate)
            except DispatchClaimError as error:
                coordinator.abandon(candidate)
                if error.code not in _RETRYABLE_CLAIM_CODES:
                    raise
                last_code = error.code
        raise DispatchClaimError("DISPATCH_CLAIM_BUSY")

    def native_check(self, claim):
        import time as _time

        if type(claim) is not DispatchClaim or claim._seal is not self._coordinator._core._seal:
            raise DispatchClaimError("INVALID_CLAIM")
        limit = claim.claimed_ns + self._max_send_delay_ns

        def check() -> bool:
            if _time.monotonic_ns() > limit:
                raise DispatchClaimError("DISPATCH_CLAIM_EXPIRED")
            return True

        return check

    def retire(self, claim) -> None:
        self._coordinator.retire(claim)


class GatewayJournalDispatchChangePort:
    """Journal change port fencing both journal-held dispatch domains.

    The Gateway command journal begins one change on its first main-database
    write in every transaction. Control causes and parent/child facts both
    live in that journal, so each write invalidates both ``parent_facts`` and
    ``control`` before COMMIT makes it visible. Writers outside this process
    or bypassing the journal object are not covered (deployment trust model).
    """

    _JOURNAL_DOMAINS = ("parent_facts", "control")

    def __init__(self, coordinator: GatewayDispatchClaimCoordinator) -> None:
        if type(coordinator) is not GatewayDispatchClaimCoordinator:
            raise DispatchClaimError("INVALID_CHANGE_PORT")
        self._coordinator = coordinator

    def matches_owner(self, account, owner_generation):
        return self._coordinator.matches_owner(account, owner_generation)

    def begin_change(self, domain):
        if domain != "parent_facts":
            raise DispatchClaimError("INVALID_DOMAIN")
        tokens = []
        try:
            for name in self._JOURNAL_DOMAINS:
                tokens.append(self._coordinator.begin_change(name))
        except BaseException:
            for token in tokens:
                try:
                    self._coordinator.poison(token)
                except Exception:
                    pass
            raise
        return tuple(tokens)

    def finish_change(self, token, _status):
        if type(token) is not tuple or len(token) != len(self._JOURNAL_DOMAINS):
            raise DispatchClaimError("INVALID_CHANGE")
        for inner in token:
            self._coordinator.finish_change_current(inner)

    def poison(self, token):
        if type(token) is not tuple:
            raise DispatchClaimError("INVALID_CHANGE")
        for inner in token:
            self._coordinator.poison(inner)

    def poison_domain(self, _domain):
        for name in self._JOURNAL_DOMAINS:
            self._coordinator.poison_domain(name)


GATEWAY_DISPATCH_COVERAGE = {
    "registry": ("service_registry.transactions", "service_registry.locked_mutations"),
    "market": ("gateway_market_dispatch_gate.market_change",),
    "parent_facts": ("direct_command_journal.authorizer",),
    "control": ("direct_command_journal.authorizer",),
    "session_source": ("ctp.trader.connection_change", "ctp.md_stream.source_change"),
}
# Present only when the composition runs a periodic clock refresher; a single
# startup calibration has no later writer.
GATEWAY_DISPATCH_OPTIONAL_COVERAGE = {
    "session_source": ("gateway_server_clock_refresh.sample_change",),
}


def seal_gateway_dispatch_coverage(coordinator: GatewayDispatchClaimCoordinator,
                                   sealer: CoverageSealer, *, wired: frozenset) -> None:
    """Seal after the trusted owner attached every listed writer, then publish READY.

    ``wired`` names the writer labels the composition actually attached; a
    missing mandatory label leaves the coordinator unsealed and dispatch
    refused. Settlement-state writers of the CTP trader are not hooked: the
    provider rechecks session, readiness and source under its own session
    lock immediately before the native call, so they cannot be bypassed.
    """
    if type(coordinator) is not GatewayDispatchClaimCoordinator or type(sealer) is not CoverageSealer:
        raise DispatchClaimError("INVALID_SEALER")
    required = {label for labels in GATEWAY_DISPATCH_COVERAGE.values() for label in labels}
    if type(wired) is not frozenset or not required <= wired:
        raise DispatchClaimError("COVERAGE_INCOMPLETE")
    coverage = {}
    for domain, labels in GATEWAY_DISPATCH_COVERAGE.items():
        optional = tuple(label for label in GATEWAY_DISPATCH_OPTIONAL_COVERAGE.get(domain, ())
                         if label in wired)
        coverage[domain] = labels + optional
    sealer.seal(coverage, not_selected=("risk",))
    for domain in GATEWAY_DISPATCH_COVERAGE:
        token = coordinator.begin_change(domain)
        try:
            coordinator.finish_change(token, "READY")
        except BaseException:
            coordinator.poison_domain(domain)
            raise
