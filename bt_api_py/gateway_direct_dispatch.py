"""Explicit trusted-local SDK/Gateway dispatch composition.

The caller supplies the account owner, an already acquired guard, admission,
and a coordinator whose selected route is a *local native provider*. The
caller must not provide a remote Gateway-backed provider: doing so can recurse.
The supplied provider object binds this composition to the selected route; it
does not prove that the object is native or authenticate its source. Future
remote providers must be excluded by provider declarations or composition.
This module does not create an account session or write until invoked.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ._contracts.models import OrderRequest
from .direct_write import DirectWriteCoordinator

if TYPE_CHECKING:
    from bt_api_gateway.direct_command_journal import (
        DirectCommandRecord,
        GatewayDirectCommandJournal,
    )
    from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher
    from bt_api_gateway.session_guard import LocalAccountSessionGuard


def compose_gateway_direct_dispatcher(
    coordinator: DirectWriteCoordinator,
    exchange_name: str,
    journal: GatewayDirectCommandJournal,
    guard: LocalAccountSessionGuard,
    assert_admitted: Callable[[DirectCommandRecord], object],
    *,
    expected_local_provider: object,
) -> GatewayDirectDispatcher:
    """Bind a trusted local native route to the Gateway durable dispatch port.

    ``expected_local_provider`` is injected by the trusted account owner and
    must be the selected route's provider, both during composition and before
    each dispatch. This binding does not prove it is native. The persisted
    payload must be the complete canonical scalar ``OrderRequest.to_dict()``;
    managed authority and nested identities are not accepted. Gateway is
    imported only when selected.
    """
    if type(coordinator) is not DirectWriteCoordinator:
        raise ValueError("coordinator must be a DirectWriteCoordinator")
    if (
        expected_local_provider is None
        or coordinator.provider(exchange_name) is not expected_local_provider
    ):
        raise ValueError("local provider does not match coordinator route")
    if not callable(assert_admitted):
        raise ValueError("assert_admitted must be callable")

    from bt_api_gateway.direct_dispatch import GatewayDirectDispatcher, NativeDispatchOutcome

    def native_place(
        payload: dict[str, object], port: Any, host_scope: str
    ) -> NativeDispatchOutcome:
        if coordinator.provider(exchange_name) is not expected_local_provider:
            raise ValueError("local provider does not match coordinator route")
        summary = dict(payload)
        request = decode_gateway_order_request(summary, host_scope)
        expected = _canonical(summary)

        class CheckedCommitPort:
            def commit(self, record: Any) -> Any:
                if (
                    type(request) is not OrderRequest
                    or _canonical(request.to_dict()) != expected
                    or _canonical(dict(record.request_summary)) != expected
                ):
                    raise ValueError("order request changed before commit")
                return port.commit(record)

        if coordinator.provider(exchange_name) is not expected_local_provider:
            raise ValueError("local provider does not match coordinator route")
        result = coordinator.place(
            exchange_name,
            request,
            commit_port=CheckedCommitPort(),
            host_scope=host_scope,
            request_summary=summary,
        )
        return NativeDispatchOutcome(
            result.sent, result.identity, result.marker_id, result.error_code
        )

    return GatewayDirectDispatcher(
        journal,
        exchange_name,
        guard,
        native_place,
        lambda identity: coordinator.strong_identity_keys(exchange_name, identity),
        assert_admitted,
    )


_REQUIRED_TEXT = frozenset(
    {
        "symbol",
        "side",
        "order_type",
        "quantity",
        "account_id",
        "client_order_id",
        "time_in_force",
        "idempotency_key",
        "quantity_unit",
    }
)
_OPTIONAL_TEXT = frozenset(
    {
        "price",
        "position_side",
        "offset",
        "position_id",
        "position_mode",
        "exchange_id",
        "hedge_flag",
        "hedge_class",
    }
)
_FORBIDDEN = frozenset(
    {
        "instrument",
        "ctp_order_identity",
        "execution_cycle_id",
        "execution_role",
        "strategy_identity_sha256",
        "managed_intent_id",
        "runtime_order_id",
    }
)
_FIELDS = _REQUIRED_TEXT | _OPTIONAL_TEXT | _FORBIDDEN | {"reduce_only"}


def _canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


class GatewayDirectCompositionError(ValueError):
    """Finite failure of the local Gateway order composition contract."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def decode_gateway_order_request(payload: dict[str, object], account_scope: str) -> OrderRequest:
    """Rebuild a complete, scalar, non-managed canonical order for an account.

    Invalid shapes, fields and representations raise ``ORDER_REQUEST_INVALID``;
    decoding neither authenticates a caller nor authorizes an account write.
    """
    try:
        if (
            type(payload) is not dict
            or type(account_scope) is not str
            or not account_scope
            or set(payload) != _FIELDS
            or any(type(payload[name]) is not str for name in _REQUIRED_TEXT)
            or any(
                payload[name] is not None and type(payload[name]) is not str
                for name in _OPTIONAL_TEXT
            )
            or any(payload[name] is not None for name in _FORBIDDEN)
            or type(payload["reduce_only"]) is not bool
            or payload["account_id"] != account_scope
        ):
            raise ValueError("unsupported direct order request")
        request = OrderRequest.from_dict(payload)
        if type(request) is not OrderRequest or _canonical(request.to_dict()) != _canonical(
            payload
        ):
            raise ValueError("noncanonical direct order request")
        return request
    except Exception:
        raise GatewayDirectCompositionError("ORDER_REQUEST_INVALID") from None
