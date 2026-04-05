# Base classes
from __future__ import annotations

from bt_api_py.feeds.live_binance.account_wss_base import BinanceAccountWssData

# Algo Trading
from bt_api_py.feeds.live_binance.algo import BinanceRequestDataAlgo

# COIN-M Futures
from bt_api_py.feeds.live_binance.coin_m import (
    BinanceAccountWssDataCoinM,
    BinanceMarketWssDataCoinM,
    BinanceRequestDataCoinM,
)

# Grid Trading API
from bt_api_py.feeds.live_binance.grid import BinanceRequestDataGrid

# Margin Trading
from bt_api_py.feeds.live_binance.margin import (
    BinanceAccountWssDataMargin,
    BinanceMarketWssDataMargin,
    BinanceRequestDataMargin,
)
from bt_api_py.feeds.live_binance.market_wss_base import BinanceMarketWssData

# Mining API
from bt_api_py.feeds.live_binance.mining import BinanceRequestDataMining

# European Options
from bt_api_py.feeds.live_binance.option import (
    BinanceAccountWssDataOption,
    BinanceMarketWssDataOption,
    BinanceRequestDataOption,
)

# Portfolio Margin API
from bt_api_py.feeds.live_binance.portfolio import BinanceRequestDataPortfolio
from bt_api_py.feeds.live_binance.request_base import BinanceRequestData

# Spot
from bt_api_py.feeds.live_binance.spot import (
    BinanceAccountWssDataSpot,
    BinanceMarketWssDataSpot,
    BinanceRequestDataSpot,
)

# Staking API
from bt_api_py.feeds.live_binance.staking import BinanceRequestDataStaking

# Sub-account API
from bt_api_py.feeds.live_binance.sub_account import BinanceRequestDataSubAccount

# Swap (USDT-M Futures)
from bt_api_py.feeds.live_binance.swap import (
    BinanceAccountWssDataSwap,
    BinanceMarketWssDataSwap,
    BinanceRequestDataSwap,
)

# VIP Loan API
from bt_api_py.feeds.live_binance.vip_loan import BinanceRequestDataVipLoan

# Wallet API
from bt_api_py.feeds.live_binance.wallet import BinanceRequestDataWallet

__all__ = [
    # Base
    "BinanceRequestData",
    "BinanceMarketWssData",
    "BinanceAccountWssData",
    # Swap
    "BinanceRequestDataSwap",
    "BinanceMarketWssDataSwap",
    "BinanceAccountWssDataSwap",
    # Spot
    "BinanceRequestDataSpot",
    "BinanceMarketWssDataSpot",
    "BinanceAccountWssDataSpot",
    # COIN-M
    "BinanceRequestDataCoinM",
    "BinanceMarketWssDataCoinM",
    "BinanceAccountWssDataCoinM",
    # Option
    "BinanceRequestDataOption",
    "BinanceMarketWssDataOption",
    "BinanceAccountWssDataOption",
    # Margin
    "BinanceRequestDataMargin",
    "BinanceMarketWssDataMargin",
    "BinanceAccountWssDataMargin",
    # Algo
    "BinanceRequestDataAlgo",
    # Wallet
    "BinanceRequestDataWallet",
    # Sub-account
    "BinanceRequestDataSubAccount",
    # Portfolio
    "BinanceRequestDataPortfolio",
    # Grid
    "BinanceRequestDataGrid",
    # Staking
    "BinanceRequestDataStaking",
    # Mining
    "BinanceRequestDataMining",
    # VIP Loan
    "BinanceRequestDataVipLoan",
]
