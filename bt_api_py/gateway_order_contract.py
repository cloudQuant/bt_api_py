"""Canonical neutral Gateway order-request decoding."""

from __future__ import annotations

import json

from ._contracts.models import OrderRequest

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
