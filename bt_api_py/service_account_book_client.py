"""Typed SDK client for an exact strategy-owned account-book route."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any

from bt_api_base.account_book_protocol import (
    SAME_TRANSACTION_CUT,
    STRATEGY_ACCOUNT_BOOK_CONTRACT_VERSION,
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
# Mirrors bt_api_py.service_execution_account_books (the producer); kept here
# so this client does not import the optional Execution service module.
_TYPED_REPLY_SCHEMA = "bt_api.service.strategy_account_book.reply"
_TYPED_REPLY_VERSION = 3
_TYPED_FAILURE_FIELDS = frozenset({"schema_id", "schema_version", "error_code"})
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
        return self._typed_snapshot(reply)

    def _typed_snapshot(self, reply: Any) -> StrategyAccountBookSnapshot:
        """Decode the reply's typed ``strategy_account_book`` message (G5).

        Error replies keep their finite remote codes (through the Base reply
        parser). A successful reply must carry the typed field: the untyped
        ``OWNER_BOOK`` shape alone is refused. The typed part's schema, minor
        and codec registry digest must match this client; the
        decoded book must be this client's strategy and account; the reply's
        scoped facts (view, owner, complete coverage) are still checked here.
        """

        if not isinstance(reply, Mapping) or reply.get("ok") is not True:
            # Remote rejections keep their finite codes.
            return parse_strategy_account_book_reply(
                reply, strategy_ref=self.strategy_ref, account_ref=self.account_ref
            )
        result = reply.get("result")
        if not isinstance(result, Mapping):
            raise ServiceStrategyAccountBookClientError("INVALID_REPLY")
        if (
            result.get("view") != "OWNER_BOOK"
            or result.get("owner_kind") != "STRATEGY"
            or result.get("owner_ref") != self.strategy_ref
        ):
            raise ServiceStrategyAccountBookClientError("OWNER_BINDING_MISMATCH")
        typed = result.get("strategy_account_book")
        if isinstance(typed, Mapping) and typed.get("schema_id") == _TYPED_REPLY_SCHEMA:
            # The producer's finite failure form: never a partial book. It is
            # read before coverage so its reason is not hidden.
            error_code = typed.get("error_code")
            if set(typed) != _TYPED_FAILURE_FIELDS or type(error_code) is not str:
                raise ServiceStrategyAccountBookClientError("ACCOUNT_BOOK_TYPED_CONTRACT_MISMATCH")
            if typed.get("schema_version") != _TYPED_REPLY_VERSION:
                raise ServiceStrategyAccountBookClientError(
                    "ACCOUNT_BOOK_TYPED_VERSION_UNSUPPORTED"
                )
            raise ServiceStrategyAccountBookClientError(error_code)
        coverage = result.get("coverage")
        if not isinstance(coverage, Mapping) or coverage.get("complete") is not True:
            raise ServiceStrategyAccountBookClientError("STRATEGY_BOOK_COVERAGE_INCOMPLETE")
        if not isinstance(typed, Mapping):
            raise ServiceStrategyAccountBookClientError("ACCOUNT_BOOK_TYPED_REPLY_REQUIRED")

        from bt_api_base.wire_codec_v1 import (
            TYPED_PART_SCHEMA,
            WireCodecError,
            decode_typed_part,
        )

        if typed.get("schema_id") != TYPED_PART_SCHEMA:
            raise ServiceStrategyAccountBookClientError("ACCOUNT_BOOK_TYPED_CONTRACT_MISMATCH")
        data = typed.get("data")
        if isinstance(data, Mapping):
            # Explicit refusals before the strict decode, which would only
            # report a generic payload or registry mismatch for these.
            if data.get("contract_version") != STRATEGY_ACCOUNT_BOOK_CONTRACT_VERSION:
                raise ServiceStrategyAccountBookClientError(
                    "ACCOUNT_BOOK_TYPED_VERSION_UNSUPPORTED"
                )
            if data.get("financial_cut_relationship") == SAME_TRANSACTION_CUT and (
                data.get("position_cut_id") is None
                or data.get("financial_cut_id") != data.get("position_cut_id")
            ):
                raise ServiceStrategyAccountBookClientError("STRATEGY_FINANCIAL_CUT_MISMATCH")
        try:
            # One direct rebuild of the Base snapshot from the decoded envelope.
            snapshot = decode_typed_part(typed, kind="strategy_account_book")
        except WireCodecError as error:
            if error.code == "WIRE_TYPED_CONTRACT_MISMATCH":
                raise ServiceStrategyAccountBookClientError(
                    "ACCOUNT_BOOK_TYPED_CONTRACT_MISMATCH"
                ) from None
            raise ServiceStrategyAccountBookClientError(error.code) from None
        if type(snapshot) is not StrategyAccountBookSnapshot:
            raise ServiceStrategyAccountBookClientError("ACCOUNT_BOOK_TYPED_REPLY_INVALID")
        if snapshot.strategy_ref != self.strategy_ref or snapshot.account_ref != self.account_ref:
            raise ServiceStrategyAccountBookClientError("OWNER_BINDING_MISMATCH")
        if (
            coverage.get("journal_generation") != snapshot.journal_generation
            or coverage.get("current_execution_cursor") != snapshot.current_execution_cursor
        ):
            raise ServiceStrategyAccountBookClientError("ACCOUNT_BOOK_TYPED_CUT_MISMATCH")
        return snapshot

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
