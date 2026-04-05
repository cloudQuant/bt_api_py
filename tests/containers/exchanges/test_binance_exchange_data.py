"""Tests for BinanceExchangeData classes."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from bt_api_py.containers.exchanges.binance_exchange_data import (
    BinanceExchangeData,
    BinanceExchangeDataAlgo,
    BinanceExchangeDataCoinM,
    BinanceExchangeDataGrid,
    BinanceExchangeDataMargin,
    BinanceExchangeDataMining,
    BinanceExchangeDataOption,
    BinanceExchangeDataPortfolio,
    BinanceExchangeDataSpot,
    BinanceExchangeDataStaking,
    BinanceExchangeDataSubAccount,
    BinanceExchangeDataSwap,
    BinanceExchangeDataVipLoan,
    BinanceExchangeDataWallet,
)


class TestBinanceExchangeData:
    """Tests for BinanceExchangeData base class."""

    def test_init(self):
        """Test initialization."""
        exchange = BinanceExchangeData()

        assert exchange.exchange_name == "binance"
        assert exchange.rest_url == ""
        assert exchange.acct_wss_url == ""
        assert exchange.wss_url == ""
        assert exchange.rest_paths == {}
        assert exchange.wss_paths == {}

    def test_kline_periods(self):
        """Test kline_periods initialization."""
        exchange = BinanceExchangeData()

        assert "1m" in exchange.kline_periods
        assert "1h" in exchange.kline_periods
        assert "1d" in exchange.kline_periods
        assert exchange.kline_periods["1m"] == "1m"
        assert exchange.kline_periods["1h"] == "1h"

    def test_reverse_kline_periods(self):
        """Test reverse_kline_periods initialization."""
        exchange = BinanceExchangeData()

        assert exchange.reverse_kline_periods["1m"] == "1m"
        assert exchange.reverse_kline_periods["1h"] == "1h"

    def test_legal_currency(self):
        """Test legal_currency initialization."""
        exchange = BinanceExchangeData()

        assert "USDT" in exchange.legal_currency
        assert "USD" in exchange.legal_currency
        assert "BTC" in exchange.legal_currency
        assert "ETH" in exchange.legal_currency

    def test_get_symbol(self):
        """Test get_symbol method."""
        exchange = BinanceExchangeData()

        assert exchange.get_symbol("BTC-USDT") == "BTCUSDT"
        assert exchange.get_symbol("ETH-USDT") == "ETHUSDT"

    def test_get_period(self):
        """Test get_period method."""
        exchange = BinanceExchangeData()

        assert exchange.get_period("1m") == "1m"
        assert exchange.get_period("1h") == "1h"

    def test_account_wss_symbol(self):
        """Test account_wss_symbol method."""
        exchange = BinanceExchangeData()

        result = exchange.account_wss_symbol("BTCUSDT")
        assert result == "btc/usdt"

        result = exchange.account_wss_symbol("ETHUSDT")
        assert result == "eth/usdt"

    def test_get_rest_path_raises_error(self):
        """Test get_rest_path raises error for missing path."""
        exchange = BinanceExchangeData()
        exchange.rest_paths = {}

        with pytest.raises(NotImplementedError):
            exchange.get_rest_path("nonexistent_key")

    def test_get_rest_path_returns_path(self):
        """Test get_rest_path returns correct path."""
        exchange = BinanceExchangeData()
        exchange.rest_paths = {"ticker": "/api/v3/ticker/price"}

        result = exchange.get_rest_path("ticker")
        assert result == "/api/v3/ticker/price"

    def test_get_rest_path_empty_string_raises(self):
        """Test get_rest_path raises error for empty path."""
        exchange = BinanceExchangeData()
        exchange.rest_paths = {"ticker": ""}

        with pytest.raises(NotImplementedError):
            exchange.get_rest_path("ticker")

    def test_get_wss_path_raises_error(self):
        """Test get_wss_path raises error for missing path."""
        exchange = BinanceExchangeData()
        exchange.wss_paths = {}

        with pytest.raises(NotImplementedError):
            exchange.get_wss_path(topic="nonexistent")

    def test_get_wss_path_with_symbol(self):
        """Test get_wss_path with symbol substitution."""
        exchange = BinanceExchangeData()
        exchange.wss_paths = {
            "depth": {"params": ["<symbol>@depth20"], "method": "SUBSCRIBE", "id": 1}
        }

        result = exchange.get_wss_path(topic="depth", symbol="BTCUSDT")
        parsed = json.loads(result)

        assert parsed["method"] == "SUBSCRIBE"
        assert "btcusdt@depth20" in parsed["params"]

    def test_get_wss_path_with_period(self):
        """Test get_wss_path with period substitution."""
        exchange = BinanceExchangeData()
        exchange.wss_paths = {
            "kline": {"params": ["<symbol>@kline_<period>"], "method": "SUBSCRIBE", "id": 1}
        }

        result = exchange.get_wss_path(topic="kline", symbol="BTCUSDT", period="1m")
        parsed = json.loads(result)

        assert "btcusdt@kline_1m" in parsed["params"]

    def test_get_wss_path_with_symbol_list(self):
        """Test get_wss_path with symbol_list."""
        exchange = BinanceExchangeData()
        exchange.wss_paths = {
            "depth": {"params": ["<symbol>@depth20"], "method": "SUBSCRIBE", "id": 1}
        }

        result = exchange.get_wss_path(topic="depth", symbol_list=["BTCUSDT", "ETHUSDT"])
        parsed = json.loads(result)

        assert len(parsed["params"]) == 2
        assert "btcusdt@depth20" in parsed["params"]
        assert "ethusdt@depth20" in parsed["params"]


class TestBinanceExchangeDataSwap:
    """Tests for BinanceExchangeDataSwap."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataSwap, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataSwap()

            assert exchange.exchange_name == "binance"
            assert "BTC-USDT" in exchange.symbol_leverage_dict
            assert exchange.symbol_leverage_dict["BTC-USDT"] == 100


