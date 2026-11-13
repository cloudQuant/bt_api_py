"""Internal value comparison for a proposed Gateway first Risk reservation.

Success only establishes agreement between supplied values. It does not prove
their source, account ownership, provider identity, authorization or freshness.
It does not perform admission, dispatch, storage or any other side effect.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

from bt_api_base.gateway_direct_protocol import gateway_payload_digest
from bt_api_base.gateway_risk_first_reserve_v2 import GatewayRiskFirstReserveRequestV2

from .gateway_order_contract import decode_gateway_order_request

_FIXED_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")


class RiskFirstPayloadConsistencyError(ValueError):
    """Finite, value-free refusal of an invalid or inconsistent pair."""


def _text(value: str) -> bool:
    return bool(value) and value == value.strip() and value.isprintable()


def _scalar_snapshot(payload: dict[str, object]) -> dict[str, object]:
    """Copy only exact immutable scalars, without invoking payload callbacks."""
    snapshot = payload.copy()
    if any(type(key) is not str for key in snapshot) or any(
        value is not None and type(value) not in (str, bool) for value in snapshot.values()
    ):
        raise ValueError
    return snapshot


def validate_risk_first_payload_consistency(
    request: GatewayRiskFirstReserveRequestV2, payload: dict[str, object]
) -> Mapping[str, object]:
    """Compare a v2 DTO with the existing canonical neutral order mapping.

    Exact account, idempotency key, native symbol and venue must agree, and the
    existing Gateway digest must cover the entire original payload. The order
    mapping has no comparable provider, environment, product type or canonical
    instrument ID; this function makes no assertion about those fields. A missing
    venue is refused because the pair cannot establish its value consistency.
    On success return a read-only copy of the checked scalar payload. The input
    may change even immediately after return; any future authority must hold and
    bind this same snapshot, with its own atomic source and authorization checks.
    Comparing the input at the end detects differences then, not mutations that
    were reverted or changes after that comparison. This is not source
    verification or an authorization to write.
    """
    try:
        if type(request) is not GatewayRiskFirstReserveRequestV2 or type(payload) is not dict:
            raise ValueError
        # Revalidate nested immutable values as well, including objects that may
        # have been altered after construction through Python's low-level APIs.
        checked_request = GatewayRiskFirstReserveRequestV2.from_mapping(request.to_mapping())
        command = checked_request.command
        snapshot = _scalar_snapshot(payload)
        # Bound the raw data before decoding. Exponents cannot be emitted by
        # OrderRequest.to_dict(), and formatting one could allocate huge output.
        if gateway_payload_digest(snapshot) != command.payload_digest:
            raise ValueError
        for name in ("quantity", "price"):
            value = snapshot.get(name)
            if value is None and name == "price":
                continue
            if type(value) is not str or _FIXED_DECIMAL.fullmatch(value) is None:
                raise ValueError
        decoder_input = snapshot.copy()
        order = decode_gateway_order_request(decoder_input, command.account_scope)
        # Decoder enforces its exact frozen field set and scalar types. Reject
        # blank, padded or non-printable text even where the model permits it.
        if any(type(value) is str and not _text(value) for value in snapshot.values()):
            raise ValueError
        if (
            order.idempotency_key != command.request_key
            or order.symbol != command.instrument.native_symbol
            or order.exchange_id is None
            or order.exchange_id != command.instrument.venue
            or order.account_id != command.account_scope
            or _scalar_snapshot(decoder_input) != snapshot
            or _scalar_snapshot(payload) != snapshot
            or GatewayRiskFirstReserveRequestV2.from_mapping(request.to_mapping()) != checked_request
        ):
            raise ValueError
        return MappingProxyType(snapshot)
    except Exception:  # noqa: S110 - do not retain or log potentially sensitive input
        pass
    # Raise outside the handler so the value-free error retains no inner
    # exception in __context__ for callers or logging systems to inspect.
    raise RiskFirstPayloadConsistencyError("RISK_FIRST_PAYLOAD_INCONSISTENT")
