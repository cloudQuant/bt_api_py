"""
CTP Feed 实现
基于 ctp-python 包的 MdClient / TraderClient 高层封装进行对接
行情和交易是两个独立的 API（MdApi / TraderApi）

依赖: pip install ctp-python  (https://github.com/xxx/ctp-python)

注意事项:
  - CTP 下单必须指定开平方向 (open/close/close_today/close_yesterday)
  - CTP 有交易时段限制 (日盘/夜盘)
  - 每天首次登录需要确认前一日结算单
  - 品种名称格式: IF2506, rb2510 等
  - CTP 查询接口有流控限制: 每秒最多1次查询
"""

import time

from bt_api_py.containers.ctp.ctp_account import CtpAccountData
from bt_api_py.containers.ctp.ctp_order import CtpOrderData
from bt_api_py.containers.ctp.ctp_position import CtpPositionData
from bt_api_py.containers.ctp.ctp_ticker import CtpTickerData
from bt_api_py.containers.ctp.ctp_trade import CtpTradeData
from bt_api_py.containers.exchanges.ctp_exchange_data import CtpExchangeDataFuture
from bt_api_py.containers.requestdatas.request_data import RequestData
from bt_api_py.error import CTPErrorTranslator
from bt_api_py.exceptions import ExchangeConnectionAlias as BtConnectionError
from bt_api_py.feeds.base_stream import BaseDataStream, ConnectionState
from bt_api_py.feeds.capability import Capability
from bt_api_py.feeds.feed import Feed
from bt_api_py.logging_factory import get_logger

# CTP 开平方向映射
CTP_OFFSET_FLAG = {
    "open": "0",
    "close": "1",
    "force_close": "2",
    "close_today": "3",
    "close_yesterday": "4",
    "force_close_yesterday": "5",
    "local_force_close": "6",
}

# CTP 买卖方向映射
CTP_DIRECTION_FLAG = {
    "buy": "0",
    "sell": "1",
}


def _ctp_field_to_dict(field):
    """将 CTP SPI 回调的 field 对象转为 dict，方便 Container 解析
    CTP SWIG 生成的对象支持属性访问，遍历常用字段提取值
    """
    if field is None:
        return {}
    result = {}
    # 遍历对象的所有属性，排除内部属性和方法
    for attr in dir(field):
        if attr.startswith("_") or attr == "this" or attr == "thisown":
            continue
        try:
            val = getattr(field, attr)
            if not callable(val):
                result[attr] = val
        except Exception:
            pass
    return result


