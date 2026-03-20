"""Regression tests for quality improvement batch v4.

Covers:
1. AsyncConnectionPool.release semaphore leak fix
2. InstrumentManager.register_many locking efficiency refactor
3. EventBus.on() redundant None check simplification
4. SecureCredentialManager.mask_credential negative visible_chars guard
5. AsyncPooledConnection async context manager
"""

import asyncio

import pytest

from bt_api_py.connection_pool import (
    AsyncConnectionPool,
    AsyncPooledConnection,
    ConnectionPool,
    PooledConnection,
)
from bt_api_py.containers.instrument import AssetType, Instrument
from bt_api_py.event_bus import EventBus
from bt_api_py.instrument_manager import InstrumentManager
from bt_api_py.security import SecureCredentialManager


# ── AsyncConnectionPool.release semaphore fix ────────────────────


class TestAsyncConnectionPoolReleaseFix:
    """Verify that releasing an unknown connection does not leak semaphore permits."""

    def _run(self, coro):
        return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)

    def test_release_unknown_conn_does_not_leak_semaphore(self):
        """Releasing a connection not in _in_use should not reduce pool capacity."""

        async def _test():
            pool = AsyncConnectionPool(factory=lambda: object(), max_size=2)
            c1 = await pool.acquire()
            c2 = await pool.acquire()
            # Pool is at capacity now (2/2 in use)
            await pool.release(c1)
            # Release an unknown connection — should warn but not leak
            await pool.release(object())
            # We should still be able to acquire 2 connections
            c3 = await pool.acquire()
            assert c3 is not None
            await pool.release(c2)
            c4 = await pool.acquire()
            assert c4 is not None
            await pool.release(c3)
            await pool.release(c4)

        self._run(_test())

    def test_double_release_does_not_leak(self):
        """Double-releasing the same connection should not leak semaphore."""

        async def _test():
            pool = AsyncConnectionPool(factory=lambda: object(), max_size=2)
            c1 = await pool.acquire()
            await pool.release(c1)
            # Second release — conn is no longer in _in_use
            await pool.release(c1)
            # Pool should still function normally
            c2 = await pool.acquire()
            c3 = await pool.acquire()
            assert c2 is not None
            assert c3 is not None
            await pool.release(c2)
            await pool.release(c3)

        self._run(_test())

    def test_normal_acquire_release_cycle(self):
        """Normal acquire/release cycle still works correctly."""

        async def _test():
            counter = 0

            def factory():
                nonlocal counter
                counter += 1
                return f"conn-{counter}"

            pool = AsyncConnectionPool(factory=factory, max_size=3)
            conns = []
            for _ in range(3):
                conns.append(await pool.acquire())
            assert len(conns) == 3
            for c in conns:
                await pool.release(c)
            # Re-acquire — should reuse pooled connections
            c = await pool.acquire()
            assert c in conns
            await pool.release(c)

        self._run(_test())


# ── InstrumentManager.register_many refactor ─────────────────────


def _make_instrument(internal: str, venue: str = "BINANCE", venue_symbol: str | None = None):
    return Instrument(
        internal=internal,
        venue=venue,
        venue_symbol=venue_symbol or internal,
        asset_type=AssetType.SPOT,
        base_currency="BTC",
        quote_currency="USDT",
    )


class TestInstrumentManagerRegisterMany:
    """Verify register_many behaves identically after the _register_unlocked refactor."""

    def test_register_many_basic(self):
        mgr = InstrumentManager()
        instruments = [_make_instrument(f"INST_{i}") for i in range(5)]
        mgr.register_many(instruments)
        assert mgr.count() == 5
        for inst in instruments:
            assert mgr.get(inst.internal) is inst

    def test_register_many_updates_existing(self):
        mgr = InstrumentManager()
        inst_v1 = _make_instrument("BTC_USDT", venue_symbol="BTCUSDT")
        mgr.register(inst_v1)
        inst_v2 = _make_instrument("BTC_USDT", venue_symbol="BTCUSDT_V2")
        mgr.register_many([inst_v2])
        assert mgr.count() == 1
        assert mgr.get("BTC_USDT") is inst_v2
        assert mgr.get_by_venue("BINANCE", "BTCUSDT") is None
        assert mgr.get_by_venue("BINANCE", "BTCUSDT_V2") is inst_v2

    def test_register_many_atomicity(self):
        """All items in register_many should be visible after the call."""
        mgr = InstrumentManager()
        instruments = [_make_instrument(f"SYM_{i}", venue_symbol=f"VS_{i}") for i in range(10)]
        mgr.register_many(instruments)
        assert mgr.count() == 10
        for inst in instruments:
            assert mgr.get_by_venue("BINANCE", inst.venue_symbol) is inst

    def test_single_register_still_works(self):
        mgr = InstrumentManager()
        inst = _make_instrument("SOLO")
        mgr.register(inst)
        assert mgr.count() == 1
        assert mgr.get("SOLO") is inst


