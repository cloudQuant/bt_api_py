"""Owner-local, opt-in source of complete strategy-origin instrument identity.

The operator installs trusted local callbacks.  This wrapper does not turn a
configured symbol, peer claim, or order payload into provider authority.  A
deployment without an independently trusted resolver must not install it.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable

from bt_api_base.market_protocol import MarketInstrumentKey

from bt_api_py._contracts.models import OrderRequest
from bt_api_py.service_registry import ServiceCallContext


class GatewayStrategyOriginSourceError(RuntimeError):
    """Finite failure without exposing source details."""


class GatewayStrategyOriginSource:
    """Resolve and recheck under the same installed owner thread and process.

    Both callbacks must be trusted, local and nonblocking.  ``resolve`` receives
    the authenticated context and decoded order.  ``recheck`` must return the
    literal ``True`` only while the exact complete instrument remains trusted.
    Caller owns the account and Registry fences; this type does not grant them.
    """

    __slots__ = ("_resolve", "_recheck", "_pid", "_thread", "_closed")

    def __init__(self, *, resolve: Callable, recheck: Callable):
        if not callable(resolve) or not callable(recheck):
            raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_INVALID")
        self._resolve, self._recheck = resolve, recheck
        self._pid, self._thread, self._closed = os.getpid(), threading.current_thread(), False

    def _assert_current(self) -> None:
        if self._closed or self._pid != os.getpid() or self._thread is not threading.current_thread():
            raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_UNAVAILABLE")

    def resolve(self, context: ServiceCallContext, order: OrderRequest) -> MarketInstrumentKey:
        self._assert_current()
        if type(context) is not ServiceCallContext or type(order) is not OrderRequest:
            raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_UNAVAILABLE")
        try:
            instrument = self._resolve(context, order)
            self._assert_current()
            if type(instrument) is not MarketInstrumentKey:
                raise ValueError
            return instrument
        except Exception:
            raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_UNAVAILABLE") from None

    def recheck(self, instrument: MarketInstrumentKey) -> bool:
        self._assert_current()
        if type(instrument) is not MarketInstrumentKey:
            return False
        try:
            result = self._recheck(instrument)
            self._assert_current()
            return result is True
        except Exception:
            raise GatewayStrategyOriginSourceError("STRATEGY_ORIGIN_SOURCE_UNAVAILABLE") from None

    def close(self) -> None:
        self._assert_current()
        self._closed = True
