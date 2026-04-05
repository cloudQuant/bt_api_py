"""Tests for CTP ticker module."""

import pytest

from bt_api_py.containers.ctp.ctp_ticker import CtpTickerData


class TestCtpTickerData:
    """Tests for CtpTickerData class."""

    def test_init(self):
        """Test initialization."""
        ticker = CtpTickerData(
            {"InstrumentID": "IF2506"},
            symbol_name="IF2506",
            asset_type="FUTURE",
            has_been_json_encoded=True,
        )

        assert ticker.exchange_name == "CTP"
        assert ticker.symbol_name == "IF2506"
        assert ticker.asset_type == "FUTURE"

    def test_init_data(self):
        """Test init_data method."""
        ticker_info = {
            "InstrumentID": "IF2506",
            "LastPrice": 4000.0,
            "PreSettlementPrice": 3950.0,
            "OpenPrice": 3955.0,
            "HighestPrice": 4010.0,
            "LowestPrice": 3950.0,
            "BidPrice1": 3999.0,
            "BidVolume1": 10,
            "AskPrice1": 4000.0,
            "AskVolume1": 5,
            "Volume": 1000,
            "OpenInterest": 5000,
            "UpperLimitPrice": 4200.0,
            "LowerLimitPrice": 3700.0,
        }
        ticker = CtpTickerData(
            ticker_info, symbol_name="IF2506", asset_type="FUTURE", has_been_json_encoded=True
        )
        ticker.init_data()

        assert ticker.instrument_id == "IF2506"
        assert ticker.last_price_val == 4000.0
        assert ticker.open_price_val == 3955.0
        assert ticker.highest_price == 4010.0
        assert ticker.lowest_price == 3950.0
        assert ticker.bid_price_1 == 3999.0
        assert ticker.bid_volume_1 == 10
        assert ticker.ask_price_1 == 4000.0
        assert ticker.ask_volume_1 == 5
        assert ticker.upper_limit_price == 4200.0
        assert ticker.lower_limit_price == 3700.0

    def test_get_exchange_name(self):
        """Test get_exchange_name method."""
        ticker = CtpTickerData({}, has_been_json_encoded=True)

        assert ticker.exchange_name == "CTP"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
