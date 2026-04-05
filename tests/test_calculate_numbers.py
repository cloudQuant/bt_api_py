"""Tests for calculate_numbers module."""

from __future__ import annotations

import random

import pytest

from bt_api_py.functions.calculate_numbers import (
    allocate_value_to_arr,
    cal_sum_of_key_values,
    merge_zheng_order,
    round_number,
)


class TestRoundNumber:
    """Tests for round_number function."""

    def test_round_down(self):
        """Test rounding down."""
        result = round_number(123.456, 0.1, "down")
        assert result == 123.4

    def test_round_up(self):
        """Test rounding up."""
        result = round_number(123.456, 0.1, "up")
        assert result == 123.5

    def test_round_down_small_unit(self):
        """Test rounding down with small unit."""
        result = round_number(123.456, 0.01, "down")
        assert result == 123.45

    def test_round_up_small_unit(self):
        """Test rounding up with small unit."""
        result = round_number(123.456, 0.01, "up")
        assert result == 123.46

    def test_round_random(self):
        """Test random rounding."""
        random.seed(42)
        result = round_number(123.456, 0.1, "random")
        # Random choice between up and down
        assert result in [123.4, 123.5]

    def test_round_with_exponential_notation(self):
        """Test rounding with exponential notation min_unit."""
        result = round_number(123.456, 1e-2, "down")
        assert abs(result - 123.45) < 0.001

    def test_round_integer_min_unit(self):
        """Test rounding with integer min_unit."""
        result = round_number(123.456, 1, "down")
        assert result == 123.0

    def test_round_up_integer_min_unit(self):
        """Test rounding up with integer min_unit."""
        result = round_number(123.456, 1, "up")
        assert result == 124.0

    def test_round_exact_value(self):
        """Test rounding exact value - rounds to nearest unit."""
        result = round_number(123.4, 0.1, "down")
        # 123.4 // 0.1 = 1233, 1233 * 0.1 = 123.3
        assert result == 123.3

    def test_round_exact_value_up(self):
        """Test rounding exact value up."""
        result = round_number(123.4, 0.1, "up")
        # 123.4 // 0.1 = 1233, 1233 * 0.1 + 0.1 = 123.4
        assert result == 123.4


class TestAllocateValueToArr:
    """Tests for allocate_value_to_arr function."""

    def test_allocate_below_total(self):
        """Test allocation below total capacity."""
        arr = [10, 20, 30, 40]
        target = 50
        result = allocate_value_to_arr(arr, target)

        # 50/100 = 0.5, so each gets half
        assert result == [5, 10, 15, 20]

    def test_allocate_equal_to_total(self):
        """Test allocation equal to total."""
        arr = [10, 20, 30, 40]
        target = 100
        result = allocate_value_to_arr(arr, target)

        # 100/100 = 1, so keep original
        assert result == [10, 20, 30, 40]

    def test_allocate_above_total(self):
        """Test allocation above total capacity."""
        arr = [10, 20, 30, 40]
        target = 150
        result = allocate_value_to_arr(arr, target)

        # 150/100 = 1.5 > 1, so keep original
        assert result == [10, 20, 30, 40]

    def test_allocate_zero_target(self):
        """Test allocation with zero target."""
        arr = [10, 20, 30, 40]
        target = 0
        result = allocate_value_to_arr(arr, target)

        assert result == [0, 0, 0, 0]

    def test_allocate_empty_arr(self):
        """Test allocation with empty array."""
        arr = []
        target = 100
        result = allocate_value_to_arr(arr, target)

        assert result == []

    def test_allocate_single_element(self):
        """Test allocation with single element."""
        arr = [100]
        target = 50
        result = allocate_value_to_arr(arr, target)

        assert result == [50]


class TestMergeZhengOrder:
    """Tests for merge_zheng_order function."""

    def test_empty_dict(self):
        """Test with empty dictionary."""
        result = merge_zheng_order({})
        assert result == {}

    def test_long_only(self):
        """Test with only long positions."""
        orders = {
            "1": {"qty_wbf": 10, "price_binance": 100, "price_wbf": 95},
            "2": {"qty_wbf": 20, "price_binance": 101, "price_wbf": 96},
        }
        result = merge_zheng_order(orders)

        # No short positions, should return unchanged
        assert result == orders

    def test_short_only(self):
        """Test with only short positions."""
        orders = {
            "1": {"qty_wbf": -10, "price_binance": 100, "price_wbf": 105},
            "2": {"qty_wbf": -20, "price_binance": 101, "price_wbf": 106},
        }
        result = merge_zheng_order(orders)

        # No long positions, should return unchanged
        assert result == orders

    def test_net_long_position(self):
        """Test with net long position."""
        orders = {
            "1": {"qty_wbf": 100, "price_binance": 100, "price_wbf": 95},  # Long
            "2": {"qty_wbf": -30, "price_binance": 100, "price_wbf": 105},  # Short
        }
        result = merge_zheng_order(orders)

        # Net = 70 > 0, should merge and remove shorts
        assert len(result) == 1
        assert "1" in result

    def test_net_short_position(self):
        """Test with net short position."""
        orders = {
            "1": {"qty_wbf": 30, "price_binance": 100, "price_wbf": 95},  # Long
            "2": {"qty_wbf": -100, "price_binance": 100, "price_wbf": 105},  # Short
        }
        result = merge_zheng_order(orders)

        # Net = -70 < 0, should merge and remove longs
        assert len(result) == 1
        assert "2" in result

    def test_balanced_position(self):
        """Test with balanced position."""
        orders = {
            "1": {"qty_wbf": 50, "price_binance": 100, "price_wbf": 95},
            "2": {"qty_wbf": -50, "price_binance": 100, "price_wbf": 105},
        }
        result = merge_zheng_order(orders)

        # Net = 0, should return unchanged
        assert result == orders


class TestCalSumOfKeyValues:
    """Tests for cal_sum_of_key_values function."""

    def test_sum_single_key(self):
        """Test summing values for a single key."""
        data = {
            "a": {"value": 10, "other": 1},
            "b": {"value": 20, "other": 2},
            "c": {"value": 30, "other": 3},
        }
        result = cal_sum_of_key_values(data, "value")
        assert result == 60

    def test_sum_empty_dict(self):
        """Test summing empty dictionary."""
        result = cal_sum_of_key_values({}, "value")
        assert result == 0

    def test_sum_negative_values(self):
        """Test summing negative values."""
        data = {
            "a": {"value": -10},
            "b": {"value": -20},
        }
        result = cal_sum_of_key_values(data, "value")
        assert result == -30

    def test_sum_float_values(self):
        """Test summing float values."""
        data = {
            "a": {"value": 10.5},
            "b": {"value": 20.25},
        }
        result = cal_sum_of_key_values(data, "value")
        assert result == 30.75


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
