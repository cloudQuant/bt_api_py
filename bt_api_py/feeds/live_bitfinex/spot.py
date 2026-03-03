import json
import time

from bt_api_py.containers.accounts.bitfinex_account import BitfinexSpotWssAccountData
from bt_api_py.containers.exchanges.bitfinex_exchange_data import BitfinexExchangeDataSpot
from bt_api_py.containers.orders.bitfinex_order import (
    BitfinexRequestOrderData,
    BitfinexWssOrderData,
)
from bt_api_py.containers.trades.bitfinex_trade import BitfinexSpotWssTradeData
from bt_api_py.feeds.live_bitfinex.request_base import BitfinexRequestData
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class BitfinexRequestDataSpot(BitfinexRequestData):
    """Bitfinex Spot Trading Request Data Handler"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "bitfinex_spot_feed.log")
        self._params = BitfinexExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

    def _make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """创建订单"""
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path(request_type)

        # 解析订单类型
        side, order_type_subtype = order_type.split("-")

        # 准备参数
        params = {
            "type": order_type_subtype.upper(),
            "symbol": request_symbol,
            "amount": str(vol),
            "flags": 64 if post_only else 0  # 64 = 隐藏订单
        }

        # 限价单添加价格
        if price is not None and order_type_subtype != "market":
            params["price"] = str(price)

        # 添加客户端订单 ID
        if client_order_id is not None:
            params["cid"] = int(client_order_id)

        # 添加杠杆
        if "lev" in kwargs:
            params["lev"] = kwargs["lev"]

        # 准备额外数据
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "post_only": post_only,
                "normalize_function": self._make_order_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """标准化订单响应数据"""
        status = input_data is not None

        if isinstance(input_data, list):
            data = [
                BitfinexRequestOrderData(i, extra_data["symbol_name"], extra_data["asset_type"], True)
                for i in input_data
            ]
        elif isinstance(input_data, dict):
            data = [BitfinexRequestOrderData(input_data, extra_data["symbol_name"], extra_data["asset_type"], True)]
        else:
            data = []

        return data, status

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data (public method)"""
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        return self._request(path, params, extra_data)

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """Get orderbook data (public method)"""
        path, params, extra_data = self._get_order_book(symbol, "P0", str(count), extra_data, **kwargs)
        return self._request(path, params, extra_data)

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """Get kline data (public method)"""
        path, params, extra_data = self._get_klines(symbol, period, count, extra_data, **kwargs)
        return self._request(path, params, extra_data)

    def _cancel_order(self, order_id, extra_data=None, **kwargs):
        """取消订单"""
        request_type = "cancel_order"
        path = self._params.get_rest_path(request_type)

        params = {"id": order_id}

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "order_id": order_id,
                "asset_type": self.asset_type,
                "exchange_name": self.exchange_name,
                "normalize_function": self._cancel_order_normalize_function,
            },
        )

        return path, params, extra_data

    @staticmethod
    def _cancel_order_normalize_function(input_data, extra_data):
        """标准化取消订单响应数据"""
        status = input_data is not None

        if isinstance(input_data, list) and len(input_data) > 0:
            order_id = extra_data["order_id"]
            asset_type = extra_data["asset_type"]

            # Bitfinex 取消响应格式: [ID, ...]
            cancel_data = {
                "id": input_data[0],
                "status": "SUCCESS"
            }

            data = [BitfinexRequestOrderData(cancel_data, f"order_{order_id}", asset_type, True)]
        else:
            data = []

        return data, status


# Market WebSocket Data Handler (Placeholder - WebSocket implementation would be separate)
class BitfinexMarketWssDataSpot:
    """Bitfinex Spot Market WebSocket Data Handler"""

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self._params = BitfinexExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "bitfinex_spot_market_wss.log")
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

        # WebSocket URL
        self.wss_url = kwargs.get("wss_url", "wss://api-pub.bitfinex.com/ws/2")

        # 订阅的主题
        self.topics = kwargs.get("topics", [])

        # 认证信息（如果有）
        self.api_key = kwargs.get("api_key", None)
        self.api_secret = kwargs.get("api_secret", None)

        # 运行状态
        self.running = False

    def start(self):
        """启动 WebSocket 连接"""
        self.running = True
        # 这里应该实现实际的 WebSocket 连接逻辑
        # 为了简化，这里只记录日志
        self.request_logger.info(f"Starting Bitfinex Spot WebSocket connection to: {self.wss_url}")
        self.request_logger.info(f"Subscribing to topics: {self.topics}")

        # 模拟数据推送
        if self.topics:
            self._simulate_data_stream()

    def stop(self):
        """停止 WebSocket 连接"""
        self.running = False
        self.request_logger.info("Bitfinex Spot WebSocket connection stopped")

    def _simulate_data_stream(self):
        """模拟数据流（实际实现应该连接真正的 WebSocket）"""
        symbols = [topic.get("symbol", "tBTCUSD") for topic in self.topics if "symbol" in topic]

        # 模拟发送 ticker 数据
        for symbol in symbols:
            ticker_data = {
                "symbol": symbol,
                "bid": 45000.0,
                "bid_size": 1.5,
                "ask": 45010.0,
                "ask_size": 2.0,
                "daily_change": 100.0,
                "daily_change_perc": 0.0022,
                "last_price": 45005.0,
                "volume": 1000.0,
                "high": 45200.0,
                "low": 44800.0,
                "timestamp": int(time.time() * 1000)
            }

            # 放入数据队列
            # self.data_queue.put(("ticker", symbol, ticker_data))

            time.sleep(5)  # 每 5 秒发送一次

    def subscribe(self, topic):
        """订阅主题"""
        self.topics.append(topic)
        self.request_logger.info(f"Subscribed to topic: {topic}")

    def unsubscribe(self, topic):
        """取消订阅主题"""
        if topic in self.topics:
            self.topics.remove(topic)
            self.request_logger.info(f"Unsubscribed from topic: {topic}")


