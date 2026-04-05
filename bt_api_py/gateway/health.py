"""Gateway health status aggregator.

Collects and exposes a unified health snapshot covering:
- Gateway state (running / stopped / error)
- Market and trade connection status
- Heartbeat timestamps
- Subscription and strategy counts
- Recent errors
- TickWriter stats
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from bt_api_py._compat import StrEnum


class GatewayState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class ErrorRecord:
    timestamp: float
    source: str
    message: str


class GatewayHealth:
    """Thread-safe gateway health aggregator.

    Args:
        max_errors: Maximum number of recent errors to keep.

    Usage::

        health = GatewayHealth()
        health.set_state(GatewayState.RUNNING)
        health.update_market_connection(ConnectionState.CONNECTED)
        health.record_heartbeat()

        snap = health.snapshot()
    """

    def __init__(self, max_errors: int = 50) -> None:
        self._lock = threading.Lock()
        self._state: GatewayState = GatewayState.STOPPED
        self._market_conn: ConnectionState = ConnectionState.DISCONNECTED
        self._trade_conn: ConnectionState = ConnectionState.DISCONNECTED
        self._last_heartbeat: float = 0.0
        self._last_tick_time: float = 0.0
        self._last_order_time: float = 0.0
        self._start_time: float = 0.0
        self._strategy_count: int = 0
        self._symbol_count: int = 0
        self._tick_count: int = 0
        self._order_count: int = 0
        self._errors: deque[ErrorRecord] = deque(maxlen=max_errors)
        self._extra: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def set_state(self, state: GatewayState) -> None:
        with self._lock:
            self._state = state
            if state == GatewayState.RUNNING:
                self._start_time = time.time()

    @property
    def state(self) -> GatewayState:
        with self._lock:
            return self._state

    # ------------------------------------------------------------------
    # Connection status
    # ------------------------------------------------------------------

    def update_market_connection(self, state: ConnectionState) -> None:
        with self._lock:
            self._market_conn = state

    def update_trade_connection(self, state: ConnectionState) -> None:
        with self._lock:
            self._trade_conn = state

    # ------------------------------------------------------------------
    # Heartbeat & activity
    # ------------------------------------------------------------------

    def record_heartbeat(self) -> None:
        with self._lock:
            self._last_heartbeat = time.time()

    def record_tick(self) -> None:
        with self._lock:
            self._tick_count += 1
            self._last_tick_time = time.time()

    def record_order(self) -> None:
        with self._lock:
            self._order_count += 1
            self._last_order_time = time.time()

    # ------------------------------------------------------------------
    # Counters (fed from SubscriptionManager)
    # ------------------------------------------------------------------

    def update_counts(self, *, strategy_count: int = 0, symbol_count: int = 0) -> None:
        with self._lock:
            self._strategy_count = strategy_count
            self._symbol_count = symbol_count

    # ------------------------------------------------------------------
    # Error recording
    # ------------------------------------------------------------------

    def record_error(self, source: str, message: str) -> None:
        with self._lock:
            self._errors.append(ErrorRecord(timestamp=time.time(), source=source, message=message))

    # ------------------------------------------------------------------
    # Extra metadata
    # ------------------------------------------------------------------

    def set_extra(self, key: str, value: Any) -> None:
        with self._lock:
            self._extra[key] = value

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    @property
    def is_healthy(self) -> bool:
        with self._lock:
            return (
                self._state == GatewayState.RUNNING
                and self._market_conn == ConnectionState.CONNECTED
            )

    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable health snapshot."""
        now = time.time()
        with self._lock:
            uptime = now - self._start_time if self._start_time > 0 else 0.0
            heartbeat_age = now - self._last_heartbeat if self._last_heartbeat > 0 else None
            return {
                "state": self._state.value,
                "is_healthy": (
                    self._state == GatewayState.RUNNING
                    and self._market_conn == ConnectionState.CONNECTED
                ),
                "market_connection": self._market_conn.value,
                "trade_connection": self._trade_conn.value,
                "uptime_sec": round(uptime, 2),
                "last_heartbeat": self._last_heartbeat or None,
                "heartbeat_age_sec": round(heartbeat_age, 2) if heartbeat_age is not None else None,
                "last_tick_time": self._last_tick_time or None,
                "last_order_time": self._last_order_time or None,
                "strategy_count": self._strategy_count,
                "symbol_count": self._symbol_count,
                "tick_count": self._tick_count,
                "order_count": self._order_count,
                "recent_errors": [
                    {
                        "timestamp": e.timestamp,
                        "source": e.source,
                        "message": e.message,
                    }
                    for e in self._errors
                ],
                **self._extra,
            }
