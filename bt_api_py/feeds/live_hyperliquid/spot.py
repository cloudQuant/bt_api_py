"""
Hyperliquid Spot Trading Implementation

Provides spot trading functionality for Hyperliquid exchange.
"""

import json
import time

from bt_api_py.containers.accounts.hyperliquid_account import HyperliquidSpotWssAccountData
from bt_api_py.containers.balances.hyperliquid_balance import HyperliquidSpotRequestBalanceData
from bt_api_py.containers.exchanges.hyperliquid_exchange_data import HyperliquidExchangeDataSpot
from bt_api_py.containers.orders.hyperliquid_order import (
    HyperliquidRequestOrderData,
    HyperliquidSpotWssOrderData,
)
from bt_api_py.containers.tickers.hyperliquid_ticker import HyperliquidTickerData
from bt_api_py.containers.trades.hyperliquid_trade import HyperliquidSpotWssTradeData
from bt_api_py.feeds.live_hyperliquid.market_wss_base import HyperliquidMarketWssData
from bt_api_py.feeds.live_hyperliquid.account_wss_base import HyperliquidAccountWssData
from bt_api_py.feeds.live_hyperliquid.request_base import HyperliquidRequestData
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger
from bt_api_py.utils.hyperliquid_types import (
    LIMIT_ORDER,
    MARKET_ORDER,
    TIF_GTC,
    SIDE_BUY,
    SIDE_SELL,
)


