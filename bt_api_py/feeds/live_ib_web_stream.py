"""
Interactive Brokers Web API 流式数据 (WebSocket)

IB Web API WebSocket 协议:
  - 连接: wss://{host}/v1/api/ws
  - 市场数据订阅: smd+{CONID}+{"fields":["31","84","85","86","88"]}
  - 取消订阅: umd+{CONID}+{}
  - 账户订阅: sacct (subscribe account data)
  - 服务端心跳: hb 消息
  - 客户端需定时发送 tic 心跳保持连接

依赖: pip install websocket-client
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from typing import Any
from urllib.parse import urlparse

from bt_api_py.feeds.base_stream import BaseDataStream, ConnectionState


def _is_local_base_url(base_url: str) -> bool:
    host = (urlparse(str(base_url or "")).hostname or "").lower()
    return host in {"localhost", "127.0.0.1"}


def _normalize_ws_message(message: Any) -> str:
    if isinstance(message, (bytes, bytearray)):
        return bytes(message).decode("utf-8", errors="replace")
    return str(message)


class IbWebDataStream(BaseDataStream):
    """IB Web API 市场数据 WebSocket 流

    订阅市场数据后，服务端会推送实时行情快照更新。
    每个 conid 需要单独订阅。
    """

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.base_url = kwargs.get("base_url", "https://localhost:5000")
        self.access_token = kwargs.get("access_token")
        self.verify_ssl = kwargs.get("verify_ssl", False)
        self.topics = kwargs.get("topics", [])
        self._ws: Any = None
        self._heartbeat_interval = 30  # 秒
        self._heartbeat_thread: threading.Thread | None = None
        self._ws_thread: threading.Thread | None = None
        self._reconnect_delay = 5
        self._max_reconnect_delay = 60
        self._subscribed_conids: set[int] = set()

    def _build_ws_url(self) -> Any:
        """构建 WebSocket URL"""
        base = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        if "/v1/api" in base:
            return f"{base}/ws"
        return f"{base}/v1/api/ws"

    def connect(self):
        """建立 WebSocket 连接"""
        try:
            import websocket
        except ImportError:
            raise ImportError(
                "websocket-client required. Install: pip install websocket-client"
            ) from None

        self.state = ConnectionState.CONNECTING
        ws_url = self._build_ws_url()
        self.logger.info(f"Connecting to IB WebSocket: {ws_url}")

        ssl_opts = {}
        if not self.verify_ssl:
            import ssl

            ssl_opts = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}

        self._ws = websocket.WebSocketApp(
            ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )

        self._ws_thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={
                **({"sslopt": ssl_opts} if ssl_opts else {}),
                **(
                    {"http_no_proxy": ["localhost", "127.0.0.1"]}
                    if _is_local_base_url(self.base_url)
                    else {}
                ),
            },
            daemon=True,
        )
        self._ws_thread.start()

    def disconnect(self):
        """断开 WebSocket 连接"""
        self._running = False
        if self._ws:
            with contextlib.suppress(Exception):
                self._ws.close()
        self.state = ConnectionState.DISCONNECTED

    def subscribe_topics(self, topics):
        """订阅市场数据主题

        :param topics: list of dicts, 每个包含:
            - topic: "market_data"
            - conid: int, 合约ID
            - fields: list of str, 字段ID (可选)
        """
        for topic in topics:
            topic_type = topic.get("topic", "")
            if topic_type == "market_data":
                conid = topic.get("conid")
                fields = topic.get("fields", ["31", "84", "85", "86", "88"])
                if conid:
                    self._subscribe_market_data(conid, fields)

    def unsubscribe_market_data(self, conid):
        """取消订阅市场数据"""
        if self._ws and self._running:
            msg = f"umd+{conid}+{{}}"
            try:
                self._ws.send(msg)
                self._subscribed_conids.discard(conid)
                self.logger.info(f"Unsubscribed market data: conid={conid}")
            except Exception as e:
                self.logger.warning(f"Unsubscribe failed for conid={conid}: {e}")

    def _subscribe_market_data(self, conid, fields=None) -> Any:
        """发送市场数据订阅消息"""
        if fields is None:
            fields = ["31", "84", "85", "86", "88"]
        payload = json.dumps({"fields": fields})
        msg = f"smd+{conid}+{payload}"
        if self._ws and self._running:
            try:
                self._ws.send(msg)
                self._subscribed_conids.add(conid)
                self.logger.info(f"Subscribed market data: conid={conid}, fields={fields}")
            except Exception as e:
                self.logger.warning(f"Subscribe failed for conid={conid}: {e}")

    def _on_open(self, ws) -> Any:
        """WebSocket 连接建立回调"""
        self.state = ConnectionState.CONNECTED
        self.logger.info("IB WebSocket connected")

        # 启动心跳线程
        self._start_heartbeat()

        # 订阅已注册的主题
        if self.topics:
            self.subscribe_topics(self.topics)

    def _on_message(self, ws, message) -> Any:
        """WebSocket 消息处理"""
        try:
            text = _normalize_ws_message(message)
            # IB WebSocket 消息格式可能是 JSON 或特殊格式
            if text.startswith("{") or text.startswith("["):
                data = json.loads(text)
                self._process_message(data)
            elif text.startswith("hb"):
                # 心跳响应
                pass
            else:
                self.logger.debug(f"Unknown WS message: {text[:100]}")
        except json.JSONDecodeError:
            self.logger.debug(f"Non-JSON WS message: {text[:100]}")
        except Exception as e:
            self.logger.warning(f"WS message processing error: {e}")

    def _process_message(self, data) -> Any:
        """处理解析后的 JSON 消息"""
        if isinstance(data, dict):
            # 市场数据更新
            if "conid" in data or "conidEx" in data:
                self.push_data(
                    {
                        "type": "market_data",
                        "exchange": "IB_WEB",
                        "data": data,
                    }
                )
            # 系统消息
            elif "topic" in data:
                topic = data.get("topic", "")
                if topic.startswith("smd"):
                    self.push_data(
                        {
                            "type": "market_data",
                            "exchange": "IB_WEB",
                            "data": data,
                        }
                    )
                elif topic.startswith("sor"):
                    self.push_data(
                        {
                            "type": "order_update",
                            "exchange": "IB_WEB",
                            "data": data,
                        }
                    )
                elif topic.startswith("spl"):
                    self.push_data(
                        {
                            "type": "pnl_update",
                            "exchange": "IB_WEB",
                            "data": data,
                        }
                    )
                else:
                    self.push_data(
                        {
                            "type": "system",
                            "exchange": "IB_WEB",
                            "data": data,
                        }
                    )

    def _on_error(self, ws, error) -> Any:
        """WebSocket 错误回调"""
        self.logger.warning(f"IB WebSocket error: {error}")
        self.state = ConnectionState.ERROR

    def _on_close(self, ws, close_status_code, close_msg) -> Any:
        """WebSocket 关闭回调"""
        self.logger.info(f"IB WebSocket closed: {close_status_code} {close_msg}")
        self.state = ConnectionState.DISCONNECTED

        # 自动重连
        if self._running:
            self.logger.info(f"Reconnecting in {self._reconnect_delay}s...")
            time.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
            try:
                self.connect()
                # 重新订阅
                for conid in list(self._subscribed_conids):
                    self._subscribe_market_data(conid)
            except Exception as e:
                self.logger.warning(f"Reconnect failed: {e}")

    def _start_heartbeat(self) -> Any:
        """启动心跳发送线程"""

        def heartbeat_loop():
            while self._running and self._ws:
                try:
                    self._ws.send("tic")
                except Exception:
                    break
                time.sleep(self._heartbeat_interval)

        self._heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _run_loop(self) -> Any:
        """主循环 (由 BaseDataStream.start() 在线程中调用)"""
        self.connect()
        while self._running:
            time.sleep(1)


class IbWebAccountStream(BaseDataStream):
    """IB Web API 账户数据 WebSocket 流

    订阅账户数据后，服务端推送:
      - 账户汇总更新 (sacct)
      - 订单状态更新 (sor)
      - 盈亏更新 (spl)
    """

    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        super().__init__(data_queue, **kwargs)
        self.base_url = kwargs.get("base_url", "https://localhost:5000")
        self.access_token = kwargs.get("access_token")
        self.verify_ssl = kwargs.get("verify_ssl", False)
        self.account_id = kwargs.get("account_id")
        self.topics = kwargs.get("topics", [])
        self._ws: Any = None
        self._heartbeat_interval = 30
        self._reconnect_delay = 5
        self._max_reconnect_delay = 60
        self._ws_thread: threading.Thread | None = None

    def _build_ws_url(self) -> Any:
        base = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        if "/v1/api" in base:
            return f"{base}/ws"
        return f"{base}/v1/api/ws"

    def connect(self) -> None:
        try:
            import websocket
        except ImportError:
            raise ImportError(
                "websocket-client required. Install: pip install websocket-client"
            ) from None

        self.state = ConnectionState.CONNECTING
        ws_url = self._build_ws_url()
        self.logger.info(f"Connecting to IB Account WebSocket: {ws_url}")

        ssl_opts = {}
        if not self.verify_ssl:
            import ssl

            ssl_opts = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}

        self._ws = websocket.WebSocketApp(
            ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws_thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={
                **({"sslopt": ssl_opts} if ssl_opts else {}),
                **(
                    {"http_no_proxy": ["localhost", "127.0.0.1"]}
                    if _is_local_base_url(self.base_url)
                    else {}
                ),
            },
            daemon=True,
        )
        self._ws_thread.start()

    def disconnect(self):
        self._running = False
        if self._ws:
            with contextlib.suppress(Exception):
                self._ws.close()
        self.state = ConnectionState.DISCONNECTED

    def subscribe_topics(self, topics):
        """订阅账户主题

        :param topics: list of dicts, 每个包含:
            - topic: "account" / "order" / "pnl" / "trade"
        """
        for topic in topics:
            topic_type = topic.get("topic", "")
            if topic_type == "account":
                self._send_ws("sacct")
            elif topic_type == "order":
                self._send_ws("sor")
            elif topic_type == "pnl":
                if self.account_id:
                    self._send_ws(f"spl+{self.account_id}")
                else:
                    self._send_ws("spl")
            elif topic_type == "trade":
                self._send_ws("str")

    def _send_ws(self, msg) -> Any:
        if self._ws and self._running:
            try:
                self._ws.send(msg)
                self.logger.info(f"Sent WS message: {msg}")
            except Exception as e:
                self.logger.warning(f"WS send failed: {e}")

    def _on_open(self, ws) -> Any:
        self.state = ConnectionState.CONNECTED
        self.logger.info("IB Account WebSocket connected")
        self._start_heartbeat()
        if self.topics:
            self.subscribe_topics(self.topics)

    def _on_message(self, ws, message) -> Any:
        try:
            text = _normalize_ws_message(message)
            if text.startswith("{") or text.startswith("["):
                data = json.loads(text)
                self._process_message(data)
            elif text.startswith("hb"):
                pass
            else:
                self.logger.debug(f"Unknown account WS message: {text[:100]}")
        except json.JSONDecodeError:
            self.logger.debug(f"Non-JSON account WS message: {text[:100]}")
        except Exception as e:
            self.logger.warning(f"Account WS message error: {e}")

    def _process_message(self, data) -> Any:
        if isinstance(data, dict):
            topic = data.get("topic", "")
            if topic.startswith("spl"):
                self.push_data(
                    {
                        "type": "pnl_update",
                        "exchange": "IB_WEB",
                        "data": data,
                    }
                )
            elif topic.startswith("sor"):
                self.push_data(
                    {
                        "type": "order_update",
                        "exchange": "IB_WEB",
                        "data": data,
                    }
                )
            elif topic.startswith("sacct") or "accountId" in data:
                self.push_data(
                    {
                        "type": "account_update",
                        "exchange": "IB_WEB",
                        "data": data,
                    }
                )
            elif topic.startswith("str"):
                self.push_data(
                    {
                        "type": "trade_update",
                        "exchange": "IB_WEB",
                        "data": data,
                    }
                )
            else:
                self.push_data(
                    {
                        "type": "account_system",
                        "exchange": "IB_WEB",
                        "data": data,
                    }
                )

    def _on_error(self, ws, error) -> Any:
        self.logger.warning(f"IB Account WebSocket error: {error}")
        self.state = ConnectionState.ERROR

    def _on_close(self, ws, close_status_code, close_msg) -> Any:
        self.logger.info(f"IB Account WebSocket closed: {close_status_code} {close_msg}")
        self.state = ConnectionState.DISCONNECTED
        if self._running:
            self.logger.info(f"Account WS reconnecting in {self._reconnect_delay}s...")
            time.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
            try:
                self.connect()
            except Exception as e:
                self.logger.warning(f"Account WS reconnect failed: {e}")

    def _start_heartbeat(self) -> Any:
        def heartbeat_loop():
            while self._running and self._ws:
                try:
                    self._ws.send("tic")
                except Exception:
                    break
                time.sleep(self._heartbeat_interval)

        t = threading.Thread(target=heartbeat_loop, daemon=True)
        t.start()

    def _run_loop(self) -> Any:
        self.connect()
        while self._running:
            time.sleep(1)
