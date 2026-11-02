"""Typed SDK client for an exact strategy-owned account-book route."""

from __future__ import annotations

import threading
from typing import Any

from bt_api_base.account_book_protocol import (
    StrategyAccountBookSnapshot,
    parse_strategy_account_book_reply,
)
from bt_api_base.service_protocol import WireScope

from bt_api_py.service_runtime import (
    RegisteredBinding,
    ServiceClientRuntime,
    ServiceRuntimeError,
)


STRATEGY_ACCOUNT_BOOKS_OPERATION = "execution.account_books.query"
# The server caps the projected result at 48 KiB; reserve space for the RPC
# envelope while keeping the consumer-side reply bound explicit.
_MAX_REPLY_BYTES = 64 * 1024


class ServiceStrategyAccountBookClientError(RuntimeError):
    """Finite route/configuration diagnostic for the typed book reader."""

    def __init__(self, code: str) -> None:
        if type(code) is not str or not code.isascii() or not code or len(code) > 64:
            code = "ACCOUNT_BOOK_CLIENT_UNAVAILABLE"
        self.code = code
        super().__init__(code)


class ServiceStrategyAccountBookClient:
    """Read a strategy book through one registered runtime route.

    The runtime remains the owner of transport, thread, health and registration
    leases. This client binds the query to the exact route and strategy scope;
    it does not create a runtime or infer authorization from its arguments.
    """

    __slots__ = (
        "_runtime",
        "_mount",
        "_scope_ref",
        "strategy_ref",
        "account_ref",
        "_owner_thread_id",
        "_frozen",
    )

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError("strategy account-book clients are immutable")
        if name not in self.__slots__:
            raise AttributeError("strategy account-book client fields are fixed")
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError("strategy account-book clients are immutable")
        object.__delattr__(self, name)

    def __init__(
        self,
        runtime: ServiceClientRuntime,
        *,
        mount: str,
        scope_ref: str,
        strategy_ref: str,
        account_ref: str,
    ) -> None:
        if type(runtime) is not ServiceClientRuntime:
            raise ServiceStrategyAccountBookClientError("INVALID_RUNTIME")
        values = (mount, scope_ref, strategy_ref, account_ref)
        if any(type(value) is not str or not value or value != value.strip() for value in values):
            raise ServiceStrategyAccountBookClientError("INVALID_SCOPE")
        try:
            runtime.next_maintenance_delay()
        except ServiceRuntimeError:
            raise ServiceStrategyAccountBookClientError("WRONG_OWNER_THREAD") from None
        if runtime.state != "registered":
            raise ServiceStrategyAccountBookClientError("RUNTIME_NOT_REGISTERED")

        object.__setattr__(self, "_runtime", runtime)
        object.__setattr__(self, "_mount", mount)
        object.__setattr__(self, "_scope_ref", scope_ref)
        object.__setattr__(self, "strategy_ref", strategy_ref)
        object.__setattr__(self, "account_ref", account_ref)
        object.__setattr__(self, "_owner_thread_id", threading.get_ident())
        object.__setattr__(self, "_frozen", True)
        self._require_exact_binding()

    def snapshot(self) -> StrategyAccountBookSnapshot:
        """Return one exact-owner response parsed by the neutral base contract."""

        if threading.get_ident() != self._owner_thread_id:
            raise ServiceStrategyAccountBookClientError("WRONG_OWNER_THREAD")
        self._require_exact_binding()
        try:
            reply = self._runtime.call(
                self._mount,
                STRATEGY_ACCOUNT_BOOKS_OPERATION,
                {},
                scope_ref=self._scope_ref,
                max_reply_message_bytes=_MAX_REPLY_BYTES,
            )
        except ServiceRuntimeError:
            raise
        except Exception:
            raise ServiceStrategyAccountBookClientError("REMOTE_QUERY_FAILED") from None
        return parse_strategy_account_book_reply(
            reply, strategy_ref=self.strategy_ref, account_ref=self.account_ref
        )

    def _require_exact_binding(self) -> None:
        try:
            bindings = self._runtime.bindings
        except Exception:
            raise ServiceStrategyAccountBookClientError("RUNTIME_BINDINGS_UNAVAILABLE") from None
        if type(bindings) is not tuple:
            raise ServiceStrategyAccountBookClientError("RUNTIME_BINDINGS_INVALID")
        matches = tuple(
            binding
            for binding in bindings
            if type(binding) is RegisteredBinding
            and binding.active is True
            and binding.mount == self._mount
            and binding.scope_ref == self._scope_ref
            and STRATEGY_ACCOUNT_BOOKS_OPERATION in binding.operations
        )
        if len(matches) != 1:
            raise ServiceStrategyAccountBookClientError("ACCOUNT_BOOK_ROUTE_UNAVAILABLE")
        scope = matches[0].scope
        if (
            type(scope) is not WireScope
            or scope.strategy_ref != self.strategy_ref
            or scope.account_ref != self.account_ref
            or scope.account_refs
            or scope.manual_book_ref is not None
            or scope.portfolio_ref is not None
            or scope.group_id is not None
            or scope.leg_id is not None
        ):
            raise ServiceStrategyAccountBookClientError("ACCOUNT_BOOK_SCOPE_MISMATCH")


__all__ = [
    "STRATEGY_ACCOUNT_BOOKS_OPERATION",
    "ServiceStrategyAccountBookClient",
    "ServiceStrategyAccountBookClientError",
]
