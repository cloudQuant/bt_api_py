"""Tests for gateway core modules: SubscriptionManager, OrderIdentityMap, OrderRefAllocator."""

from __future__ import annotations

import json
import threading

from bt_api_py.gateway.order_identity_map import OrderIdentityMap
from bt_api_py.gateway.order_ref_allocator import OrderRefAllocator
from bt_api_py.gateway.subscription_manager import SubscriptionManager

# ---------------------------------------------------------------------------
# SubscriptionManager
# ---------------------------------------------------------------------------


class TestSubscriptionManager:
    def test_add_returns_newly_subscribed(self):
        mgr = SubscriptionManager()
        added = mgr.add("s1", ["A", "B"])
        assert added == {"A", "B"}

    def test_add_duplicate_symbol_same_strategy(self):
        mgr = SubscriptionManager()
        mgr.add("s1", ["A"])
        added = mgr.add("s1", ["A"])
        assert added == set()

    def test_add_shared_symbol_different_strategies(self):
        mgr = SubscriptionManager()
        mgr.add("s1", ["A", "B"])
        added = mgr.add("s2", ["B", "C"])
        assert added == {"C"}  # B already subscribed

    def test_ref_count(self):
        mgr = SubscriptionManager()
        mgr.add("s1", ["A"])
        mgr.add("s2", ["A"])
        assert mgr.ref_count("A") == 2
        assert mgr.ref_count("X") == 0

    def test_remove_returns_unsubscribed(self):
        mgr = SubscriptionManager()
        mgr.add("s1", ["A", "B"])
        mgr.add("s2", ["A"])
        removed = mgr.remove("s1", ["A", "B"])
        assert removed == {"B"}  # A still held by s2

    def test_remove_last_ref(self):
        mgr = SubscriptionManager()
        mgr.add("s1", ["A"])
        removed = mgr.remove("s1", ["A"])
        assert removed == {"A"}
        assert mgr.symbol_count == 0

    def test_remove_strategy(self):
        mgr = SubscriptionManager()
        mgr.add("s1", ["A", "B"])
        mgr.add("s2", ["B", "C"])
        removed = mgr.remove_strategy("s1")
        assert removed == {"A"}  # B still held by s2
        assert mgr.get_active_symbols() == {"B", "C"}
        assert mgr.strategy_count == 1

    def test_get_active_symbols(self):
        mgr = SubscriptionManager()
        mgr.add("s1", ["A"])
        mgr.add("s2", ["B"])
        assert mgr.get_active_symbols() == {"A", "B"}

    def test_get_strategy_symbols(self):
        mgr = SubscriptionManager()
        mgr.add("s1", ["A", "B"])
        assert mgr.get_strategy_symbols("s1") == {"A", "B"}
        assert mgr.get_strategy_symbols("unknown") == set()

    def test_get_strategies(self):
        mgr = SubscriptionManager()
        mgr.add("s1", ["A"])
        mgr.add("s2", ["B"])
        assert sorted(mgr.get_strategies()) == ["s1", "s2"]

    def test_symbol_count_and_strategy_count(self):
        mgr = SubscriptionManager()
        assert mgr.symbol_count == 0
        assert mgr.strategy_count == 0
        mgr.add("s1", ["A", "B", "C"])
        assert mgr.symbol_count == 3
        assert mgr.strategy_count == 1

    def test_snapshot(self):
        mgr = SubscriptionManager()
        mgr.add("s1", ["A", "B"])
        snap = mgr.snapshot()
        assert snap["strategy_count"] == 1
        assert snap["symbol_count"] == 2
        assert "s1" in snap["strategies"]
        assert snap["ref_counts"]["A"] == 1

    def test_thread_safety(self):
        mgr = SubscriptionManager()
        errors = []

        def worker(sid: str, symbols: list[str]):
            try:
                mgr.add(sid, symbols)
                mgr.get_active_symbols()
                mgr.remove(sid, symbols[:1])
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(f"s{i}", [f"SYM{j}" for j in range(10)]))
            for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


# ---------------------------------------------------------------------------
# OrderIdentityMap
# ---------------------------------------------------------------------------


