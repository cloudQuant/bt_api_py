"""Tests for timer module."""

import pytest

from bt_api_py.containers.timers.timer import TimerData


class TestTimerData:
    """Tests for TimerData class."""

    def test_init(self):
        """Test initialization."""
        timer = TimerData({"time": 1705315800000})

        assert timer.event_type == "Timer_update"
        assert timer.data == {"time": 1705315800000}

    def test_init_with_string(self):
        """Test initialization with string data."""
        timer = TimerData("timer_data")

        assert timer.event_type == "Timer_update"
        assert timer.data == "timer_data"

    def test_get_data(self):
        """Test get_data method."""
        timer = TimerData({"time": 1705315800000})

        assert timer.get_data() == {"time": 1705315800000}

    def test_str(self):
        """Test __str__ method."""
        timer = TimerData({"time": 1705315800000})

        assert str(timer) == "{'time': 1705315800000}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
