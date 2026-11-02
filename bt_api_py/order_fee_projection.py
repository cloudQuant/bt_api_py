"""Project complete normalized fills onto one terminal-order fee summary."""

from __future__ import annotations

from decimal import Decimal


def enrich_order_commission(exchange_name, order, *, get_deals):
    """Attach fees only when the normalized fills reconcile the order quantity."""
    try:
        fills = get_deals(exchange_name, order["symbol"], count=1000, normalized=True)
        matching = {
            fill["trade_id"]: fill
            for fill in fills
            if fill.get("trade_id") and fill.get("order_id") == order["order_id"]
        }
        quantity = sum(
            (Decimal(str(fill.get("size") or 0)) for fill in matching.values()),
            Decimal(0),
        )
        currencies = {fill.get("fee_currency") for fill in matching.values()}
        if (
            quantity != Decimal(str(order["filled"]))
            or len(currencies) != 1
            or None in currencies
            or "" in currencies
            or any(fill.get("fee") is None for fill in matching.values())
        ):
            return
        order["cumulative_commission"] = float(
            sum((Decimal(str(fill["fee"])) for fill in matching.values()), Decimal(0))
        )
        order["commission_currency"] = order["fee_currency"] = currencies.pop()
        order["commission_source"] = "exchange"
    except Exception:
        order["commission_source"] = "unavailable"


__all__ = ("enrich_order_commission",)