class TestOrderIdentityMap:
    def test_register_and_lookup_by_request(self):
        oid = OrderIdentityMap()
        entry = oid.register("req-1", "strat-1", symbol="BTCUSDT")
        assert entry.request_id == "req-1"
        assert entry.strategy_id == "strat-1"
        assert oid.by_request("req-1") is entry

    def test_set_client_order_id(self):
        oid = OrderIdentityMap()
        oid.register("req-1", "strat-1")
        oid.set_client_order_id("req-1", "cli-123")
        assert oid.by_client("cli-123").request_id == "req-1"

    def test_set_venue_order_id(self):
        oid = OrderIdentityMap()
        oid.register("req-1", "strat-1")
        oid.set_venue_order_id("req-1", "venue-456")
        entry = oid.by_venue("venue-456")
        assert entry is not None
        assert entry.strategy_id == "strat-1"

    def test_set_venue_by_client(self):
        oid = OrderIdentityMap()
        oid.register("req-1", "strat-1", client_order_id="cli-1")
        oid.set_venue_order_id_by_client("cli-1", "venue-1")
        assert oid.by_venue("venue-1").request_id == "req-1"

    def test_strategy_for_request_and_venue(self):
        oid = OrderIdentityMap()
        oid.register("req-1", "strat-1", venue_order_id="v1")
        assert oid.strategy_for_request("req-1") == "strat-1"
        assert oid.strategy_for_venue("v1") == "strat-1"
        assert oid.strategy_for_request("nonexistent") is None

    def test_update_status(self):
        oid = OrderIdentityMap()
        oid.register("req-1", "strat-1")
        oid.update_status("req-1", "filled")
        assert oid.by_request("req-1").status == "filled"

    def test_remove(self):
        oid = OrderIdentityMap()
        oid.register("req-1", "strat-1", client_order_id="c1", venue_order_id="v1")
        removed = oid.remove("req-1")
        assert removed is not None
        assert oid.by_request("req-1") is None
        assert oid.by_client("c1") is None
        assert oid.by_venue("v1") is None
        assert oid.count == 0

    def test_remove_nonexistent(self):
        oid = OrderIdentityMap()
        assert oid.remove("nope") is None

    def test_orders_for_strategy(self):
        oid = OrderIdentityMap()
        oid.register("req-1", "strat-1")
        oid.register("req-2", "strat-1")
        oid.register("req-3", "strat-2")
        orders = oid.orders_for_strategy("strat-1")
        assert len(orders) == 2
        assert all(e.strategy_id == "strat-1" for e in orders)

    def test_count(self):
        oid = OrderIdentityMap()
        assert oid.count == 0
        oid.register("req-1", "s1")
        oid.register("req-2", "s1")
        assert oid.count == 2

    def test_snapshot(self):
        oid = OrderIdentityMap()
        oid.register("req-1", "strat-1", client_order_id="c1", venue_order_id="v1", symbol="X")
        snap = oid.snapshot()
        assert len(snap) == 1
        assert snap[0]["request_id"] == "req-1"
        assert snap[0]["symbol"] == "X"

    def test_register_with_extra(self):
        oid = OrderIdentityMap()
        entry = oid.register("req-1", "s1", side="BUY", price=100.0)
        assert entry.extra["side"] == "BUY"
        assert entry.extra["price"] == 100.0

    def test_set_on_missing_returns_none(self):
        oid = OrderIdentityMap()
        assert oid.set_client_order_id("nope", "c1") is None
        assert oid.set_venue_order_id("nope", "v1") is None
        assert oid.set_venue_order_id_by_client("nope", "v1") is None

    def test_thread_safety(self):
        oid = OrderIdentityMap()
        errors = []

        def worker(i: int):
            try:
                rid = f"req-{i}"
                oid.register(rid, f"strat-{i % 3}", symbol=f"SYM{i}")
                oid.set_client_order_id(rid, f"cli-{i}")
                oid.set_venue_order_id(rid, f"ven-{i}")
                oid.by_venue(f"ven-{i}")
                oid.orders_for_strategy(f"strat-{i % 3}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert oid.count == 50


# ---------------------------------------------------------------------------
# OrderRefAllocator
# ---------------------------------------------------------------------------


class TestOrderRefAllocator:
    def test_next_increments(self, tmp_path):
        alloc = OrderRefAllocator("acc-1", state_dir=tmp_path)
        assert alloc.next() == "1"
        assert alloc.next() == "2"
        assert alloc.next() == "3"

    def test_current_does_not_increment(self, tmp_path):
        alloc = OrderRefAllocator("acc-1", state_dir=tmp_path)
        assert alloc.current() == 0
        alloc.next()
        assert alloc.current() == 1

    def test_persistence(self, tmp_path):
        alloc = OrderRefAllocator("acc-1", state_dir=tmp_path)
        alloc.next()
        alloc.next()
        alloc.next()

        # New allocator loads from state file
        alloc2 = OrderRefAllocator("acc-1", state_dir=tmp_path)
        assert alloc2.current() == 3
        assert alloc2.next() == "4"

    def test_align_with_max(self, tmp_path):
        alloc = OrderRefAllocator("acc-1", state_dir=tmp_path)
        alloc.next()  # 1
        alloc.align_with_max(100)
        assert alloc.current() == 100
        assert alloc.next() == "101"

    def test_align_with_max_no_downgrade(self, tmp_path):
        alloc = OrderRefAllocator("acc-1", state_dir=tmp_path, initial_value=200)
        alloc.align_with_max(100)  # should not go backwards
        assert alloc.current() == 200

    def test_align_with_string(self, tmp_path):
        alloc = OrderRefAllocator("acc-1", state_dir=tmp_path)
        alloc.align_with_max("50")
        assert alloc.current() == 50

    def test_reset(self, tmp_path):
        alloc = OrderRefAllocator("acc-1", state_dir=tmp_path)
        alloc.next()
        alloc.next()
        alloc.reset(0)
        assert alloc.current() == 0
        assert alloc.next() == "1"

    def test_state_file_path(self, tmp_path):
        alloc = OrderRefAllocator("my-acct", state_dir=tmp_path)
        alloc.next()
        expected = tmp_path / "gateway_my-acct_state.json"
        assert expected.exists()
        data = json.loads(expected.read_text())
        assert data["last_order_ref"] == 1

    def test_snapshot(self, tmp_path):
        alloc = OrderRefAllocator("acc-1", state_dir=tmp_path)
        alloc.next()
        snap = alloc.snapshot()
        assert snap["account_id"] == "acc-1"
        assert snap["last_order_ref"] == 1
        assert "state_file" in snap

    def test_corrupt_state_file(self, tmp_path):
        state_file = tmp_path / "gateway_acc-1_state.json"
        state_file.write_text("NOT JSON!!!", encoding="utf-8")
        alloc = OrderRefAllocator("acc-1", state_dir=tmp_path)
        # Should not crash, falls back to initial
        assert alloc.current() == 0
        assert alloc.next() == "1"

    def test_thread_safety(self, tmp_path):
        alloc = OrderRefAllocator("acc-1", state_dir=tmp_path)
        results = []
        errors = []

        def worker():
            try:
                for _ in range(100):
                    results.append(int(alloc.next()))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # 5 threads × 100 = 500 unique values
        assert len(set(results)) == 500
        assert alloc.current() == 500
