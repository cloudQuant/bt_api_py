"""Tests for decorators module."""

import asyncio
import warnings

import pytest

from bt_api_py.functions.decorators import deprecated, time_async_function, time_this_function


class TestDeprecated:
    """Tests for deprecated decorator."""

    def test_deprecated_default_message(self):
        """Test deprecated decorator with default message."""

        @deprecated()
        def old_function():
            return "result"

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = old_function()

            assert result == "result"
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
            assert "old_function is deprecated" in str(w[0].message)

    def test_deprecated_custom_message(self):
        """Test deprecated decorator with custom message."""

        @deprecated(msg="Use new_function instead")
        def old_function():
            return "result"

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = old_function()

            assert result == "result"
            assert len(w) == 1
            assert "Use new_function instead" in str(w[0].message)

    def test_deprecated_with_args(self):
        """Test deprecated decorator with arguments."""

        @deprecated(msg="This is deprecated")
        def old_function(x, y):
            return x + y

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = old_function(1, 2)

            assert result == 3
            assert len(w) == 1

    def test_deprecated_with_kwargs(self):
        """Test deprecated decorator with keyword arguments."""

        @deprecated()
        def old_function(x, y=10):
            return x + y

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = old_function(5, y=15)

            assert result == 20
            assert len(w) == 1


class TestTimeThisFunction:
    """Tests for time_this_function decorator."""

    @pytest.mark.asyncio
    async def test_time_this_function(self):
        """Test timing async function."""

        @time_this_function
        async def slow_function():
            await asyncio.sleep(0.01)
            return "done"

        result = await slow_function()

        assert result == "done"

    @pytest.mark.asyncio
    async def test_time_this_function_with_args(self):
        """Test timing async function with arguments."""

        @time_this_function
        async def add_function(a, b):
            await asyncio.sleep(0.01)
            return a + b

        result = await add_function(1, 2)

        assert result == 3

    @pytest.mark.asyncio
    async def test_time_this_function_with_exception(self):
        """Test timing async function that raises exception."""

        @time_this_function
        async def failing_function():
            await asyncio.sleep(0.01)
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            await failing_function()


class TestTimeAsyncFunction:
    """Tests for time_async_function decorator."""

    @pytest.mark.asyncio
    async def test_time_async_function(self, capsys):
        """Test timing async function with print output."""

        @time_async_function()
        async def slow_function():
            await asyncio.sleep(0.01)
            return "done"

        result = await slow_function()

        assert result == "done"
        captured = capsys.readouterr()
        assert "start" in captured.out
        assert "consume:" in captured.out

    @pytest.mark.asyncio
    async def test_time_async_function_with_args(self, capsys):
        """Test timing async function with arguments."""

        @time_async_function()
        async def add_function(a, b):
            await asyncio.sleep(0.01)
            return a + b

        result = await add_function(1, 2)

        assert result == 3
        captured = capsys.readouterr()
        assert "start" in captured.out

    @pytest.mark.asyncio
    async def test_time_async_function_with_exception(self, capsys):
        """Test timing async function that raises exception."""

        @time_async_function()
        async def failing_function():
            await asyncio.sleep(0.01)
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            await failing_function()

        captured = capsys.readouterr()
        assert "start" in captured.out
        assert "consume:" in captured.out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
