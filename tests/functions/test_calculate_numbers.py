"""Tests for functions/calculate_numbers.py."""

from types import SimpleNamespace

import pytest

from bt_api_py.functions.calculate_numbers import (
    allocate_value_to_arr,
    cal_sum_of_key_values,
    merge_zheng_order,
    normalise_hedge_data_to_base,
    round_number,
)


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


class TestNormaliseHedgeDataToBase:
    def test_equal_price_units(self):
        depth = SimpleNamespace(
            bid_price_list=[10.12, 10.02],
            bid_volume_list=[100, 200],
            ask_price_list=[10.15, 10.25],
            ask_volume_list=[150, 250],
        )
        params = {
            "base_spot_price_unit": 0.1,
            "hedge_swap_price_unit": 0.1,
            "qty_percent": 50,
            "base_spot_qty_unit": 10,
        }

        bid_price, bid_vol, ask_price, ask_vol = normalise_hedge_data_to_base(depth, params)

        assert bid_price == [10.1, 10.0]
        assert ask_price == [10.2, 10.3]
        assert bid_vol == [5, 10]
        assert ask_vol == [7, 12]

    def test_different_price_units_merges_same_prices(self):
        depth = SimpleNamespace(
            bid_price_list=[10.12, 10.18, 10.28],
            bid_volume_list=[100, 50, 20],
            ask_price_list=[10.11, 10.19, 10.31],
            ask_volume_list=[100, 50, 25],
        )
        params = {
            "base_spot_price_unit": 0.1,
            "hedge_swap_price_unit": 0.01,
            "qty_percent": 100,
            "base_spot_qty_unit": 10,
        }

        bid_price, bid_vol, ask_price, ask_vol = normalise_hedge_data_to_base(depth, params)

        assert bid_price == [10.1, 10.2]
        assert bid_vol == [15, 2]
        assert ask_price == [10.2, 10.4]
        assert ask_vol == [15, 2]


class TestMergeZhengOrder:
    def test_empty_orders(self):
        assert merge_zheng_order({}) == {}

    def test_one_sided_orders_return_original(self):
        orders = {"a": {"qty_wbf": 1, "price_binance": 10, "price_wbf": 9}}
        assert merge_zheng_order(orders) == orders

    def test_positive_net_keeps_best_long_orders(self):
        orders = {
            "long_best": {"qty_wbf": 5, "price_binance": 11, "price_wbf": 10},
            "long_worse": {"qty_wbf": 4, "price_binance": 10.5, "price_wbf": 10},
            "short": {"qty_wbf": -3, "price_binance": 9.5, "price_wbf": 10},
        }

        result = merge_zheng_order(orders)

        assert set(result.keys()) == {"long_best", "long_worse"}
        assert result["long_best"]["qty_wbf"] == 2
        assert result["long_worse"]["qty_wbf"] == 4

    def test_negative_net_keeps_best_short_orders(self):
        orders = {
            "short_best": {"qty_wbf": -5, "price_binance": 9, "price_wbf": 10},
            "short_worse": {"qty_wbf": -4, "price_binance": 9.5, "price_wbf": 10},
            "long": {"qty_wbf": 3, "price_binance": 11, "price_wbf": 10},
        }

        result = merge_zheng_order(orders)

        assert set(result.keys()) == {"short_best", "short_worse"}
        assert result["short_best"]["qty_wbf"] == -2
        assert result["short_worse"]["qty_wbf"] == -4


class TestCalSumOfKeyValues:
    def test_sum_of_key_values(self):
        orders = {"a": {"qty_wbf": 1.5}, "b": {"qty_wbf": -0.5}, "c": {"qty_wbf": 2.0}}
        assert cal_sum_of_key_values(orders, "qty_wbf") == 3.0
