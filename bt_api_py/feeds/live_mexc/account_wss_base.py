"""
MEXC Account WebSocket Base Class

Provides base functionality for MEXC account data WebSocket connections.
"""

import json
import threading
import time

import websocket

from bt_api_py.logging_factory import get_logger


class MexcAccountWssData:
    """Base class for MEXC account data WebSocket connections"""

    def __init__(self, data_queue, **kwargs):
        self.data_queue = data_queue
        self.wss_url = kwargs.get("wss_url", "")
        self.exchange_data = kwargs.get("exchange_data")
        self.topics = kwargs.get("topics", [])
        self.logger_name = kwargs.get("logger_name", "mexc_account_wss.log")
        self.request_logger = None

        # WebSocket connection state
        self.ws = None
        self.is_running = False
        self.reconnect_interval = 5
        self.max_reconnect_attempts = 10
        self.reconnect_attempts = 0

        # Thread management
        self.thread = None

        # Listen key for user data stream
        self.listen_key = None

        # Create logger
        self._setup_logger()

    def _setup_logger(self):
        """Setup logger for the WebSocket connection"""
        self.request_logger = get_logger("mexc_account_wss")

    def start(self):
        """Start the WebSocket connection"""
        if self.is_running:
            return

        self.is_running = True
        self.thread = threading.Thread(target=self._run_websocket)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        """Stop the WebSocket connection"""
        self.is_running = False
        if self.ws:
            self.ws.close()

        if self.thread and self.thread.is_alive():
            self.thread.join()

    def _run_websocket(self):
        """Run the WebSocket connection with reconnection logic"""
        while self.is_running and self.reconnect_attempts < self.max_reconnect_attempts:
            try:
                # Get listen key for user data stream
                self._get_listen_key()

                if self.listen_key:
                    # Update URL with listen key
                    ws_url = f"{self.wss_url}?listenKey={self.listen_key}"
                    self.logger.info(f"Connecting to MEXC account WebSocket: {ws_url}")

                    self.ws = websocket.WebSocketApp(
                        ws_url,
                        on_open=self._on_open,
                        on_message=self._on_message,
                        on_error=self._on_error,
                        on_close=self._on_close,
                    )

                    # Run the WebSocket connection
                    self.ws.run_forever()

            except Exception as e:
                self.logger.error(f"WebSocket error: {e}")
                self.reconnect_attempts += 1
                time.sleep(self.reconnect_interval)

        if self.reconnect_attempts >= self.max_reconnect_attempts:
            self.logger.error("Max reconnection attempts reached")

    def _get_listen_key(self):
        """Get listen key for user data stream"""
        try:
            # This should be implemented using the REST API
            # For now, we'll use a placeholder
            self.listen_key = "dummy_listen_key"
            self.logger.info("Received listen key for user data stream")
        except Exception as e:
            self.logger.error(f"Error getting listen key: {e}")
            self.listen_key = None

    def _keepalive_listen_key(self):
        """Keep the listen key alive"""
        if self.listen_key and self.is_running:
            try:
                # This should be implemented using the REST API
                # For now, we'll just log it
                self.logger.info("Keeping listen key alive")
            except Exception as e:
                self.logger.error(f"Error keeping listen key alive: {e}")

    def _on_open(self, ws):
        """Handle WebSocket connection open"""
        self.logger.info("WebSocket connection opened")
        self.reconnect_attempts = 0

        # Subscribe to topics
        self._subscribe_topics()

    def _subscribe_topics(self):
        """Subscribe to the configured topics"""
        for topic_info in self.topics:
            topic = topic_info.get("topic")
            if topic:
                self._subscribe_topic(topic)

    def _subscribe_topic(self, topic):
        """Subscribe to a specific topic"""
        try:
            subscription = {"method": "SUBSCRIPTION", "params": [topic]}

            message = json.dumps(subscription)
            self.ws.send(message)
            self.logger.info(f"Subscribed to: {topic}")

        except Exception as e:
            self.logger.error(f"Error subscribing to topic {topic}: {e}")

    def _on_message(self, ws, message):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(message)
            self._process_message(data)
        except json.JSONDecodeError:
            self.logger.error(f"Invalid JSON message: {message}")
        except Exception as e:
            self.logger.error(f"Error processing message: {e}")

    def _process_message(self, data):
        """Process incoming message data"""
        # This method should be overridden by subclasses
        pass

    def _on_error(self, ws, error):
        """Handle WebSocket errors"""
        self.logger.error(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        """Handle WebSocket connection close"""
        self.logger.info(f"WebSocket connection closed: {close_status_code} - {close_msg}")

        # Attempt to reconnect if still running
        if self.is_running:
            self.reconnect_attempts += 1
            time.sleep(self.reconnect_interval)
            self._run_websocket()

    def _send_ping(self):
        """Send ping message to keep connection alive"""
        if self.ws and self.is_running:
            try:
                ping_message = json.dumps({"method": "PING"})
                self.ws.send(ping_message)
            except Exception as e:
                self.logger.error(f"Error sending ping: {e}")

    def _start_ping_thread(self):
        """Start a background thread to send ping messages"""

        def ping_worker():
            while self.is_running:
                time.sleep(30)  # Send ping every 30 seconds
                self._send_ping()
                # Also keep the listen key alive periodically
                if time.time() % 1200 < 30:  # Every 20 minutes
                    self._keepalive_listen_key()

        ping_thread = threading.Thread(target=ping_worker)
        ping_thread.daemon = True
        ping_thread.start()
