"""
Thread safety and concurrency tests for bt_api_py
Target: 20+ tests for concurrent access patterns
"""

import threading
import time
from queue import Queue

from bt_api_py.event_bus import EventBus
from bt_api_py.registry import ExchangeRegistry


class MockFeed:
    """Mock feed class for testing"""

    def __init__(self, data_queue=None, **kwargs):
        self.data_queue = data_queue
        self.kwargs = kwargs


class TestRegistryThreadSafety:
    """Test thread safety of ExchangeRegistry"""

    def test_concurrent_feed_registration(self):
        """Registry should handle concurrent feed registrations"""
        registry = ExchangeRegistry()
        errors = []

        def register_exchange(i):
            try:
                registry.register_feed(f"TEST{i}___SPOT", MockFeed)
            except Exception as e:
                errors.append((i, e))

        threads = [threading.Thread(target=register_exchange, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert registry.has_exchange("TEST99___SPOT")
        assert registry.has_exchange("TEST0___SPOT")
        assert registry.has_exchange("TEST50___SPOT")

    def test_concurrent_feed_creation(self):
        """Registry should handle concurrent feed creation"""
        registry = ExchangeRegistry()
        registry.register_feed("TEST___SPOT", MockFeed)
        errors = []
        feeds = []

        def create_feed():
            try:
                feed = registry.create_feed("TEST___SPOT", data_queue="test_queue")
                feeds.append(feed)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_feed) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(feeds) == 50
        for feed in feeds:
            assert isinstance(feed, MockFeed)
            assert feed.data_queue == "test_queue"

    def test_concurrent_stream_registration(self):
        """Registry should handle concurrent stream registrations"""
        registry = ExchangeRegistry()
        errors = []

        def register_stream(i):
            try:
                registry.register_stream("TEST___SPOT", f"stream{i}", MockFeed)
            except Exception as e:
                errors.append((i, e))

        threads = [threading.Thread(target=register_stream, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_concurrent_has_exchange_checks(self):
        """Registry should handle concurrent has_exchange checks"""
        registry = ExchangeRegistry()
        registry.register_feed("TEST1___SPOT", MockFeed)
        registry.register_feed("TEST2___SPOT", MockFeed)
        results = []

        def check_exchange(name):
            result = registry.has_exchange(name)
            results.append(result)

        threads = [
            threading.Thread(target=check_exchange, args=(f"TEST{i}___SPOT",))
            for _ in range(50)
            for i in range(1, 3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 100
        assert all(results)

    def test_concurrent_list_exchanges(self):
        """Registry should handle concurrent list_exchanges calls"""
        registry = ExchangeRegistry()
        for i in range(10):
            registry.register_feed(f"TEST{i}___SPOT", MockFeed)

        errors = []
        results = []

        def get_names():
            try:
                names = registry.list_exchanges()
                results.append(names)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_names) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 50
        for names in results:
            assert len(names) == 10


class TestEventBusThreadSafety:
    """Test thread safety of EventBus"""

    def test_concurrent_event_emission(self):
        """EventBus should handle concurrent event emissions"""
        event_bus = EventBus()
        received_events = []
        errors = []

        def event_handler(event):
            received_events.append(event)

        event_bus.on("test_event", event_handler)

        def emit_event(i):
            try:
                event_bus.emit("test_event", {"id": i})
            except Exception as e:
                errors.append((i, e))

        threads = [threading.Thread(target=emit_event, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(received_events) == 100

    def test_concurrent_subscription(self):
        """EventBus should handle concurrent subscriptions"""
        event_bus = EventBus()
        errors = []

        def subscribe_handler(i):
            try:
                event_bus.on(f"event_{i}", lambda e: None)
            except Exception as e:
                errors.append((i, e))

        threads = [threading.Thread(target=subscribe_handler, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_concurrent_off(self):
        """EventBus should handle concurrent off operations"""
        event_bus = EventBus()
        handlers = []

        for i in range(50):
            handler = lambda e: None
            event_bus.on("test_event", handler)
            handlers.append(handler)

        errors = []

        def unsubscribe_handler(handler):
            try:
                event_bus.off("test_event", handler)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=unsubscribe_handler, args=(handler,)) for handler in handlers
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestConcurrentAPIAccess:
    """Test concurrent API access patterns"""

    def test_concurrent_registry_singleton_access(self):
        """Registry singleton should be thread-safe"""
        registries = []
        errors = []

        def get_registry():
            try:
                registry = ExchangeRegistry()
                registries.append(registry)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=get_registry) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(registries) == 50
        assert all(r is registries[0] for r in registries)


class TestRaceConditions:
    """Test for potential race conditions"""

    def test_registry_registration_race_condition(self):
        """Test race condition in feed registration"""
        registry = ExchangeRegistry()
        success_count = [0]
        lock = threading.Lock()

        def register_and_count():
            try:
                registry.register_feed("SHARED___SPOT", MockFeed)
                with lock:
                    success_count[0] += 1
            except Exception:
                pass

        threads = [threading.Thread(target=register_and_count) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert registry.has_exchange("SHARED___SPOT")

    def test_event_bus_emit_race_condition(self):
        """Test race condition in event emission"""
        event_bus = EventBus()
        events_received = []
        lock = threading.Lock()

        def handler(event):
            with lock:
                events_received.append(event)

        event_bus.on("race_test", handler)

        def emit():
            event_bus.emit("race_test", {"timestamp": time.time()})

        threads = [threading.Thread(target=emit) for _ in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        time.sleep(0.1)
        assert len(events_received) == 30


class TestThreadSafetyWithQueue:
    """Test thread safety with queue operations"""

    def test_concurrent_queue_operations(self):
        """Test concurrent queue put/get operations"""
        data_queue = Queue()
        results = []

        def producer():
            for i in range(10):
                data_queue.put({"id": i})

        def consumer():
            while True:
                try:
                    item = data_queue.get(timeout=0.1)
                    results.append(item)
                    data_queue.task_done()
                except:
                    break

        producer_threads = [threading.Thread(target=producer) for _ in range(5)]
        consumer_threads = [threading.Thread(target=consumer) for _ in range(3)]

        for t in producer_threads:
            t.start()
        for t in producer_threads:
            t.join()

        for t in consumer_threads:
            t.start()
        for t in consumer_threads:
            t.join()

        assert len(results) == 50


class TestStressTesting:
    """Stress testing for thread safety"""

    def test_high_concurrency_registry_access(self):
        """Stress test with high concurrency"""
        registry = ExchangeRegistry()
        errors = []

        def stress_operation(i):
            try:
                if i % 3 == 0:
                    registry.register_feed(f"STRESS{i}___SPOT", MockFeed)
                elif i % 3 == 1:
                    registry.has_exchange(f"STRESS{i - 1}___SPOT")
                else:
                    registry.list_exchanges()
            except Exception as e:
                errors.append((i, e))

        threads = [threading.Thread(target=stress_operation, args=(i,)) for i in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_sustained_concurrent_load(self):
        """Test sustained concurrent load"""
        event_bus = EventBus()
        event_count = [0]
        lock = threading.Lock()

        def handler(event):
            with lock:
                event_count[0] += 1

        event_bus.on("load_test", handler)

        def sustained_emit():
            for _ in range(50):
                event_bus.emit("load_test", {})

        threads = [threading.Thread(target=sustained_emit) for _ in range(10)]
        start_time = time.time()

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        end_time = time.time()
        duration = end_time - start_time

        assert event_count[0] == 500
        assert duration < 5.0


class TestDeadlockPrevention:
    """Test deadlock prevention mechanisms"""

    def test_no_deadlock_on_concurrent_operations(self):
        """Ensure no deadlock occurs during concurrent operations"""
        registry = ExchangeRegistry()
        event_bus = EventBus()
        completed = [0]
        lock = threading.Lock()

        def mixed_operations():
            for _ in range(10):
                registry.register_feed("DEADLOCK_TEST___SPOT", MockFeed)
                registry.has_exchange("DEADLOCK_TEST___SPOT")
                event_bus.emit("test", {})
                with lock:
                    completed[0] += 1

        threads = [threading.Thread(target=mixed_operations) for _ in range(20)]

        for t in threads:
            t.start()

        all_finished = True
        for t in threads:
            t.join(timeout=2.0)
            if t.is_alive():
                all_finished = False

        assert all_finished
        assert completed[0] == 200
