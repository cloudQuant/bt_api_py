from __future__ import annotations

import datetime
import os
import random
import ssl
import threading
import time
import traceback
from typing import Any

import websocket

from bt_api_py.functions.utils import get_project_log_path
from bt_api_py.logging_factory import get_logger


_PROXY_ENV_KEYS = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
    "SOCKS_PROXY",
    "socks_proxy",
)

# from bt_api_py.containers.exchanges.binance_swap_exchange_data import BinanceExchangeData
# from bt_api_py.containers.exchanges.okx_swap_exchange_data import OkxSwapExchangeData


class MyWebsocketApp:
    def __init__(self, data_queue: Any = None, **kwargs: Any) -> None:
        self.ws: websocket.WebSocketApp | None = None
        self.data_queue = data_queue
        self.wss_name = kwargs.get("wss_name", "default_name")
        self._params = kwargs.get("exchange_data")
        self.wss_url = kwargs.get("wss_url")
        if self.wss_url is None:
            assert self._params is not None, "exchange_data required when wss_url not provided"
            self.wss_url = self._params.get_wss_url()
        self.ping_interval = kwargs.get("ping_interval", 10)
        self.ping_timeout = kwargs.get("ping_timeout", 5)
        self.sslopt = kwargs.get("sslopt", {"cert_reqs": ssl.CERT_NONE})
        self.start_config = {
            "ping_interval": self.ping_interval,
            "ping_timeout": self.ping_timeout,
            "sslopt": self.sslopt,
        }
        self.restart_gap = kwargs.get("restart_gap", 0)
        self.http_proxy_host = kwargs.get("http_proxy_host")
        self.http_proxy_port = kwargs.get("http_proxy_port")
        if self.http_proxy_host is None:
            try:
                import urllib.request
                from urllib.parse import urlparse

                system_proxies = urllib.request.getproxies()
                proxy_url = system_proxies.get("http") or system_proxies.get("https")
                if proxy_url:
                    parsed = urlparse(proxy_url)
                    if parsed.scheme in ("http", "https"):
                        self.http_proxy_host = parsed.hostname
                        self.http_proxy_port = parsed.port
            except Exception as e:
                get_logger("my_websocket_app").debug(
                    "Failed to parse system proxy: %s", e, exc_info=True
                )
        default_log = get_project_log_path("my_websocket_app.log")
        self.log_file_name = kwargs.get("log_file_name", default_log)
        self.wss_logger = get_logger("unknown")
        self._running_flag = False  # 阻塞，防止短时间连接数超限
        self._restart_flag = True  # 默认重启
        self.process = threading.Thread(target=self.run, daemon=True)

        # ── 指数退避重连参数 ──────────────────────────────────────
        self._reconnect_base_delay = kwargs.get("reconnect_base_delay", 1.0)
        self._reconnect_max_delay = kwargs.get("reconnect_max_delay", 60.0)
        self._max_reconnect_attempts = kwargs.get("max_reconnect_attempts", 0)  # 0=无限
        self._reconnect_attempt = 0
        self._current_delay = self._reconnect_base_delay

        # ── EventBus 支持（可选） ────────────────────────────────
        self._event_bus = kwargs.get("event_bus")

    # noinspection PyMethodMayBeStatic
    def get_timestamp(self, time_str) -> Any:
        dt = datetime.datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        timestamp = int((time.mktime(dt.timetuple()) + dt.microsecond / 1000000) * 1000)
        return timestamp

    def subscribe(self, **kwargs):
        if self._params is None:
            raise ValueError("exchange_data (params) is required for subscribe")
        req = self._params.get_wss_path(**kwargs)
        if self.ws is None:
            raise ConnectionError("WebSocket connection not established")
        self.ws.send(req)
        # time.sleep(0.3)

    def _emit_event(self, event_type, **payload):
        """通过 EventBus 发送 WebSocket 状态事件（如果已配置）."""
        if self._event_bus is not None:
            self._event_bus.emit(
                event_type,
                {
                    "wss_name": self.wss_name,
                    "wss_url": self.wss_url,
                    **payload,
                },
            )

    def _backoff_delay(self):
        """计算指数退避延迟（带抖动），并更新下次延迟."""
        jitter = random.uniform(0, self._current_delay * 0.1)
        delay = min(self._current_delay + jitter, self._reconnect_max_delay)
        self._current_delay = min(self._current_delay * 2, self._reconnect_max_delay)
        return delay

    def _reset_backoff(self):
        """连接成功后重置退避计数器."""
        self._reconnect_attempt = 0
        self._current_delay = self._reconnect_base_delay

    def on_open(self, _ws):
        try:
            self.open_rsp()
        except Exception as e:
            self.wss_logger.warning(f"{self.wss_name},{self.wss_url},{e},{traceback.format_exc()}")
        self._running_flag = True
        self._reset_backoff()
        self._emit_event("ws.connected")

    def open_rsp(self):
        pass

    def on_message(self, _ws, message):
        try:
            self.message_rsp(message)
        except Exception as e:
            self.wss_logger.warning(f"{self.wss_name},{self.wss_url},{e},{traceback.format_exc()}")

    # noinspection PyMethodMayBeStatic
    def message_rsp(self, message):
        self.wss_logger.debug(message)

    def on_error(self, _ws, error):
        try:
            self.error_rsp(f"error: {error}")
        except Exception as e:
            self.wss_logger.warning(f"{self.wss_name},{self.wss_url},{e},{traceback.format_exc()}")
        self._emit_event("ws.error", error=str(error))

    # noinspection PyMethodMayBeStatic
    def error_rsp(self, error):
        self.wss_logger.warning(f"name: {self.wss_name}, url: {self.wss_url}, error: {error}")

    def on_close(self, _ws, _close_status_code, _close_msg):
        self._running_flag = False
        self._emit_event("ws.disconnected", code=_close_status_code, msg=_close_msg)
        try:
            self.close_rsp(self._restart_flag)
        except Exception as e:
            self.wss_logger.warning(f"{self.wss_name},{self.wss_url},{e},{traceback.format_exc()}")

    def on_ping(self, _ws, ping):
        self.wss_logger.info(
            f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} Websocket ping {ping} ====="
        )
        if self.ws is not None and self.ws.sock is not None:
            self.ws.sock.pong(ping)

    def on_pong(self, _ws, pong):
        self.wss_logger.info(
            f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} Websocket pong {pong} ====="
        )

    # noinspection PyMethodMayBeStatic
    def close_rsp(self, _is_restart):
        self._restart_flag = False
        self.wss_logger.info(
            f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} Websocket Disconnected ====="
        )

    def _clear_proxy_env(self) -> None:
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)

    def _create_websocket_app(self) -> websocket.WebSocketApp:
        if self.wss_url is None:
            raise ValueError("wss_url is required for WebSocket connection")
        return websocket.WebSocketApp(
            self.wss_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_ping=self.on_ping,
            on_pong=self.on_pong,
        )

    def run(self):
        # websocket.enableTrace(True)  # 调试
        # 设置超时
        # print("run begin")
        websocket.setdefaulttimeout(self.ping_timeout)
        if self.wss_url is None:
            raise ValueError("wss_url is required for WebSocket connection")
        self.ws = websocket.WebSocketApp(
            self.wss_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_ping=self.on_ping,
            on_pong=self.on_pong,
        )
        while True:
            # 检查最大重连次数
            if (
                self._max_reconnect_attempts > 0
                and self._reconnect_attempt >= self._max_reconnect_attempts
            ):
                self.wss_logger.warning(
                    f"{self.wss_name}: max reconnect attempts ({self._max_reconnect_attempts}) reached, giving up"
                )
                self._emit_event("ws.max_reconnect", attempts=self._reconnect_attempt)
                break

            try:
                run_kwargs = {
                    "ping_interval": self.ping_interval,
                    "ping_timeout": self.ping_timeout,
                    "sslopt": self.sslopt,
                }
                if self.http_proxy_host:
                    run_kwargs["http_proxy_host"] = self.http_proxy_host
                    run_kwargs["proxy_type"] = "http"
                    if self.http_proxy_port:
                        run_kwargs["http_proxy_port"] = self.http_proxy_port
                elif self.http_proxy_host == "":
                    self._clear_proxy_env()
                    run_kwargs["http_no_proxy"] = ["*"]
                if self.ws is not None:
                    self.ws.run_forever(**run_kwargs)
                self.wss_logger.info("----------wss running----------------")
            except Exception as e:
                self.wss_logger.warning(
                    f"{self.wss_name},{self.wss_url},{e},{traceback.format_exc()}"
                )

            # 指数退避重连延迟
            self._reconnect_attempt += 1
            delay = self._backoff_delay()
            self._emit_event("ws.reconnecting", attempt=self._reconnect_attempt, delay=delay)
            self.wss_logger.info(
                f"{self.wss_name}: reconnecting in {delay:.1f}s (attempt {self._reconnect_attempt})"
            )
            time.sleep(delay)

    def start(self, connect_timeout=30):
        self.process = threading.Thread(target=self.run, daemon=True)
        self.process.start()
        _elapsed: float = 0.0
        if self._params is None:
            raise ValueError("exchange_data (params) is required to start WebSocket")
        while not self._running_flag:
            self.wss_logger.info(
                f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"Wait {self._params.exchange_name} Websocket Connecting... ====="
            )
            time.sleep(0.5)
            _elapsed += 0.5
            if _elapsed >= connect_timeout:
                self.wss_logger.warning(
                    f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"{self._params.exchange_name} Websocket Connect Timeout ({connect_timeout}s)! ====="
                )
                break
        # 重启定时器
        if self.restart_gap:
            restart_timer = threading.Thread(target=self.restart_timer)
            restart_timer.start()

    def restart(self):
        # 重启直接关闭ws, 然后创建新的ws
        self.wss_logger.info(f"===== {time.strftime('%Y-%m-%d %H:%M:')}, 重启ws")
        self.stop()
        # 创建新的ws
        self.start()

    def stop(self):
        self._restart_flag = False
        if self.ws is not None:
            self.ws.close()
            self.ws = None

    def restart_timer(self):
        """重启定时器."""
        time_gap = self.restart_gap
        while True:
            time.sleep(time_gap)
            try:
                self.wss_logger.info("restartTimer Working....")
                self.restart()
            except Exception as e:
                self.wss_logger.warning(
                    f"{self.wss_name},{self.wss_url},{e},{traceback.format_exc()}"
                )


if __name__ == "__main__":

    def restart(task: list, timeout1=5000, _timeout2=8000):
        while True:
            time.sleep(int(timeout1 / 1000) - 1)
            try:
                for exc in task:
                    # print(exc.wss_name, "begin_to_run")
                    exc.start()
            except Exception as e:
                get_logger("my_websocket_app").debug(
                    "WebSocket restart task error: %s", e, exc_info=True
                )
