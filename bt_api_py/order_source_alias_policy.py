"""Select and fingerprint versioned source aliases for an SDK provider route.

This is a source-field contract, not proof of a provider or account. The
current provider-scoped schemas bind the route, source fields and selected maps.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from bt_api_base.order_provenance_fields import PROVIDER_SCOPED_ORDER_PROVENANCE_SCHEMAS
from bt_api_base.provider_order_source_aliases import (
    GROUP_FILL_SOURCE_SEMANTICS,
    GROUP_ORDER_SOURCE_SEMANTICS,
    RISK_ORDER_SOURCE_SEMANTICS,
)


class OrderSourceAliasPolicyError(ValueError):
    """A route has no usable alias declaration for the requested schema."""


@dataclass(frozen=True)
class OrderSourceAliasContract:
    """Selected source maps and their Risk and group-proof identities."""

    risk_order: Mapping[str, tuple[str, ...]]
    group_order: Mapping[str, tuple[str, ...]]
    group_fill: Mapping[str, tuple[str, ...]]
    risk_sha256: str
    group_sha256: str


def _digest(payload):
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8", "strict")
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value, *, expected, source_fields=None):
    if not isinstance(value, Mapping) or set(value) != set(expected):
        raise OrderSourceAliasPolicyError("provider_source_aliases_unavailable")
    result = {}
    for semantic in expected:
        names = value[semantic]
        if (
            type(names) is not tuple
            or not names
            or any(type(name) is not str or not name for name in names)
            or len(set(names)) != len(names)
            or (source_fields is not None and any(name not in source_fields for name in names))
        ):
            raise OrderSourceAliasPolicyError("provider_source_aliases_invalid")
        result[semantic] = names
    return MappingProxyType(result)


def resolve_order_source_alias_contract(exchange_name, schema):
    """Resolve the selected provider's current scoped alias declaration."""
    if schema not in PROVIDER_SCOPED_ORDER_PROVENANCE_SCHEMAS:
        raise OrderSourceAliasPolicyError("provider_source_schema_unavailable")
    if type(exchange_name) is not str or not exchange_name:
        raise OrderSourceAliasPolicyError("provider_source_route_invalid")
    from ._contracts.errors import NormalizedApiError
    from .venue_normalization_policy import normalization_policy

    try:
        policy = normalization_policy(exchange_name)
    except NormalizedApiError:
        raise OrderSourceAliasPolicyError("provider_source_aliases_unavailable") from None
    fields = getattr(policy, "order_provenance_source_fields", None)
    envelope_fields = getattr(policy, "order_provenance_envelope_fields", None)
    if (
        type(fields) is not frozenset
        or any(type(field) is not str or not field for field in fields)
        or type(envelope_fields) is not frozenset
        or any(type(field) is not str or not field for field in envelope_fields)
        or getattr(policy, "order_provenance_schemas", None)
        != PROVIDER_SCOPED_ORDER_PROVENANCE_SCHEMAS
    ):
        raise OrderSourceAliasPolicyError("provider_source_aliases_unavailable")
    risk = _mapping(getattr(policy, "risk_order_source_aliases", None),
                    expected=RISK_ORDER_SOURCE_SEMANTICS, source_fields=fields)
    orders = _mapping(getattr(policy, "group_order_source_aliases", None),
                      expected=GROUP_ORDER_SOURCE_SEMANTICS, source_fields=fields)
    fills = _mapping(getattr(policy, "group_fill_source_aliases", None),
                     expected=GROUP_FILL_SOURCE_SEMANTICS, source_fields=fields)
    risk_map = {key: list(value) for key, value in risk.items()}
    order_map = {key: list(value) for key, value in orders.items()}
    fill_map = {key: list(value) for key, value in fills.items()}
    route = {"exchange_name": exchange_name, "normalizer_schema": schema,
             "source_fields": sorted(fields), "envelope_fields": sorted(envelope_fields)}
    risk_digest = _digest({**route, "orders": risk_map, "fills": fill_map,
                           "schema": "bt-api-risk-source-aliases-v2"})
    group_digest = _digest({**route, "orders": order_map, "fills": fill_map,
                            "schema": "bt-api-execution-group-source-aliases-v2"})
    return OrderSourceAliasContract(risk, orders, fills, risk_digest, group_digest)
