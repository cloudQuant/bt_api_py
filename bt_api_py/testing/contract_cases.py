"""Module-level docstring."""
from __future__ import annotations

from bt_api_py.brokers.base import BrokerAdapter
from bt_api_py.testing.fixtures import make_contract_order_request


async def run_broker_contract_cases(adapter: BrokerAdapter) -> dict[str, object]:
    """run_broker_contract_cases function"""
    methods = [
        "connect",
        "disconnect",
        "health",
        "capabilities",
        "list_accounts",
        "get_account",
        "list_positions",
        "list_orders",
        "place_order",
        "cancel_order",
        "get_quote",
        "stream_events",
    ]
    cases: list[dict[str, object]] = []
    await adapter.connect()
    cases.append({"name": "connect", "passed": True})
    health = await adapter.health()
    cases.append({"name": "health", "passed": isinstance(health, dict)})
    caps = adapter.capabilities()
    cases.append({"name": "capabilities", "passed": hasattr(caps, "as_dict")})
    accounts = await adapter.list_accounts()
    cases.append(
        {"name": "list_accounts", "passed": isinstance(accounts, list) and len(accounts) > 0}
    )
    account = accounts[0]
    account_snapshot = await adapter.get_account(account.account_id)
    cases.append(
        {
            "name": "get_account",
            "passed": getattr(account_snapshot, "account_id", None) == account.account_id,
        }
    )
    positions = await adapter.list_positions(account.account_id)
    cases.append({"name": "list_positions", "passed": isinstance(positions, list)})
    orders_before = await adapter.list_orders(account.account_id)
    cases.append({"name": "list_orders_before", "passed": isinstance(orders_before, list)})
    order = await adapter.place_order(make_contract_order_request(account.account_id))
    cases.append(
        {
            "name": "place_order",
            "passed": order.status in {"new", "submitted", "filled", "cancelled", "rejected"},
        }
    )
    orders_after = await adapter.list_orders(account.account_id)
    cases.append({"name": "list_orders_after", "passed": isinstance(orders_after, list)})
    quote = await adapter.get_quote("RB2510")
    cases.append({"name": "get_quote", "passed": quote.get("symbol") == "RB2510"})
    await adapter.disconnect()
    cases.append({"name": "disconnect", "passed": True})
    return {
        "passed": all(bool(case["passed"]) for case in cases),
        "method_count": len(methods),
        "capabilities": caps.as_dict(),
        "cases": cases,
    }
