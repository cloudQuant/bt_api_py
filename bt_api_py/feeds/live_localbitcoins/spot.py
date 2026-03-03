import json
import time

from bt_api_py.containers.exchanges.localbitcoins_exchange_data import LocalBitcoinsExchangeDataSpot
from bt_api_py.containers.orders.localbitcoins_order import LocalBitcoinsRequestOrderData
from bt_api_py.containers.trades.localbitcoins_trade import LocalBitcoinsSpotWssTradeData
from bt_api_py.feeds.live_localbitcoins.request_base import LocalBitcoinsRequestData
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class LocalBitcoinsRequestDataSpot(LocalBitcoinsRequestData):
    """LocalBitcoins Spot Trading Request Data Handler"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "localbitcoins_spot_feed.log")
        self._params = LocalBitcoinsExchangeDataSpot()
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
            Capability.GET_EXCHANGE_INFO,
        }


class LocalBitcoinsMarketWssDataSpot:
    """LocalBitcoins Spot Market WebSocket Data Handler

    Note: LocalBitcoins does not provide WebSocket API.
    This is a placeholder for future compatibility.
    """

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self._params = LocalBitcoinsExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "localbitcoins_spot_market_wss.log")
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

        # WebSocket URL (not supported)
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
            self.request_logger.warn("LocalBitcoins WebSocket not supported")
            return

        self.running = True
        self.request_logger.info(f"Starting LocalBitcoins Spot WebSocket connection to: {self.wss_url}")
        self.request_logger.info(f"Subscribing to topics: {self.topics}")

    def stop(self):
        """停止 WebSocket 连接"""
        self.running = False
        self.request_logger.info("LocalBitcoins Spot WebSocket connection stopped")

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


class LocalBitcoinsAccountWssDataSpot:
    """LocalBitcoins Spot Account WebSocket Data Handler

    Note: LocalBitcoins does not provide WebSocket API.
    This is a placeholder for future compatibility.
    """

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self._params = LocalBitcoinsExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "localbitcoins_spot_account_wss.log")
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
            self.request_logger.warn("LocalBitcoins WebSocket not supported")
            return

        self.running = True

        self.request_logger.info(f"Starting LocalBitcoins Spot Account WebSocket connection to: {self.wss_url}")
        self.request_logger.info(f"Subscribing to topics: {self.topics}")

    def stop(self):
        """停止 WebSocket 连接"""
        self.running = False
        self.request_logger.info("LocalBitcoins Spot Account WebSocket connection stopped")

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
