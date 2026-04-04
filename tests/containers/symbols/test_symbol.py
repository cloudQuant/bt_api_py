"""Tests for SymbolData base container."""

import pytest

from bt_api_py.containers.symbols.symbol import SymbolData


class TestSymbolData:
    """Tests for SymbolData base class."""

    def test_init(self):
        """Test initialization."""
        symbol = SymbolData({}, has_been_json_encoded=True)

        assert symbol.event == "SymbolEvent"

    def test_init_data_raises_not_implemented(self):
        """Test init_data raises NotImplementedError."""
        symbol = SymbolData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            symbol.init_data()

    def test_get_event(self):
        """Test get_event method."""
        symbol = SymbolData({}, has_been_json_encoded=True)

        assert symbol.get_event() == "SymbolEvent"

    def test_get_all_data_raises_not_implemented(self):
        """Test get_all_data raises NotImplementedError."""
        symbol = SymbolData({}, has_been_json_encoded=True)

        with pytest.raises(NotImplementedError):
            symbol.get_all_data()
