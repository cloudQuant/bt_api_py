"""Tests for UniswapTicker container."""

from bt_api_py.containers.exchanges.uniswap_ticker import UniswapTicker


class TestUniswapTicker:
    """Tests for UniswapTicker dataclass."""

    def test_init(self):
        """Test initialization."""
        ticker = UniswapTicker(symbol="WETH", name="Wrapped Ether", address="0x123", decimals=18)
        assert ticker.symbol == "WETH"
        assert ticker.name == "Wrapped Ether"
