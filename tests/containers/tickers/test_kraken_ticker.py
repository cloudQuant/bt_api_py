"""Tests for KrakenRequestTickerData container."""


from bt_api_py.containers.tickers.kraken_ticker import KrakenRequestTickerData


class TestKrakenRequestTickerData:
    """Tests for KrakenRequestTickerData."""

    def test_init(self):
        """Test initialization."""
        ticker = KrakenRequestTickerData({}, symbol="XBTUSD", asset_type="SPOT")

        assert ticker.symbol == "XBTUSD"
        assert ticker.asset_type == "SPOT"

    def test_parse_data(self):
        """Test parsing data."""
        data = {
            "result": {
                "XXBTZUSD": {
                    "a": ["50010.0", "1.0", "1.0"],
                    "b": ["49990.0", "1.0", "1.0"],
                    "c": ["50000.0", "1.0"],
                    "v": ["1000.0", "2000.0"],
                    "p": ["49500.0", "49800.0"],
                    "h": ["51000.0", "52000.0"],
                    "l": ["49000.0", "48000.0"],
                }
            }
        }
        ticker = KrakenRequestTickerData(data, symbol="XBTUSD", asset_type="SPOT")

        assert ticker.last_price == 50000.0

    def test_to_dict(self):
        """Test to_dict."""
        ticker = KrakenRequestTickerData({}, symbol="XBTUSD", asset_type="SPOT")
        result = ticker.to_dict()

        assert result is not None

    def test_str_representation(self):
        """Test __str__ method."""
        ticker = KrakenRequestTickerData({}, symbol="XBTUSD", asset_type="SPOT")
        result = str(ticker)

        assert result is not None
