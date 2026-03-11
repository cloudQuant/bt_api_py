"""OrderIdentityMap — bidirectional order ID mapping.

Maintains the relationships between:
- request_id (client-generated UUID per order request)
- client_order_id (gateway-generated, e.g. CTP OrderRef or exchange clientOid)
- venue_order_id (exchange-assigned, e.g. CTP OrderSysID or Binance orderId)
- strategy_id (which strategy owns the order)

Thread-safe for concurrent access from the runtime main loop and
adapter callback threads.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OrderEntry:
    """A single order's identity record."""

    request_id: str
    strategy_id: str
    client_order_id: str | None = None
    venue_order_id: str | None = None
    symbol: str | None = None
    status: str = "pending"
    extra: dict[str, Any] = field(default_factory=dict)


class OrderIdentityMap:
    """Thread-safe bidirectional order identity map.

    Usage::

        oid_map = OrderIdentityMap()
        oid_map.register("req-1", "strat-1", symbol="BTCUSDT")
        oid_map.set_client_order_id("req-1", "client-123")
        oid_map.set_venue_order_id("req-1", "venue-456")

        entry = oid_map.by_venue("venue-456")
        assert entry.strategy_id == "strat-1"
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Primary index: request_id -> OrderEntry
        self._by_request: dict[str, OrderEntry] = {}
        # Secondary indices
        self._by_client_oid: dict[str, OrderEntry] = {}
        self._by_venue_oid: dict[str, OrderEntry] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        request_id: str,
        strategy_id: str,
        *,
        client_order_id: str | None = None,
        venue_order_id: str | None = None,
        symbol: str | None = None,
        **extra: Any,
    ) -> OrderEntry:
        """Create a new order entry.

        Args:
            request_id: Client-generated UUID for this order request.
            strategy_id: Which strategy submitted the order.
            client_order_id: Optional pre-assigned client order ID.
            venue_order_id: Optional pre-assigned venue order ID.
            symbol: Trading symbol.
            **extra: Arbitrary extra metadata.

        Returns:
            The newly created OrderEntry.
        """
        entry = OrderEntry(
            request_id=request_id,
            strategy_id=strategy_id,
            client_order_id=client_order_id,
            venue_order_id=venue_order_id,
            symbol=symbol,
            extra=dict(extra),
        )
        with self._lock:
            self._by_request[request_id] = entry
            if client_order_id:
                self._by_client_oid[client_order_id] = entry
            if venue_order_id:
                self._by_venue_oid[venue_order_id] = entry
        return entry

    # ------------------------------------------------------------------
    # Update indices
    # ------------------------------------------------------------------

    def set_client_order_id(self, request_id: str, client_order_id: str) -> OrderEntry | None:
        """Associate a client_order_id with an existing entry."""
        with self._lock:
            entry = self._by_request.get(request_id)
            if entry is None:
                return None
            entry.client_order_id = client_order_id
            self._by_client_oid[client_order_id] = entry
        return entry

    def set_venue_order_id(self, request_id: str, venue_order_id: str) -> OrderEntry | None:
        """Associate a venue_order_id with an existing entry."""
        with self._lock:
            entry = self._by_request.get(request_id)
            if entry is None:
                return None
            entry.venue_order_id = venue_order_id
            self._by_venue_oid[venue_order_id] = entry
        return entry

    def set_venue_order_id_by_client(
        self, client_order_id: str, venue_order_id: str
    ) -> OrderEntry | None:
        """Associate a venue_order_id using the client_order_id as lookup key."""
        with self._lock:
            entry = self._by_client_oid.get(client_order_id)
            if entry is None:
                return None
            entry.venue_order_id = venue_order_id
            self._by_venue_oid[venue_order_id] = entry
        return entry

    def update_status(self, request_id: str, status: str) -> OrderEntry | None:
        """Update the status of an order by request_id."""
        with self._lock:
            entry = self._by_request.get(request_id)
            if entry is not None:
                entry.status = status
        return entry

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def by_request(self, request_id: str) -> OrderEntry | None:
        """Lookup by request_id."""
        with self._lock:
            return self._by_request.get(request_id)

    def by_client(self, client_order_id: str) -> OrderEntry | None:
        """Lookup by client_order_id."""
        with self._lock:
            return self._by_client_oid.get(client_order_id)

    def by_venue(self, venue_order_id: str) -> OrderEntry | None:
        """Lookup by venue_order_id (exchange-assigned)."""
        with self._lock:
            return self._by_venue_oid.get(venue_order_id)

    def strategy_for_request(self, request_id: str) -> str | None:
        entry = self.by_request(request_id)
        return entry.strategy_id if entry else None

    def strategy_for_venue(self, venue_order_id: str) -> str | None:
        entry = self.by_venue(venue_order_id)
        return entry.strategy_id if entry else None

    # ------------------------------------------------------------------
    # Removal & iteration
    # ------------------------------------------------------------------

    def remove(self, request_id: str) -> OrderEntry | None:
        """Remove an order entry and all its index entries."""
        with self._lock:
            entry = self._by_request.pop(request_id, None)
            if entry is None:
                return None
            if entry.client_order_id:
                self._by_client_oid.pop(entry.client_order_id, None)
            if entry.venue_order_id:
                self._by_venue_oid.pop(entry.venue_order_id, None)
        return entry

    def orders_for_strategy(self, strategy_id: str) -> list[OrderEntry]:
        """Return all order entries belonging to *strategy_id*."""
        with self._lock:
            return [e for e in self._by_request.values() if e.strategy_id == strategy_id]

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._by_request)

    def snapshot(self) -> list[dict[str, Any]]:
        """Return a serialisable snapshot of all entries."""
        with self._lock:
            return [
                {
                    "request_id": e.request_id,
                    "strategy_id": e.strategy_id,
                    "client_order_id": e.client_order_id,
                    "venue_order_id": e.venue_order_id,
                    "symbol": e.symbol,
                    "status": e.status,
                }
                for e in self._by_request.values()
            ]