# ── EventBus.on() redundant None check ──────────────────────────


class TestEventBusOnValidation:
    """Verify that on() still rejects falsy event_type values correctly."""

    def test_none_event_type_raises(self):
        bus = EventBus()
        with pytest.raises(ValueError, match="non-empty"):
            bus.on(None, lambda d: d)

    def test_empty_string_event_type_raises(self):
        bus = EventBus()
        with pytest.raises(ValueError, match="non-empty"):
            bus.on("", lambda d: d)

    def test_valid_event_type_works(self):
        bus = EventBus()
        called = []
        bus.on("test", called.append)
        bus.emit("test", 42)
        assert called == [42]


# ── SecureCredentialManager.mask_credential negative guard ───────


class TestMaskCredentialNegativeGuard:
    """Verify mask_credential handles negative visible_chars gracefully."""

    def test_negative_visible_chars_returns_masked(self):
        result = SecureCredentialManager.mask_credential("abcdefghijkl", visible_chars=-5)
        # After clamping to 0, visible_chars*2=0, len("abcdefghijkl")=12 > 0
        # So it masks the entire string with stars
        assert "*" in result
        # Should not contain any original characters at start/end
        assert not result.startswith("a")
        assert not result.endswith("l")

    def test_zero_visible_chars(self):
        result = SecureCredentialManager.mask_credential("abcdefghijkl", visible_chars=0)
        # All characters masked
        assert result == "*" * 12

    def test_positive_visible_chars_normal(self):
        result = SecureCredentialManager.mask_credential("abcdefghijklmnop", visible_chars=4)
        assert result.startswith("abcd")
        assert result.endswith("mnop")
        assert "****" in result

    def test_empty_credential(self):
        result = SecureCredentialManager.mask_credential("", visible_chars=-1)
        assert result == "****"

    def test_short_credential_with_negative(self):
        # After clamping to 0, visible_chars*2=0, len("ab")=2 > 0
        # So it masks with stars
        result = SecureCredentialManager.mask_credential("ab", visible_chars=-3)
        assert result == "**"


# ── AsyncPooledConnection ────────────────────────────────────────


class TestAsyncPooledConnection:
    """Verify the new AsyncPooledConnection context manager."""

    def _run(self, coro):
        return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)

    def test_basic_usage(self):
        async def _test():
            pool = AsyncConnectionPool(factory=lambda: "conn", max_size=2)
            async with AsyncPooledConnection(pool) as conn:
                assert conn == "conn"
            # Connection should be back in the pool
            avail = pool._pool
            assert len(avail) == 1

        self._run(_test())

    def test_exception_releases_connection(self):
        async def _test():
            pool = AsyncConnectionPool(factory=lambda: "conn", max_size=1)
            with pytest.raises(ValueError):
                async with AsyncPooledConnection(pool) as conn:
                    raise ValueError("test error")
            # Connection should still be released back to pool
            c = await pool.acquire()
            assert c == "conn"
            await pool.release(c)

        self._run(_test())

    def test_multiple_sequential_uses(self):
        async def _test():
            counter = 0

            def factory():
                nonlocal counter
                counter += 1
                return f"conn-{counter}"

            pool = AsyncConnectionPool(factory=factory, max_size=1)
            results = []
            for _ in range(3):
                async with AsyncPooledConnection(pool) as conn:
                    results.append(conn)
            # Should reuse the same connection
            assert results == ["conn-1", "conn-1", "conn-1"]

        self._run(_test())


# ── PooledConnection (sync) still works ──────────────────────────


class TestPooledConnectionSync:
    """Sanity check that existing PooledConnection still works after edits."""

    def test_sync_pooled_connection(self):
        pool = ConnectionPool(factory=lambda: "sync_conn", max_size=2)
        pool.start()
        try:
            with PooledConnection(pool) as conn:
                assert conn == "sync_conn"
            avail, in_use = pool.size()
            assert in_use == 0
            assert avail >= 1
        finally:
            pool.stop()
