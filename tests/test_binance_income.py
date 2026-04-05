"""Tests for Binance income module."""

import pytest

from bt_api_py.containers.incomes.binance_income import BinanceIncomeData


class TestBinanceIncomeData:
    """Tests for BinanceIncomeData class."""

    def test_init(self):
        """Test initialization."""
        income = BinanceIncomeData(
            {
                "time": 1705315800000,
                "incomeType": "REALIZED_PNL",
                "income": "0.001",
                "asset": "USDT",
            },
            exchange_name="BINANCE",
            symbol_name="BTCUSDT",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert income.exchange_name == "BINANCE"
        assert income.symbol_name == "BTCUSDT"
        assert income.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data method."""
        income_info = {
            "time": 1705315800000,
            "incomeType": "REALIZED_PNL",
            "income": "0.001",
            "asset": "USDT",
        }
        income = BinanceIncomeData(
            income_info,
            exchange_name="BINANCE",
            symbol_name="BTCUSDT",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )
        income.init_data()

        assert income.server_time == 1705315800000.0
        assert income.income_type == "REALIZED_PNL"
        assert income.income_value == 0.001
        assert income.income_asset == "USDT"

    def test_get_all_data(self):
        """Test get_all_data method."""
        income_info = {
            "time": 1705315800000,
            "incomeType": "REALIZED_PNL",
            "income": "0.001",
            "asset": "USDT",
        }
        income = BinanceIncomeData(
            income_info,
            exchange_name="BINANCE",
            symbol_name="BTCUSDT",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )
        income.init_data()
        result = income.get_all_data()

        assert result["exchange_name"] == "BINANCE"
        assert result["symbol_name"] == "BTCUSDT"
        assert result["income_type"] == "REALIZED_PNL"

    def test_get_exchange_name(self):
        """Test get_exchange_name method."""
        income = BinanceIncomeData(
            {},
            exchange_name="BINANCE",
            symbol_name="BTCUSDT",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert income.get_exchange_name() == "BINANCE"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