class HyperliquidRequestDataSpot(HyperliquidRequestData):
    """Hyperliquid spot trading request data"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "hyperliquid_spot_feed.log")
        self._params = HyperliquidExchangeDataSpot()
        self.request_logger = get_logger("hyperliquid_spot_feed")
        self.async_logger = get_logger("hyperliquid_spot_feed")

    # ── Standard Interface: make_order ──────────────────────────────

    def _make_order(self, symbol, volume, price, order_type, offset="open",
                    post_only=False, client_order_id=None, extra_data=None, **kwargs):
        """Prepare order request parameters. Returns (path, body, extra_data)."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("make_order")
        coin = self._params.get_symbol(symbol)

        # Determine side from order_type or kwargs
        side = kwargs.get("side", "buy")
        if isinstance(order_type, str) and order_type.startswith("buy"):
            side = "buy"
            order_type = order_type.replace("buy_", "")
        elif isinstance(order_type, str) and order_type.startswith("sell"):
            side = "sell"
            order_type = order_type.replace("sell_", "")

        order_params = {
            "coin": coin,
            "is_buy": side == "buy",
            "sz": str(volume),
        }

        if order_type in ("limit", LIMIT_ORDER) or price:
            order_params["limit_px"] = str(price) if price else "0"
            tif = kwargs.get("time_in_force", TIF_GTC)
            order_params["order_type"] = {"limit": {"tif": tif}}
            if post_only:
                order_params["order_type"]["limit"]["postOnly"] = True
        elif order_type in ("market", MARKET_ORDER):
            order_params["order_type"] = {"market": {}}
        else:
            order_params["order_type"] = {"limit": {"tif": TIF_GTC}}
            if price:
                order_params["limit_px"] = str(price)

        extra_data.update({
            "exchange_name": self._params.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "make_order",
            "side": side,
            "quantity": volume,
            "price": price,
            "order_type": order_type,
        })
        return path, order_params, extra_data

    def make_order(self, symbol, volume, price, order_type, offset="open",
                   post_only=False, client_order_id=None, extra_data=None, **kwargs):
        """Place order following standard Feed interface. Returns RequestData."""
        path, body, extra_data = self._make_order(
            symbol, volume, price, order_type, offset, post_only,
            client_order_id, extra_data, **kwargs
        )
        return self.request(path, body=body, extra_data=extra_data, is_sign=True)

    def place_order(self, symbol, side, quantity, order_type="limit", price=None,
                    time_in_force=TIF_GTC, client_order_id=None, post_only=False, **kwargs):
        """Place order (legacy Hyperliquid-specific interface). Returns RequestData."""
        return self.make_order(
            symbol, quantity, price, order_type, post_only=post_only,
            client_order_id=client_order_id, side=side, time_in_force=time_in_force, **kwargs
        )

    # ── Standard Interface: cancel_order ────────────────────────────

    def _cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Prepare cancel order request. Returns (path, body, extra_data)."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("cancel_order")
        coin = self._params.get_symbol(symbol)
        cancel_params = {
            "coin": coin,
            "oid": order_id,
        }
        extra_data.update({
            "exchange_name": self._params.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "cancel_order",
            "order_id": order_id,
        })
        return path, cancel_params, extra_data

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Cancel order following standard Feed interface. Returns RequestData."""
        path, body, extra_data = self._cancel_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra_data, is_sign=True)

    # ── Standard Interface: query_order ─────────────────────────────

    def _query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Prepare query order request. Returns (path, body, extra_data)."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("get_order_status")
        user = kwargs.get("user", self.address or "")
        body = {"type": "orderStatus", "user": user, "oid": order_id}
        extra_data.update({
            "exchange_name": self._params.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "query_order",
            "order_id": order_id,
        })
        return path, body, extra_data

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order status. Returns RequestData."""
        path, body, extra_data = self._query_order(symbol, order_id, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra_data)

    # ── Standard Interface: get_open_orders ─────────────────────────

    def _get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Prepare open orders request. Returns (path, body, extra_data)."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("get_order_status")
        user = kwargs.get("user", self.address or "")
        body = {"type": "openOrders", "user": user}
        extra_data.update({
            "exchange_name": self._params.exchange_name,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "request_type": "get_open_orders",
        })
        return path, body, extra_data

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders. Returns RequestData."""
        path, body, extra_data = self._get_open_orders(symbol, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra_data)

    # ── Standard Interface: get_account ─────────────────────────────

    def _get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Prepare account request. Returns (path, body, extra_data)."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("get_spot_clearinghouse_state")
        user = kwargs.get("user", self.address or "")
        body = {"type": "spotClearinghouseState", "user": user}
        extra_data.update({
            "exchange_name": self._params.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "get_account",
        })
        return path, body, extra_data

    def get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """Get account info. Returns RequestData."""
        path, body, extra_data = self._get_account(symbol, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra_data)

    # ── Standard Interface: get_balance ─────────────────────────────

    def _get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Prepare balance request. Returns (path, body, extra_data)."""
        if extra_data is None:
            extra_data = {}
        path = self._params.get_rest_path("get_spot_clearinghouse_state")
        user = kwargs.get("user", self.address or "")
        body = {"type": "spotClearinghouseState", "user": user}
        extra_data.update({
            "exchange_name": self._params.exchange_name,
            "symbol_name": symbol or "",
            "asset_type": self.asset_type,
            "request_type": "get_balance",
        })
        return path, body, extra_data

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """Get balance info. Returns RequestData."""
        path, body, extra_data = self._get_balance(symbol, extra_data, **kwargs)
        return self.request(path, body=body, extra_data=extra_data)

    # ── Hyperliquid-specific: modify_order ──────────────────────────

    def modify_order(self, symbol, order_id, side=None, quantity=None, price=None, **kwargs):
        """Modify spot order on Hyperliquid. Returns RequestData."""
        path = self._params.get_rest_path("modify_order")
        modify_params = {
            "coin": self._params.get_symbol(symbol),
            "oid": order_id,
        }
        if side:
            modify_params["is_buy"] = side == "buy"
        if quantity:
            modify_params["sz"] = str(quantity)
        if price:
            modify_params["limit_px"] = str(price)

        extra_data = {
            "exchange_name": self._params.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": "modify_order",
            "order_id": order_id,
        }
        return self.request(path, body=modify_params, extra_data=extra_data, is_sign=True)


