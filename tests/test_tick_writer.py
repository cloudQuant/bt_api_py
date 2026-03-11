"""Tests for TickWriter — buffered Parquet tick recorder."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from bt_api_py.gateway.storage.tick_writer import TICK_SCHEMA, TickWriter


@dataclass
class FakeTick:
    timestamp: float
    symbol: str
    exchange: str = "TEST"
    asset_type: str = "SWAP"
    price: float = 100.0
    volume: float = 1.0
    bid_price: float = 99.5
    ask_price: float = 100.5
    bid_volume: float = 10.0
    ask_volume: float = 20.0


def _make_tick(symbol: str = "BTCUSDT", price: float = 42000.0) -> FakeTick:
    return FakeTick(
        timestamp=time.time(),
        symbol=symbol,
        exchange="BINANCE",
        asset_type="SWAP",
        price=price,
        volume=1.5,
        bid_price=price - 1,
        ask_price=price + 1,
        bid_volume=10.0,
        ask_volume=20.0,
    )


class TestTickWriterBasic:
    def test_write_and_manual_flush(self, tmp_path):
        writer = TickWriter(tmp_path, "BINANCE", "SWAP", flush_count=9999)
        writer.write(_make_tick("BTCUSDT"))
        writer.write(_make_tick("BTCUSDT"))
        assert writer.buffered_count == 2
        assert writer._total_written == 2

        writer._flush_all()
        assert writer.buffered_count == 0
        assert writer._total_flushed == 2

    def test_flush_on_count_threshold(self, tmp_path):
        writer = TickWriter(tmp_path, "BINANCE", "SWAP", flush_count=3)
        writer.write(_make_tick("ETHUSDT"))
        writer.write(_make_tick("ETHUSDT"))
        assert writer.buffered_count == 2

        writer.write(_make_tick("ETHUSDT"))  # triggers flush
        assert writer._total_flushed == 3
        assert writer.buffered_count == 0

    def test_parquet_file_created(self, tmp_path):
        writer = TickWriter(tmp_path, "OKX", "SWAP", flush_count=2)
        writer.write(_make_tick("BTC-USDT-SWAP", price=50000))
        writer.write(_make_tick("BTC-USDT-SWAP", price=50001))

        # Find the parquet file
        parquet_files = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) == 1
        assert "BTC-USDT-SWAP.parquet" in parquet_files[0].name

        table = pq.read_table(str(parquet_files[0]))
        assert table.num_rows == 2
        assert table.schema == TICK_SCHEMA
        prices = table.column("price").to_pylist()
        assert 50000.0 in prices
        assert 50001.0 in prices

    def test_directory_layout(self, tmp_path):
        writer = TickWriter(tmp_path, "BINANCE", "SWAP", flush_count=1)
        writer.write(_make_tick("BTCUSDT"))

        # Should be: base_dir/BINANCE/SWAP/{YYYYMMDD}/BTCUSDT.parquet
        parquet_files = list(tmp_path.rglob("*.parquet"))
        assert len(parquet_files) == 1
        parts = parquet_files[0].relative_to(tmp_path).parts
        assert parts[0] == "BINANCE"
        assert parts[1] == "SWAP"
        assert len(parts[2]) == 8  # YYYYMMDD
        assert parts[3] == "BTCUSDT.parquet"

    def test_append_to_existing(self, tmp_path):
        writer = TickWriter(tmp_path, "BINANCE", "SWAP", flush_count=1)
        writer.write(_make_tick("BTCUSDT", price=100))
        writer.write(_make_tick("BTCUSDT", price=200))

        parquet_files = list(tmp_path.rglob("BTCUSDT.parquet"))
        assert len(parquet_files) == 1
        table = pq.read_table(str(parquet_files[0]))
        assert table.num_rows == 2

    def test_multiple_symbols(self, tmp_path):
        writer = TickWriter(tmp_path, "BINANCE", "SWAP", flush_count=1)
        writer.write(_make_tick("BTCUSDT"))
        writer.write(_make_tick("ETHUSDT"))

        parquet_files = sorted(f.name for f in tmp_path.rglob("*.parquet"))
        assert parquet_files == ["BTCUSDT.parquet", "ETHUSDT.parquet"]


class TestTickWriterLifecycle:
    def test_start_stop(self, tmp_path):
        writer = TickWriter(
            tmp_path, "BINANCE", "SWAP",
            flush_count=9999, flush_interval_sec=0.2,
        )
        writer.start()
        assert writer._running is True

        writer.write(_make_tick("BTCUSDT"))
        time.sleep(0.5)  # wait for flush loop
        assert writer._total_flushed >= 1

        writer.stop()
        assert writer._running is False

    def test_stop_flushes_remaining(self, tmp_path):
        writer = TickWriter(
            tmp_path, "BINANCE", "SWAP",
            flush_count=9999, flush_interval_sec=999,
        )
        writer.start()
        writer.write(_make_tick("BTCUSDT"))
        writer.write(_make_tick("BTCUSDT"))
        writer.stop()

        assert writer._total_flushed == 2
        assert writer.buffered_count == 0

    def test_double_start_is_safe(self, tmp_path):
        writer = TickWriter(tmp_path, "BINANCE", "SWAP")
        writer.start()
        writer.start()  # should not crash or create second thread
        writer.stop()


class TestTickWriterDiagnostics:
    def test_snapshot(self, tmp_path):
        writer = TickWriter(tmp_path, "BINANCE", "SWAP")
        writer.write(_make_tick("BTCUSDT"))
        snap = writer.snapshot()
        assert snap["exchange"] == "BINANCE"
        assert snap["asset_type"] == "SWAP"
        assert snap["total_written"] == 1
        assert snap["total_flushed"] == 0
        assert "BTCUSDT" in snap["buffered"]

    def test_buffered_count(self, tmp_path):
        writer = TickWriter(tmp_path, "BINANCE", "SWAP", flush_count=9999)
        assert writer.buffered_count == 0
        writer.write(_make_tick("A"))
        writer.write(_make_tick("B"))
        writer.write(_make_tick("A"))
        assert writer.buffered_count == 3