class CtpRequestData(Feed):
    """CTP 同步请求封装，基于 ctp.client.TraderClient"""

    @classmethod
    def _capabilities(cls):
        return {
            Capability.GET_TICK,
            Capability.MAKE_ORDER,
            Capability.CANCEL_ORDER,
            Capability.QUERY_ORDER,
            Capability.QUERY_OPEN_ORDERS,
            Capability.GET_DEALS,
            Capability.GET_BALANCE,
            Capability.GET_ACCOUNT,
            Capability.GET_POSITION,
            Capability.MARKET_STREAM,
            Capability.ACCOUNT_STREAM,
        }

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue)
        self.data_queue = data_queue
        self.broker_id = kwargs.get("broker_id", "")
        self.user_id = kwargs.get("user_id", "")
        self.password = kwargs.get("password", "")
        self.auth_code = kwargs.get("auth_code", "0000000000000000")
        self.app_id = kwargs.get("app_id", "simnow_client_test")
        self.td_front = kwargs.get("td_front", "")
        self.md_front = kwargs.get("md_front", "")
        self.asset_type = kwargs.get("asset_type", "FUTURE")
        self.exchange_name = "CTP"
        self._params = CtpExchangeDataFuture()
        self.logger_name = kwargs.get("logger_name", "ctp_feed.log")
        self.request_logger = get_logger("ctp_feed")
        self._error_translator = CTPErrorTranslator()
        # TraderClient 实例 — 负责交易查询和下单
        self._trader = None
        self._connected = False
        self._connect_timeout = kwargs.get("connect_timeout", 15)

    def translate_error(self, raw_response):
        """将原始 CTP 错误信息翻译为 UnifiedError（如有错误），否则返回 None"""
        if isinstance(raw_response, dict):
            error_id = raw_response.get("ErrorID", 0)
            if error_id != 0:
                return self._error_translator.translate(raw_response, self.exchange_name)
        return None

    def _ensure_connected(self):
        """确保 TraderClient 已连接并就绪"""
        if self._trader is None or not self._trader.is_ready:
            self.connect()
        if not self._trader.is_ready:
            raise BtConnectionError("CTP", "TraderClient not ready after connect()")

    def connect(self):
        """连接 CTP 交易前置并完成认证流程
        流程: 连接前置 -> 认证 -> 登录 -> 确认结算单 -> 就绪
        """
        if self._trader is not None and self._trader.is_ready:
            return
        from bt_api_py.ctp.client import TraderClient

        self._trader = TraderClient(
            self.td_front,
            self.broker_id,
            self.user_id,
            self.password,
            app_id=self.app_id,
            auth_code=self.auth_code,
        )
        self._trader.start(block=False)
        ready = self._trader.wait_ready(timeout=self._connect_timeout)
        if ready:
            self._connected = True
            self.request_logger.info("CTP TraderClient connected and ready")
        else:
            self.request_logger.error(
                f"CTP TraderClient failed to connect within {self._connect_timeout}s"
            )

    def disconnect(self):
        """断开交易连接"""
        if self._trader is not None:
            self._trader.stop()
            self._trader = None
        self._connected = False

    def _make_request_data(
        self, data_list, request_type, symbol_name=None, extra_data=None, status=True
    ):
        """构造 RequestData 返回对象，使用正常构造函数"""
        ed = extra_data.copy() if extra_data else {}
        ed.setdefault("exchange_name", self.exchange_name)
        ed.setdefault("symbol_name", symbol_name or "")
        ed.setdefault("asset_type", self.asset_type)
        ed.setdefault("request_type", request_type)
        rd = RequestData(data_list, ed, status=status)
        rd.data = data_list
        rd.has_been_init_data = True
        return rd

    def get_account(self, symbol=None, extra_data=None, **kwargs):
        """查询账户资金，返回 RequestData 包含 [CtpAccountData]"""
        self._ensure_connected()
        raw = self._trader.query_account(timeout=5)
        if raw is not None:
            account_dict = _ctp_field_to_dict(raw)
            data_list = [CtpAccountData(account_dict, symbol, self.asset_type, True)]
            return self._make_request_data(data_list, "get_account", symbol, extra_data)
        return self._make_request_data([], "get_account", symbol, extra_data, status=False)

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """查询账户余额（同 get_account）"""
        return self.get_account(symbol, extra_data, **kwargs)

    def get_position(self, symbol=None, extra_data=None, **kwargs):
        """查询持仓，返回 RequestData 包含 [CtpPositionData, ...]"""
        self._ensure_connected()
        raw_list = self._trader.query_positions(timeout=5)
        data_list = []
        for raw in raw_list:
            pos_dict = _ctp_field_to_dict(raw)
            instrument = pos_dict.get("InstrumentID", symbol)
            data_list.append(CtpPositionData(pos_dict, instrument, self.asset_type, True))
        return self._make_request_data(data_list, "get_position", symbol, extra_data)

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """查询最新行情快照
        注意: CTP 行情快照查询能力有限，推荐使用 CtpMarketStream 订阅实时 tick
        """
        self.request_logger.warn("CTP get_tick is limited; use CtpMarketStream for real-time ticks")
        return self._make_request_data([], "get_tick", symbol, extra_data, status=False)

    def get_depth(self, symbol, count=5, extra_data=None, **kwargs):
        """查询深度行情 (CTP tick 数据自带5档买卖盘，通过 CtpMarketStream 获取)"""
        self.request_logger.warn("CTP depth is included in tick data; use CtpMarketStream")
        return self._make_request_data([], "get_depth", symbol, extra_data, status=False)

    def get_kline(
        self, symbol, period, count=100, start_time=None, end_time=None, extra_data=None, **kwargs
    ):
        """获取K线 (CTP 不直接提供，需从 tick 数据合成或从第三方获取)"""
        self.request_logger.warn("CTP does not provide kline API directly")
        return self._make_request_data([], "get_kline", symbol, extra_data, status=False)

    def make_order(
        self,
        symbol,
        volume,
        price=None,
        order_type="buy-limit",
        offset="open",
        post_only=False,
        client_order_id=None,
        extra_data=None,
        **kwargs,
    ):
        """下单
        :param symbol: 合约代码, 如 'IF2506'
        :param volume: 数量（手数）
        :param price: 委托价格，市价单时可为 None
        :param order_type: 'buy-limit' / 'sell-limit' / 'buy-market' / 'sell-market'
        :param offset: 'open' / 'close' / 'close_today' / 'close_yesterday' (CTP 必填)
        :param client_order_id: 客户端自定义订单引用
        :param kwargs: exchange_id='CFFEX' 等额外参数
        """
        self._ensure_connected()
        from bt_api_py.ctp.ctp_structs_order import CThostFtdcInputOrderField

        side, otype = order_type.split("-")
        direction = CTP_DIRECTION_FLAG.get(side.lower(), "0")
        offset_flag = CTP_OFFSET_FLAG.get(offset, "0")
        exchange_id = kwargs.get("exchange_id", "")

        field = CThostFtdcInputOrderField()
        field.BrokerID = self.broker_id
        field.InvestorID = self.user_id
        field.InstrumentID = symbol
        if exchange_id:
            field.ExchangeID = exchange_id
        field.Direction = direction
        field.CombOffsetFlag = offset_flag
        field.CombHedgeFlag = "1"  # 投机
        field.VolumeTotalOriginal = int(volume)
        field.ForceCloseReason = "0"
        field.IsAutoSuspend = 0
        field.UserForceClose = 0

        if otype.lower() == "market":
            field.OrderPriceType = "1"  # 市价
            field.TimeCondition = "1"  # IOC
            field.VolumeCondition = "1"  # 任意数量
            field.LimitPrice = 0.0
        else:
            field.OrderPriceType = "2"  # 限价
            field.TimeCondition = "3"  # GFD (当日有效)
            field.VolumeCondition = "1"  # 任意数量
            field.LimitPrice = float(price) if price else 0.0
            field.ContingentCondition = "1"

        if client_order_id:
            field.OrderRef = str(client_order_id)

        self._trader._req_id += 1
        ret = self._trader.api.ReqOrderInsert(field, self._trader._req_id)
        order_dict = _ctp_field_to_dict(field)
        order_dict["_ret"] = ret

        if ret == 0:
            self.request_logger.info(
                f"CTP order sent: {symbol} {order_type} {offset} " f"price={price} vol={volume}"
            )
            data_list = [CtpOrderData(order_dict, symbol, self.asset_type, True)]
            return self._make_request_data(data_list, "make_order", symbol, extra_data)
        else:
            self.request_logger.error(f"CTP order send failed: ret={ret}")
            return self._make_request_data([], "make_order", symbol, extra_data, status=False)

    def cancel_order(self, symbol, order_id=None, extra_data=None, **kwargs):
        """撤单
        :param symbol: 合约代码
        :param order_id: OrderSysID 或 OrderRef
        :param kwargs: exchange_id, front_id, session_id, order_ref 等
        """
        self._ensure_connected()
        from bt_api_py.ctp.ctp_structs_order import CThostFtdcInputOrderActionField

        field = CThostFtdcInputOrderActionField()
        field.BrokerID = self.broker_id
        field.InvestorID = self.user_id
        field.InstrumentID = symbol
        field.ActionFlag = "0"  # 删除

        exchange_id = kwargs.get("exchange_id", "")
        if exchange_id:
            field.ExchangeID = exchange_id

        order_ref = kwargs.get("order_ref", "")
        front_id = kwargs.get("front_id", 0)
        session_id = kwargs.get("session_id", 0)

        if order_id:
            field.OrderSysID = str(order_id)
        if order_ref:
            field.OrderRef = str(order_ref)
            field.FrontID = int(front_id) if front_id else self._trader._front_id
            field.SessionID = int(session_id) if session_id else self._trader._session_id

        self._trader._req_id += 1
        ret = self._trader.api.ReqOrderAction(field, self._trader._req_id)
        if ret == 0:
            self.request_logger.info(f"CTP cancel order sent: {symbol} order_id={order_id}")
        else:
            self.request_logger.error(f"CTP cancel order failed: ret={ret}")
        return self._make_request_data(
            [_ctp_field_to_dict(field)], "cancel_order", symbol, extra_data, status=(ret == 0)
        )

    def query_order(self, symbol=None, order_id=None, extra_data=None, **kwargs):
        """查询订单 (CTP 暂不直接支持单笔查询，返回空)"""
        self.request_logger.warn("CTP single order query not implemented; use on_order callback")
        return self._make_request_data([], "query_order", symbol, extra_data, status=False)

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """查询未成交委托 (CTP 暂不直接支持，返回空)"""
        self.request_logger.warn("CTP open orders query not implemented; use on_order callback")
        return self._make_request_data([], "get_open_orders", symbol, extra_data, status=False)

    def get_deals(
        self, symbol=None, count=100, start_time=None, end_time=None, extra_data=None, **kwargs
    ):
        """查询成交记录 (CTP 暂不直接支持，返回空)"""
        self.request_logger.warn("CTP deals query not implemented; use on_trade callback")
        return self._make_request_data([], "get_deals", symbol, extra_data, status=False)

    def get_server_time(self):
        """获取服务器时间（通过 TraderClient 登录信息）"""
        if self._trader and self._trader.is_ready:
            return {"server_time": time.time()}
        return None

    @property
    def trader_client(self):
        """获取底层 TraderClient，用于高级操作"""
        return self._trader


