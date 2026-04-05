"""Unit tests for AutoInitMixin — automatic init_data() triggering."""

from __future__ import annotations

from bt_api_py.containers.auto_init_mixin import AutoInitMixin


class _SampleContainer(AutoInitMixin):
    """A minimal container for testing AutoInitMixin behavior."""

    def __init__(self, raw_data):
        self.raw_data = raw_data
        self.parsed_value = None

    def init_data(self):
        self.parsed_value = self.raw_data * 2

    def get_event(self):
        return "TestEvent"

    def get_parsed_value(self):
        return self.parsed_value


class TestAutoInitMixin:
    def test_auto_init_on_get_method(self):
        """get_* methods should auto-trigger init_data()."""
        c = _SampleContainer(5)
        assert c.parsed_value is None  # not yet initialized
        result = c.get_parsed_value()
        assert result == 10  # init_data was auto-called

    def test_get_event_skips_auto_init(self):
        """get_event should NOT trigger init_data (excluded)."""
        c = _SampleContainer(5)
        event = c.get_event()
        assert event == "TestEvent"
        assert c.parsed_value is None  # init_data was NOT called

    def test_manual_init_data_still_works(self):
        """Calling init_data() manually should parse data correctly."""
        c = _SampleContainer(5)
        c.init_data()
        assert c.parsed_value == 10
        # After manual init_data, get_* should not re-init (ensure_init detects parsed state)
        c._ensure_init()
        assert c.parsed_value == 10  # still the same value

    def test_no_double_init(self):
        """init_data should only run once even with multiple get_* calls."""
        call_count = 0

        class _CountingContainer(AutoInitMixin):
            def __init__(self):
                self.value = 0

            def init_data(self):
                nonlocal call_count
                call_count += 1
                self.value = 42

            def get_value(self):
                return self.value

        c = _CountingContainer()
        c.get_value()
        c.get_value()
        c.get_value()
        assert call_count == 1

    def test_ensure_init_method(self):
        """_ensure_init() should be callable directly."""
        c = _SampleContainer(7)
        c._ensure_init()
        assert c.parsed_value == 14
        assert c._initialized is True
