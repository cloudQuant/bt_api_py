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
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data
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
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

    def place_order(
        self,
        symbol,
        side,
        quantity,
        order_type="limit",
        price=None,
        time_in_force=TIF_GTC,
        client_order_id=None,
        post_only=False,
        **kwargs
    ):
        """Place spot order on Hyperliquid"""
        if not self.account:
            raise ValueError("Private key required for order placement")

        request_type = "make_order"

        # Prepare order parameters
        order_params = {
            "coin": self._params.get_symbol(symbol),
            "is_buy": side == "buy",
            "sz": str(quantity),
        }

        # Set order type and price
        if order_type == "limit":
            if not price:
                raise ValueError("Price required for limit orders")
            order_params["limit_px"] = str(price)
            order_params["order_type"] = {"limit": {"tif": time_in_force}}
            if post_only:
                order_params["order_type"]["limit"]["postOnly"] = True
        elif order_type == "market":
            order_params["order_type"] = {"market": {}}
        else:
            raise ValueError(f"Unsupported order type: {order_type}")

        # Place order
        result = self._make_signed_request(request_type, **order_params)

        # Create RequestData object
        extra_data = {
            "exchange_name": self._params.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": request_type,
            "side": side,
            "quantity": quantity,
            "price": price,
            "order_type": order_type,
        }

        return self._get_request_data(result, extra_data)

    def cancel_order(self, symbol, order_id=None, client_order_id=None):
        """Cancel spot order on Hyperliquid"""
        if not self.account:
            raise ValueError("Private key required for order cancellation")

        request_type = "cancel_order"

        cancel_params = {
            "coin": self._params.get_symbol(symbol),
        }

        if order_id:
            cancel_params["oid"] = order_id
        elif client_order_id:
            cancel_params["oid"] = client_order_id
        else:
            raise ValueError("Either order_id or client_order_id required")

        # Cancel order
        result = self._make_signed_request(request_type, **cancel_params)

        # Create RequestData object
        extra_data = {
            "exchange_name": self._params.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": request_type,
            "order_id": order_id,
            "client_order_id": client_order_id,
        }

        return self._get_request_data(result, extra_data)

    def modify_order(
        self,
        symbol,
        order_id,
        side=None,
        quantity=None,
        price=None,
        **kwargs
    ):
        """Modify spot order on Hyperliquid"""
        if not self.account:
            raise ValueError("Private key required for order modification")

        request_type = "modify_order"

        modify_params = {
            "coin": self._params.get_symbol(symbol),
            "oid": order_id,
        }

        # Update parameters if provided
        if side:
            modify_params["is_buy"] = side == "buy"
        if quantity:
            modify_params["sz"] = str(quantity)
        if price:
            modify_params["limit_px"] = str(price)

        # Modify order
        result = self._make_signed_request(request_type, **modify_params)

        # Create RequestData object
        extra_data = {
            "exchange_name": self._params.exchange_name,
            "symbol_name": symbol,
            "asset_type": self.asset_type,
            "request_type": request_type,
            "order_id": order_id,
            "side": side,
            "quantity": quantity,
            "price": price,
        }

        return self._get_request_data(result, extra_data)

    def get_spot_balances(self, user=None):
        """Get spot account balances"""
        user = user or self.address
        if not user:
            raise ValueError("User address required for balance query")

        result = self.get_spot_clearinghouse_state(user)

        # Process balance data
        balance_data = []
        if result.data and "balances" in result.data:
            for balance in result.data["balances"]:
                if float(balance.get("total", 0)) > 0:
                    balance_data.append(balance)

        # Create balance RequestData objects
        balances = []
        for balance in balance_data:
            extra_data = {
                "exchange_name": self._params.exchange_name,
                "symbol_name": balance.get("coin"),
                "asset_type": self.asset_type,
                "request_type": "get_spot_balances",
            }
            balance_obj = HyperliquidSpotRequestBalanceData(balance, balance.get("coin"), self.asset_type)
            balance_obj.init_data()
            balances.append(balance_obj)

        return balances

    def get_open_orders(self, user=None):
        """Get open spot orders"""
        user = user or self.address
        if not user:
            raise ValueError("User address required for order query")

        # Get user fills to get order history
        fills_result = self.get_user_fills(user, limit=100)

        # Extract order IDs from fills
        order_ids = set()
        if fills_result.data:
            for fill in fills_result.data:
                if "orderOid" in fill:
                    order_ids.add(fill["orderOid"])

        # Get order status for each order
        orders = []
        for order_id in order_ids:
            order_result = self.get_order_status(user, order_id)
            if order_result.data:
                extra_data = {
                    "exchange_name": self._params.exchange_name,
                    "symbol_name": "",
                    "asset_type": self.asset_type,
                    "request_type": "get_open_orders",
                }
                order_obj = HyperliquidRequestOrderData(order_result.data, "", self.asset_type)
                order_obj.init_data()
                orders.append(order_obj)

        return orders


class HyperliquidMarketWssDataSpot(HyperliquidMarketWssData):
    """Hyperliquid spot market WebSocket data"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.symbols = kwargs.get("symbols", [])
        self.logger_name = kwargs.get("logger_name", "hyperliquid_spot_market_wss.log")
        self._params = HyperliquidExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

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
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

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