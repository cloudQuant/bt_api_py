"""Tests for functions/calculate_numbers.py."""

import pytest

from bt_api_py.functions.calculate_numbers import round_number, allocate_value_to_arr


class TestRoundNumber:
    """Tests for round_number function."""

    def test_round_down(self):
        """Test rounding down."""
        result = round_number(1.23, 0.1, "down")
        assert result == 1.2

    def test_round_up(self):
        """Test rounding up."""
        result = round_number(1.21, 0.1, "up")
        assert result == 1.3

    def test_round_with_small_unit(self):
        """Test rounding with small unit."""
        result = round_number(1.234, 0.01, "down")
        assert result == 1.23

    def test_round_random(self):
        """Test random rounding returns valid value."""
        import random
        random.seed(42)  # Set seed for reproducibility
        result = round_number(1.25, 0.1, "random")
        # Random should give either 1.2 or 1.3
        assert result in [1.2, 1.3]


class TestAllocateValueToArr:
    """Tests for allocate_value_to_arr function."""

    def test_allocate_below_total(self):
        """Test allocation below total capacity."""
        arr = [100, 100, 100]
        target = 150
        result = allocate_value_to_arr(arr, target)
        assert sum(result) == 150

    def test_allocate_above_total(self):
        """Test allocation above total capacity."""
        arr = [100, 100, 100]
        target = 400
        result = allocate_value_to_arr(arr, target)
        # Total capacity is 300, so should return original array
        assert result == arr

    def test_allocate_empty_array(self):
        """Test allocation with empty array."""
        result = allocate_value_to_arr([], 100)
        assert result == []

    def test_allocate_zero_target(self):
        """Test allocation with zero target."""
        arr = [100, 100, 100]
        result = allocate_value_to_arr(arr, 0)
        assert sum(result) == 0

    def test_allocate_proportional(self):
        """Test proportional allocation."""
        arr = [50, 100, 150]
        target = 100
        result = allocate_value_to_arr(arr, target)
        # Total is 300, target is 100, so each gets 1/3
        assert abs(sum(result) - 100) < 0.01
