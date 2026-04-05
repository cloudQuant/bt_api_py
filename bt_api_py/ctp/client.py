"""
高层封装 / High-level CTP Client Wrappers

提供简洁的 API，减少样板代码。3 行即可收行情或完成交易登录。

用法 / Usage:

    # 行情客户端
    from bt_api_py.ctp.client import MdClient

    def on_tick(data):
        print(data.InstrumentID, data.LastPrice)

    client = MdClient("tcp://182.254.243.31:30011", "9999", "user", "pass")
    client.on_tick = on_tick
    client.subscribe(["IF2603", "IC2603"])
    client.start()  # 阻塞

    # 交易客户端
    from bt_api_py.ctp.client import TraderClient

    client = TraderClient("tcp://182.254.243.31:30001", "9999", "user", "pass",
                          app_id="simnow_client_test", auth_code="0000000000000000")
    client.start()
    client.wait_ready(timeout=15)
    print(client.query_account())
"""

from __future__ import annotations

import hashlib
import os
import queue
import tempfile
import threading
import time
from contextlib import suppress

from ._ctp_base import get_ctp_import_error, is_ctp_native_loaded

try:
    from ctp import (
        CThostFtdcMdApi,
        CThostFtdcMdSpi,
        CThostFtdcQryInvestorPositionField,
        CThostFtdcQryTradingAccountField,
        CThostFtdcReqAuthenticateField,
        CThostFtdcReqUserLoginField,
        CThostFtdcSettlementInfoConfirmField,
        CThostFtdcTraderApi,
        CThostFtdcTraderSpi,
    )

    _CTP_RUNTIME_SOURCE = "external_ctp_python"
except ImportError:
    from .ctp_md_api import CThostFtdcMdApi, CThostFtdcMdSpi
    from .ctp_structs_common import (
        CThostFtdcReqAuthenticateField,
        CThostFtdcReqUserLoginField,
        CThostFtdcSettlementInfoConfirmField,
    )
    from .ctp_structs_query import (
        CThostFtdcQryInvestorPositionField,
        CThostFtdcQryTradingAccountField,
    )
    from .ctp_trader_api import CThostFtdcTraderApi, CThostFtdcTraderSpi

    _CTP_RUNTIME_SOURCE = "vendored_bt_api_py"


def _check_native_module():
    """Raise ImportError early if the CTP C++ extension is not available."""
    if _CTP_RUNTIME_SOURCE == "external_ctp_python":
        return
    if not is_ctp_native_loaded():
        err = get_ctp_import_error()
        raise ImportError(
            f"CTP C++ extension (_ctp) not available: {err}. "
            "Connections will silently fail. "
            "If using Git LFS, run: git lfs install && git lfs pull"
        )


def get_ctp_runtime_source() -> str:
    return _CTP_RUNTIME_SOURCE


def _flow_dir(prefix):
    """Create a temp directory for CTP flow files."""
    h = hashlib.md5(prefix.encode("utf-8"), usedforsecurity=False).hexdigest()
    path = os.path.join(tempfile.gettempdir(), "ctp_client", h) + os.sep
    os.makedirs(path, exist_ok=True)
    return path


def _snapshot_ctp_field(field):
    """Create a plain dict snapshot from a SWIG field.

    Order / trade callbacks arrive on CTP's background thread. Converting the
    field to a plain dict inside the callback avoids leaking thread-bound SWIG
    objects to other threads or test assertions.
    """
    if field is None:
        return {}

    result = {}
    for attr in dir(field):
        if attr.startswith("_") or attr in {"this", "thisown"}:
            continue
        try:
            value = getattr(field, attr)
        except Exception:
            continue
        if not callable(value):
            result[attr] = value
    return result


# ===========================================================================
#  MdClient - 行情客户端
# ===========================================================================


