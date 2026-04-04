"""Tests for Mercado Bitcoin ticker data container."""

import pytest

from bt_api_py.containers.tickers.mercado_bitcoin_ticker import MercadoBitcoinRequestTickerData


class TestMercadoBitcoinRequestTickerData:
    """Tests for MercadoBitcoinRequestTickerData class."""

    def test_init_with_dict(self):
        """Test initialization with dict data."""
        ticker_info = {
            "ticker": {
                "last": 500000.0,
                "buy": 499999.0,
                "sell": 500001.0,
                "vol": 100.0,
            }
        }
        ticker = MercadoBitcoinRequestTickerData(
            ticker_info, "BTCBRL", "SPOT", has_been_json_encoded=True
        )

        assert ticker.symbol_name == "BTCBRL"
        assert ticker.exchange_name == "MERCADO_BITCOIN"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "ticker": {
                "last": 500000.0,
                "buy": 499999.0,
                "sell": 500001.0,
                "vol": 100.0,
            }
        }
        ticker = MercadoBitcoinRequestTickerData(
            ticker_info, "BTCBRL", "SPOT", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.last_price == 500000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