class CtpMarketStream(BaseDataStream):
    """CTP 行情流 — 基于 ctp.client.MdClient

    接收实时 tick 数据并推送到 data_queue
    """

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.md_front = kwargs.get("md_front", "")
        self.broker_id = kwargs.get("broker_id", "")
        self.user_id = kwargs.get("user_id", "")
        self.password = kwargs.get("password", "")
        self.topics = kwargs.get("topics", [])
        self.asset_type = kwargs.get("asset_type", "FUTURE")
        self._md_client = None

    def connect(self):
        """连接 CTP 行情前置"""
        from bt_api_py.ctp.client import MdClient

        self.state = ConnectionState.CONNECTING
        self._md_client = MdClient(self.md_front, self.broker_id, self.user_id, self.password)
        # 设置 tick 回调 — 将 CTP field 转为 CtpTickerData 推送到队列
        self._md_client.on_tick = self._on_tick
        self._md_client.on_login = self._on_login
        self._md_client.on_error = self._on_error
        # 收集需要订阅的合约
        instruments = []
        for topic in self.topics:
            if topic.get("topic") in ("tick", "ticker", "depth"):
                sym = topic.get("symbol", "")
                if sym:
                    instruments.append(sym)
                sym_list = topic.get("symbol_list", [])
                instruments.extend(sym_list)
        if instruments:
            self._md_client.subscribe(instruments)
        self._md_client.start(block=False)
        self.logger.info(
            f"CTP MdClient connecting to {self.md_front}, " f"subscribing {instruments}"
        )

    def _on_login(self, login_field):
        self.state = ConnectionState.AUTHENTICATED
        self.logger.info(f"CTP MdClient logged in, TradingDay={login_field.TradingDay}")

    def _on_tick(self, tick_field):
        """CTP tick 回调 — 转为 CtpTickerData 推送到队列"""
        tick_dict = _ctp_field_to_dict(tick_field)
        symbol = tick_dict.get("InstrumentID", "")
        ticker_data = CtpTickerData(tick_dict, symbol, self.asset_type, True)
        self.push_data(ticker_data)

    def _on_error(self, rsp_info):
        if rsp_info:
            self.logger.error(f"CTP MdClient error: [{rsp_info.ErrorID}] {rsp_info.ErrorMsg}")
        self.state = ConnectionState.ERROR

    def disconnect(self):
        """断开行情连接"""
        if self._md_client is not None:
            self._md_client.stop()
            self._md_client = None
        self.state = ConnectionState.DISCONNECTED

    def subscribe_topics(self, topics):
        """追加订阅行情"""
        instruments = []
        for topic in topics:
            if topic.get("topic") in ("tick", "ticker", "depth"):
                sym = topic.get("symbol", "")
                if sym:
                    instruments.append(sym)
        if instruments and self._md_client:
            self._md_client.subscribe(instruments)
            self.logger.info(f"CTP MdClient subscribed: {instruments}")

    def _run_loop(self):
        """启动 MdClient 并保持存活"""
        self.connect()
        while self._running:
            time.sleep(1)


