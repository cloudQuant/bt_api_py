"""Tests for FundingRateData base container."""

import pytest

from bt_api_py.containers.fundingrates.funding_rate import FundingRateData


class TestFundingRateData:
    """Tests for FundingRateData base class."""

    def test_init(self):
        """Test initialization."""
        rate = FundingRateData({}, has_been_json_encoded=True)

        assert rate.event == "FundingEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        rate = FundingRateData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            rate.init_data()

    def test_get_event(self):
        """Test get_event method."""
        rate = FundingRateData({}, has_been_json_encoded=True)

        assert rate.get_event() == "FundingEvent"

    def test_get_all_data(self):
        """Test get_all_data method - raises NotImplementedError via init_data."""
        rate = FundingRateData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            rate.get_all_data()
