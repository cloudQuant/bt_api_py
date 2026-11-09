"""Owner-thread settlement readback and durable daily attempt tracking.

This controller consumes provider-issued observations but does not attest them.
The owner supplies a guard-bound dispatcher and a trusted native account binding.

New confirmations follow user decision A (2026-11-09, "admitted confirmation is
in flight"): the account-level authority (route write admission, current owner
generation, no account-scope FREEZE) and the exact unconfirmed observation are
checked, the daily attempt marker is persisted, and the same checks run again
on the owner thread as the single claim. Every writer of those inputs runs on
this owner thread (controls via owner service calls), so the claim is atomic
with respect to them; session changes are rechecked by the provider under its
own state lock right before the native request. A failed claim never reached
the provider, so its marker is removed. After the claim the request may reach
the venue even if authority is revoked meanwhile; a lost or unclear result
keeps the original marker as UNKNOWN and is only read back, never re-sent.
Success requires a trusted readback of this account and trading day.
No automatic retry follows a durable historical attempt; readiness is a live
local ticket, not an inference from an earlier durable CONFIRMED row.
Settlement-free routes are not made ready by this controller.
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from bt_api_base.direct_write_protocol import (
    PreDispatchRecord,
    SettlementObservation,
    SettlementStatus,
    WriteOperation,
)
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher

from bt_api_py.direct_write import DirectWriteCoordinator


class GatewaySettlementObservationStaleError(ValueError):
    """The exact settlement observation failed its source fence."""

    code = "SETTLEMENT_OBSERVATION_STALE"

    def __init__(self) -> None:
        super().__init__(self.code)


class GatewaySettlementAuthorityError(RuntimeError):
    """Account-level settlement authority is absent at this instant."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _MarkerCommitPort:
    """Return the already persisted daily marker for the exact same attempt."""

    def __init__(self, observation: SettlementObservation, marker_id: str, host_scope: str):
        self._observation, self._marker_id, self._host_scope = observation, marker_id, host_scope
        self._used = False

    def commit(self, record: PreDispatchRecord) -> str | None:
        observation = self._observation
        if (
            self._used
            or type(record) is not PreDispatchRecord
            or record.operation is not WriteOperation.SETTLEMENT_CONFIRM
            or record.host_scope != self._host_scope
            or record.exchange_name != observation.exchange_name
            or record.trading_day != observation.trading_day
            or record.session_generation != observation.session_generation
            or dict(record.request_summary) != {
                "account_binding": observation.account_binding,
                "observation_id": observation.observation_id,
            }
        ):
            return None
        self._used = True
        return self._marker_id


@dataclass(frozen=True)
class GatewaySettlementSnapshot:
    observed_ready: bool
    state: str
    trading_day: str | None = None
    session_generation: int | None = None


