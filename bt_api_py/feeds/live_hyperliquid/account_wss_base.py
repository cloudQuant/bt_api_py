"""
Hyperliquid Account WebSocket Base Class

Provides common functionality for account WebSocket data handling.
"""

import json
import time
import threading
import websocket

from bt_api_py.feeds.feed import Feed
from bt_api_py.functions.log_message import SpdLogManager


class HyperliquidAccountWssData(Feed):
    """Base class for Hyperliquid account WebSocket data"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "hyperliquid_account_wss.log")
        self._params = kwargs.get("exchange_data", None)
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()
        self.async_logger = SpdLogManager(
            "./logs/" + self.logger_name, "async_request", 0, 0, False
        ).create_logger()

        self.ws_url = kwargs.get("ws_url", self._params.wss_url if self._params else "wss://api.hyperliquid.xyz/ws")
        self.user_address = kwargs.get("user_address", "")
        self.subscriptions = []
        self.is_running = False
        self.ws_thread = None

    def _get_request_data(self, data, extra_data):
        """Create RequestData object"""
        from bt_api_py.containers.requestdatas.request_data import RequestData
        return RequestData(data, extra_data)

    def on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            self.process_message(data)
        except Exception as e:
            self.request_logger.error(f"Error processing WebSocket message: {e}")

    def process_message(self, data):
        """Process incoming message - to be overridden by subclasses"""
        pass

    def on_open(self, ws):
        """Handle WebSocket connection open"""
        self.request_logger.info("Account WebSocket connection opened")

        # Send subscriptions
        for subscription in self.subscriptions:
            ws.send(json.dumps(subscription))

    def on_error(self, ws, error):
        """Handle WebSocket errors"""
        self.request_logger.error(f"WebSocket error: {error}")

    def on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection close"""
        self.request_logger.info("WebSocket connection closed")
        self.is_running = False

    def subscribe(self, subscription):
        """Add subscription"""
        self.subscriptions.append(subscription)

    def start(self):
        """Start WebSocket connection"""
        if self.is_running:
            return

        self.is_running = True
        self.ws_thread = threading.Thread(target=self._run_websocket)
        self.ws_thread.daemon = True
        self.ws_thread.start()

    def _run_websocket(self):
        """Run WebSocket in separate thread"""
        ws = websocket.WebSocketApp(
            self.ws_url,
            on_message=self.on_message,
            on_open=self.on_open,
            on_error=self.on_error,
            on_close=self.on_close
        )
        ws.run_forever(ping_interval=30)

    def stop(self):
        """Stop WebSocket connection"""
        self.is_running = False
        if self.ws_thread:
            self.ws_thread.join(timeout=5)
