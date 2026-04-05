"""Unit tests for EventBus publish/subscribe mechanism."""

from __future__ import annotations

import pytest

from bt_api_py.event_bus import ErrorHandlerMode, EventBus
from bt_api_py.exceptions import BtApiError


class TestEventBus:
    def setup_method(self):
        self.bus = EventBus()
        self.received = []

    def _handler(self, data):
        self.received.append(data)

    def test_on_and_emit(self):
        self.bus.on("tick", self._handler)
        self.bus.emit("tick", {"price": 100})
        assert self.received == [{"price": 100}]

    def test_emit_no_handlers(self):
        # Should not raise
        self.bus.emit("unknown_event", {})

    def test_multiple_handlers(self):
        h2_received = []
        self.bus.on("tick", self._handler)
        self.bus.on("tick", lambda d: h2_received.append(d))
        self.bus.emit("tick", "x")
        assert self.received == ["x"]
        assert h2_received == ["x"]

    def test_no_duplicate_registration(self):
        self.bus.on("tick", self._handler)
        self.bus.on("tick", self._handler)  # same handler again
        self.bus.emit("tick", 1)
        assert self.received == [1]  # only called once

    def test_off_removes_handler(self):
        self.bus.on("tick", self._handler)
        self.bus.off("tick", self._handler)
        self.bus.emit("tick", "should_not_receive")
        assert self.received == []

    def test_off_all_handlers(self):
        self.bus.on("tick", self._handler)
        self.bus.on("tick", lambda d: None)
        self.bus.off("tick")  # remove all
        self.bus.emit("tick", "x")
        assert self.received == []

    def test_handler_error_does_not_stop_others(self):
        """A failing handler should not prevent other handlers from executing."""

        def bad_handler(data):
            raise ValueError("boom")

        self.bus.on("tick", bad_handler)
        self.bus.on("tick", self._handler)
        self.bus.emit("tick", "data")
        # _handler should still have received the event
        assert self.received == ["data"]

    def test_different_event_types_isolated(self):
        other = []
        self.bus.on("tick", self._handler)
        self.bus.on("order", lambda d: other.append(d))
        self.bus.emit("tick", "t")
        self.bus.emit("order", "o")
        assert self.received == ["t"]
        assert other == ["o"]


class TestEventBusValidation:
    """Tests for EventBus validation."""

    def test_on_empty_event_type_raises(self):
        """Test that empty event_type raises ValueError."""
        bus = EventBus()

        with pytest.raises(ValueError, match="event_type must be a non-empty string"):
            bus.on("", lambda x: x)

    def test_on_non_callable_handler_raises(self):
        """Test that non-callable handler raises TypeError."""
        bus = EventBus()

        with pytest.raises(TypeError, match="handler must be callable"):
            bus.on("tick", "not_callable")


class TestEventBusErrorModes:
    """Tests for EventBus error handling modes."""

    def test_raise_mode_re_raises_exception(self):
        """Test RAISE mode re-raises exception."""
        bus = EventBus(error_mode=ErrorHandlerMode.RAISE)

        def bad_handler(data):
            raise ValueError("test error")

        bus.on("tick", bad_handler)

        with pytest.raises(ValueError, match="test error"):
            bus.emit("tick", "data")

    def test_log_mode_continues_on_error(self):
        """Test LOG mode continues after error."""
        bus = EventBus(error_mode=ErrorHandlerMode.LOG)
        received = []

        def bad_handler(data):
            raise ValueError("test error")

        def good_handler(data):
            received.append(data)

        bus.on("tick", bad_handler)
        bus.on("tick", good_handler)

        errors = bus.emit("tick", "data")

        assert len(errors) == 1
        assert received == ["data"]

    def test_get_last_errors(self):
        """Test get_last_errors returns error info."""
        bus = EventBus()

        def bad_handler(data):
            raise ValueError("test error")

        bus.on("tick", bad_handler)
        bus.emit("tick", "data")

        errors = bus.get_last_errors()

        assert len(errors) == 1
        assert errors[0][0] == "tick"
        assert isinstance(errors[0][2], ValueError)

    def test_clear_errors(self):
        """Test clear_errors clears error list."""
        bus = EventBus()

        def bad_handler(data):
            raise ValueError("test error")

        bus.on("tick", bad_handler)
        bus.emit("tick", "data")
        bus.clear_errors()

        assert bus.get_last_errors() == []

    def test_has_handlers(self):
        """Test has_handlers method."""
        bus = EventBus()

        assert bus.has_handlers("tick") is False

        bus.on("tick", lambda x: x)

        assert bus.has_handlers("tick") is True

    def test_clear(self):
        """Test clear method clears all handlers."""
        bus = EventBus()
        bus.on("tick", lambda x: x)
        bus.on("order", lambda x: x)

        bus.clear()

        assert bus.has_handlers("tick") is False
        assert bus.has_handlers("order") is False


class TestErrorClassification:
    """Tests for error classification."""

    def test_user_error_classification(self):
        """Test user error types are classified correctly."""
        bus = EventBus()
        received = []

        def type_error_handler(data):
            raise TypeError("type error")

        def value_error_handler(data):
            raise ValueError("value error")

        def attr_error_handler(data):
            raise AttributeError("attr error")

        def key_error_handler(data):
            raise KeyError("key error")

        def index_error_handler(data):
            raise IndexError("index error")

        bus.on("tick", type_error_handler)
        bus.on("tick", value_error_handler)
        bus.on("tick", attr_error_handler)
        bus.on("tick", key_error_handler)
        bus.on("tick", index_error_handler)
        bus.on("tick", lambda d: received.append(d))

        errors = bus.emit("tick", "data")

        assert len(errors) == 5
        assert received == ["data"]

    def test_business_error_classification(self):
        """Test BtApiError is classified as business error."""
        bus = EventBus()

        def business_error_handler(data):
            raise BtApiError("business error")

        bus.on("tick", business_error_handler)
        errors = bus.emit("tick", "data")

        assert len(errors) == 1
        assert isinstance(errors[0], BtApiError)

    def test_system_error_classification(self):
        """Test system error types are classified correctly."""
        bus = EventBus()

        def runtime_error_handler(data):
            raise RuntimeError("runtime error")

        bus.on("tick", runtime_error_handler)
        errors = bus.emit("tick", "data")

        assert len(errors) == 1
        assert isinstance(errors[0], RuntimeError)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