class GatewaySettlementController:
    """Run only on the existing Gateway owner thread; no second guard consumer."""

    def __init__(
        self,
        coordinator: DirectWriteCoordinator,
        exchange_name: str,
        *,
        expected_local_provider: object,
        expected_account_binding: str,
        journal: GatewayDirectCommandJournal,
        dispatcher: GatewayDirectDispatcher,
    ):
        if (
            type(coordinator) is not DirectWriteCoordinator
            or type(journal) is not GatewayDirectCommandJournal
            or type(dispatcher) is not GatewayDirectDispatcher
            or dispatcher.journal is not journal
            or type(exchange_name) is not str
            or not exchange_name
            or exchange_name != exchange_name.strip()
            or type(expected_account_binding) is not str
            or not expected_account_binding
            or expected_account_binding != expected_account_binding.strip()
            or coordinator.provider(exchange_name) is not expected_local_provider
            or getattr(expected_local_provider, "settlement_required", None) is not True
        ):
            raise ValueError("INVALID_SETTLEMENT_COMPOSITION")
        dispatcher.assert_owned()
        self._coordinator, self._exchange = coordinator, exchange_name
        self._provider, self._account_binding = expected_local_provider, expected_account_binding
        self._journal, self._dispatcher = journal, dispatcher
        self._pid = os.getpid()
        self._owner_thread = threading.current_thread()
        self._lock = threading.Lock()
        self._snapshot = GatewaySettlementSnapshot(False, "NOT_OBSERVED")
        self._ticket: SettlementObservation | None = None

    def _ticket_valid(self, observation: Any, *, confirmed: bool = False) -> bool:
        try:
            return (
                type(observation) is SettlementObservation
                and observation.exchange_name == self._exchange
                and observation.account_binding == self._account_binding
                and (not confirmed or observation.status is SettlementStatus.CONFIRMED)
                and self._coordinator._providers.get(self._exchange) is self._provider
                and getattr(self._provider, "settlement_required", None) is True
                and self._provider.validate_settlement_observation(observation) is True
                and getattr(self._provider, "settlement_required", None) is True
                and self._coordinator._providers.get(self._exchange) is self._provider
            )
        except Exception:
            return False

    def _publish(
        self, state: str, observation: SettlementObservation | None = None, *, ready=False
    ):
        snapshot = GatewaySettlementSnapshot(
            ready,
            state,
            None if observation is None else observation.trading_day,
            None if observation is None else observation.session_generation,
        )
        with self._lock:
            self._snapshot = snapshot
            self._ticket = observation if ready else None
        return snapshot

    def _require_owner(self):
        if os.getpid() != self._pid:
            raise RuntimeError("SETTLEMENT_WRONG_PROCESS")
        if threading.current_thread() is not self._owner_thread:
            raise RuntimeError("SETTLEMENT_WRONG_THREAD")

    def invalidate(self):
        self._require_owner()
        self._publish("INVALIDATED")

    @property
    def ready(self) -> bool:
        """Owner-thread gate only; no network/provider I/O is performed here."""
        if os.getpid() != self._pid or threading.current_thread() is not self._owner_thread:
            return False
        try:
            self._dispatcher.assert_owned()
        except Exception:
            self.invalidate()
            return False
        snapshot, ticket = self._snapshot, self._ticket
        # validate_settlement_observation is a local, non-I/O ticket check.
        if not snapshot.observed_ready or not self._ticket_valid(ticket, confirmed=True):
            self.invalidate()
            return False
        try:
            self._dispatcher.assert_owned()
        except Exception:
            self.invalidate()
            return False
        return True

    @property
    def status(self) -> GatewaySettlementSnapshot:
        """Cached diagnostic only; observed_ready is not dispatch authorization."""
        if os.getpid() != self._pid:
            return GatewaySettlementSnapshot(False, "WRONG_PROCESS")
        return self._snapshot

    def tick(self) -> GatewaySettlementSnapshot:
        self._require_owner()
        try:
            self._dispatcher.assert_owned()
            self.invalidate()
            return self._tick()
        except BaseException:
            self._publish("ERROR")
            raise

    def _tick(self) -> GatewaySettlementSnapshot:
        self._dispatcher.assert_owned()
        if self._coordinator._providers.get(self._exchange) is not self._provider:
            return self._publish("SOURCE_CHANGED")
        observation = self._coordinator.observe_settlement(self._exchange)
        if not self._ticket_valid(observation):
            return self._publish("OBSERVATION_UNAVAILABLE")
        if observation.status is SettlementStatus.CONFIRMED:
            return self._accept_confirmation(observation)
        if observation.status is not SettlementStatus.UNCONFIRMED:
            return self._publish("UNKNOWN", observation)
        prior = self._journal.settlement_get(self._exchange, observation.trading_day)
        if prior is not None:
            if prior.account_binding != self._account_binding:
                return self._publish("ACCOUNT_MISMATCH", observation)
            return self._publish("READBACK_REQUIRED", observation)
        if not self._coordinator.admission.is_enabled(self._exchange):
            return self._publish("ADMISSION_DISABLED", observation)
        return self._confirm_new(observation)

    def _authority_check(self, observation: SettlementObservation) -> None:
        """Account-level claim inputs; owner thread only, no provider I/O."""
        self._dispatcher.assert_owned()
        if not self._coordinator.admission.is_enabled(self._exchange):
            raise GatewaySettlementAuthorityError("ADMISSION_DISABLED")
        if self._journal.settlement_account_frozen():
            raise GatewaySettlementAuthorityError("ACCOUNT_FROZEN")
        if not self._ticket_valid(observation):
            raise GatewaySettlementObservationStaleError()
        self._dispatcher.assert_owned()

    def _confirm_new(self, observation: SettlementObservation) -> GatewaySettlementSnapshot:
        try:
            self._authority_check(observation)
        except GatewaySettlementAuthorityError as error:
            return self._publish(error.code, observation)
        except GatewaySettlementObservationStaleError:
            return self._publish("OBSERVATION_UNAVAILABLE", observation)
        marker_id = uuid.uuid4().hex

        def fenced():
            self._authority_check(observation)

        try:
            start = self._journal.settlement_begin(
                observation, marker_id, before_begin=fenced, before_commit=fenced
            )
        except GatewaySettlementAuthorityError as error:
            # Refused before COMMIT: rolled back, no marker, nothing sent.
            return self._publish(error.code, observation)
        except GatewaySettlementObservationStaleError:
            return self._publish("OBSERVATION_UNAVAILABLE", observation)
        except Exception:
            # The marker commit outcome is unclear (e.g. a lost reply). The
            # provider was not called, but keep any marker conservatively as
            # UNKNOWN: it is only read back, never re-sent.
            try:
                self._journal.settlement_mark_unknown(observation, marker_id)
            except Exception:
                pass
            return self._publish("CONFIRMATION_UNKNOWN", observation)
        if not start.newly_started:
            return self._publish("READBACK_REQUIRED", observation)
        # The single claim: recheck on the owner thread after the durable
        # marker. Failure here means the provider was never called.
        try:
            self._authority_check(observation)
        except Exception:
            self._journal.settlement_abandon_unsent(observation, marker_id)
            return self._publish("CONFIRMATION_REVOKED", observation)
        # In flight from here (decision A).
        port = _MarkerCommitPort(observation, marker_id, self._journal.account_scope)
        try:
            status = self._coordinator.ensure_settlement(
                self._exchange, commit_port=port, host_scope=self._journal.account_scope,
                observation=observation,
            )
        except Exception:
            status = SettlementStatus.UNKNOWN
        if status is not SettlementStatus.CONFIRMED:
            self._journal.settlement_mark_unknown(observation, marker_id)
            return self._publish("CONFIRMATION_UNKNOWN", observation)
        readback = self._coordinator.observe_settlement(self._exchange)
        if (
            not self._ticket_valid(readback, confirmed=True)
            or readback.trading_day != observation.trading_day
            or readback.account_binding != observation.account_binding
        ):
            self._journal.settlement_mark_unknown(observation, marker_id)
            return self._publish("CONFIRMATION_UNKNOWN", observation)
        return self._accept_confirmation(readback)

    def _accept_confirmation(self, observation: SettlementObservation) -> GatewaySettlementSnapshot:
        def fenced():
            self._dispatcher.assert_owned()
            if not self._ticket_valid(observation, confirmed=True):
                raise GatewaySettlementObservationStaleError()
            self._dispatcher.assert_owned()

        fenced()
        self._journal.settlement_confirm(observation, before_begin=fenced, before_commit=fenced)
        fenced()
        return self._publish("CONFIRMED", observation, ready=True)
