"""Tests for UniswapQuote container."""

from decimal import Decimal

from bt_api_py.containers.exchanges.uniswap_quote import (
    UniswapQuote,
    UniswapQuoteRoute,
    UniswapQuoteToken,
)


class TestUniswapQuoteToken:
    """Tests for UniswapQuoteToken dataclass."""

    def test_init(self):
        """Test initialization."""
        token = UniswapQuoteToken(address="0x123", symbol="WETH", name="Wrapped Ether")
        assert token.address == "0x123"
        assert token.symbol == "WETH"


class TestUniswapQuoteRoute:
    """Tests for UniswapQuoteRoute dataclass."""

    def test_init(self):
        """Test initialization."""
        route = UniswapQuoteRoute(pool_address="0xpool", token_in="WETH", token_out="USDC")
        assert route.pool_address == "0xpool"


class TestUniswapQuote:
    """Tests for UniswapQuote dataclass."""

    def test_init(self):
        """Test initialization."""
        token_in = UniswapQuoteToken(address="0x123", symbol="WETH", name="Wrapped Ether")
        token_out = UniswapQuoteToken(address="0x456", symbol="USDC", name="USD Coin")
        quote = UniswapQuote(
            quote_id="q1",
            token_in=token_in,
            token_out=token_out,
            swap_type="EXACT_IN",
            amount_in=Decimal("1.0"),
            amount_out=Decimal("2000.0"),
        )
        assert quote.quote_id == "q1"
