"""Unit tests for ExchangeRegistry — both global and instance-level usage."""

import pytest

from bt_api_py.exceptions import ExchangeNotFoundError
from bt_api_py.registry import ExchangeRegistry


class _MockFeed:
    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self.kwargs = kwargs


class _MockExchangeData:
    pass


def _mock_balance_handler(account_list):
    return 100.0, 50.0


class _MockStream:
    pass


# ── 实例级隔离测试 ─────────────────────────────────────────────


class TestRegistryInstance:
    """Test ExchangeRegistry as an isolated instance."""

    def setup_method(self):
        self.reg = ExchangeRegistry()
        self.reg.clear()  # Start each test with clean state (singleton shared across tests)

    def test_register_and_create_feed(self):
        self.reg.register_feed("TEST___SPOT", _MockFeed)
        feed = self.reg.create_feed("TEST___SPOT", data_queue="q1", key="val")
        assert isinstance(feed, _MockFeed)
        assert feed.data_queue == "q1"
        assert feed.kwargs["key"] == "val"

    def test_create_feed_unknown_raises(self):
        with pytest.raises(ExchangeNotFoundError):
            self.reg.create_feed("NONEXISTENT", data_queue="q")

    def test_register_and_create_exchange_data(self):
        self.reg.register_exchange_data("TEST___SPOT", _MockExchangeData)
        ed = self.reg.create_exchange_data("TEST___SPOT")
        assert isinstance(ed, _MockExchangeData)

    def test_create_exchange_data_unknown_raises(self):
        with pytest.raises(ExchangeNotFoundError):
            self.reg.create_exchange_data("NONEXISTENT")

    def test_register_stream(self):
        self.reg.register_stream("TEST___SPOT", "market", _MockStream)
        self.reg.register_stream("TEST___SPOT", "account", _MockStream)
        classes = self.reg.get_stream_classes("TEST___SPOT")
        assert "market" in classes
        assert "account" in classes
        assert self.reg.get_stream_class("TEST___SPOT", "market") is _MockStream

    def test_get_stream_class_missing(self):
        assert self.reg.get_stream_class("NONE", "market") is None

    def test_register_balance_handler(self):
        self.reg.register_balance_handler("TEST___SPOT", _mock_balance_handler)
        handler = self.reg.get_balance_handler("TEST___SPOT")
        assert handler is _mock_balance_handler
        val, cash = handler([])
        assert val == 100.0

    def test_get_balance_handler_missing(self):
        assert self.reg.get_balance_handler("NONE") is None

    def test_has_exchange(self):
        assert not self.reg.has_exchange("TEST___SPOT")
        self.reg.register_feed("TEST___SPOT", _MockFeed)
        assert self.reg.has_exchange("TEST___SPOT")

    def test_list_exchanges(self):
        assert self.reg.list_exchanges() == []
        self.reg.register_feed("A", _MockFeed)
        self.reg.register_feed("B", _MockFeed)
        assert sorted(self.reg.list_exchanges()) == ["A", "B"]

    def test_clear(self):
        self.reg.register_feed("X", _MockFeed)
        self.reg.register_exchange_data("X", _MockExchangeData)
        self.reg.register_stream("X", "market", _MockStream)
        self.reg.register_balance_handler("X", _mock_balance_handler)
        self.reg.clear()
        assert self.reg.list_exchanges() == []
        assert self.reg.get_stream_classes("X") == {}
        assert self.reg.get_balance_handler("X") is None

    def test_instance_isolation(self):
        """ExchangeRegistry is a singleton; reg1 and reg2 are the same instance."""
        reg1 = ExchangeRegistry()
        reg2 = ExchangeRegistry()
        assert reg1 is reg2  # Singleton: same object
        reg1.register_feed("ONLY_IN_REG1", _MockFeed)
        assert reg1.has_exchange("ONLY_IN_REG1")
        assert reg2.has_exchange("ONLY_IN_REG1")  # Same instance, shared state

    def test_create_isolated_does_not_share_state_with_default_registry(self):
        global_reg = ExchangeRegistry()
        global_reg.clear()

        isolated = ExchangeRegistry.create_isolated()
        isolated.register_feed("ISO___SPOT", _MockFeed)

        assert isolated.has_exchange("ISO___SPOT")
        assert not global_reg.has_exchange("ISO___SPOT")


# ── 全局类级调用（向后兼容）测试 ─────────────────────────────────


class TestRegistryGlobal:
    """Test that ExchangeRegistry.method() class-level calls work via metaclass delegation."""

    def setup_method(self):
        # 保存并清空全局状态
        self._saved = ExchangeRegistry._default
        ExchangeRegistry._default = None

    def teardown_method(self):
        # 恢复全局状态
        ExchangeRegistry._default = self._saved

    def test_class_level_register_and_create(self):
        ExchangeRegistry.register_feed("GLOBAL___TEST", _MockFeed)
        assert ExchangeRegistry.has_exchange("GLOBAL___TEST")
        feed = ExchangeRegistry.create_feed("GLOBAL___TEST", data_queue="gq")
        assert isinstance(feed, _MockFeed)

    def test_class_level_list_exchanges(self):
        ExchangeRegistry.register_feed("G1", _MockFeed)
        assert "G1" in ExchangeRegistry.list_exchanges()

    def test_class_level_clear(self):
        ExchangeRegistry.register_feed("G2", _MockFeed)
        ExchangeRegistry.clear()
        assert ExchangeRegistry.list_exchanges() == []
