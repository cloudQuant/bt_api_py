"""TickWriter — buffered Parquet tick recorder.

Receives standardised ``GatewayTick`` objects, buffers them per symbol,
and flushes to Parquet files when a buffer reaches *flush_count* ticks
or *flush_interval_sec* seconds have elapsed.

Directory layout::

    {base_dir}/{exchange}/{asset_type}/{trading_day}/{symbol}.parquet

Thread-safe: the ``write`` method can be called from the runtime's
event-dispatch thread while ``_flush_loop`` runs in a background thread.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

# Arrow schema for tick records
TICK_SCHEMA = pa.schema(
    [
        ("timestamp", pa.float64()),
        ("symbol", pa.utf8()),
        ("exchange", pa.utf8()),
        ("asset_type", pa.utf8()),
        ("price", pa.float64()),
        ("volume", pa.float64()),
        ("bid_price", pa.float64()),
        ("ask_price", pa.float64()),
        ("bid_volume", pa.float64()),
        ("ask_volume", pa.float64()),
    ]
)


class TickWriter:
    """Buffered, per-symbol Parquet tick writer.

    Args:
        base_dir: Root directory for tick storage.
        exchange: Exchange identifier (e.g. "BINANCE").
        asset_type: Asset type (e.g. "SWAP").
        flush_count: Flush buffer when it reaches this many ticks.
        flush_interval_sec: Flush buffer after this many seconds.
        trading_day_fmt: strftime format for the trading-day subdirectory.

    Usage::

        writer = TickWriter("/data/ticks", "BINANCE", "SWAP")
        writer.start()
        writer.write(gateway_tick)
        ...
        writer.stop()
    """

    def __init__(
        self,
        base_dir: str | Path,
        exchange: str,
        asset_type: str,
        *,
        flush_count: int = 1000,
        flush_interval_sec: float = 5.0,
        trading_day_fmt: str = "%Y%m%d",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.exchange = exchange.upper()
        self.asset_type = asset_type.upper()
        self.flush_count = flush_count
        self.flush_interval_sec = flush_interval_sec
        self.trading_day_fmt = trading_day_fmt

        self._lock = threading.Lock()
        # symbol -> list of tick dicts
        self._buffers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._last_flush_time: float = time.time()
        self._running = False
        self._flush_thread: threading.Thread | None = None

        # Stats
        self._total_written: int = 0
        self._total_flushed: int = 0
        self._flush_errors: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background flush loop."""
        if self._running:
            return
        self._running = True
        self._last_flush_time = time.time()
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="tick-writer-flush"
        )
        self._flush_thread.start()
        logger.info(
            "TickWriter started: %s/%s -> %s",
            self.exchange,
            self.asset_type,
            self.base_dir,
        )

    def stop(self) -> None:
        """Stop the flush loop and flush remaining buffers."""
        self._running = False
        if self._flush_thread is not None:
            self._flush_thread.join(timeout=10.0)
            if self._flush_thread.is_alive():
                logger.warning(
                    "TickWriter flush thread did not stop within timeout for %s/%s",
                    self.exchange,
                    self.asset_type,
                )
            self._flush_thread = None
        self._flush_all()
        logger.info(
            "TickWriter stopped. total_written=%d total_flushed=%d errors=%d",
            self._total_written,
            self._total_flushed,
            self._flush_errors,
        )

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def write(self, tick: Any) -> None:
        """Buffer a single GatewayTick for writing.

        Args:
            tick: A ``GatewayTick`` instance (or any object with
                  timestamp, symbol, exchange, price, volume, etc.).
        """
        row = {
            "timestamp": getattr(tick, "timestamp", 0.0),
            "symbol": getattr(tick, "symbol", ""),
            "exchange": getattr(tick, "exchange", self.exchange),
            "asset_type": getattr(tick, "asset_type", self.asset_type),
            "price": getattr(tick, "price", 0.0),
            "volume": getattr(tick, "volume", 0.0),
            "bid_price": getattr(tick, "bid_price", 0.0),
            "ask_price": getattr(tick, "ask_price", 0.0),
            "bid_volume": getattr(tick, "bid_volume", 0.0),
            "ask_volume": getattr(tick, "ask_volume", 0.0),
        }
        symbol = row["symbol"]
        flush_needed = False
        with self._lock:
            self._buffers[symbol].append(row)
            self._total_written += 1
            if len(self._buffers[symbol]) >= self.flush_count:
                flush_needed = True

        if flush_needed:
            self._flush_symbol(symbol)

    # ------------------------------------------------------------------
    # Flush logic
    # ------------------------------------------------------------------

    def _flush_loop(self) -> None:
        """Background loop: flush all buffers on interval."""
        while self._running:
            time.sleep(min(self.flush_interval_sec, 1.0))
            elapsed = time.time() - self._last_flush_time
            if elapsed >= self.flush_interval_sec:
                self._flush_all()
                self._last_flush_time = time.time()

    def _flush_all(self) -> None:
        """Flush all symbol buffers."""
        with self._lock:
            symbols = list(self._buffers.keys())
        for sym in symbols:
            self._flush_symbol(sym)

    def _flush_symbol(self, symbol: str) -> None:
        """Flush the buffer for a single symbol to Parquet."""
        with self._lock:
            rows = self._buffers.pop(symbol, [])
        if not rows:
            return

        try:
            trading_day = self._trading_day(rows[0].get("timestamp", time.time()))
            out_dir = (
                self.base_dir / self.exchange / self.asset_type / trading_day
            )
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{symbol}.parquet"

            table = pa.table(
                {col: [r[col] for r in rows] for col in TICK_SCHEMA.names},
                schema=TICK_SCHEMA,
            )

            if out_path.exists():
                existing = pq.read_table(str(out_path), schema=TICK_SCHEMA)
                table = pa.concat_tables([existing, table])

            pq.write_table(table, str(out_path), compression="snappy")
            self._total_flushed += len(rows)
            logger.debug(
                "TickWriter flushed %d rows for %s -> %s", len(rows), symbol, out_path
            )
        except Exception:
            self._flush_errors += 1
            logger.exception("TickWriter flush error for %s", symbol)
            # Re-queue rows for retry
            with self._lock:
                self._buffers[symbol] = rows + self._buffers.get(symbol, [])

    def _trading_day(self, timestamp: float) -> str:
        """Derive trading-day string from a unix timestamp."""
        dt = datetime.fromtimestamp(timestamp, tz=UTC)
        return dt.strftime(self.trading_day_fmt)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def buffered_count(self) -> int:
        """Total ticks currently buffered across all symbols."""
        with self._lock:
            return sum(len(v) for v in self._buffers.values())

    def snapshot(self) -> dict[str, Any]:
        """Return a serialisable snapshot for diagnostics."""
        with self._lock:
            buf_counts = {sym: len(rows) for sym, rows in self._buffers.items()}
        return {
            "exchange": self.exchange,
            "asset_type": self.asset_type,
            "base_dir": str(self.base_dir),
            "total_written": self._total_written,
            "total_flushed": self._total_flushed,
            "flush_errors": self._flush_errors,
            "buffered": buf_counts,
        }
