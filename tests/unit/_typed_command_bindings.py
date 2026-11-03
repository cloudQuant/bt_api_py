"""Test helper: deployment declarations for typed order-command bindings.

Iteration 60 R2-B. Host and client test setups build their typed command
bindings through the same SDK derivation the deployment path uses
(``bt_api_py.service_command_wire``), never by constructing
``ServiceCommandBinding`` by hand, so a test host and a test client cannot
drift apart.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from bt_api_py.service_command_wire import (
    derive_command_binding,
    derive_host_command_bindings,
)


def declaration(
    *,
    provider: str,
    venue: str,
    environment: str,
    account_id: str,
    instruments: Iterable[str],
    product_type: str = "spot",
    quantity_unit: str = "base",
    price_currency: str | None = None,
    position_mode: str | None = None,
    hedge_class: str | None = None,
) -> dict[str, Any]:
    """One plain ``command_binding`` declaration (config-file shape)."""

    payload: dict[str, Any] = {
        "binding_schema_version": 1,
        "account_key": {
            "provider": provider,
            "venue": venue,
            "environment": environment,
            "account_id": account_id,
        },
        "instruments": [
            {
                "provider": provider,
                "venue": venue,
                "environment": environment,
                "product_type": product_type,
                "instrument_id": instrument,
                "native_symbol": instrument,
            }
            for instrument in instruments
        ],
        "quantity_unit": quantity_unit,
    }
    for name, value in (
        ("price_currency", price_currency),
        ("position_mode", position_mode),
        ("hedge_class", hedge_class),
    ):
        if value is not None:
            payload[name] = value
    return payload


def client_binding(
    declaration_payload: Mapping[str, Any], *, service_id: str, instance_id: str
) -> Any:
    """The client's binding for one configured ``broker.orders`` route."""

    return derive_command_binding(
        declaration_payload,
        service_id=service_id,
        instance_id=instance_id,
        account_ref=declaration_payload["account_key"]["account_id"],
    )


def host_bindings(
    targets: Mapping[Any, Any],
    declarations: list[Mapping[str, Any]],
    *,
    service_id: str,
    instance_id: str,
) -> Mapping[Any, Any]:
    """A host's ``command_bindings`` resource for its targets."""

    return derive_host_command_bindings(
        declarations, service_id=service_id, instance_id=instance_id, targets=targets
    )
