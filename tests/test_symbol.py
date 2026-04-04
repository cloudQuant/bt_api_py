"""Tests for symbol module."""

import pytest

from bt_api_py.containers.symbols.symbol import SymbolData


class TestSymbolData:
    """Tests for SymbolData class."""

    def test_init(self):
        """Test initialization."""
        symbol = SymbolData({"symbol": "BTCUSDT"}, has_been_json_encoded=True)

        assert symbol.event == "SymbolEvent"
        assert symbol.symbol_info == {"symbol": "BTCUSDT"}
        assert symbol.has_been_json_encoded is True

    def test_init_without_json_encoded(self):
        """Test initialization without json encoded."""
        symbol = SymbolData('{"symbol": "BTCUSDT"}', has_been_json_encoded=False)

        assert symbol.event == "SymbolEvent"
        assert symbol.symbol_info == '{"symbol": "BTCUSDT"}'
        assert symbol.has_been_json_encoded is False

    def test_get_event(self):
        """Test get_event method."""
        symbol = SymbolData({}, has_been_json_encoded=True)

        assert symbol.get_event() == "SymbolEvent"

    def test_init_data_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        symbol = SymbolData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            symbol.init_data()

    def test_get_exchange_name_not_implemented(self):
        """Test get_exchange_name raises NotImplementedError."""
        symbol = SymbolData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            symbol.get_exchange_name()

    def test_get_all_data_not_implemented(self):
        """Test get_all_data raises NotImplementedError."""
        symbol = SymbolData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            symbol.get_all_data()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
