"""Owner-bound immutable strategy-origin configuration, not provider evidence."""
from __future__ import annotations

import os
import threading

from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py._contracts.models import OrderRequest
from bt_api_py.service_registry import ServiceCallContext


class GatewayStrategyOriginSourceError(RuntimeError):
    """Finite failure without exposing source details."""


def _key_values(instrument):
    if type(instrument) is not MarketInstrumentKey:
        raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_INVALID")
    values = tuple(getattr(instrument, field) for field in (
        "provider", "venue", "environment", "product_type", "instrument_id", "native_symbol"))
    # Revalidate and retain only the six immutable, exact string fields.
    if any(type(value) is not str for value in values):
        raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_INVALID")
    try:
        MarketInstrumentKey(*values)
    except Exception:
        raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_INVALID") from None
    return values


def snapshot_strategy_origin_instruments(instruments):
    """Copy operator declarations; no reference to caller-owned keys survives."""
    if type(instruments) is not tuple or not instruments:
        raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_INVALID")
    entries = tuple(_key_values(instrument) for instrument in instruments)
    if len({(entry[1], entry[5]) for entry in entries}) != len(entries):
        raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_INVALID")
    return entries


class GatewayStrategyOriginSource:
    """Static declaration for one owner generation; Registry grants remain separate.

    Construction accepts keys only, never resolver or recheck callbacks.  The
    snapshot is not evidence of venue state, provider identity or permission.
    Claim coverage of owner lifecycle writers is independently required.
    """

    __slots__ = ("__entries", "__pid", "__thread", "__closed", "__binding", "__close_incomplete")

    def __init__(self, *, instruments):
        entries = snapshot_strategy_origin_instruments(instruments)
        object.__setattr__(self, "_GatewayStrategyOriginSource__entries", entries)
        object.__setattr__(self, "_GatewayStrategyOriginSource__pid", os.getpid())
        object.__setattr__(self, "_GatewayStrategyOriginSource__thread", threading.current_thread())
        object.__setattr__(self, "_GatewayStrategyOriginSource__closed", False)
        object.__setattr__(self, "_GatewayStrategyOriginSource__binding", None)
        object.__setattr__(self, "_GatewayStrategyOriginSource__close_incomplete", False)

    def __setattr__(self, name, value):
        raise AttributeError("strategy origin source is immutable")

    def _matches_snapshot(self, snapshot):
        """Compare all six fields against the composition root's copied declarations."""
        return type(snapshot) is tuple and snapshot == self.__entries

    def _bind_owner(self, owner, journal, guard, session, coordinator, owner_guard_binding):
        """Bind once on the creating owner thread, before publishing the owner."""
        from bt_api_ctp.gateway_session import CtpGatewaySession
        from bt_api_gateway.direct_command_journal import GatewayDirectCommandJournal
        from bt_api_gateway.session_guard import LocalAccountSessionGuard

        from bt_api_py._gateway_owner_guard_binding import _GatewayOwnerGuardBinding
        from bt_api_py.gateway_direct_owner import GatewayDirectOwner
        from bt_api_py.gateway_dispatch_claim import GatewayDispatchClaimCoordinator

        try:
            if (self.__binding is not None or self.__closed or self.__pid != os.getpid()
                    or self.__thread is not threading.current_thread()
                    or type(owner) is not GatewayDirectOwner
                    or type(journal) is not GatewayDirectCommandJournal
                    or type(guard) is not LocalAccountSessionGuard
                    or type(session) is not CtpGatewaySession
                    or type(coordinator) is not GatewayDispatchClaimCoordinator
                    or type(owner_guard_binding) is not _GatewayOwnerGuardBinding
                    or not coordinator.matches_owner(journal.account_scope, journal.generation)):
                raise ValueError
            journal._assert_account_guard(guard)
            with owner._condition:
                if (owner._pid != self.__pid or owner._thread is not self.__thread
                        or owner._closing or owner._lifecycle_shutdown_requested
                        or owner._done.is_set() or owner._binding is not None):
                    raise ValueError
                epoch = owner._lifecycle_epoch
            object.__setattr__(self, "_GatewayStrategyOriginSource__binding", (
                owner, journal, guard, session, coordinator, owner_guard_binding,
                journal.account_scope, journal.journal_id, journal.generation, epoch))
        except Exception:
            raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_UNAVAILABLE") from None

    def _assert_current(self):
        if (self.__closed or self.__pid != os.getpid()
                or self.__thread is not threading.current_thread() or self.__binding is None):
            raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_UNAVAILABLE")
        owner, journal, _guard, _session, coordinator, owner_binding, account, journal_id, generation, epoch = self.__binding
        try:
            if (journal.account_scope != account or journal.journal_id != journal_id
                    or journal.generation != generation
                    or not coordinator.matches_owner(account, generation)):
                raise ValueError
            owner_binding.assert_bound(owner, journal)
            with owner._condition:
                if owner._lifecycle_shutdown_requested or owner._lifecycle_epoch != epoch:
                    raise ValueError
        except Exception:
            raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_UNAVAILABLE") from None

    def bound_to(self, owner, journal, coordinator):
        try:
            self._assert_current()
            return (self.__binding[0] is owner and self.__binding[1] is journal
                    and self.__binding[4] is coordinator)
        except Exception:
            return False

    def resolve(self, context: ServiceCallContext, order: OrderRequest) -> MarketInstrumentKey:
        self._assert_current()
        if type(context) is not ServiceCallContext or type(order) is not OrderRequest:
            raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_UNAVAILABLE")
        for values in self.__entries:
            if (values[1], values[5]) == (order.exchange_id, order.symbol):
                return MarketInstrumentKey(*values)
        raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_UNAVAILABLE")

    def recheck(self, instrument: MarketInstrumentKey) -> bool:
        self._assert_current()
        try:
            return _key_values(instrument) in self.__entries
        except Exception:
            return False

    def _poison_after_failure(self, coordinator):
        try:
            coordinator.poison_domain("session_source")
        except BaseException:
            return False
        return True

    def close(self) -> None:
        # Cleanup runs after owner closing, so it cannot require assert_bound.
        if self.__pid != os.getpid() or self.__thread is not threading.current_thread():
            raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_UNAVAILABLE")
        if self.__closed:
            if self.__close_incomplete:
                self.__binding[4].poison_domain("session_source")
                object.__setattr__(self, "_GatewayStrategyOriginSource__close_incomplete", False)
            return
        binding = self.__binding
        if binding is None:
            object.__setattr__(self, "_GatewayStrategyOriginSource__closed", True)
            return
        coordinator = binding[4]
        try:
            change = coordinator.begin_change("session_source")
        except BaseException:
            object.__setattr__(self, "_GatewayStrategyOriginSource__closed", True)
            object.__setattr__(self, "_GatewayStrategyOriginSource__close_incomplete",
                               not self._poison_after_failure(coordinator))
            raise
        object.__setattr__(self, "_GatewayStrategyOriginSource__closed", True)
        try:
            coordinator.finish_change(change, "UNAVAILABLE")
        except BaseException:
            object.__setattr__(self, "_GatewayStrategyOriginSource__close_incomplete",
                               not self._poison_after_failure(coordinator))
            raise