class _MdSpi(CThostFtdcMdSpi):
    def __init__(self, client):
        super().__init__()
        self._c = client

    def OnFrontConnected(self):
        self._c._connected = True
        field = CThostFtdcReqUserLoginField()
        field.BrokerID = self._c.broker_id
        field.UserID = self._c.user_id
        field.Password = self._c.password
        self._c._api.ReqUserLogin(field, 1)

    def OnFrontDisconnected(self, nReason):
        self._c._connected = False
        self._c._loggedin = False

    def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID == 0:
            self._c._loggedin = True
            if self._c._pending_instruments:
                self._c._api.SubscribeMarketData(self._c._pending_instruments)
            if self._c.on_login:
                self._c.on_login(pRspUserLogin)
        else:
            if self._c.on_error:
                self._c.on_error(pRspInfo)

    def OnRtnDepthMarketData(self, pDepthMarketData):
        if self._c.on_tick:
            self._c.on_tick(pDepthMarketData)

    def OnRspSubMarketData(self, pSpecificInstrument, pRspInfo, nRequestID, bIsLast):
        pass

    def OnRspError(self, pRspInfo, nRequestID, bIsLast):
        if self._c.on_error:
            self._c.on_error(pRspInfo)


class MdClient:
    """行情客户端封装

    Args:
        front: 前置地址，如 "tcp://182.254.243.31:30011"
        broker_id: 经纪商代码
        user_id: 投资者代码
        password: 密码
    """

    def __init__(self, front, broker_id, user_id, password):
        self.front = front
        self.broker_id = broker_id
        self.user_id = user_id
        self.password = password

        self.on_tick = None  # callback(CThostFtdcDepthMarketDataField)
        self.on_login = None  # callback(CThostFtdcRspUserLoginField)
        self.on_error = None  # callback(CThostFtdcRspInfoField)

        self._connected = False
        self._loggedin = False
        self._pending_instruments = []
        self._api = None
        self._spi = None
        self._thread = None

    def subscribe(self, instruments):
        """订阅合约列表（可在 start 前或后调用）"""
        self._pending_instruments = list(instruments)
        if self._loggedin and self._api:
            self._api.SubscribeMarketData(self._pending_instruments)

    def start(self, block=True):
        """启动连接

        Args:
            block: True=阻塞直到断开, False=后台线程运行
        """
        _check_native_module()
        flow = _flow_dir(f"md_{self.broker_id}_{self.user_id}")
        self._api = CThostFtdcMdApi.CreateFtdcMdApi(flow)
        self._spi = _MdSpi(self)
        self._api.RegisterSpi(self._spi)
        self._api.RegisterFront(self.front)
        self._api.Init()

        if block:
            try:
                self._api.Join()
            except KeyboardInterrupt:
                pass
            finally:
                self.stop()
        else:
            self._thread = threading.Thread(target=self._api.Join, daemon=True)
            self._thread.start()

    def wait_ready(self, timeout=15):
        """等待登录就绪"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._loggedin:
                return True
            time.sleep(0.2)
        return self._loggedin

    def stop(self):
        """停止并释放资源

        macOS 上 CTP C++ API 的 Release() 在 Join() 仍然运行于
        另一个线程时会触发 segfault。因此:
        - 非阻塞模式 (daemon thread): 仅置空引用，让 daemon 线程随进程退出
        - 阻塞模式 (Join 已返回): 安全调用 Release()
        """
        self._loggedin = False
        self._connected = False
        api = self._api
        self._api = None
        self._spi = None
        if api is not None and (self._thread is None or not self._thread.is_alive()):
            try:
                api.RegisterSpi(None)
                api.Release()
            except Exception:
                pass
            # 如果 daemon thread 还活着，不调用 Release，
            # daemon=True 线程会在进程退出时自动终止

    @property
    def is_ready(self):
        return self._connected and self._loggedin


# ===========================================================================
#  TraderClient - 交易客户端
# ===========================================================================


class _TraderSpi(CThostFtdcTraderSpi):
    def __init__(self, client):
        super().__init__()
        self._c = client

    def OnFrontConnected(self):
        self._c._connected = True
        field = CThostFtdcReqAuthenticateField()
        field.BrokerID = self._c.broker_id
        field.UserID = self._c.user_id
        field.AppID = self._c.app_id
        field.AuthCode = self._c.auth_code
        self._c._req_id += 1
        self._c._api.ReqAuthenticate(field, self._c._req_id)

    def OnFrontDisconnected(self, nReason):
        self._c._connected = False
        self._c._ready = False

    def OnRspAuthenticate(self, pRspAuthenticateField, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID == 0:
            field = CThostFtdcReqUserLoginField()
            field.BrokerID = self._c.broker_id
            field.UserID = self._c.user_id
            field.Password = self._c.password
            self._c._req_id += 1
            self._c._api.ReqUserLogin(field, self._c._req_id)
        elif self._c.on_error:
            self._c.on_error(pRspInfo)

    def OnRspUserLogin(self, pRspUserLogin, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID == 0:
            self._c._front_id = pRspUserLogin.FrontID
            self._c._session_id = pRspUserLogin.SessionID
            with suppress(TypeError, ValueError):
                self._c._max_order_ref = max(
                    self._c._max_order_ref,
                    int(getattr(pRspUserLogin, "MaxOrderRef", "") or 0),
                )
            field = CThostFtdcSettlementInfoConfirmField()
            field.BrokerID = self._c.broker_id
            field.InvestorID = self._c.user_id
            self._c._req_id += 1
            self._c._api.ReqSettlementInfoConfirm(field, self._c._req_id)
            if self._c.on_login:
                self._c.on_login(pRspUserLogin)
        elif self._c.on_error:
            self._c.on_error(pRspInfo)

    def OnRspSettlementInfoConfirm(self, pSettlementInfoConfirm, pRspInfo, nRequestID, bIsLast):
        if pRspInfo and pRspInfo.ErrorID == 0:
            self._c._ready = True

    def OnRspQryTradingAccount(self, pTradingAccount, pRspInfo, nRequestID, bIsLast):
        if pTradingAccount:
            self._c._last_account = pTradingAccount
        if bIsLast:
            self._c._query_done.set()

    def OnRspQryInvestorPosition(self, pPos, pRspInfo, nRequestID, bIsLast):
        if pPos and pPos.Position > 0:
            self._c._last_positions.append(pPos)
        if bIsLast:
            self._c._query_done.set()

    def OnRtnOrder(self, pOrder):
        self._c._push_order_event(pOrder)

    def OnRtnTrade(self, pTrade):
        self._c._push_trade_event(pTrade)

    def OnRspOrderInsert(self, pInputOrder, pRspInfo, nRequestID, bIsLast):
        self._c._push_error_event(
            event_type="order_insert_response",
            rsp_info=pRspInfo,
            field=pInputOrder,
            request_id=nRequestID,
        )

    def OnErrRtnOrderInsert(self, pInputOrder, pRspInfo):
        self._c._push_error_event(
            event_type="order_insert_error",
            rsp_info=pRspInfo,
            field=pInputOrder,
        )

    def OnRspError(self, pRspInfo, nRequestID, bIsLast):
        self._c._push_error_event(
            event_type="response_error",
            rsp_info=pRspInfo,
            request_id=nRequestID,
        )


class TraderClient:
    """交易客户端封装

    Args:
        front: 交易前置地址
        broker_id: 经纪商代码
        user_id: 投资者代码
        password: 密码
        app_id: 客户端 AppID
        auth_code: 认证码
    """

    def __init__(
        self,
        front,
        broker_id,
        user_id,
        password,
        app_id="simnow_client_test",
        auth_code="0000000000000000",
    ):
        self.front = front
        self.broker_id = broker_id
        self.user_id = user_id
        self.password = password
        self.app_id = app_id
        self.auth_code = auth_code

        self.on_login = None  # callback(CThostFtdcRspUserLoginField)
        self.on_order = None  # callback(CThostFtdcOrderField)
        self.on_trade = None  # callback(CThostFtdcTradeField)
        self.on_error = None  # callback(CThostFtdcRspInfoField)

        self._connected = False
        self._ready = False
        self._req_id = 0
        self._front_id = 0
        self._session_id = 0
        self._api = None
        self._spi = None
        self._thread = None
        self._query_done = threading.Event()
        self._last_account = None
        self._last_positions = []
        self._max_order_ref = 0
        self._order_ref_lock = threading.Lock()
        self._order_events = queue.Queue()
        self._trade_events = queue.Queue()
        self._error_events = queue.Queue()

    def start(self, block=False):
        """启动连接（默认后台运行）"""
        _check_native_module()
        flow = _flow_dir(f"td_{self.broker_id}_{self.user_id}")
        self._api = CThostFtdcTraderApi.CreateFtdcTraderApi(flow)
        self._spi = _TraderSpi(self)
        self._api.RegisterSpi(self._spi)
        self._api.SubscribePrivateTopic(2)
        self._api.SubscribePublicTopic(2)
        self._api.RegisterFront(self.front)
        self._api.Init()

        if block:
            try:
                self._api.Join()
            except KeyboardInterrupt:
                pass
            finally:
                self.stop()
        else:
            self._thread = threading.Thread(target=self._api.Join, daemon=True)
            self._thread.start()

    def wait_ready(self, timeout=15):
        """等待完成认证→登录→结算确认"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._ready:
                return True
            time.sleep(0.2)
        return self._ready

    def query_account(self, timeout=5):
        """查询资金账户，返回 CThostFtdcTradingAccountField 或 None"""
        if not self._ready:
            return None
        self._query_done.clear()
        self._last_account = None
        field = CThostFtdcQryTradingAccountField()
        field.BrokerID = self.broker_id
        field.InvestorID = self.user_id
        self._req_id += 1
        self._api.ReqQryTradingAccount(field, self._req_id)
        self._query_done.wait(timeout)
        return self._last_account

    def query_positions(self, timeout=5):
        """查询持仓，返回 list[CThostFtdcInvestorPositionField]"""
        if not self._ready:
            return []
        self._query_done.clear()
        self._last_positions = []
        field = CThostFtdcQryInvestorPositionField()
        field.BrokerID = self.broker_id
        field.InvestorID = self.user_id
        self._req_id += 1
        self._api.ReqQryInvestorPosition(field, self._req_id)
        self._query_done.wait(timeout)
        return self._last_positions

    def next_order_ref(self) -> str:
        """Return the next monotonic CTP OrderRef.

        CTP expects OrderRef to be unique and increasing during a session.
        """
        with self._order_ref_lock:
            self._max_order_ref += 1
            return str(self._max_order_ref)

    def wait_order_event(self, timeout=5):
        """Wait for the next order callback snapshot."""
        try:
            return self._order_events.get(timeout=timeout)
        except queue.Empty:
            return None

    def wait_trade_event(self, timeout=5):
        """Wait for the next trade callback snapshot."""
        try:
            return self._trade_events.get(timeout=timeout)
        except queue.Empty:
            return None

    def wait_error_event(self, timeout=5):
        """Wait for the next error callback snapshot."""
        try:
            return self._error_events.get(timeout=timeout)
        except queue.Empty:
            return None

    def _push_order_event(self, order_field) -> None:
        snapshot = _snapshot_ctp_field(order_field)
        if snapshot:
            self._order_events.put(snapshot)
        if self.on_order:
            self.on_order(order_field)

    def _push_trade_event(self, trade_field) -> None:
        snapshot = _snapshot_ctp_field(trade_field)
        if snapshot:
            self._trade_events.put(snapshot)
        if self.on_trade:
            self.on_trade(trade_field)

    def _push_error_event(self, event_type, rsp_info=None, field=None, request_id=None) -> None:
        payload = {
            "event": event_type,
            "request_id": request_id,
            "error_id": getattr(rsp_info, "ErrorID", 0) if rsp_info is not None else 0,
            "error_msg": getattr(rsp_info, "ErrorMsg", "") if rsp_info is not None else "",
            "field": _snapshot_ctp_field(field),
        }
        self._error_events.put(payload)
        if self.on_error and rsp_info is not None:
            self.on_error(rsp_info)

    @property
    def api(self):
        """获取底层 CThostFtdcTraderApi 对象，用于发送自定义请求"""
        return self._api

    def stop(self):
        """停止并释放资源

        macOS 上 CTP C++ API 的 Release() 在 Join() 仍然运行于
        另一个线程时会触发 segfault。因此:
        - 非阻塞模式 (daemon thread): 仅置空引用，让 daemon 线程随进程退出
        - 阻塞模式 (Join 已返回): 安全调用 Release()
        """
        self._ready = False
        self._connected = False
        api = self._api
        self._api = None
        self._spi = None
        if api is not None and (self._thread is None or not self._thread.is_alive()):
            try:
                api.RegisterSpi(None)
                api.Release()
            except Exception:
                pass

    @property
    def is_ready(self):
        return self._connected and self._ready
