"""Synthetic provider leaves used by the group local-process integration test.

Extracted from the SQLite-backed fixture definitions in
``test_service_risk_order_exposure_integration.py`` at source SHA-256
CD6640BDEBFF44A8E4073A0BEBA3A6D378E0E3968B4B05FAD7BD4328A5E65275.
The account id is explicit here so this fixture has no test-module dependency.

Initial states are ``filled`` (full fill), ``open`` (zero fill), ``rejected``
(zero fill) or ``<raw_status>:<quantity>`` such as ``cancelled:1.5`` for a
strictly positive partial fill carried by one fill row.
"""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from typing import Any

_V2_INSTRUMENT = "BTC-USD"
_V2_METADATA_VERSION = "risk-order-exposure-metadata-v2"
_V2_ORDER_SCHEMA = "fixture-order-v2"
_V2_FILL_SCHEMA = "fixture-fill-v2"
# Matches the committed risk-only fixture's default provider-account value.
_V2_PROVIDER_ACCOUNT = "provider-account-a"


class _V2SyntheticBtApiFeed:
    """Synthetic leaves behind the real DirectBackend and BtApi normalizer."""

    def __init__(
        self, ledger_path: str, initial_states: tuple[str, ...], provider_account: str
    ) -> None:
        self._ledger_path = ledger_path
        self._initial_states = initial_states
        self._provider_account = provider_account
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_writes ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_orders ("
                "client_order_id TEXT PRIMARY KEY, order_json TEXT NOT NULL, fills_json TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS provider_counters ("
                "name TEXT PRIMARY KEY, count INTEGER NOT NULL)"
            )
            connection.execute("CREATE TABLE IF NOT EXISTS provider_faults (name TEXT PRIMARY KEY)")
            for name in ("order_queries", "fill_queries"):
                connection.execute(
                    "INSERT OR IGNORE INTO provider_counters(name, count) VALUES (?, 0)",
                    (name,),
                )

    @staticmethod
    def _fill(
        *,
        account_id: str,
        client_order_id: str,
        order_id: str,
        symbol: str,
        side: str,
        trade_id: str,
        quantity: str,
        price: str,
        revision: int,
        fee: str,
    ) -> dict[str, Any]:
        return {
            "account_id": account_id,
            "client_order_id": client_order_id,
            "fee_amount": fee,
            "fee_currency": "USD",
            "fill_revision": revision,
            "order_id": order_id,
            "quantity": quantity,
            "side": side,
            "symbol": symbol,
            "trade_id": trade_id,
            "trade_price": price,
        }

    @staticmethod
    def _order_envelope(row: dict[str, Any], *, complete: bool = True) -> dict[str, Any]:
        return {
            "account_id": row["account_id"],
            "client_order_id": row["client_order_id"],
            "complete": complete,
            "fill_watermark": max(1, int(row["order_revision"])),
            "schema_version": _V2_ORDER_SCHEMA,
            "watermark": f"order:{row['client_order_id']}:{row['order_revision']}",
            "data": [dict(row)],
        }

    def make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        *,
        client_order_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        quantity = format(Decimal(str(volume)), "f")
        order_price = format(Decimal(str(price)), "f")
        side = order_type.split("-", 1)[0].lower()
        order_id = "provider-" + client_order_id
        with sqlite3.connect(self._ledger_path) as connection:
            sequence = int(connection.execute("SELECT COUNT(*) FROM provider_writes").fetchone()[0])
            state_token = (
                self._initial_states[sequence] if sequence < len(self._initial_states) else "open"
            )
            # ``<status>:<quantity>`` models a terminal status that carries a
            # strictly positive partial fill (e.g. an IOC remainder cancel).
            state, _, partial_quantity = state_token.partition(":")
            filled = (
                quantity if state == "filled" else (partial_quantity if partial_quantity else "0")
            )
            row: dict[str, Any] = {
                "account_id": self._provider_account,
                "client_order_id": client_order_id,
                "filled": filled,
                "order_id": order_id,
                "order_revision": 1,
                "price": order_price,
                "quantity": quantity,
                "side": side,
                "status": state,
                "symbol": symbol,
            }
            fills = []
            if filled != "0":
                row["avg_price"] = order_price
                fills.append(
                    self._fill(
                        account_id=self._provider_account,
                        client_order_id=client_order_id,
                        order_id=order_id,
                        symbol=symbol,
                        side=side,
                        trade_id="fill-" + client_order_id + "-1",
                        quantity=filled,
                        price=order_price,
                        revision=1,
                        fee="-0.05",
                    )
                )
            connection.execute(
                "INSERT INTO provider_writes(client_order_id) VALUES (?)", (client_order_id,)
            )
            connection.execute(
                "INSERT INTO provider_orders(client_order_id, order_json, fills_json) "
                "VALUES (?, ?, ?)",
                (
                    client_order_id,
                    json.dumps(row, sort_keys=True, separators=(",", ":")),
                    json.dumps(fills, sort_keys=True, separators=(",", ":")),
                ),
            )
        return self._order_envelope(row)

    def query_order(self, _symbol: str, client_order_id: str, **_kwargs: Any) -> dict[str, Any]:
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "UPDATE provider_counters SET count=count+1 WHERE name='order_queries'"
            )
            found = connection.execute(
                "SELECT order_json FROM provider_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
        if found is None:
            return {
                "account_id": self._provider_account,
                "client_order_id": client_order_id,
                "complete": True,
                "fill_watermark": 1,
                "schema_version": _V2_ORDER_SCHEMA,
                "watermark": "missing-order",
                "data": [],
            }
        return self._order_envelope(json.loads(found[0]))

    def get_deals(
        self,
        _symbol: str | None,
        *,
        client_order_id: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        with sqlite3.connect(self._ledger_path) as connection:
            connection.execute(
                "UPDATE provider_counters SET count=count+1 WHERE name='fill_queries'"
            )
            found = connection.execute(
                "SELECT order_json, fills_json FROM provider_orders WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
            fault = connection.execute(
                "SELECT name FROM provider_faults WHERE name='incomplete_next_fills'"
            ).fetchone()
            if fault is not None:
                connection.execute("DELETE FROM provider_faults WHERE name='incomplete_next_fills'")
        row = (
            json.loads(found[0])
            if found is not None
            else {"account_id": self._provider_account, "order_revision": 1}
        )
        fills = [] if found is None else json.loads(found[1])
        watermark = max(1, int(row["order_revision"]))
        return {
            "account_id": self._provider_account,
            "client_order_id": client_order_id,
            "complete": fault is None,
            "fill_watermark": watermark,
            "schema_version": _V2_FILL_SCHEMA,
            "watermark": f"fills:{client_order_id}:{watermark}",
            "data": fills,
        }

    def snapshot(self) -> tuple[tuple[str, ...], dict[str, int]]:
        with sqlite3.connect(self._ledger_path) as connection:
            writes = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT client_order_id FROM provider_writes ORDER BY sequence"
                )
            )
            counters = {
                str(row[0]): int(row[1])
                for row in connection.execute(
                    "SELECT name, count FROM provider_counters ORDER BY name"
                )
            }
        return writes, counters


def _arm_incomplete_fills(ledger_path: str) -> None:
    with sqlite3.connect(ledger_path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO provider_faults(name) VALUES ('incomplete_next_fills')"
        )
