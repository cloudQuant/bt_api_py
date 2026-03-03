import json
import time

from bt_api_py.containers.exchanges.latoken_exchange_data import LatokenExchangeDataSpot
from bt_api_py.containers.orders.latoken_order import LatokenRequestOrderData
from bt_api_py.containers.trades.latoken_trade import LatokenSpotWssTradeData
from bt_api_py.feeds.live_latoken.request_base import LatokenRequestData
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class LatokenRequestDataSpot(LatokenRequestData):
    """Latoken Spot Trading Request Data Handler"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "latoken_spot_feed.log")
        self._params = LatokenExchangeDataSpot()
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

    @classmethod
    def _capabilities(cls):
        """Return the capabilities of this feed"""
        from bt_api_py.feeds.capability import Capability

        return {
            Capability.GET_TICK,
            Capability.GET_DEPTH,
            Capability.GET_KLINE,
            Capability.GET_EXCHANGE_INFO,
        }


class LatokenMarketWssDataSpot:
    """Latoken Spot Market WebSocket Data Handler

    Note: Latoken's WebSocket support is limited/not fully documented.
    This is a placeholder for future implementation.
    """

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self._params = LatokenExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "latoken_spot_market_wss.log")
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

        # WebSocket URL (not fully documented)
        self.wss_url = kwargs.get("wss_url", "")

        # Subscribed topics
        self.topics = kwargs.get("topics", [])

        # API credentials
        self.api_key = kwargs.get("api_key", None)
        self.api_secret = kwargs.get("api_secret", None)

        # Running state
        self.running = False

    def start(self):
        """启动 WebSocket 连接"""
        if not self.wss_url:
            self.request_logger.warn("Latoken WebSocket URL not configured")
            return

        self.running = True
        self.request_logger.info(f"Starting Latoken Spot WebSocket connection to: {self.wss_url}")
        self.request_logger.info(f"Subscribing to topics: {self.topics}")

        # Placeholder for WebSocket implementation
        if self.topics:
            self._simulate_data_stream()

    def stop(self):
        """停止 WebSocket 连接"""
        self.running = False
        self.request_logger.info("Latoken Spot WebSocket connection stopped")

    def _simulate_data_stream(self):
        """模拟数据流"""
        time.sleep(5)

    def subscribe(self, topic):
        """订阅主题"""
        self.topics.append(topic)
        self.request_logger.info(f"Subscribed to topic: {topic}")

    def unsubscribe(self, topic):
        """取消订阅主题"""
        if topic in self.topics:
            self.topics.remove(topic)
            self.request_logger.info(f"Unsubscribed from topic: {topic}")

    def _send_message(self, message):
        """发送 WebSocket 消息"""
        message_str = json.dumps(message)
        self.request_logger.debug(f"Sending WebSocket message: {message_str}")


class LatokenAccountWssDataSpot:
    """Latoken Spot Account WebSocket Data Handler

    Note: Latoken's WebSocket support is limited/not fully documented.
    This is a placeholder for future implementation.
    """

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self._params = LatokenExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "latoken_spot_account_wss.log")
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

        # WebSocket URL
        self.wss_url = kwargs.get("wss_url", "")

        # Subscribed topics
        self.topics = kwargs.get("topics", [])

        # API credentials
        self.api_key = kwargs.get("api_key", None)
        self.api_secret = kwargs.get("api_secret", None)

        # Running state
        self.running = False

    def start(self):
        """启动 WebSocket 连接"""
        if not self.wss_url:
            self.request_logger.warn("Latoken WebSocket URL not configured")
            return

        self.running = True

        # Authenticate if credentials provided
        if self.api_key and self.api_secret:
            self._authenticate()

        self.request_logger.info(f"Starting Latoken Spot Account WebSocket connection to: {self.wss_url}")
        self.request_logger.info(f"Subscribing to topics: {self.topics}")

        if self.topics:
            self._simulate_account_stream()

    def stop(self):
        """停止 WebSocket 连接"""
        self.running = False
        self.request_logger.info("Latoken Spot Account WebSocket connection stopped")

    def _authenticate(self):
        """WebSocket 认证"""
        if not self.api_key or not self.api_secret:
            return

        auth_message = {
            'apiKey': self.api_key
        }

        self._send_message(auth_message)
        self.request_logger.info("Sent WebSocket authentication message")

    def _simulate_account_stream(self):
        """模拟账户数据流"""
        time.sleep(10)

    def _send_message(self, message):
        """发送 WebSocket 消息"""
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
