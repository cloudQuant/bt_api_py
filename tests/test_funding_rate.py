"""Tests for funding_rate module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.fundingrates.funding_rate import FundingRateData


class TestFundingRateData:
    """Tests for FundingRateData class."""

    def test_init(self):
        """Test initialization."""
        funding_rate = FundingRateData({"fundingRate": 0.0001}, has_been_json_encoded=True)

        assert funding_rate.event == "FundingEvent"
        assert funding_rate.funding_rate_info == {"fundingRate": 0.0001}
        assert funding_rate.has_been_json_encoded is True
        assert funding_rate.exchange_name is None
        assert funding_rate.symbol_name is None

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        funding_rate = FundingRateData('{"fundingRate": 0.0001}', has_been_json_encoded=False)

        assert funding_rate.event == "FundingEvent"
        assert funding_rate.funding_rate_info == '{"fundingRate": 0.0001}'
        assert funding_rate.has_been_json_encoded is False
        assert funding_rate.funding_rate_data is None

    def test_get_event(self):
        """Test get_event method."""
        funding_rate = FundingRateData({}, has_been_json_encoded=True)

        assert funding_rate.get_event() == "FundingEvent"

    def test_get_event_type(self):
        """Test get_event_type method."""
        funding_rate = FundingRateData({}, has_been_json_encoded=True)

        assert funding_rate.get_event_type() == "FundingEvent"

    def test_init_data_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        funding_rate = FundingRateData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            funding_rate.init_data()

    def test_get_exchange_name_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        funding_rate = FundingRateData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            funding_rate.get_exchange_name()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
