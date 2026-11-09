"""Owner-thread settlement readback and durable historical attempt tracking.

This controller consumes provider-issued observations but does not attest them.
The owner supplies a guard-bound dispatcher and a trusted native account binding.
New Gateway confirmations are unavailable until their final authorization
claim is proven. No automatic retry follows a durable historical attempt;
readiness is a live local ticket, not an inference from an earlier durable
CONFIRMED row. Settlement-free routes are not made ready by this controller.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

from bt_api_base.direct_write_protocol import (
    SettlementObservation,
    SettlementStatus,
)
from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher

from bt_api_py.direct_write import DirectWriteCoordinator


class GatewaySettlementObservationStaleError(ValueError):
    """The exact settlement observation failed its source fence."""

    code = "SETTLEMENT_OBSERVATION_STALE"

    def __init__(self) -> None:
        super().__init__(self.code)


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
        # Gateway settlement has not yet established a final native
        # authorization fence that observes revocation.  Do not create an
        # attempt marker or call the confirmation provider until that contract
        # exists; a confirmed provider readback above remains available.
        return self._publish("CONFIRMATION_AUTHORITY_UNAVAILABLE", observation)

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