class CtpTradeStream(BaseDataStream):
    """CTP 交易流 — 基于 ctp.client.TraderClient

    接收订单回报、成交回报推送到 data_queue
    """

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.td_front = kwargs.get("td_front", "")
        self.broker_id = kwargs.get("broker_id", "")
        self.user_id = kwargs.get("user_id", "")
        self.password = kwargs.get("password", "")
        self.auth_code = kwargs.get("auth_code", "0000000000000000")
        self.app_id = kwargs.get("app_id", "simnow_client_test")
        self.asset_type = kwargs.get("asset_type", "FUTURE")
        self._trader = None

    def connect(self):
        """连接 CTP 交易前置"""
        from bt_api_py.ctp.client import TraderClient

        self.state = ConnectionState.CONNECTING
        self._trader = TraderClient(
            self.td_front,
            self.broker_id,
            self.user_id,
            self.password,
            app_id=self.app_id,
            auth_code=self.auth_code,
        )
        # 设置回调 — 将 CTP field 转为 Container 对象推送到队列
        self._trader.on_order = self._on_order
        self._trader.on_trade = self._on_trade
        self._trader.on_login = self._on_login
        self._trader.on_error = self._on_error
        self._trader.start(block=False)
        self.logger.info(f"CTP TraderClient connecting to {self.td_front}")

    def _on_login(self, login_field):
        self.state = ConnectionState.AUTHENTICATED
        self.logger.info(f"CTP TraderClient logged in, TradingDay={login_field.TradingDay}")

    def _on_order(self, order_field):
        """CTP 订单回报 — 转为 CtpOrderData 推送到队列"""
        order_dict = _ctp_field_to_dict(order_field)
        symbol = order_dict.get("InstrumentID", "")
        order_data = CtpOrderData(order_dict, symbol, self.asset_type, True)
        self.push_data(order_data)

    def _on_trade(self, trade_field):
        """CTP 成交回报 — 转为 CtpTradeData 推送到队列"""
        trade_dict = _ctp_field_to_dict(trade_field)
        symbol = trade_dict.get("InstrumentID", "")
        trade_data = CtpTradeData(trade_dict, symbol, self.asset_type, True)
        self.push_data(trade_data)

    def _on_error(self, rsp_info):
        if rsp_info:
            self.logger.error(f"CTP TraderClient error: [{rsp_info.ErrorID}] {rsp_info.ErrorMsg}")
        self.state = ConnectionState.ERROR

    def disconnect(self):
        if self._trader is not None:
            self._trader.stop()
            self._trader = None
        self.state = ConnectionState.DISCONNECTED

    def subscribe_topics(self, topics):
        """CTP 交易推送是自动的，不需要显式订阅"""
        pass

    def _run_loop(self):
        """启动 TraderClient 并保持存活"""
        self.connect()
        while self._running:
            time.sleep(1)

    @property
    def trader_client(self):
        """获取底层 TraderClient，用于高级操作"""
        return self._trader


class CtpRequestDataFuture(CtpRequestData):
    """CTP 期货 Feed"""

    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.asset_type = kwargs.get("asset_type", "FUTURE")
        self._params = CtpExchangeDataFuture()
