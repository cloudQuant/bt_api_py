"""Experimental settlement claim; production authority is deliberately unavailable.

The trusted owner must bracket *every* change to write admission (including
revocation after grant consumption), SDK admission/source, provider session,
owner, observation and journal marker using these domains. The owner must persist
the original marker before the first capture, then validate the observation,
grant and existing marker outside the lock between the two captures.
Neither a DTO nor a READY publication proves any of that coverage.
An external operation that can change an authorization fact must begin an
active domain change before it starts. Faulting after an external exception
only fences claims once the core lock is actually acquired; claims before
that point are already in flight.

This in-memory claim is not durable and is not a venue receipt.  After restart,
the durable original marker must be recovered as UNKNOWN and only read back;
the original confirmation must never be sent again.  No production constructor
or configuration can enable claims until trusted source coverage is integrated.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime

from bt_api_base.claim_fence import ClaimFence
from bt_api_py.gateway_dispatch_claim import (
    DispatchClaimError,
    DispatchCommandBinding,
    GatewayDispatchClaimCoordinator,
)

_DOMAINS = {
    "write_admission": "registry",
    "sdk_source": "market",
    "provider_session": "parent_facts",
    "owner": "control",
    "observation": "session_source",
    # Settlement does not use the order risk domain; reuse its version slot
    # for marker/journal mutations, which must be fenced independently.
    "journal_marker": "risk",
}


def _valid_text(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= 256
            and value == value.strip() and value.isprintable())


@dataclass(frozen=True, slots=True)
class SettlementClaimBinding:
    """Identity of the original observation and persisted intent, not authority."""

    exchange_name: str
    account_binding: str
    trading_day: str
    observation_id: str
    session_generation: int
    owner_generation: str
    journal_id: str
    marker_id: str

    def __post_init__(self) -> None:
        if not all(_valid_text(value) for value in (
                self.exchange_name, self.account_binding, self.observation_id,
                self.owner_generation, self.journal_id, self.marker_id)):
            raise DispatchClaimError("INVALID_SETTLEMENT_BINDING")
        if (type(self.trading_day) is not str or len(self.trading_day) != 8
                or not self.trading_day.isascii() or not self.trading_day.isdigit()
                or type(self.session_generation) is not int or self.session_generation <= 0):
            raise DispatchClaimError("INVALID_SETTLEMENT_BINDING")
        try:
            datetime.strptime(self.trading_day, "%Y%m%d")
        except ValueError:
            raise DispatchClaimError("INVALID_SETTLEMENT_BINDING") from None

    def _values(self) -> tuple:
        return (self.exchange_name, self.account_binding, self.trading_day,
                self.observation_id, self.session_generation, self.owner_generation,
                self.journal_id, self.marker_id)


class GatewaySettlementClaimCoordinator:
    """One-account experimental fence; only ``_for_test`` can claim.

    Sources never execute while the claim lock is held.  An open change or
    inconsistent overlapping changes fail closed.  The exact account/day is
    reserved permanently in this process once prepared, even after invalidation;
    recovery and cross-process uniqueness require the external durable journal.
    """

    def __init__(self, account_binding: str, owner_generation: str, **limits):
        self._pid = os.getpid()
        self._state_lock = threading.Lock()
        self._failed = False
        self._inner = GatewayDispatchClaimCoordinator(account_binding, owner_generation, **limits)
        self._core = self._inner._core

    @classmethod
    def _for_test(cls, account_binding: str, owner_generation: str, **limits):
        """Synthetic model only; never use from a service or configuration."""
        instance = cls.__new__(cls)
        instance._pid = os.getpid()
        instance._state_lock = threading.Lock()
        instance._failed = False
        instance._inner = GatewayDispatchClaimCoordinator._for_test(
            account_binding, owner_generation, **limits
        )
        instance._core = instance._inner._core
        return instance

    def _process(self):
        # Check before touching any possibly inherited locked synchronization.
        if os.getpid() != self._pid:
            raise DispatchClaimError("COORDINATOR_PROCESS_CHANGED")

    def _check_locked(self):
        if self._failed:
            raise DispatchClaimError("COORDINATOR_FAILED")

    def _discard_pair(self, before, after):
        # Cleanup and setting the irreversible fault are serialized with claim:
        # a claim that won the lock first is already in flight.
        self._process()
        with self._state_lock:
            try:
                return ClaimFence.discard_pair(self._core, before, after)
            except BaseException:
                self._failed = True
                try:
                    ClaimFence.mark_failed(self._core)
                except BaseException:
                    # Lock acquisition/process failure is not proven fenced.
                    pass
                raise

    def _fault_core(self):
        self._process()
        with self._state_lock:
            self._failed = True
            ClaimFence.mark_failed(self._core)

    def _fail_closed(self, original: BaseException, *, cleanup_failed=True,
                     fault_marked=None) -> None:
        # The original error remains primary. Give its recipient a finite
        # diagnostic without including arbitrary exception text.
        closed = False
        try:
            self._process()
            self._inner.close()
            closed = True
        except BaseException:
            pass
        try:
            original.settlement_cleanup_failed = cleanup_failed
            original.settlement_coordinator_closed = closed
            if fault_marked is not None:
                original.settlement_core_fault_marked = fault_marked
            if hasattr(original, "add_note"):
                original.add_note("settlement coordinator fault; coordinator "
                                  + ("closed" if closed else "close failed"))
        except BaseException:
            pass

    def begin_change(self, domain: str):
        self._process()
        if type(domain) is not str or domain not in _DOMAINS:
            raise DispatchClaimError("INVALID_DOMAIN")
        with self._state_lock:
            self._check_locked()
            return self._inner.begin_change(_DOMAINS[domain])

    def finish_change(self, token, status: str) -> None:
        self._process()
        if type(status) is not str or status not in ("READY", "UNAVAILABLE"):
            raise DispatchClaimError("INVALID_CHANGE")
        with self._state_lock:
            self._check_locked()
            self._inner.finish_change(token, status)

    def poison(self, token) -> None:
        self._process()
        with self._state_lock:
            self._check_locked()
            self._inner.poison(token)

    def capture(self):
        self._process()
        with self._state_lock:
            self._check_locked()
            return self._inner.capture()

    def discard_snapshot(self, snapshot) -> None:
        self._process()
        with self._state_lock:
            self._check_locked()
            self._inner.discard_snapshot(snapshot)

    def discard_snapshots(self, before, after) -> tuple[bool, bool]:
        """Release exact owned tickets after external validation fails.

        Invalid, foreign or already-consumed tickets return False per slot.
        Callers handling another exception must preserve it if cleanup fails.
        """
        self._process()
        try:
            return self._discard_pair(before, after)
        except BaseException as original:
            self._fail_closed(original)
            raise

    def prepare(self, binding: SettlementClaimBinding, before, after, *, deadline_ns: int):
        self._process()
        try:
            if type(binding) is not SettlementClaimBinding:
                raise DispatchClaimError("INVALID_SETTLEMENT_BINDING")
            # Re-run validation and copy primitives before the shared short lock.
            values = SettlementClaimBinding(*binding._values())._values()
            exchange, account, day, observation, session, owner, journal, marker = values
            digest = hashlib.sha256(json.dumps(values, ensure_ascii=True, separators=(",", ":"))
                                    .encode("ascii")).hexdigest()
            command = DispatchCommandBinding(
                account=account, owner_generation=owner, journal_id=journal,
                strategy="settlement", principal=exchange, run_id=observation,
                request_key=day, reservation_id=str(session), payload_digest=digest,
                native_identity_digest=digest, marker_ref=marker,
            )
        except BaseException as original:
            # Core releases both authentic local tickets under one short lock.
            # Preserve the original failure even if cleanup itself fails.
            expected = (type(original) is DispatchClaimError
                        and original.code == "INVALID_SETTLEMENT_BINDING")
            if not expected:
                # Unexpected external validation failures cannot be proven
                # harmless; fence the raw core before any further cleanup.
                marked = False
                try:
                    self._fault_core()
                    marked = True
                except BaseException:
                    pass
                self._fail_closed(original, cleanup_failed=False, fault_marked=marked)
                raise
            try:
                self._discard_pair(before, after)
            except BaseException:
                self._fail_closed(original)
            raise
        with self._state_lock:
            self._check_locked()
            return self._inner.prepare(command, before, after, deadline_ns=deadline_ns)

    def try_claim(self, candidate):
        """Claim once; never calls native code, SQLite or caller callbacks."""
        self._process()
        with self._state_lock:
            self._check_locked()
            return self._inner.try_claim(candidate)

    def capacity_status(self):
        self._process()
        with self._state_lock:
            self._check_locked()
            return self._inner.capacity_status()

    def close(self) -> None:
        self._process()
        with self._state_lock:
            self._failed = True
            self._inner.close()
