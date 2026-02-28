"""Backward-compatible re-export wrapper.

All classes have been moved to the ``bt_api_py.feeds.live_binance`` package.
Import from here continues to work so that existing code is not broken.
"""

# Re-export exchange data classes for convenience
from bt_api_py.containers.exchanges.binance_exchange_data import (  # noqa: F401
    BinanceExchangeDataSpot,
    BinanceExchangeDataSwap,
)
from bt_api_py.feeds.live_binance.account_wss_base import BinanceAccountWssData  # noqa: F401

# Algo Trading
from bt_api_py.feeds.live_binance.algo import BinanceRequestDataAlgo  # noqa: F401

# COIN-M Futures
from bt_api_py.feeds.live_binance.coin_m import (  # noqa: F401
    BinanceAccountWssDataCoinM,
    BinanceMarketWssDataCoinM,
    BinanceRequestDataCoinM,
)

# Margin Trading
from bt_api_py.feeds.live_binance.margin import (  # noqa: F401
    BinanceAccountWssDataMargin,
    BinanceMarketWssDataMargin,
    BinanceRequestDataMargin,
)
from bt_api_py.feeds.live_binance.market_wss_base import BinanceMarketWssData  # noqa: F401

# European Options
from bt_api_py.feeds.live_binance.option import (  # noqa: F401
    BinanceAccountWssDataOption,
    BinanceMarketWssDataOption,
    BinanceRequestDataOption,
)

# Base classes
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData  # noqa: F401

# Spot
from bt_api_py.feeds.live_binance.spot import (  # noqa: F401
    BinanceAccountWssDataSpot,
    BinanceMarketWssDataSpot,
    BinanceRequestDataSpot,
)

# Swap (USDT-M Futures)
from bt_api_py.feeds.live_binance.swap import (  # noqa: F401
    BinanceAccountWssDataSwap,
    BinanceMarketWssDataSwap,
    BinanceRequestDataSwap,
)

# Old class definitions have been moved to bt_api_py.feeds.live_binance package.
# This file is kept for backward compatibility — all imports above re-export
# the original classes from their new locations.
