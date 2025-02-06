# -*- coding: utf-8 -*-
import datetime
import ssl
import threading
import time
import traceback
import websocket
from bt_api_py.functions.log_message import SpdLogManager


# from bt_api_py.containers.exchanges.binance_swap_exchange_data import BinanceExchangeData
# from bt_api_py.containers.exchanges.okx_swap_exchange_data import OkxSwapExchangeData


class MyWebsocketApp(object):
    def __init__(self, data_queue, **kwargs):
        self.ws = None
        self.data_queue = data_queue
        self.wss_name = kwargs.get('wss_name', "default_name")
        self._params = kwargs.get('exchange_data')
        self.wss_url = kwargs.get('wss_url', None)
        self.wss_url = self._params.get_wss_url() if self.wss_url is None else self.wss_url
        self.ping_interval = kwargs.get('ping_interval', 10)
        self.ping_timeout = kwargs.get('ping_timeout', 5)
        self.sslopt = kwargs.get('sslopt', {'cert_reqs': ssl.CERT_NONE})
        self.start_config = {

            'ping_interval': self.ping_interval,
            'ping_timeout': self.ping_timeout,
            'sslopt': self.sslopt,
        }
        self.restart_gap = kwargs.get('restart_gap', 0)
        self.log_file_name = kwargs.get('log_file_name', "./logs/my_websocket_app.log")
        self.wss_logger = SpdLogManager(self.log_file_name, self.wss_name,
                                        0, 0, False
                                        ).create_logger()
        self._running_flag = False  # 阻塞，防止短时间连接数超限
        self._restart_flag = True  # 默认重启
        self.process = threading.Thread(target=self.run, daemon=True)

    # noinspection PyMethodMayBeStatic
    def get_timestamp(self, time_str):
        dt = datetime.datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        timestamp = int((time.mktime(dt.timetuple()) + dt.microsecond / 1000000) * 1000)
        return timestamp

    def subscribe(self, **kwargs):
        req = self._params.get_wss_path(**kwargs)
        # print("req", req)
        self.ws.send(req)
        # time.sleep(0.3)

    def on_open(self, _ws):
        try:
            self.open_rsp()
        except Exception as e:
            self.wss_logger.warn(f"{self.wss_name},{self.wss_url},{e},{traceback.format_exc()}")
        self._running_flag = True

    def open_rsp(self):
        pass

    def on_message(self, _ws, message):
        try:
            self.message_rsp(message)
        except Exception as e:
            self.wss_logger.warn(f"{self.wss_name},{self.wss_url},{e},{traceback.format_exc()}")

    # noinspection PyMethodMayBeStatic
    def message_rsp(self, message):
        print(message)

    def on_error(self, _ws, error):
        try:
            self.error_rsp(f'error: {error}')
        except Exception as e:
            self.wss_logger.warn(f"{self.wss_name},{self.wss_url},{e},{traceback.format_exc()}")

    # noinspection PyMethodMayBeStatic
    def error_rsp(self, error):
        self.wss_logger.warn(f"name: {self.wss_name}, url: {self.wss_url}, error: {error}")

    def on_close(self, _ws, _close_status_code, _close_msg):
        self._running_flag = False
        try:
            self.close_rsp(self._restart_flag)
        except Exception as e:
            self.wss_logger.warn(f"{self.wss_name},{self.wss_url},{e},{traceback.format_exc()}")

    def on_ping(self, _ws, ping):
        self.wss_logger.info(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} Websocket ping {ping} =====")
        self.ws.sock.pong('pong')

    def on_pong(self, _ws, pong):
        self.wss_logger.info(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} Websocket pong {pong} =====")

    # noinspection PyMethodMayBeStatic
    def close_rsp(self, _is_restart):
        self._restart_flag = False
        self.wss_logger.info(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} Websocket Disconnected =====")

    def run(self):
        # websocket.enableTrace(True)  # 调试
        # 设置超时
        # print("run begin")
        websocket.setdefaulttimeout(self.ping_timeout)
        self.ws = websocket.WebSocketApp(
            self.wss_url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_ping=self.on_ping,
            on_pong=self.on_pong
        )
        # print("初始化run成功, self.wss_url=", self.wss_url)
        while True:
            try:
                self.ws.run_forever(
                    ping_interval=self.ping_interval,
                    ping_timeout=self.ping_timeout,
                    sslopt=self.sslopt,
                )
                self.wss_logger.info("----------wss running----------------")
            except Exception as e:
                self.wss_logger.warn(f"{self.wss_name},{self.wss_url},{e},{traceback.format_exc()}")
                # self.wss_logger.info(f"===== 失败重启,重新创建新的线程=====")
                # # self.process = threading.Thread(target=self.run, daemon=True)
                # self.restart()
                # time.sleep(1)
            time.sleep(1)

    def start(self):
        # print("Starting WebSocket Server")
        self.process = threading.Thread(target=self.run, daemon=True)
        self.process.start()
        _timeout = 0
        # print("WebSocket Server running")
        while not self._running_flag:  # 阻塞
            self.wss_logger.info(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                                 f"Wait {self._params.exchange_name} Websocket Connecting... =====")
            # _timeout += 1
            time.sleep(0.5)
            # if _timeout >= 10:
            #     print(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')}
            #     {self._params.exchange_name} Websocket Connected Timeout!! =====")
            #     # break # 不重连了
            #     timeout = 0 # 重置
            continue
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
        """重启定时器
        """
        time_gap = self.restart_gap
        while True:
            time.sleep(time_gap)
            try:
                self.wss_logger.info('restartTimer Working....')
                self.restart()
            except Exception as e:
                self.wss_logger.warn(f"{self.wss_name},{self.wss_url},{e},{traceback.format_exc()}")


if __name__ == '__main__':
    def restart(task: list, timeout1=5000, _timeout2=8000):
        while True:
            time.sleep(int(timeout1 / 1000) - 1)
            try:
                for exc in task:
                    # print(exc.wss_name, "begin_to_run")
                    exc.start()
            except Exception as e:
                print(e)
