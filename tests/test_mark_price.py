"""Tests for mark_price module."""

from __future__ import annotations

import pytest

from bt_api_py.containers.markprices.mark_price import MarkPriceData


class TestMarkPriceData:
    """Tests for MarkPriceData class."""

    def test_init(self):
        """Test initialization."""
        mark_price = MarkPriceData({"markPrice": 50000.0}, has_been_json_encoded=True)

        assert mark_price.event == "MarkPriceEvent"
        assert mark_price.mark_price_info == {"markPrice": 50000.0}
        assert mark_price.has_been_json_encoded is True
        assert mark_price.exchange_name is None
        assert mark_price.symbol_name is None

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        mark_price = MarkPriceData('{"markPrice": 50000.0}', has_been_json_encoded=False)

        assert mark_price.event == "MarkPriceEvent"
        assert mark_price.mark_price_info == '{"markPrice": 50000.0}'
        assert mark_price.has_been_json_encoded is False
        assert mark_price.mark_price_data is None

    def test_get_event(self):
        """Test get_event method."""
        mark_price = MarkPriceData({}, has_been_json_encoded=True)

        assert mark_price.get_event() == "MarkPriceEvent"

    def test_init_data_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        mark_price = MarkPriceData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            mark_price.init_data()

    def test_get_exchange_name_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        mark_price = MarkPriceData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            mark_price.get_exchange_name()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
