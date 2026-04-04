"""Tests for timer_event module."""

import time

import pytest

from bt_api_py.functions.timer_event import run_on_timer


class TestRunOnTimer:
    """Tests for run_on_timer function."""

    def test_runs_function_after_interval(self):
        """Test that function runs after specified interval."""
        call_count = 0

        def callback():
            nonlocal call_count
            call_count += 1

        start = time.time()
        run_on_timer(0.1, callback)
        elapsed = time.time() - start

        assert call_count == 1
        assert elapsed >= 0.1

    def test_passes_args(self):
        """Test that args are passed to function."""
        result = []

        def callback(value):
            result.append(value)

        run_on_timer(0.01, callback, "test")

        assert result == ["test"]

    def test_passes_kwargs(self):
        """Test that kwargs are passed to function."""
        result = []

        def callback(key=None):
            result.append(key)

        run_on_timer(0.01, callback, key="value")

        assert result == ["value"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
