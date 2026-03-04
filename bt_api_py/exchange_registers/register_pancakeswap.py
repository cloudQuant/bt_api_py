"""
PancakeSwap Registration Module

Registers PancakeSpot feed and error translator with the system.
"""

from bt_api_py.containers.exchanges.pancakeswap_exchange_data import PancakeSwapExchangeData
from bt_api_py.containers.pools.pancakeswap_pool import PancakeSwapPoolData, PancakeSwapPoolList
from bt_api_py.containers.tickers.pancakeswap_ticker import PancakeSwapRequestTickerData
from bt_api_py.feeds.live_pancakeswap.spot import PancakeSpotRequestData
from bt_api_py.feeds.registry import register

# Register PancakeSpot feed
@register("PANCakeSwap")
class PancakeSpotFeed(PancakeSpotRequestData):
    """
    PancakeSpot Feed Implementation

    Provides spot trading functionality for PancakeSwap on BNB Chain.
    Uses GraphQL for market data queries and supports:
    - Ticker information
    - Order book (simulated from liquidity pools)
    - K-line data
    - Pool information
    - Token information
    - Trading operations (basic implementation)
    """

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)

    def get_exchange_info(self):
        """Get comprehensive exchange information"""
        return PancakeSwapExchangeData()

    def get_supported_symbols(self):
        """Get list of supported trading symbols"""
        exchange_data = PancakeSwapExchangeData()
        return exchange_data.get_supported_pairs()

    def get_pool_info(self, pool_address):
        """Get detailed pool information"""
        return PancakeSwapPoolData.from_dict(self._get_pool_info(pool_address))

    def get_all_pools(self, limit=100):
        """Get all available pools"""
        pools_data = self._get_all_pools(limit)
        pools = []

        for pool_data in pools_data:
            pool = PancakeSwapPoolData.from_dict(pool_data)
            pools.append(pool)

        return PancakeSwapPoolList(pools=pools, total_pools=len(pools),
                                 total_liquidity_usd=0, total_volume_24h_usd=0)

    def get_ticker_data(self, symbol):
        """Get ticker data for a symbol"""
        ticker_data = self._get_ticker(symbol)
        return PancakeSwapRequestTickerData.from_dict(ticker_data)


# Register error translator
from bt_api_py.errors.error_framework_pancakeswap_error_translator import PancakeSwapErrorTranslator

# The error translator is automatically imported and can be used by the feed
# No separate registration needed for error translators

__all__ = [
    'PancakeSpotFeed',
    'PancakeSwapExchangeData',
    'PancakeSwapPoolData',
    'PancakeSwapPoolList',
    'PancakeSwapRequestTickerData',
    'PancakeSwapErrorTranslator'
]