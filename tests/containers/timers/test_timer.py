"""Tests for Timer container."""

from __future__ import annotations

from bt_api_py.containers.timers.timer import TimerData


class TestTimerData:
    """Tests for TimerData container."""

    def test_init_with_string(self):
        """Test initialization with string data."""
        timer = TimerData("test_data")

        assert timer.event_type == "Timer_update"
        assert timer.data == "test_data"

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        data = {"key": "value", "number": 123}
        timer = TimerData(data)

        assert timer.event_type == "Timer_update"
        assert timer.data == data

    def test_init_with_list(self):
        """Test initialization with list data."""
        data = [1, 2, 3, 4, 5]
        timer = TimerData(data)

        assert timer.event_type == "Timer_update"
        assert timer.data == data

    def test_init_with_none(self):
        """Test initialization with None."""
        timer = TimerData(None)

        assert timer.event_type == "Timer_update"
        assert timer.data is None

    def test_get_data(self):
        """Test get_data method."""
        timer = TimerData({"test": "value"})

        assert timer.get_data() == {"test": "value"}

    def test_str_with_string(self):
        """Test __str__ with string data."""
        timer = TimerData("hello")

        assert str(timer) == "hello"

    def test_str_with_dict(self):
        """Test __str__ with dict data."""
        timer = TimerData({"key": "value"})

        assert str(timer) == "{'key': 'value'}"

    def test_str_with_list(self):
        """Test __str__ with list data."""
        timer = TimerData([1, 2, 3])

        assert str(timer) == "[1, 2, 3]"

    def test_str_with_none(self):
        """Test __str__ with None data."""
        timer = TimerData(None)

        assert str(timer) == "None"
