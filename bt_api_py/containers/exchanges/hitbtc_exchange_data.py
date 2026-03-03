"""
HitBTC Exchange Data Container

This module provides HitBTC-specific configuration and path management.
Loads configuration from YAML file and provides REST/WSS endpoints.
"""

import copy
import datetime
import json
import os
import time

from bt_api_py.containers.exchanges.exchange_data import ExchangeData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="hitbtc_exchange_data.log", logger_name="hitbtc_data", print_info=False
).create_logger()

# ── 配置加载缓存 ──────────────────────────────────────────────
_hitbtc_config = None
_hitbtc_config_loaded = False


def _get_hitbtc_config():
    """延迟加载并缓存 HitBTC YAML 配置"""
    global _hitbtc_config, _hitbtc_config_loaded
    if _hitbtc_config_loaded:
        return _hitbtc_config
    try:
        from bt_api_py.config_loader import load_exchange_config

        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "configs",
            "hitbtc.yaml",
        )
        if os.path.exists(config_path):
            _hitbtc_config = load_exchange_config(config_path)
        _hitbtc_config_loaded = True
    except Exception as e:
        logger.warn(f"Failed to load hitbtc.yaml config: {e}")
    return _hitbtc_config


class HitBtcExchangeData(ExchangeData):
    """Base class for HitBTC exchange data.

    Provides shared utility methods (get_symbol, get_period, get_rest_path,
    get_wss_path) and default kline_periods.
    Subclasses MUST set exchange-specific: exchange_name, rest_paths, wss_paths.
    """

    def __init__(self):
        """Initialize exchange data with configuration"""
        super().__init__()

        # Load YAML config
        config = _get_hitbtc_config()
        if config:
            self.rest_url = config.get("rest_url", "https://api.hitbtc.com/api/3")
            self.ws_url = config.get("ws_url", "wss://api.hitbtc.com/api/3/ws/public")
            self.ws_trading_url = config.get("ws_trading_url", "wss://api.hitbtc.com/api/3/ws/trading")
            self.ws_wallet_url = config.get("ws_wallet_url", "wss://api.hitbtc.com/api/3/ws/wallet")
            self.timeout = config.get("timeout", 10)
            self.rate_limits = config.get("rate_limits", {})
        else:
            # Default values
            self.rest_url = "https://api.hitbtc.com/api/3"
            self.ws_url = "wss://api.hitbtc.com/api/3/ws/public"
            self.ws_trading_url = "wss://api.hitbtc.com/api/3/ws/trading"
            self.ws_wallet_url = "wss://api.hitbtc.com/api/3/ws/wallet"
            self.timeout = 10
            self.rate_limits = {}

        # Exchange-specific settings
        self.exchange_name = "HITBTC"
        self.asset_type = "SPOT"

        # Standard kline periods (matching HitBTC API)
        self.kline_periods = {
            "1m": "M1",
            "3m": "M3",
            "5m": "M5",
            "15m": "M15",
            "30m": "M30",
            "1h": "H1",
            "2h": "H2",
            "4h": "H4",
            "1d": "D1",
            "1w": "D7",
            "1M": "1M",
        }

        # REST API paths
        self.rest_paths = {
            # Public endpoints
            "get_server_time": "/public/time",
            "get_exchange_info": "/public/symbol",
            "get_ticker": "/public/ticker/{symbol}",
            "get_ticker_all": "/public/ticker",
            "get_orderbook": "/public/orderbook/{symbol}",
            "get_trades": "/public/trades/{symbol}",
            "get_candles": "/public/candles/{symbol}",

            # Trading endpoints
            "place_order": "/spot/order",
            "cancel_order": "/spot/order/{client_order_id}",
            "cancel_all_orders": "/spot/order",
            "get_order": "/spot/order/{client_order_id}",
            "get_open_orders": "/spot/order",
            "get_order_history": "/spot/history/order",
            "get_trades_history": "/spot/history/trade",

            # Account endpoints
            "get_balance": "/spot/balance",
            "get_account": "/account",
        }

        # WebSocket paths/channels
        self.wss_paths = {
            "ticker": "ticker",
            "orderbook": "orderbook/full",
            "trades": "trades",
            "candles": "candles",
        }

    def get_rest_path(self, request_type, symbol=None):
        """Get REST API path for request type"""
        path = self.rest_paths.get(request_type)
        if path is None:
            raise ValueError(f"Unknown request type: {request_type}")

        # Replace symbol placeholder
        if symbol and "{symbol}" in path:
            path = path.replace("{symbol}", symbol)

        # Replace client_order_id placeholder
        if symbol and "{client_order_id}" in path:
            path = path.replace("{client_order_id}", symbol)

        return path

    def get_wss_path(self, channel, symbol=None):
        """Get WebSocket channel path"""
        if channel not in self.wss_paths:
            raise ValueError(f"Unknown channel: {channel}")

        if symbol:
            return f"{self.wss_paths[channel]}/{symbol}"
        return self.wss_paths[channel]

    def get_symbol(self, symbol):
        """Normalize symbol format for HitBTC"""
        # HitBTC uses uppercase symbols without separator
        return symbol.upper().replace("/", "")

    def get_period(self, period):
        """Convert period to HitBTC format"""
        # kline_periods uses lowercase keys like "1m", "5m", "1h", "1d"
        return self.kline_periods.get(period.lower(), period.upper())

    def get_rate_limit(self, endpoint_type="market_data"):
        """Get rate limit for endpoint type"""
        return self.rate_limits.get(endpoint_type, {"calls": 100, "seconds": 1})

    def get_timeframe(self, timeframe):
        """Convert timeframe to HitBTC format"""
        return self.kline_periods.get(timeframe, timeframe)

    def __str__(self):
        return f"{self.exchange_name} {self.asset_type} Exchange Data"

    def __repr__(self):
        return f"<HitBtcExchangeData exchange='{self.exchange_name}' asset_type='{self.asset_type}'>"


class HitBtcSpotExchangeData(HitBtcExchangeData):
    """HitBTC Spot Trading exchange data"""

    def __init__(self):
        super().__init__()
        self.exchange_name = "HITBTC_SPOT"
        self.asset_type = "SPOT"
        self.rest_url = _get_hitbtc_config().get("rest_url", "https://api.hitbtc.com/api/3") if _get_hitbtc_config() else "https://api.hitbtc.com/api/3"