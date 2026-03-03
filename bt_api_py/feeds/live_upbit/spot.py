"""
Upbit Spot Trading Feed

Provides real-time market data and trading functionality for Upbit spot trading.
"""

import json
import time
from queue import Queue

import websocket

from bt_api_py.containers.exchanges.upbit_exchange_data import UpbitExchangeDataSpot
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.upbit_ticker import UpbitTickerData
from bt_api_py.containers.orders.upbit_order import UpbitOrderData
from bt_api_py.containers.balances.upbit_balance import UpbitBalanceData
from bt_api_py.feeds.capability import Capability
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.upbit_ticker import UpbitTickerData
from bt_api_py.containers.orders.upbit_order import UpbitOrderData
from bt_api_py.containers.balances.upbit_balance import UpbitBalanceData
from bt_api_py.functions.log_message import SpdLogManager

logger = SpdLogManager(
    file_name="upbit_spot.log", logger_name="upbit_spot", print_info=False
).create_logger()


class UpbitSpotFeed(RequestData):
    """Upbit Spot Trading Feed

    Provides both REST API and WebSocket functionality for Upbit spot trading.
    """

    # ── 类属性 ─────────────────────────────────────────────────────
    exchange_name = "UPBIT___SPOT"
    asset_type = "spot"
    capabilities = [
        Capability.GET_TICK,
        Capability.GET_DEPTH,
        Capability.GET_KLINE,
        Capability.MAKE_ORDER,
        Capability.CANCEL_ORDER,
        Capability.QUERY_ORDER,
        Capability.QUERY_OPEN_ORDERS,
        Capability.GET_BALANCE,
        Capability.GET_ACCOUNT,
        Capability.MARKET_STREAM,
        Capability.ACCOUNT_STREAM,
        Capability.GET_EXCHANGE_INFO,
        Capability.GET_SERVER_TIME,
    ]

    def __init__(self, config: dict = None):
        # Initialize with empty data and extra_data
        extra_data = {
            "exchange_name": self.exchange_name,
            "symbol_name": "",
            "asset_type": self.asset_type,
            "request_type": ""
        }
        super().__init__([], extra_data)
        self.exchange_data = UpbitExchangeDataSpot()

        # WebSocket
        self.ws = None
        self.ws_queue = Queue()
        self.is_ws_connected = False
        self.ws_subscriptions = []

        # Rate limiting
        self._init_rate_limiter()

    def _init_rate_limiter(self):
        """Initialize rate limiter for Upbit"""
        from bt_api_py.rate_limiter import RateLimiter, RateLimitRule, RateLimitScope, RateLimitType

        rules = [
            RateLimitRule(
                name="public_requests",
                type=RateLimitType.SLIDING_WINDOW,
                limit=900,
                interval=60,
                scope=RateLimitScope.GLOBAL,
                weight=1,
            ),
            RateLimitRule(
                name="private_requests",
                type=RateLimitType.FIXED_WINDOW,
                limit=30,
                interval=60,
                scope=RateLimitScope.GLOBAL,
                weight=1,
            ),
            RateLimitRule(
                name="order_requests",
                type=RateLimitType.FIXED_WINDOW,
                limit=8,
                interval=1,
                scope=RateLimitScope.ENDPOINT,
                weight=1,
                endpoint=r"/v1/orders*",
            ),
        ]
        self.rate_limiter = RateLimiter(rules=rules)

    # ── WebSocket 连接管理 ──────────────────────────────────────────

    def connect_ws(self):
        """连接 WebSocket"""
        if self.ws and self.is_ws_connected:
            logger.warning("WebSocket already connected")
            return

        try:
            self.ws = websocket.WebSocketApp(
                self.exchange_data.wss_url,
                on_open=self._on_ws_open,
                on_message=self._on_ws_message,
                on_error=self._on_ws_error,
                on_close=self._on_ws_close,
            )
            self.ws.run_forever(ping_interval=30)
        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")

    def disconnect_ws(self):
        """断开 WebSocket 连接"""
        if self.ws:
            self.ws.close()
            self.is_ws_connected = False
            self.ws_subscriptions = []

    def _on_ws_open(self, ws):
        """WebSocket 连接打开回调"""
        self.is_ws_connected = True
        logger.info("WebSocket connected to Upbit")

        # 重新订阅之前的频道
        for subscription in self.ws_subscriptions:
            ws.send(subscription)

    def _on_ws_message(self, ws, message):
        """WebSocket 消息回调"""
        try:
            data = json.loads(message)
            self.ws_queue.put(data)
        except Exception as e:
            logger.error(f"Error processing WebSocket message: {e}")

    def _on_ws_error(self, ws, error):
        """WebSocket 错误回调"""
        logger.error(f"WebSocket error: {error}")

    def _on_ws_close(self, ws, close_status_code, close_msg):
        """WebSocket 关闭回调"""
        self.is_ws_connected = False
        logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")

    # ── WebSocket 订阅管理 ──────────────────────────────────────────

    def subscribe_ticker(self, symbol):
        """订阅 ticker 数据"""
        if not self.is_ws_connected:
            self.connect_ws()

        subscription = self.exchange_data.get_wss_path(topic="ticker", market=symbol)
        self.ws.send(subscription)
        self.ws_subscriptions.append(subscription)
        logger.info(f"Subscribed to ticker for {symbol}")

    def subscribe_trades(self, symbol):
        """订阅成交数据"""
        if not self.is_ws_connected:
            self.connect_ws()

        subscription = self.exchange_data.get_wss_path(topic="trade", market=symbol)
        self.ws.send(subscription)
        self.ws_subscriptions.append(subscription)
        logger.info(f"Subscribed to trades for {symbol}")

    def subscribe_orderbook(self, symbol):
        """订阅订单簿数据"""
        if not self.is_ws_connected:
            self.connect_ws()

        subscription = self.exchange_data.get_wss_path(topic="orderbook", market=symbol)
        self.ws.send(subscription)
        self.ws_subscriptions.append(subscription)
        logger.info(f"Subscribed to orderbook for {symbol}")

    def subscribe_multiple(self, subscriptions):
        """批量订阅多个频道"""
        if not self.is_ws_connected:
            self.connect_ws()

        for sub in subscriptions:
            topic = sub["topic"]
            symbol = sub["symbol"]

            if topic == "ticker":
                self.subscribe_ticker(symbol)
            elif topic == "trade":
                self.subscribe_trades(symbol)
            elif topic == "orderbook":
                self.subscribe_orderbook(symbol)

    # ── 数据处理 ──────────────────────────────────────────────────

    def process_ticker_data(self, data):
        """处理 ticker 数据"""
        if data.get("type") == "ticker":
            market = data.get("code", "")
            symbol = market.replace("-", "")

            ticker = UpbitTickerData(
                data,
                symbol=symbol,
                asset_type=self.asset_type
            )
            ticker.init_data()
            return ticker

        return None

    def process_trade_data(self, data):
        """处理成交数据"""
        if data.get("type") == "trade":
            market = data.get("code", "")
            symbol = market.replace("-", "")

            trade_info = {
                "symbol": symbol,
                "price": data.get("trade_price"),
                "volume": data.get("trade_volume"),
                "side": data.get("ask_bid"),  # "bid" or "ask"
                "timestamp": data.get("timestamp"),
                "trade_date_utc": data.get("trade_date_utc"),
                "trade_time_utc": data.get("trade_time_utc"),
            }

            return trade_info

        return None

    def process_orderbook_data(self, data):
        """处理订单簿数据"""
        if data.get("type") == "orderbook":
            market = data.get("code", "")
            symbol = market.replace("-", "")

            orderbook_info = {
                "symbol": symbol,
                "orderbook_units": data.get("orderbook_units", []),
                "total_bid_size": data.get("total_bid_size"),
                "total_ask_size": data.get("total_ask_size"),
            }

            return orderbook_info

        return None

    # ── 数据获取方法 ──────────────────────────────────────────────

    def get_latest_ws_data(self):
        """获取最新的 WebSocket 数据"""
        try:
            data = self.ws_queue.get_nowait()
            self.ws_queue.task_done()

            msg_type = data.get("type", "")

            if msg_type == "ticker":
                return self.process_ticker_data(data)
            elif msg_type == "trade":
                return self.process_trade_data(data)
            elif msg_type == "orderbook":
                return self.process_orderbook_data(data)

            return data

        except:
            return None

    def wait_for_ws_data(self, timeout=1.0):
        """等待 WebSocket 数据"""
        try:
            data = self.ws_queue.get(timeout=timeout)
            self.ws_queue.task_done()

            msg_type = data.get("type", "")

            if msg_type == "ticker":
                return self.process_ticker_data(data)
            elif msg_type == "trade":
                return self.process_trade_data(data)
            elif msg_type == "orderbook":
                return self.process_orderbook_data(data)

            return data

        except:
            return None

    # ── 便捷方法 ──────────────────────────────────────────────────

    def get_orderbook_snapshot(self, symbol, level=5):
        """获取订单簿快照"""
        import requests

        url = f"{self.exchange_data.rest_url}{self.exchange_data.get_rest_path('orderbook')}"
        params = {"markets": symbol}

        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            if data:
                book = data[0]
                return {
                    "symbol": symbol,
                    "bids": book["orderbook_units"][:level],
                    "asks": book["orderbook_units"][:level],
                    "timestamp": book.get("timestamp")
                }

        return None

    def get_ticker_snapshot(self, symbol):
        """获取 ticker 快照"""
        import requests

        url = f"{self.exchange_data.rest_url}{self.exchange_data.get_rest_path('ticker')}"
        params = {"markets": symbol}

        response = requests.get(url, params=params)
        if response.status_code == 200:
            data = response.json()
            if data:
                ticker = data[0]
                return {
                    "symbol": symbol,
                    "last_price": ticker.get("trade_price"),
                    "bid_price": ticker.get("bid_price"),
                    "ask_price": ticker.get("ask_price"),
                    "volume": ticker.get("acc_trade_volume_24h"),
                    "high_price": ticker.get("high_price"),
                    "low_price": ticker.get("low_price"),
                    "timestamp": ticker.get("timestamp")
                }

        return None

    def get_recent_trades(self, symbol, limit=10):
        """获取最近成交记录"""
        import requests

        url = f"{self.exchange_data.rest_url}{self.exchange_data.get_rest_path('trades_ticks')}"
        params = {"market": symbol, "count": min(limit, 50)}

        response = requests.get(url, params=params)
        if response.status_code == 200:
            return response.json()

        return []

    def get_klines(self, symbol, interval, limit=200):
        """获取 K 线数据"""
        interval_map = {
            "1m": "1", "3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30",
            "1h": "60", "2h": "120", "4h": "240", "6h": "360", "8h": "480", "12h": "720",
            "1d": "D", "3d": "3D", "1w": "W", "1M": "M"
        }

        actual_interval = interval_map.get(interval, interval)

        if actual_interval in ["1", "3", "5", "10", "15", "30", "60", "120", "240", "360", "480", "720"]:
            # 分钟级别
            unit = actual_interval
            url = f"{self.exchange_data.rest_url}/v1/candles/minutes/{unit}"
        elif actual_interval == "D":
            url = f"{self.exchange_data.rest_url}{self.exchange_data.get_rest_path('candles_days')}"
        elif actual_interval == "3D":
            url = f"{self.exchange_data.rest_url}{self.exchange_data.get_rest_path('candles_days')}"
            limit = limit * 3  # 近似
        elif actual_interval == "W":
            url = f"{self.exchange_data.rest_url}{self.exchange_data.get_rest_path('candles_weeks')}"
        elif actual_interval == "M":
            url = f"{self.exchange_data.rest_url}{self.exchange_data.get_rest_path('candles_months')}"
        else:
            raise ValueError(f"Unsupported interval: {interval}")

        params = {"market": symbol, "count": min(limit, 200)}

        import requests
        response = requests.get(url, params=params)
        if response.status_code == 200:
            return response.json()

        return []

    # ── 私有 API 方法 ───────────────────────────────────────────────

    def _make_request(self, method, path, params=None, data=None, headers=None):
        """发送 REST API 请求"""
        import requests
        import os

        url = f"{self.exchange_data.rest_url}{path}"

        # 添加 JWT token 如果需要
        if headers is None:
            headers = {}
        if "Authorization" not in headers:
            # Upbit uses JWT token for authentication
            # This is a placeholder - actual implementation would generate JWT
            api_key = os.getenv("UPBIT_ACCESS_KEY")
            api_secret = os.getenv("UPBIT_SECRET_KEY")
            if api_key and api_secret:
                headers["Authorization"] = f"Bearer {api_key}"

        try:
            if method.upper() == "GET":
                response = requests.get(url, params=params, headers=headers)
            elif method.upper() == "POST":
                response = requests.post(url, json=params, headers=headers)
            elif method.upper() == "DELETE":
                response = requests.delete(url, json=params, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"API request failed: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Request error: {e}")
            return None

    def place_order(self, market, side, ord_type, volume=None, price=None, **kwargs):
        """下单

        Args:
            market: 交易对，如 "KRW-BTC"
            side: 买卖方向，"bid" 或 "ask"
            ord_type: 订单类型，"limit", "market", "price"
            volume: 数量
            price: 价格
            **kwargs: 其他参数

        Returns:
            订单信息
        """
        path = "/v1/orders"
        params = {
            "market": market,
            "side": side,
            "ord_type": ord_type,
        }

        if volume is not None:
            params["volume"] = volume
        if price is not None:
            params["price"] = price

        params.update(kwargs)

        return self._make_request("POST", path, params=params)

    def cancel_order(self, uuid=None):
        """取消订单"""
        if uuid is None:
            raise ValueError("Order uuid is required")

        path = "/v1/order"
        params = {"uuid": uuid}

        return self._make_request("DELETE", path, params=params)

    def get_order(self, uuid=None):
        """查询订单"""
        if uuid is None:
            raise ValueError("Order uuid is required")

        path = "/v1/order"
        params = {"uuid": uuid}

        return self._make_request("GET", path, params=params)

    def get_orders(self, **kwargs):
        """查询订单列表"""
        path = "/v1/orders"
        return self._make_request("GET", path, params=kwargs)

    def get_accounts(self):
        """获取账户信息"""
        path = "/v1/accounts"
        return self._make_request("GET", path) or []