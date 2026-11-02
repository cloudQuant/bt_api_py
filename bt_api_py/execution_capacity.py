"""Provider-neutral execution order-capacity facade and common DTO exports.

The immutable DTO and provider port live in ``bt_api_base`` so provider
packages can implement the contract without depending on this parent SDK
package. SDK route composition creates the one-route facade and is the only
path Backtrader uses to obtain an observed projection.
"""

from __future__ import annotations

from typing import Optional

from bt_api_base.execution_capacity_protocol import (
    ORDER_CAPACITY_SCHEMA,
    OrderCapacityProviderPort,
    OrderCapacitySnapshot,
    unavailable_order_capacity_snapshot,
)


class ExecutionCapacityFacade:
    """Provider-neutral one-route view over a registered capacity authority.

    The facade is created by SDK route composition. Its provider port remains
    private, and callers cannot supply counts to this method.
    """

    __slots__ = ("__exchange_name", "__provider_port", "__unavailable_snapshot")

    def __init__(
        self,
        exchange_name: str,
        provider_port: Optional[OrderCapacityProviderPort] = None,
        unavailable_reason: Optional[str] = None,
    ) -> None:
        _validate_exchange_name(exchange_name)
        if provider_port is not None and unavailable_reason is not None:
            raise ValueError("order_capacity_facade_configuration_invalid")
        if provider_port is None:
            self.__unavailable_snapshot = unavailable_order_capacity_snapshot(
                exchange_name,
                unavailable_reason or "order_capacity_capability_unavailable",
            )
        else:
            if not callable(getattr(provider_port, "order_capacity_facts", None)):
                raise TypeError("order_capacity_provider_port_invalid")
            self.__unavailable_snapshot = None

        self.__exchange_name = exchange_name
        self.__provider_port = provider_port

    @property
    def exchange_name(self) -> str:
        """The exact exchange route bound by SDK composition."""

        return self.__exchange_name

    def order_capacity_facts(self) -> OrderCapacitySnapshot:
        """Read and validate the selected authority's neutral projection."""

        if self.__provider_port is None:
            unavailable = self.__unavailable_snapshot
            if unavailable is not None:
                return unavailable
            return unavailable_order_capacity_snapshot(
                self.__exchange_name, "order_capacity_capability_unavailable"
            )
        try:
            snapshot = self.__provider_port.order_capacity_facts()
        except Exception:
            return unavailable_order_capacity_snapshot(
                self.__exchange_name, "order_capacity_provider_unavailable"
            )
        try:
            if (
                type(snapshot) is not OrderCapacitySnapshot
                or snapshot.exchange_name != self.__exchange_name
                or snapshot.schema != ORDER_CAPACITY_SCHEMA
            ):
                return unavailable_order_capacity_snapshot(
                    self.__exchange_name, "order_capacity_projection_invalid"
                )
            # Re-run the immutable DTO invariant checks in case construction
            # was bypassed through low-level Python object operations.
            OrderCapacitySnapshot.__post_init__(snapshot)
        except Exception:
            return unavailable_order_capacity_snapshot(
                self.__exchange_name, "order_capacity_projection_invalid"
            )
        return snapshot


def read_order_capacity_facts(api: object, exchange_name: str) -> OrderCapacitySnapshot:
    """Select and validate one SDK-issued capacity facade and snapshot.

    Route ownership and whether a route may be used are the caller's scope;
    this helper does not infer either from local orders or configuration.
    """
    from .facade_protocol import ExecutionCapacitySelector

    if not isinstance(api, ExecutionCapacitySelector):
        return unavailable_order_capacity_snapshot(
            exchange_name, "order_capacity_capability_unavailable"
        )
    try:
        capacity = api.order_capacity(exchange_name)
    except Exception:
        return unavailable_order_capacity_snapshot(exchange_name, "order_capacity_factory_failed")
    if type(capacity) is not ExecutionCapacityFacade or capacity.exchange_name != exchange_name:
        return unavailable_order_capacity_snapshot(
            exchange_name, "order_capacity_projection_invalid"
        )
    try:
        snapshot = capacity.order_capacity_facts()
    except Exception:
        return unavailable_order_capacity_snapshot(
            exchange_name, "order_capacity_provider_unavailable"
        )
    if (
        type(snapshot) is not OrderCapacitySnapshot
        or snapshot.exchange_name != exchange_name
        or snapshot.schema != ORDER_CAPACITY_SCHEMA
    ):
        return unavailable_order_capacity_snapshot(
            exchange_name, "order_capacity_projection_invalid"
        )
    try:
        OrderCapacitySnapshot.__post_init__(snapshot)
    except Exception:
        return unavailable_order_capacity_snapshot(
            exchange_name, "order_capacity_projection_invalid"
        )
    return snapshot


def _validate_exchange_name(exchange_name: object) -> None:
    if (
        type(exchange_name) is not str
        or not exchange_name
        or exchange_name != exchange_name.strip()
        or not exchange_name.isprintable()
        or len(exchange_name) > 256
    ):
        raise ValueError("order_capacity_exchange_name_invalid")


__all__ = (
    "ORDER_CAPACITY_SCHEMA",
    "ExecutionCapacityFacade",
    "OrderCapacityProviderPort",
    "OrderCapacitySnapshot",
    "read_order_capacity_facts",
    "unavailable_order_capacity_snapshot",
)
