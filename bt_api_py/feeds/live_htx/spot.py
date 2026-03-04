"""HTX Spot Trading Feed"""

import json
import time

from bt_api_py.containers.accounts.htx_account import HtxSpotRequestAccountData
from bt_api_py.containers.balances.htx_balance import HtxRequestBalanceData
from bt_api_py.containers.bars.htx_bar import HtxRequestBarData
from bt_api_py.containers.exchanges.htx_exchange_data import HtxExchangeDataSpot
from bt_api_py.containers.orderbooks.htx_orderbook import HtxRequestOrderBookData
from bt_api_py.containers.orders.htx_order import HtxRequestOrderData
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.containers.tickers.htx_ticker import HtxRequestTickerData
from bt_api_py.containers.trades.htx_trade import HtxRequestTradeData
from bt_api_py.feeds.live_htx.request_base import HtxRequestData
from bt_api_py.feeds.my_websocket_app import MyWebsocketApp
from bt_api_py.functions.utils import update_extra_data
from bt_api_py.logging_factory import get_logger


class HtxRequestDataSpot(HtxRequestData):
    """HTX Spot trading REST API feed."""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.logger_name = kwargs.get("logger_name", "htx_spot_feed.log")
        self._params = HtxExchangeDataSpot()
        self.request_logger = get_logger("request")
        self.async_logger = get_logger("async_request")

    # ==================== Market Data Methods ====================

    def _get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker data.

        Args:
            symbol: Trading pair symbol
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_ticker"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": request_symbol,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": HtxRequestDataSpot._get_ticker_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_ticker_normalize_function(input_data, extra_data):
        """Normalize ticker response."""
        if input_data is None:
            return [], False
        status = input_data.get("status") == "ok"
        data = [HtxRequestTickerData(input_data, extra_data["symbol_name"], extra_data["asset_type"], True)]
        return data, status

    def get_ticker(self, symbol, extra_data=None, **kwargs):
        """Get ticker data for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            RequestData: Ticker data
        """
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """Get ticker data (standard interface alias for get_ticker)."""
        return self.get_ticker(symbol, extra_data=extra_data, **kwargs)

    def _get_depth(self, symbol, depth_type="step0", extra_data=None, **kwargs):
        """Get orderbook depth.

        Args:
            symbol: Trading pair symbol
            depth_type: Depth type (step0, step1, step2, step3, step4, step5)
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_depth"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": request_symbol,
            "type": depth_type,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": HtxRequestDataSpot._get_depth_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_depth_normalize_function(input_data, extra_data):
        """Normalize depth response."""
        if input_data is None:
            return [], False
        status = input_data.get("status") == "ok"
        data = [HtxRequestOrderBookData(input_data, extra_data["symbol_name"], extra_data["asset_type"], True)]
        return data, status

    def get_depth(self, symbol, depth_type="step0", extra_data=None, **kwargs):
        """Get orderbook depth.

        Args:
            symbol: Trading pair symbol
            depth_type: Depth type

        Returns:
            RequestData: Orderbook data
        """
        path, params, extra_data = self._get_depth(symbol, depth_type, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Server Time & Exchange Info Methods ====================

    def get_server_time(self, extra_data=None, **kwargs):
        """Get server time."""
        request_type = "get_server_time"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return self.request(path, params=params, extra_data=extra_data)

    def get_symbols(self, extra_data=None, **kwargs):
        """Get trading symbols list."""
        request_type = "get_symbols"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": HtxRequestDataSpot._get_htx_data_normalize_function,
            },
        )
        return self.request(path, params=params, extra_data=extra_data)

    def get_exchange_info(self, extra_data=None, **kwargs):
        """Get exchange info (symbols)."""
        return self.get_symbols(extra_data=extra_data, **kwargs)

    def get_currencies(self, extra_data=None, **kwargs):
        """Get supported currencies list."""
        path = "GET /v1/common/currencys"
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": "get_currencies",
                "symbol_name": "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": HtxRequestDataSpot._get_htx_data_normalize_function,
            },
        )
        return self.request(path, params=params, extra_data=extra_data)

    @staticmethod
    def _get_htx_data_normalize_function(input_data, extra_data):
        """Generic normalize: extract 'data' from HTX response {"status":"ok","data":...}."""
        if input_data is None:
            return [], False
        status = input_data.get("status") == "ok"
        data = input_data.get("data", [])
        if isinstance(data, list):
            return data, status
        return [data], status

    # ==================== Kline Methods ====================

    def _get_kline(self, symbol, period, count=200, extra_data=None, **kwargs):
        """Get kline/candlestick data.

        Args:
            symbol: Trading pair symbol
            period: Kline period (1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w, 1M)
            count: Number of klines to return (max 2000)
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_period = self._params.get_period(period)
        request_type = "get_kline"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": request_symbol,
            "period": request_period,
            "size": count,
        }
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": HtxRequestDataSpot._get_kline_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_kline_normalize_function(input_data, extra_data):
        """Normalize kline response."""
        if input_data is None:
            return [], False
        status = input_data.get("status") == "ok"
        klines = input_data.get("data", [])
        data = [
            HtxRequestBarData(k, extra_data["symbol_name"], extra_data["asset_type"], True)
            for k in klines
        ]
        return data, status

    def get_kline(self, symbol, period, count=200, extra_data=None, **kwargs):
        """Get kline/candlestick data.

        Args:
            symbol: Trading pair symbol
            period: Kline period
            count: Number of klines

        Returns:
            RequestData: Kline data
        """
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Account Methods ====================

    def _get_accounts(self, extra_data=None, **kwargs):
        """Get account list.

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_accounts"
        path = self._params.get_rest_path(request_type)
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": HtxRequestDataSpot._get_accounts_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_accounts_normalize_function(input_data, extra_data):
        """Normalize accounts response."""
        if input_data is None:
            return [], False
        status = input_data.get("status") == "ok"
        data = input_data.get("data", [])
        return data, status

    def get_accounts(self, extra_data=None, **kwargs):
        """Get account list.

        Returns:
            RequestData: Account list
        """
        path, params, extra_data = self._get_accounts(extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """Get account info (standard interface alias for get_accounts)."""
        return self.get_accounts(extra_data=extra_data, **kwargs)

    def _get_balance(self, account_id=None, extra_data=None, **kwargs):
        """Get account balance.

        Args:
            account_id: Account ID (if None, uses self.account_id)
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        if account_id is None:
            account_id = self.account_id

        if account_id is None:
            raise ValueError("account_id is required. Use get_accounts() to get account ID first.")

        request_type = "get_balance"
        path = self._params.get_rest_path(request_type).replace("{account-id}", str(account_id))
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": HtxRequestDataSpot._get_balance_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_balance_normalize_function(input_data, extra_data):
        """Normalize balance response."""
        if input_data is None:
            return [], False
        status = input_data.get("status") == "ok"
        data = [HtxRequestBalanceData(input_data, extra_data["symbol_name"], extra_data["asset_type"], True)]
        return data, status

    def get_balance(self, account_id=None, extra_data=None, **kwargs):
        """Get account balance.

        Args:
            account_id: Account ID

        Returns:
            RequestData: Balance data
        """
        path, params, extra_data = self._get_balance(account_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Trading Methods ====================

    def _make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Place an order.

        Args:
            symbol: Trading pair symbol
            vol: Order volume
            price: Order price (required for limit orders)
            order_type: Order type (buy-limit, sell-limit, buy-market, sell-market)
            client_order_id: Client order ID
            extra_data: Extra data for processing

        Returns:
            tuple: (path, body, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "make_order"
        path = self._params.get_rest_path("make_order")

        # Parse order type
        side, order_type_str = order_type.split("-")

        # Build order body
        body = {
            "symbol": request_symbol,
            "type": order_type,
            "amount": str(vol),
        }

        # Add price for limit orders
        if "limit" in order_type and price is not None:
            body["price"] = str(price)

        # Add client order ID if provided
        if client_order_id is not None:
            body["client-order-id"] = str(client_order_id)

        # Add account ID
        if self.account_id is not None:
            body["account-id"] = str(self.account_id)

        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": HtxRequestDataSpot._make_order_normalize_function,
            },
        )
        return path, body, extra_data

    @staticmethod
    def _make_order_normalize_function(input_data, extra_data):
        """Normalize order response."""
        if input_data is None:
            return [], False
        status = input_data.get("status") == "ok"
        # HTX returns order ID as string in "data" field
        order_id = input_data.get("data")
        data = [{"order_id": order_id}] if order_id else []
        return data, status

    def make_order(
        self,
        symbol,
        vol,
        price=None,
        order_type="buy-limit",
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """Place an order.

        Args:
            symbol: Trading pair symbol
            vol: Order volume
            price: Order price (required for limit orders)
            order_type: Order type (buy-limit, sell-limit, buy-market, sell-market)
            client_order_id: Client order ID

        Returns:
            RequestData: Order response
        """
        path, body, extra_data = self._make_order(
            symbol, vol, price, order_type, client_order_id, extra_data, **kwargs
        )
        return self.request(path, params={}, body=body, extra_data=extra_data)

    def _cancel_order(self, order_id, extra_data=None, **kwargs):
        """Cancel an order.

        Args:
            order_id: Order ID to cancel
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "cancel_order"
        path = self._params.get_rest_path("cancel_order").replace("{order-id}", str(order_id))
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": None,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": None,
            },
        )
        return path, params, extra_data

    def cancel_order(self, order_id, extra_data=None, **kwargs):
        """Cancel an order.

        Args:
            order_id: Order ID to cancel

        Returns:
            RequestData: Cancellation response
        """
        path, params, extra_data = self._cancel_order(order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_order(self, order_id, extra_data=None, **kwargs):
        """Get order details.

        Args:
            order_id: Order ID
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_type = "get_order"
        path = self._params.get_rest_path("get_order").replace("{order-id}", str(order_id))
        params = {}
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": None,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": HtxRequestDataSpot._get_order_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_order_normalize_function(input_data, extra_data):
        """Normalize order response."""
        if input_data is None:
            return [], False
        status = input_data.get("status") == "ok"
        data = input_data.get("data", {})
        order_data = [HtxRequestOrderData(data, extra_data.get("symbol_name", ""), extra_data["asset_type"], True)]
        return order_data, status

    def get_order(self, order_id, extra_data=None, **kwargs):
        """Get order details.

        Args:
            order_id: Order ID

        Returns:
            RequestData: Order details
        """
        path, params, extra_data = self._get_order(order_id, extra_data, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def _get_orders(
        self,
        symbol,
        states="submitted,partial-filled",
        **kwargs
    ):
        """Get order list.

        Args:
            symbol: Trading pair symbol
            states: Order states (comma-separated)
            extra_data: Extra data for processing

        Returns:
            tuple: (path, params, extra_data)
        """
        request_symbol = self._params.get_symbol(symbol)
        request_type = "get_orders"
        path = self._params.get_rest_path(request_type)
        params = {
            "symbol": request_symbol,
            "states": states,
        }
        extra_data = kwargs.get("extra_data")
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol,
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": HtxRequestDataSpot._get_orders_normalize_function,
            },
        )
        return path, params, extra_data

    @staticmethod
    def _get_orders_normalize_function(input_data, extra_data):
        """Normalize orders response."""
        if input_data is None:
            return [], False
        status = input_data.get("status") == "ok"
        data = input_data.get("data", [])
        orders = [HtxRequestOrderData(order, extra_data["symbol_name"], extra_data["asset_type"], True) for order in data]
        return orders, status

    def get_orders(self, symbol, states="submitted,partial-filled", extra_data=None, **kwargs):
        """Get order list.

        Args:
            symbol: Trading pair symbol
            states: Order states

        Returns:
            RequestData: Order list
        """
        kwargs["extra_data"] = extra_data
        path, params, extra_data = self._get_orders(symbol, states, **kwargs)
        return self.request(path, params=params, extra_data=extra_data)

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """Query order details (standard interface alias for get_order)."""
        return self.get_order(order_id, extra_data=extra_data, **kwargs)

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """Get open orders for a symbol.

        Args:
            symbol: Trading pair symbol
            extra_data: Extra data for processing

        Returns:
            RequestData: Open orders list
        """
        request_type = "get_open_orders"
        path = self._params.get_rest_path(request_type)
        params = {}
        if symbol:
            request_symbol = self._params.get_symbol(symbol)
            params["symbol"] = request_symbol
        extra_data = update_extra_data(
            extra_data,
            **{
                "request_type": request_type,
                "symbol_name": symbol or "ALL",
                "exchange_name": self.exchange_name,
                "asset_type": self.asset_type,
                "normalize_function": HtxRequestDataSpot._get_orders_normalize_function,
            },
        )
        return self.request(path, params=params, extra_data=extra_data)

    # ==================== Async Methods ====================

    def async_get_tick(self, symbol, extra_data=None, **kwargs):
        """Async get ticker data, pushes result to data_queue."""
        path, params, extra_data = self._get_ticker(symbol, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_ticker(self, symbol, extra_data=None, **kwargs):
        """Async get ticker data (alias for async_get_tick)."""
        self.async_get_tick(symbol, extra_data=extra_data, **kwargs)

    def async_get_depth(self, symbol, depth_type="step0", extra_data=None, **kwargs):
        """Async get orderbook depth, pushes result to data_queue."""
        path, params, extra_data = self._get_depth(symbol, depth_type, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_kline(self, symbol, period="1min", count=200, extra_data=None, **kwargs):
        """Async get kline data, pushes result to data_queue."""
        path, params, extra_data = self._get_kline(symbol, period, count, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_account(self, extra_data=None, **kwargs):
        """Async get account list, pushes result to data_queue."""
        path, params, extra_data = self._get_accounts(extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )

    def async_get_balance(self, account_id=None, extra_data=None, **kwargs):
        """Async get balance, pushes result to data_queue."""
        path, params, extra_data = self._get_balance(account_id, extra_data, **kwargs)
        self.submit(
            self.async_request(path, params=params, extra_data=extra_data),
            callback=self.async_callback,
        )


class HtxMarketWssDataSpot(MyWebsocketApp):
    """HTX Market WebSocket data feed for Spot.

    Connects to wss://api.huobi.pro/ws (v1 market data).
    Messages are gzip-compressed binary.
    Heartbeat: responds to {"ping": ts} with {"pong": ts}.
    Subscription: {"sub": "market.btcusdt.ticker", "id": "..."}
    """

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("exchange_data", HtxExchangeDataSpot())
        # HTX uses application-level JSON ping/pong, disable protocol-level pings
        kwargs.setdefault("ping_interval", 0)
        kwargs.setdefault("ping_timeout", None)
        super().__init__(data_queue, **kwargs)
        self.topics = kwargs.get("topics", {})
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "HTX")
        self._params = kwargs.get("exchange_data", HtxExchangeDataSpot())
        self.logger = get_logger("htx_market_wss")

    def open_rsp(self):
        self.wss_logger.info(
            f"===== {self.exchange_name} Market Websocket Connected ====="
        )
        self._init()

    def _init(self):
        for topic_cfg in self.topics:
            topic = topic_cfg.get("topic", "")
            symbol = topic_cfg.get("symbol", "BTCUSDT")
            period = topic_cfg.get("period", "1min")
            depth_type = topic_cfg.get("type", "step0")

            if topic == "ticker":
                self.subscribe(topic="ticker", symbol=symbol)
            elif topic == "depth":
                self.subscribe(topic="depth", symbol=symbol, type=depth_type)
            elif topic == "kline":
                self.subscribe(topic="kline", symbol=symbol, period=period)
            elif topic == "trade":
                self.subscribe(topic="trade", symbol=symbol)
            elif topic == "detail":
                self.subscribe(topic="mini_ticker", symbol=symbol)

    def message_rsp(self, message):
        """Handle incoming WebSocket messages.

        HTX v1 market WSS sends gzip-compressed binary messages.
        Must respond to ping with pong to keep connection alive.
        """
        import gzip
        # Decompress gzip binary message
        try:
            if isinstance(message, bytes):
                message = gzip.decompress(message).decode("utf-8")
            data = json.loads(message)
        except Exception as e:
            self.wss_logger.warn(f"Failed to parse message: {e}")
            return

        # Handle ping/pong heartbeat
        if "ping" in data:
            pong_msg = json.dumps({"pong": data["ping"]})
            self.ws.send(pong_msg)
            return

        # Handle subscription confirmation
        if "subbed" in data:
            self.wss_logger.info(f"Subscribed: {data.get('subbed')}")
            return

        # Handle market data
        if "ch" in data:
            self.handle_data(data)

    def handle_data(self, content):
        """Route market data to appropriate push method."""
        ch = content.get("ch", "")
        if "ticker" in ch or "bbo" in ch:
            self.push_ticker(content)
        elif "depth" in ch:
            self.push_order_book(content)
        elif "kline" in ch:
            self.push_bar(content)
        elif "trade" in ch:
            self.push_trade(content)
        elif "detail" in ch:
            self.push_ticker(content)

    def push_ticker(self, content):
        ch = content.get("ch", "")
        parts = ch.split(".")
        symbol = parts[1] if len(parts) > 1 else "UNKNOWN"
        ticker_data = HtxRequestTickerData(content, symbol, self.asset_type, True)
        self.data_queue.put(ticker_data)

    def push_order_book(self, content):
        ch = content.get("ch", "")
        parts = ch.split(".")
        symbol = parts[1] if len(parts) > 1 else "UNKNOWN"
        order_book_data = HtxRequestOrderBookData(content, symbol, self.asset_type, True)
        self.data_queue.put(order_book_data)

    def push_bar(self, content):
        ch = content.get("ch", "")
        parts = ch.split(".")
        symbol = parts[1] if len(parts) > 1 else "UNKNOWN"
        bar_data = HtxRequestBarData(content, symbol, self.asset_type, True)
        self.data_queue.put(bar_data)

    def push_trade(self, content):
        ch = content.get("ch", "")
        parts = ch.split(".")
        symbol = parts[1] if len(parts) > 1 else "UNKNOWN"
        trade_data = HtxRequestTradeData(content, symbol, self.asset_type, True)
        self.data_queue.put(trade_data)


class HtxAccountWssDataSpot(MyWebsocketApp):
    """HTX Account WebSocket data feed for Spot.

    Connects to wss://api.huobi.pro/ws/v2 (v2 account data).
    Messages are plain JSON (not compressed).
    Requires authentication via signature.
    Heartbeat: responds to {"action":"ping","data":{"ts":...}} with pong.
    """

    def __init__(self, data_queue, **kwargs):
        kwargs.setdefault("exchange_data", HtxExchangeDataSpot())
        # Use acct_wss_url for account WebSocket
        params = kwargs.get("exchange_data", HtxExchangeDataSpot())
        if "wss_url" not in kwargs:
            kwargs["wss_url"] = params.acct_wss_url
        super().__init__(data_queue, **kwargs)
        self.topics = kwargs.get("topics", {})
        self.asset_type = kwargs.get("asset_type", "SPOT")
        self.exchange_name = kwargs.get("exchange_name", "HTX")
        self.public_key = kwargs.get("public_key", "")
        self.private_key = kwargs.get("private_key", "")
        self._params = params
        self.logger = get_logger("htx_account_wss")

    def _build_auth_params(self):
        """Build authentication message for HTX v2 WebSocket."""
        import base64
        import hashlib
        import hmac
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        params = {
            "accessKey": self.public_key,
            "signatureMethod": "HmacSHA256",
            "signatureVersion": "2.1",
            "timestamp": timestamp,
        }
        # Build signature payload
        host = "api.huobi.pro"
        path = "/ws/v2"
        sorted_params = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
        payload = f"GET\n{host}\n{path}\n{sorted_params}"
        signature = base64.b64encode(
            hmac.new(
                self.private_key.encode("utf-8"),
                payload.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")

        return {
            "action": "req",
            "ch": "auth",
            "params": {
                "authType": "api",
                "accessKey": self.public_key,
                "signatureMethod": "HmacSHA256",
                "signatureVersion": "2.1",
                "timestamp": timestamp,
                "signature": signature,
            },
        }

    def open_rsp(self):
        self.wss_logger.info(
            f"===== {self.exchange_name} Account Websocket Connected ====="
        )
        # Send authentication
        auth_msg = json.dumps(self._build_auth_params())
        self.ws.send(auth_msg)

    def _subscribe_topics(self):
        """Subscribe to account topics after authentication."""
        for topic_cfg in self.topics:
            topic = topic_cfg.get("topic", "")
            symbol = topic_cfg.get("symbol", "")
            symbol_lower = self._params.get_symbol(symbol) if symbol else "*"

            if topic == "orders":
                sub_msg = json.dumps({"action": "sub", "ch": f"orders#{symbol_lower}"})
                self.ws.send(sub_msg)
            elif topic == "account":
                sub_msg = json.dumps({"action": "sub", "ch": "accounts.update#2"})
                self.ws.send(sub_msg)
            elif topic == "deals":
                sub_msg = json.dumps({"action": "sub", "ch": f"trade.clearing#{symbol_lower}"})
                self.ws.send(sub_msg)

    def message_rsp(self, message):
        """Handle incoming WebSocket messages (v2 JSON, not compressed)."""
        try:
            data = json.loads(message)
        except Exception as e:
            self.wss_logger.warn(f"Failed to parse message: {e}")
            return

        action = data.get("action", "")

        # Handle ping/pong heartbeat (v2 format)
        if action == "ping":
            pong_msg = json.dumps({"action": "pong", "data": data.get("data", {})})
            self.ws.send(pong_msg)
            return

        # Handle auth response
        if data.get("ch") == "auth":
            if data.get("code") == 200:
                self.wss_logger.info("Account WSS authentication successful")
                self._subscribe_topics()
            else:
                self.wss_logger.warn(f"Account WSS auth failed: {data}")
            return

        # Handle subscription confirmation
        if action == "sub":
            self.wss_logger.info(f"Subscribed: {data.get('ch')}")
            return

        # Handle push data
        if action == "push":
            self.handle_data(data)

    def handle_data(self, content):
        """Route account data to appropriate push method."""
        ch = content.get("ch", "")
        if ch.startswith("orders"):
            self.push_order(content)
        elif ch.startswith("accounts"):
            self.push_account(content)
        elif ch.startswith("trade.clearing"):
            self.push_trade(content)

    def push_order(self, content):
        data = content.get("data", {})
        symbol = data.get("symbol", "UNKNOWN")
        from bt_api_py.containers.orders.htx_order import HtxRequestOrderData
        order_data = HtxRequestOrderData(data, symbol, self.asset_type, True)
        self.data_queue.put(order_data)

    def push_account(self, content):
        data = content.get("data", {})
        symbol = "ALL"
        from bt_api_py.containers.accounts.htx_account import HtxSpotRequestAccountData
        account_data = HtxSpotRequestAccountData(data, symbol, self.asset_type, True)
        self.data_queue.put(account_data)

    def push_trade(self, content):
        data = content.get("data", {})
        symbol = data.get("symbol", "UNKNOWN")
        trade_data = HtxRequestTradeData(data, symbol, self.asset_type, True)
        self.data_queue.put(trade_data)
