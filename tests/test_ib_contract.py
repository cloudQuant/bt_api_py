"""Tests for IB contract module."""

import pytest

from bt_api_py.containers.ib.ib_contract import IbContract


class TestIbContract:
    """Tests for IbContract class."""

    def test_init(self):
        """Test initialization."""
        contract = IbContract(symbol="AAPL", sec_type="STK", exchange="SMART", currency="USD")

        assert contract.symbol == "AAPL"
        assert contract.sec_type == "STK"
        assert contract.exchange == "SMART"
        assert contract.currency == "USD"

    def test_to_dict(self):
        """Test to_dict method."""
        contract = IbContract(symbol="AAPL", sec_type="STK", exchange="SMART", currency="USD")
        result = contract.to_dict()

        assert "symbol" in result
        assert result["symbol"] == "AAPL"
        assert result["sec_type"] == "STK"

    def test_str(self):
        """Test __str__ method."""
        contract = IbContract(symbol="AAPL", sec_type="STK", exchange="SMART", currency="USD")

        assert "AAPL" in str(contract)
        assert "STK" in str(contract)

    def test_repr(self):
        """Test __repr__ method."""
        contract = IbContract(symbol="AAPL", sec_type="STK", exchange="SMART", currency="USD")

        assert "IbContract" in repr(contract)

    def test_stock_classmethod(self):
        """Test stock classmethod."""
        contract = IbContract.stock("AAPL")

        assert contract.symbol == "AAPL"
        assert contract.sec_type == "STK"
        assert contract.exchange == "SMART"
        assert contract.currency == "USD"

    def test_option_fields(self):
        """Test option fields."""
        contract = IbContract(
            symbol="AAPL",
            sec_type="OPT",
            exchange="SMART",
            currency="USD",
            last_trade_date="20250620",
            strike=150.0,
            right="C",
        )

        assert contract.last_trade_date == "20250620"
        assert contract.strike == 150.0
        assert contract.right == "C"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
