"""Tests for IB contract container."""

from __future__ import annotations

from bt_api_py.containers.ib.ib_contract import IbContract


class TestIbContract:
    """Tests for IbContract."""

    def test_init(self):
        """Test initialization with defaults."""
        contract = IbContract()

        assert contract.symbol == ""
        assert contract.sec_type == "STK"
        assert contract.exchange == "SMART"
        assert contract.currency == "USD"
        assert contract.con_id == 0

    def test_init_with_params(self):
        """Test initialization with parameters."""
        contract = IbContract(
            symbol="AAPL",
            sec_type="STK",
            exchange="NYSE",
            currency="USD",
            con_id=12345,
        )

        assert contract.symbol == "AAPL"
        assert contract.sec_type == "STK"
        assert contract.exchange == "NYSE"
        assert contract.currency == "USD"
        assert contract.con_id == 12345

    def test_init_option(self):
        """Test initialization for option contract."""
        contract = IbContract(
            symbol="AAPL",
            sec_type="OPT",
            last_trade_date="20250620",
            strike=150.0,
            right="C",
        )

        assert contract.symbol == "AAPL"
        assert contract.sec_type == "OPT"
        assert contract.last_trade_date == "20250620"
        assert contract.strike == 150.0
        assert contract.right == "C"

    def test_to_dict(self):
        """Test to_dict method."""
        contract = IbContract(
            symbol="AAPL",
            sec_type="STK",
            exchange="SMART",
            currency="USD",
        )

        result = contract.to_dict()

        assert result["symbol"] == "AAPL"
        assert result["sec_type"] == "STK"
        assert result["exchange"] == "SMART"
        assert result["currency"] == "USD"

    def test_to_dict_excludes_empty_values(self):
        """Test to_dict excludes empty values."""
        contract = IbContract(symbol="AAPL")
        result = contract.to_dict()

        assert "symbol" in result
        assert "sec_type" in result  # default "STK" is truthy
        assert "exchange" in result  # default "SMART" is truthy
        # Empty values like con_id=0 are falsy and excluded
        assert "con_id" not in result
        assert "last_trade_date" not in result  # "" is falsy

    def test_str_basic(self):
        """Test __str__ for basic contract."""
        contract = IbContract(symbol="AAPL", sec_type="STK", exchange="SMART", currency="USD")

        result = str(contract)

        assert "AAPL" in result
        assert "STK" in result
        assert "SMART" in result
        assert "USD" in result

    def test_str_with_option_fields(self):
        """Test __str__ for option contract."""
        contract = IbContract(
            symbol="AAPL",
            sec_type="OPT",
            exchange="SMART",
            currency="USD",
            last_trade_date="20250620",
            strike=150.0,
            right="C",
        )

        result = str(contract)

        assert "20250620" in result
        assert "150" in result
        assert "C" in result

    def test_repr(self):
        """Test __repr__ method."""
        contract = IbContract(symbol="AAPL")
        result = repr(contract)

        assert "IbContract" in result
        assert "AAPL" in result

    def test_stock_factory(self):
        """Test stock factory method."""
        contract = IbContract.stock("AAPL")

        assert contract.symbol == "AAPL"
        assert contract.sec_type == "STK"
        assert contract.exchange == "SMART"
        assert contract.currency == "USD"

    def test_stock_factory_custom_exchange(self):
        """Test stock factory with custom exchange."""
        contract = IbContract.stock("AAPL", exchange="NYSE", currency="EUR")

        assert contract.exchange == "NYSE"
        assert contract.currency == "EUR"

    def test_future_factory(self):
        """Test future factory method."""
        contract = IbContract.future(
            "ES", exchange="GLOBEX", currency="USD", last_trade_date="20250620"
        )

        assert contract.symbol == "ES"
        assert contract.sec_type == "FUT"
        assert contract.exchange == "GLOBEX"
        assert contract.last_trade_date == "20250620"

    def test_option_factory(self):
        """Test option factory method."""
        contract = IbContract.option(
            symbol="AAPL",
            last_trade_date="20250620",
            strike=150.0,
            right="C",
        )

        assert contract.symbol == "AAPL"
        assert contract.sec_type == "OPT"
        assert contract.last_trade_date == "20250620"
        assert contract.strike == 150.0
        assert contract.right == "C"

    def test_option_factory_put(self):
        """Test option factory for put option."""
        contract = IbContract.option(
            symbol="AAPL",
            last_trade_date="20250620",
            strike=150.0,
            right="P",
        )

        assert contract.right == "P"

    def test_forex_factory(self):
        """Test forex factory method."""
        contract = IbContract.forex("EUR.USD")

        assert contract.symbol == "EUR.USD"
        assert contract.sec_type == "CASH"
        assert contract.exchange == "IDEALPRO"
        assert contract.currency == "USD"

    def test_forex_factory_custom_currency(self):
        """Test forex factory with custom currency."""
        contract = IbContract.forex("EUR", currency="GBP")

        assert contract.currency == "GBP"