class HyperliquidMarketWssDataSpot(HyperliquidMarketWssData):
    """Hyperliquid spot market WebSocket data"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.symbols = kwargs.get("symbols", [])
        self.logger_name = kwargs.get("logger_name", "hyperliquid_spot_market_wss.log")
        self._params = HyperliquidExchangeDataSpot()
        self.request_logger = get_logger("hyperliquid_spot_market_wss")
        self.async_logger = get_logger("hyperliquid_spot_market_wss")

    def subscribe_ticker(self, symbol):
        """Subscribe to ticker data for symbol"""
        subscription = {
            "method": "subscribe",
            "subscription": {"type": "allMids"}
        }
        return subscription

    def subscribe_orderbook(self, symbol, depth=5):
        """Subscribe to order book data for symbol"""
        subscription = {
            "method": "subscribe",
            "subscription": {"type": "l2Book", "coin": self._params.get_symbol(symbol)}
        }
        return subscription

    def subscribe_trades(self, symbol):
        """Subscribe to trade data for symbol"""
        subscription = {
            "method": "subscribe",
            "subscription": {"type": "trades", "coin": self._params.get_symbol(symbol)}
        }
        return subscription

    def process_ticker_message(self, message):
        """Process ticker WebSocket message"""
        if message.get("channel") == "allMids":
            data = message.get("data", {}).get("mids", {})
            for symbol, price in data.items():
                extra_data = {
                    "exchange_name": self._params.exchange_name,
                    "symbol_name": symbol,
                    "asset_type": self.asset_type,
                    "request_type": "ticker",
                }
                ticker_data = {
                    "last": float(price),
                    "symbol": symbol
                }
                ticker_obj = HyperliquidTickerData(ticker_data, symbol, self.asset_type)
                ticker_obj.init_data()
                self.data_queue.put(ticker_obj)

    def process_orderbook_message(self, message):
        """Process order book WebSocket message"""
        if message.get("channel") == "l2Book":
            data = message.get("data", {})
            coin = data.get("coin", "")
            levels = data.get("levels", [[], []])

            # Process asks (level 0)
            for level in levels[0]:
                orderbook_data = {
                    "symbol": coin,
                    "side": "ask",
                    "price": float(level["px"]),
                    "quantity": float(level["sz"]),
                    "count": int(level["n"])
                }
                # Create orderbook RequestData object
                extra_data = {
                    "exchange_name": self._params.exchange_name,
                    "symbol_name": coin,
                    "asset_type": self.asset_type,
                    "request_type": "orderbook",
                }
                orderbook_obj = self._get_request_data(orderbook_data, extra_data)
                self.data_queue.put(orderbook_obj)

            # Process bids (level 1)
            for level in levels[1]:
                orderbook_data = {
                    "symbol": coin,
                    "side": "bid",
                    "price": float(level["px"]),
                    "quantity": float(level["sz"]),
                    "count": int(level["n"])
                }
                # Create orderbook RequestData object
                extra_data = {
                    "exchange_name": self._params.exchange_name,
                    "symbol_name": coin,
                    "asset_type": self.asset_type,
                    "request_type": "orderbook",
                }
                orderbook_obj = self._get_request_data(orderbook_data, extra_data)
                self.data_queue.put(orderbook_obj)

    def process_trades_message(self, message):
        """Process trade WebSocket message"""
        if message.get("channel") == "trades":
            data = message.get("data", [])
            for trade in data:
                trade_data = {
                    "symbol": trade.get("coin"),
                    "side": trade.get("side"),
                    "price": float(trade["px"]),
                    "quantity": float(trade["sz"]),
                    "timestamp": trade.get("time")
                }
                extra_data = {
                    "exchange_name": self._params.exchange_name,
                    "symbol_name": trade.get("coin"),
                    "asset_type": self.asset_type,
                    "request_type": "trade",
                }
                trade_obj = HyperliquidSpotWssTradeData(trade_data, trade.get("coin"), self.asset_type)
                trade_obj.init_data()
                self.data_queue.put(trade_obj)


class HyperliquidAccountWssDataSpot(HyperliquidAccountWssData):
    """Hyperliquid spot account WebSocket data"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.user_address = kwargs.get("user_address", "")
        self.logger_name = kwargs.get("logger_name", "hyperliquid_spot_account_wss.log")
        self._params = HyperliquidExchangeDataSpot()
        self.request_logger = get_logger("hyperliquid_spot_account_wss")
        self.async_logger = get_logger("hyperliquid_spot_account_wss")

    def subscribe_order_updates(self):
        """Subscribe to order updates"""
        if not self.user_address:
            raise ValueError("User address required for order updates")

        subscription = {
            "method": "subscribe",
            "subscription": {"type": "orderUpdates", "user": self.user_address}
        }
        return subscription

    def process_order_update(self, message):
        """Process order update message"""
        if message.get("channel") == "orderUpdates":
            data = message.get("data", [])
            for order in data:
                extra_data = {
                    "exchange_name": self._params.exchange_name,
                    "symbol_name": order.get("coin"),
                    "asset_type": self.asset_type,
                    "request_type": "order_update",
                }
                order_obj = HyperliquidSpotWssOrderData(order, order.get("coin"), self.asset_type)
                order_obj.init_data()
                self.data_queue.put(order_obj)
