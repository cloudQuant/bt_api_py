"""Tests for UniswapPool container."""

from bt_api_py.containers.exchanges.uniswap_pool import (
    UniswapPoolFee,
    UniswapPoolStats,
    UniswapPoolToken,
)


class TestUniswapPoolToken:
    """Tests for UniswapPoolToken dataclass."""

    def test_init(self):
        """Test initialization."""
        token = UniswapPoolToken(address="0x123", symbol="WETH", name="Wrapped Ether", decimals=18)
        assert token.address == "0x123"
        assert token.symbol == "WETH"


class TestUniswapPoolFee:
    """Tests for UniswapPoolFee dataclass."""

    def test_init(self):
        """Test initialization."""
        fee = UniswapPoolFee()
        assert fee.swap_fee is None


class TestUniswapPoolStats:
    """Tests for UniswapPoolStats dataclass."""

    def test_init(self):
        """Test initialization."""
        stats = UniswapPoolStats()
        assert stats is not None
