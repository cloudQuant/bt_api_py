import asyncio
import json

# import queue
import traceback
from threading import Thread

# import aiohttp
# from aiohttp import ClientTimeout
from aiohttp import ClientSession, ClientTimeout, TCPConnector

from bt_api_py.functions.calculate_time import get_string_tz_time
from bt_api_py.logging_factory import get_logger

__all__ = ["AsyncBase"]


class AsyncBase:

    def __init__(self, **kwargs):
        self.loop = None
        self.keepalive_timeout = 30
        self.client_timeout = 5
        self.limit = 100
        self.session = None
        self.async_proxy = kwargs.get("async_proxy")
        if self.async_proxy is None:
            import urllib.request
            system_proxies = urllib.request.getproxies()
            self.async_proxy = system_proxies.get('https') or system_proxies.get('http')
        self.async_base_logger = get_logger("async_data")
        self.start_loop()

    def start_loop(self):
        if self.loop is None:
            self.loop = asyncio.new_event_loop()
        loop_thread = Thread(target=self._start_thread_loop, daemon=True)
        loop_thread.start()
        return self.loop, loop_thread

    # noinspection PyBroadException
    def _start_thread_loop(self):
        asyncio.set_event_loop(self.loop)
        while True:
            try:
                if self.loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    self.loop = loop
                self.loop.run_forever()
            except Exception:
                print(traceback.format_exc())
                self.loop.close()

    def release(self):
        self.loop.stop()

    def submit(self, func, callback=None):
        future = asyncio.run_coroutine_threadsafe(func, self.loop)
        if callback is not None:
            future.add_done_callback(callback)

    def get_session(self):
        conn = TCPConnector(ssl=False, keepalive_timeout=self.keepalive_timeout, limit=100)
        session = ClientSession(connector=conn)
        return session

    def close(self):
        self.submit(self.session.close())
        self.release()

    async def async_http_request(
        self, method: str, url, headers=None, body=None, timeout=None
    ) -> dict:
        try:
            # if not hasattr(self, 'session'):
            session = self.session
            if session is None or session.closed:
                session = self.get_session()
                self.session = session
            params = {}
            if timeout is not None:
                params["timeout"] = ClientTimeout(total=timeout)
            if headers is not None:
                params["headers"] = headers
            if body is not None:
                params["data"] = json.dumps(body, ensure_ascii=False)
            # print(f' rest _ async httpRequest params: {params}')
            if self.async_proxy:
                params["proxy"] = self.async_proxy
            func = getattr(session, method.lower())
            # print(f' func: {func.__name__}')
            async with func(url, **params) as resp:
                ret = await resp.json(content_type=None)
            return ret
        except Exception as e:
            # print(traceback.format_exc())
            self.async_base_logger.info(
                f"""rest_async错误:{get_string_tz_time()} {traceback.format_exc()}"""
            )
            raise e


if __name__ == "__main__":
    r = AsyncBase()
    _loop = asyncio.get_event_loop()
    _loop.run_until_complete(
        r.async_http_request(
            "get", "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=10"
        )
    )
