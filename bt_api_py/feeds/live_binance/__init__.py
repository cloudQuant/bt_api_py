# -*- coding: utf-8 -*-
# Base classes
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData
from bt_api_py.feeds.live_binance.market_wss_base import BinanceMarketWssData
from bt_api_py.feeds.live_binance.account_wss_base import BinanceAccountWssData

# Swap (USDT-M Futures)
from bt_api_py.feeds.live_binance.swap import (
    BinanceRequestDataSwap,
    BinanceMarketWssDataSwap,
    BinanceAccountWssDataSwap,
)

# Spot
from bt_api_py.feeds.live_binance.spot import (
    BinanceRequestDataSpot,
    BinanceMarketWssDataSpot,
    BinanceAccountWssDataSpot,
)

# COIN-M Futures
from bt_api_py.feeds.live_binance.coin_m import (
    BinanceRequestDataCoinM,
    BinanceMarketWssDataCoinM,
    BinanceAccountWssDataCoinM,
)

# European Options
from bt_api_py.feeds.live_binance.option import (
    BinanceRequestDataOption,
    BinanceMarketWssDataOption,
    BinanceAccountWssDataOption,
)

# Margin Trading
from bt_api_py.feeds.live_binance.margin import (
    BinanceRequestDataMargin,
    BinanceMarketWssDataMargin,
    BinanceAccountWssDataMargin,
)

# Algo Trading
from bt_api_py.feeds.live_binance.algo import BinanceRequestDataAlgo

__all__ = [
    # Base
    'BinanceRequestData',
    'BinanceMarketWssData',
    'BinanceAccountWssData',
    # Swap
    'BinanceRequestDataSwap',
    'BinanceMarketWssDataSwap',
    'BinanceAccountWssDataSwap',
    # Spot
    'BinanceRequestDataSpot',
    'BinanceMarketWssDataSpot',
    'BinanceAccountWssDataSpot',
    # COIN-M
    'BinanceRequestDataCoinM',
    'BinanceMarketWssDataCoinM',
    'BinanceAccountWssDataCoinM',
    # Option
    'BinanceRequestDataOption',
    'BinanceMarketWssDataOption',
    'BinanceAccountWssDataOption',
    # Margin
    'BinanceRequestDataMargin',
    'BinanceMarketWssDataMargin',
    'BinanceAccountWssDataMargin',
    # Algo
    'BinanceRequestDataAlgo',
]