class TestBinanceExchangeDataSpot:
    """Tests for BinanceExchangeDataSpot."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataSpot, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataSpot()

            assert exchange.exchange_name == "binance"

    def test_get_symbol(self):
        """Test get_symbol method."""
        with patch.object(BinanceExchangeDataSpot, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataSpot()

            assert exchange.get_symbol("BTC-USDT") == "BTCUSDT"

    def test_account_wss_symbol(self):
        """Test account_wss_symbol method."""
        with patch.object(BinanceExchangeDataSpot, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataSpot()

            result = exchange.account_wss_symbol("BTCUSDT")
            assert result == "btc/usdt"

    def test_get_wss_path_with_symbol(self):
        """Test get_wss_path with symbol substitution."""
        with patch.object(BinanceExchangeDataSpot, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataSpot()
            exchange.wss_paths = {
                "depth": {"params": ["<symbol>@depth20"], "method": "SUBSCRIBE", "id": 1}
            }

            result = exchange.get_wss_path(topic="depth", symbol="BTCUSDT")
            parsed = json.loads(result)

            assert "btcusdt@depth20" in parsed["params"]


class TestBinanceExchangeDataCoinM:
    """Tests for BinanceExchangeDataCoinM."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataCoinM, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataCoinM()

            assert exchange.exchange_name == "binance"


class TestBinanceExchangeDataOption:
    """Tests for BinanceExchangeDataOption."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataOption, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataOption()

            assert exchange.exchange_name == "binance"


class TestBinanceExchangeDataMargin:
    """Tests for BinanceExchangeDataMargin."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataMargin, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataMargin()

            assert exchange.exchange_name == "binance"


class TestBinanceExchangeDataAlgo:
    """Tests for BinanceExchangeDataAlgo."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataAlgo, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataAlgo()

            assert exchange.exchange_name == "binance"


class TestBinanceExchangeDataWallet:
    """Tests for BinanceExchangeDataWallet."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataWallet, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataWallet()

            assert exchange.exchange_name == "binance"


class TestBinanceExchangeDataSubAccount:
    """Tests for BinanceExchangeDataSubAccount."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataSubAccount, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataSubAccount()

            assert exchange.exchange_name == "binance"


class TestBinanceExchangeDataPortfolio:
    """Tests for BinanceExchangeDataPortfolio."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataPortfolio, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataPortfolio()

            assert exchange.exchange_name == "binance"


class TestBinanceExchangeDataGrid:
    """Tests for BinanceExchangeDataGrid."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataGrid, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataGrid()

            assert exchange.exchange_name == "binance"


class TestBinanceExchangeDataStaking:
    """Tests for BinanceExchangeDataStaking."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataStaking, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataStaking()

            assert exchange.exchange_name == "binance"


class TestBinanceExchangeDataMining:
    """Tests for BinanceExchangeDataMining."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataMining, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataMining()

            assert exchange.exchange_name == "binance"


class TestBinanceExchangeDataVipLoan:
    """Tests for BinanceExchangeDataVipLoan."""

    def test_init(self):
        """Test initialization."""
        with patch.object(BinanceExchangeDataVipLoan, "_load_from_config", return_value=False):
            exchange = BinanceExchangeDataVipLoan()

            assert exchange.exchange_name == "binance"
