"""
Interactive Brokers Feed 骨架实现
IB 使用 TWS API 的 EClient/EWrapper 事件驱动模型
推荐使用 ib_insync 库进行封装

依赖: pip install ib_insync  (或 ibapi)

注意事项:
  - IB 合约描述复杂: Contract 需要 symbol + secType + exchange + currency 等多字段
  - 市场数据有权限限制，需要订阅对应市场数据包
  - 订单类型丰富: Bracket, OCA, Trailing Stop 等
  - 支持全球多市场: 美股、港股、期货、期权、外汇等
"""
import time
from bt_api_py.feeds.feed import Feed
from bt_api_py.feeds.base_stream import BaseDataStream, ConnectionState
from bt_api_py.functions.log_message import SpdLogManager
from bt_api_py.containers.exchanges.ib_exchange_data import (
    IbExchangeDataStock,
    IbExchangeDataFuture,
)


class IbRequestData(Feed):
    """IB REST-like 请求封装（实际通过 TWS API 实现）"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue)
        self.data_queue = data_queue
        self.host = kwargs.get("host", "127.0.0.1")
        self.port = kwargs.get("port", 7497)
        self.client_id = kwargs.get("client_id", 1)
        self.asset_type = kwargs.get("asset_type", "STK")
        self.exchange_name = "IB"
        self._params = IbExchangeDataStock()
        self.logger_name = kwargs.get("logger_name", "ib_feed.log")
        self.request_logger = SpdLogManager(
            "./logs/" + self.logger_name, "ib_request", 0, 0, False
        ).create_logger()
        # TODO: 初始化 IB 连接
        self._ib = None
        self._connected = False

    def _ensure_connected(self):
        """确保 IB TWS 已连接"""
        if not self._connected:
            raise ConnectionError("IB TWS not connected. Call connect() first.")

    def connect(self):
        """连接 IB TWS/Gateway
        使用 ib_insync:
            from ib_insync import IB
            self._ib = IB()
            self._ib.connect(self.host, self.port, clientId=self.client_id)
        """
        raise NotImplementedError("IB connect() not yet implemented. Install ib_insync first.")

    def disconnect(self):
        """断开 IB 连接"""
        if self._ib is not None:
            # self._ib.disconnect()
            pass
        self._connected = False

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """查询账户信息"""
        # TODO: self._ib.accountSummary() 或 self._ib.accountValues()
        raise NotImplementedError

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """查询账户余额"""
        return self.get_account(symbol, extra_data, **kwargs)

    def get_position(self, symbol=None, extra_data=None, **kwargs):
        """查询持仓"""
        # TODO: self._ib.positions()
        raise NotImplementedError

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """查询最新行情"""
        # TODO: self._ib.reqMktData(contract)
        raise NotImplementedError

    def get_depth(self, symbol, count=5, extra_data=None, **kwargs):
        """查询深度行情"""
        # TODO: self._ib.reqMktDepth(contract, numRows=count)
        raise NotImplementedError

    def get_kline(self, symbol, period, count=100, start_time=None, end_time=None,
                  extra_data=None, **kwargs):
        """获取历史K线"""
        # TODO: self._ib.reqHistoricalData(contract, endDateTime, durationStr, barSizeSetting, ...)
        raise NotImplementedError

    def make_order(self, symbol, volume, price, order_type='buy-limit',
                   offset='open', post_only=False, client_order_id=None,
                   extra_data=None, **kwargs):
        """下单
        IB 特有参数:
          - sec_type: 'STK'/'FUT'/'OPT'/'CASH' 等
          - exchange: 'SMART'/'GLOBEX'/'SEHK' 等
          - currency: 'USD'/'HKD'/'CNH' 等
          - order_ref: 客户自定义引用
        """
        # TODO: 构造 Contract + Order 并调用 self._ib.placeOrder(contract, order)
        raise NotImplementedError

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """撤单"""
        # TODO: self._ib.cancelOrder(order)
        raise NotImplementedError

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """查询订单"""
        # TODO: self._ib.openOrders() 或 self._ib.trades()
        raise NotImplementedError

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """查询未成交委托"""
        # TODO: self._ib.openOrders()
        raise NotImplementedError

    def get_deals(self, symbol=None, count=100, start_time=None, end_time=None,
                  extra_data=None, **kwargs):
        """查询成交记录"""
        # TODO: self._ib.executions()
        raise NotImplementedError


class IbDataStream(BaseDataStream):
    """IB 市场数据流"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.host = kwargs.get("host", "127.0.0.1")
        self.port = kwargs.get("port", 7497)
        self.client_id = kwargs.get("client_id", 1)
        self._ib = None

    def connect(self):
        """连接 IB TWS 并订阅市场数据"""
        self.state = ConnectionState.CONNECTING
        # TODO: 实现连接
        raise NotImplementedError("IB DataStream connect() not yet implemented")

    def disconnect(self):
        if self._ib is not None:
            pass
        self.state = ConnectionState.DISCONNECTED

    def subscribe_topics(self, topics):
        """订阅市场数据
        :param topics: [{"topic": "tick", "symbol": "AAPL", "sec_type": "STK",
                         "exchange": "SMART", "currency": "USD"}, ...]
        """
        # TODO: 为每个 topic 构造 Contract 并调用 reqMktData/reqRealTimeBars
        raise NotImplementedError

    def _run_loop(self):
        """IB 事件循环"""
        self.connect()
        while self._running:
            # ib_insync: self._ib.sleep(1) 或 self._ib.run()
            time.sleep(1)


class IbAccountStream(BaseDataStream):
    """IB 账户数据流 — 接收账户/持仓/订单更新"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.host = kwargs.get("host", "127.0.0.1")
        self.port = kwargs.get("port", 7497)
        self.client_id = kwargs.get("client_id", 2)  # 使用不同的 clientId
        self._ib = None

    def connect(self):
        self.state = ConnectionState.CONNECTING
        raise NotImplementedError("IB AccountStream connect() not yet implemented")

    def disconnect(self):
        if self._ib is not None:
            pass
        self.state = ConnectionState.DISCONNECTED

    def subscribe_topics(self, topics):
        """IB 账户更新是自动推送的，连接后即可接收"""
        # TODO: self._ib.reqAccountUpdates(True, account)
        pass

    def _run_loop(self):
        self.connect()
        while self._running:
            time.sleep(1)


class IbRequestDataStock(IbRequestData):
    """IB 股票 Feed"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "STK")
        self._params = IbExchangeDataStock()


class IbRequestDataFuture(IbRequestData):
    """IB 期货 Feed"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUT")
        self._params = IbExchangeDataFuture()