# Account WebSocket Data Handler (Placeholder - WebSocket implementation would be separate)
class BitfinexAccountWssDataSpot:
    """Bitfinex Spot Account WebSocket Data Handler"""

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self._params = BitfinexExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "bitfinex_spot_account_wss.log")
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

        # WebSocket URL
        self.wss_url = kwargs.get("wss_url", "wss://api-pub.bitfinex.com/ws/2")

        # 订阅的主题
        self.topics = kwargs.get("topics", [])

        # 认证信息
        self.api_key = kwargs.get("api_key", None)
        self.api_secret = kwargs.get("api_secret", None)

        # 运行状态
        self.running = False

    def start(self):
        """启动 WebSocket 连接"""
        self.running = True

        # 如果需要认证
        if self.api_key and self.api_secret:
            self._authenticate()

        self.request_logger.info(f"Starting Bitfinex Spot Account WebSocket connection to: {self.wss_url}")
        self.request_logger.info(f"Subscribing to topics: {self.topics}")

        # 模拟数据推送
        if self.topics:
            self._simulate_account_stream()

    def stop(self):
        """停止 WebSocket 连接"""
        self.running = False
        self.request_logger.info("Bitfinex Spot Account WebSocket connection stopped")

    def _authenticate(self):
        """WebSocket 认证"""
        if not self.api_key or not self.api_secret:
            return

        # 生成认证载荷
        nonce = str(int(time.time() * 1000000))
        auth_payload = f'AUTH{nonce}'

        import hmac
        import hashlib

        signature = hmac.new(
            self.api_secret.encode(),
            auth_payload.encode(),
            hashlib.sha384
        ).hexdigest()

        auth_message = {
            'event': 'auth',
            'apiKey': self.api_key,
            'authSig': signature,
            'authPayload': auth_payload,
            'authNonce': nonce
        }

        # 发送认证消息
        # self._send_message(auth_message)

        self.request_logger.info("Sent WebSocket authentication message")

    def _simulate_account_stream(self):
        """模拟账户数据流"""
        # 模拟账户余额更新
        if any("balance" in str(topic).lower() for topic in self.topics):
            balance_data = {
                "wallet_type": "exchange",
                "currency": "USD",
                "balance": 10000.0,
                "unsettled_interest": 0.0,
                "balance_available": 9500.0,
                "timestamp": int(time.time() * 1000)
            }
            # self.data_queue.put(("balance", "USD", balance_data))

        # 模拟订单更新
        if any("order" in str(topic).lower() for topic in self.topics):
            order_data = {
                "id": 12345678,
                "symbol": "tBTCUSD",
                "amount": 0.001,
                "price": 45000.0,
                "status": "ACTIVE",
                "timestamp": int(time.time() * 1000)
            }
            # self.data_queue.put(("order", "tBTCUSD", order_data))

        # 模拟成交更新
        if any("trade" in str(topic).lower() for topic in self.topics):
            trade_data = {
                "id": 987654,
                "symbol": "tBTCUSD",
                "amount": 0.001,
                "price": 45000.0,
                "fee": 1.0,
                "timestamp": int(time.time() * 1000)
            }
            # self.data_queue.put(("trade", "tBTCUSD", trade_data))

        time.sleep(10)  # 每 10 秒发送一次

    def _send_message(self, message):
        """发送 WebSocket 消息"""
        # 实际实现应该发送真正的 WebSocket 消息
        message_str = json.dumps(message)
        self.request_logger.debug(f"Sending WebSocket message: {message_str}")

    def subscribe(self, topic):
        """订阅主题"""
        self.topics.append(topic)
        self.request_logger.info(f"Subscribed to topic: {topic}")

    def unsubscribe(self, topic):
        """取消订阅主题"""
        if topic in self.topics:
            self.topics.remove(topic)
            self.request_logger.info(f"Unsubscribed from topic: {topic}")