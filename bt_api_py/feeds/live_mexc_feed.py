"""
MEXC Live Feed Implementation

Provides WebSocket feed implementations for MEXC exchange.
"""

from bt_api_py.containers.exchanges.mexc_exchange_data import MexcExchangeDataSpot
from bt_api_py.containers.orders.mexc_order import MexcWssOrderData
from bt_api_py.containers.trades.mexc_trade import MexcWssTradeData
from bt_api_py.feeds.live_mexc.market_wss_base import MexcMarketWssData
from bt_api_py.feeds.live_mexc.account_wss_base import MexcAccountWssData


class MexcMarketWssData(MexcMarketWssData):
    """MEXC 市场数据 WebSocket 实现"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_data = kwargs.get("exchange_data", MexcExchangeDataSpot())
        self.topics = kwargs.get("topics", [])

        # 设置 WebSocket URL
        self.wss_url = kwargs.get("wss_url", "wss://wbs.mexc.com/ws")

        # 初始化订阅主题
        self.subscribed_topics = set()

        # 创建日志记录器
        self.logger_name = kwargs.get("logger_name", "mexc_market_wss.log")
        from bt_api_py.functions.log_message import SpdLogManager
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()


class MexcAccountWssData(MexcAccountWssData):
    """MEXC 账户数据 WebSocket 实现"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.exchange_data = kwargs.get("exchange_data", MexcExchangeDataSpot())
        self.topics = kwargs.get("topics", [])

        # 设置 WebSocket URL
        self.wss_url = kwargs.get("wss_url", "wss://wbs.mexc.com/ws")

        # 初始化订阅主题
        self.subscribed_topics = set()

        # 创建日志记录器
        self.logger_name = kwargs.get("logger_name", "mexc_account_wss.log")
        from bt_api_py.functions.log_message import SpdLogManager
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "request", 0, 0, False
        ).create_logger()


class MexcMarketWssDataSpot(MexcMarketWssData):
    """MEXC Spot 市场数据 WebSocket 实现"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = "SPOT"

        # Spot 特定的处理逻辑
        self.topic_handlers = {
            "ticker": self._handle_ticker,
            "trade": self._handle_trade,
            "orderbook": self._handle_orderbook,
            "kline": self._handle_kline,
        }

    def _handle_ticker(self, data):
        """处理 ticker 数据"""
        try:
            # MEXC ticker 格式示例
            # {"method": "push", "code": "0", "msg": "", "data": {"symbol": "BTCUSDT", "last": "40000.0", ...}}
            if "data" in data and "symbol" in data["data"]:
                symbol = data["data"]["symbol"]
                ticker_data = MexcWssTradeData(data, symbol, self.asset_type)
                self.data_queue.put(ticker_data)
        except Exception as e:
            self.logger.error(f"Error handling ticker data: {e}")

    def _handle_trade(self, data):
        """处理成交数据"""
        try:
            if "data" in data and isinstance(data["data"], list):
                for trade in data["data"]:
                    symbol = trade.get("symbol")
                    if symbol:
                        trade_data = MexcWssTradeData(trade, symbol, self.asset_type)
                        self.data_queue.put(trade_data)
        except Exception as e:
            self.logger.error(f"Error handling trade data: {e}")

    def _handle_orderbook(self, data):
        """处理订单簿数据"""
        try:
            if "data" in data and "symbol" in data["data"]:
                symbol = data["data"]["symbol"]
                orderbook_data = {
                    "symbol": symbol,
                    "bids": data["data"].get("bids", []),
                    "asks": data["data"].get("asks", []),
                    "time": data["data"].get("time")
                }
                from bt_api_py.containers.orderbooks.mexc_orderbook import MexcWssOrderBookData
                orderbook = MexcWssOrderBookData(orderbook_data, symbol, self.asset_type)
                self.data_queue.put(orderbook)
        except Exception as e:
            self.logger.error(f"Error handling orderbook data: {e}")

    def _handle_kline(self, data):
        """处理 K 线数据"""
        try:
            if "data" in data and "symbol" in data["data"]:
                symbol = data["data"]["symbol"]
                kline_data = data["data"]
                # 处理 K 线数据，这里可以根据需要扩展
                self.data_queue.put({
                    "type": "kline",
                    "symbol": symbol,
                    "data": kline_data
                })
        except Exception as e:
            self.logger.error(f"Error handling kline data: {e}")


class MexcAccountWssDataSpot(MexcAccountWssData):
    """MEXC Spot 账户数据 WebSocket 实现"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = "SPOT"

        # Spot 特定的处理逻辑
        self.topic_handlers = {
            "account": self._handle_account,
            "order": self._handle_order,
            "trade": self._handle_trade,
        }

    def _handle_account(self, data):
        """处理账户数据"""
        try:
            if "data" in data:
                account_data = data["data"]
                # 处理账户数据，这里可以根据需要扩展
                self.data_queue.put({
                    "type": "account",
                    "data": account_data
                })
        except Exception as e:
            self.logger.error(f"Error handling account data: {e}")

    def _handle_order(self, data):
        """处理订单数据"""
        try:
            if "data" in data and "symbol" in data["data"]:
                symbol = data["data"]["symbol"]
                order_data = MexcWssOrderData(data, symbol, self.asset_type)
                self.data_queue.put(order_data)
        except Exception as e:
            self.logger.error(f"Error handling order data: {e}")

    def _handle_trade(self, data):
        """处理成交数据"""
        try:
            if "data" in data and "symbol" in data["data"]:
                symbol = data["data"]["symbol"]
                trade_data = MexcWssTradeData(data, symbol, self.asset_type)
                self.data_queue.put(trade_data)
        except Exception as e:
            self.logger.error(f"Error handling trade data: {e}")