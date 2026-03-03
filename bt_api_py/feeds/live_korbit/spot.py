import json
import time

from bt_api_py.containers.exchanges.korbit_exchange_data import KorbitExchangeDataSpot
from bt_api_py.containers.orders.korbit_order import KorbitRequestOrderData
from bt_api_py.containers.trades.korbit_trade import KorbitSpotWssTradeData
from bt_api_py.feeds.live_korbit.request_base import KorbitRequestData
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.functions.utils import update_extra_data


class KorbitRequestDataSpot(KorbitRequestData):
    """Korbit Spot Trading Request Data Handler"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "korbit_spot_feed.log")
        self._params = KorbitExchangeDataSpot()
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


class KorbitMarketWssDataSpot:
    """Korbit Spot Market WebSocket Data Handler"""

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self._params = KorbitExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "korbit_spot_market_wss.log")
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

        # WebSocket URL
        self.wss_url = kwargs.get("wss_url", "wss://ws-api.korbit.co.kr/v2/public")

        # Subscribed topics
        self.topics = kwargs.get("topics", [])

        # API credentials
        self.api_key = kwargs.get("api_key", None)
        self.api_secret = kwargs.get("api_secret", None)

        # Running state
        self.running = False

    def start(self):
        """启动 WebSocket 连接"""
        self.running = True
        self.request_logger.info(f"Starting Korbit Spot WebSocket connection to: {self.wss_url}")
        self.request_logger.info(f"Subscribing to topics: {self.topics}")

        # Simulate data stream
        if self.topics:
            self._simulate_data_stream()

    def stop(self):
        """停止 WebSocket 连接"""
        self.running = False
        self.request_logger.info("Korbit Spot WebSocket connection stopped")

    def _simulate_data_stream(self):
        """模拟数据流（实际实现应该连接真正的 WebSocket）"""
        symbols = [topic.get("symbol", "btc_krw") for topic in self.topics if "symbol" in topic]

        # Simulate ticker data
        for symbol in symbols:
            ticker_data = {
                "symbol": symbol,
                "last": "95000000",
                "bid": "94990000",
                "ask": "95000000",
                "low": "93500000",
                "high": "95800000",
                "volume": "1234.56",
                "timestamp": int(time.time() * 1000)
            }

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


class KorbitAccountWssDataSpot:
    """Korbit Spot Account WebSocket Data Handler"""

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self._params = KorbitExchangeDataSpot()
        self.logger_name = kwargs.get("logger_name", "korbit_spot_account_wss.log")
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()

        # WebSocket URL
        self.wss_url = kwargs.get("wss_url", "wss://ws-api.korbit.co.kr/v2/private")

        # Subscribed topics
        self.topics = kwargs.get("topics", [])

        # API credentials
        self.api_key = kwargs.get("api_key", None)
        self.api_secret = kwargs.get("api_secret", None)

        # Running state
        self.running = False

    def start(self):
        """启动 WebSocket 连接"""
        self.running = True

        # Authenticate if credentials provided
        if self.api_key and self.api_secret:
            self._authenticate()

        self.request_logger.info(f"Starting Korbit Spot Account WebSocket connection to: {self.wss_url}")
        self.request_logger.info(f"Subscribing to topics: {self.topics}")

        if self.topics:
            self._simulate_account_stream()

    def stop(self):
        """停止 WebSocket 连接"""
        self.running = False
        self.request_logger.info("Korbit Spot Account WebSocket connection stopped")

    def _authenticate(self):
        """WebSocket 认证"""
        if not self.api_key or not self.api_secret:
            return

        auth_message = {
            'event': 'auth',
            'token': self.api_key
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
