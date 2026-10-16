"""Retired SDK backend import path for the legacy forwarding writer."""

from __future__ import annotations

from typing import Any, NoReturn

from bt_api_py._contracts.errors import LegacyForwardingRetiredError


_MIGRATION_DETAIL = (
    "the legacy ZMQ backend was retired; use BtApi with the direct transport "
    "for ordinary calls, ServiceClientRuntime with ServiceHost ports "
    "service.market_source/service.account_read for managed reads, or "
    "Backtrader ServiceExecutionSession with service.order_entry for managed writes"
)


class ZmqBtApiBackend:
    """Retired backend; it cannot create clients or dispatch old wire commands."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self._raise_retired("bt_api_py.forwarding.btapi_backend.ZmqBtApiBackend")

    @staticmethod
    def _raise_retired(target: str) -> NoReturn:
        raise LegacyForwardingRetiredError(target, detail=_MIGRATION_DETAIL)


__all__ = ["ZmqBtApiBackend"]
