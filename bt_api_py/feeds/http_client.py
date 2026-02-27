# -*- coding: utf-8 -*-
"""
统一 HTTP 客户端 — 基于 httpx

提供同步和异步 HTTP 请求能力，替代直接使用 requests 库。
支持连接池复用、统一错误处理、代理配置。
"""
import logging
from typing import Optional, Dict, Any

try:
    import httpx
except ImportError:
    httpx = None  # 非 HTTP 场所（CTP/IB/QMT）不强制依赖 httpx

from bt_api_py.error_framework import (
    RateLimitError, AuthenticationError, ServerError, RequestFailedError,
)

logger = logging.getLogger(__name__)


class HttpClient:
    """统一 HTTP 客户端"""

    def __init__(
        self,
        venue: str = "",
        timeout: float = 10.0,
        max_connections: int = 100,
        max_keepalive_connections: int = 20,
        verify: bool = True,
        proxies: Optional[str] = None,
        **kwargs,
    ):
        if httpx is None:
            raise ImportError(
                "httpx is required for HttpClient. Install it with: pip install httpx"
            )

        self._venue = venue
        self.timeout = timeout

        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )

        transport_kwargs = {}
        if proxies:
            transport_kwargs["proxy"] = proxies

        # 同步客户端
        self._sync_client = httpx.Client(
            timeout=timeout,
            limits=limits,
            verify=verify,
            follow_redirects=True,
            **transport_kwargs,
        )

        # 异步客户端（延迟初始化）
        self._async_client: Optional[httpx.AsyncClient] = None
        self._async_kwargs = {
            "timeout": timeout,
            "limits": limits,
            "verify": verify,
            "follow_redirects": True,
        }
        if proxies:
            self._async_kwargs["proxy"] = proxies

    def _get_async_client(self) -> "httpx.AsyncClient":
        """延迟初始化异步客户端"""
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(**self._async_kwargs)
        return self._async_client

    def request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        timeout: Optional[float] = None,
        cookies: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """同步请求"""
        req_kwargs = {}
        if headers:
            req_kwargs["headers"] = headers
        if params:
            req_kwargs["params"] = params
        if json_data:
            req_kwargs["json"] = json_data
        if data:
            req_kwargs["content"] = data
        if timeout is not None:
            req_kwargs["timeout"] = timeout

        # 将 cookies 添加到 Cookie header 以避免 httpx 警告
        if cookies:
            cookie_header = '; '.join(f'{k}={v}' for k, v in cookies.items())
            if 'headers' not in req_kwargs:
                req_kwargs['headers'] = {}
            req_kwargs['headers']['Cookie'] = cookie_header

        try:
            response = self._sync_client.request(method, url, **req_kwargs)
        except httpx.TimeoutException as e:
            raise RequestFailedError(
                venue=self._venue, message=f"Request timeout: {e}"
            )
        except httpx.ConnectError as e:
            raise RequestFailedError(
                venue=self._venue, message=f"Connection error: {e}"
            )

        return self._process_response(response)

    async def async_request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        data: Optional[Any] = None,
        timeout: Optional[float] = None,
        cookies: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """异步请求"""
        client = self._get_async_client()

        req_kwargs = {}
        if headers:
            req_kwargs["headers"] = headers
        if params:
            req_kwargs["params"] = params
        if json_data:
            req_kwargs["json"] = json_data
        if data:
            req_kwargs["content"] = data
        if timeout is not None:
            req_kwargs["timeout"] = timeout

        # 将 cookies 添加到 Cookie header 以避免 httpx 警告
        if cookies:
            cookie_header = '; '.join(f'{k}={v}' for k, v in cookies.items())
            if 'headers' not in req_kwargs:
                req_kwargs['headers'] = {}
            req_kwargs['headers']['Cookie'] = cookie_header

        try:
            response = await client.request(method, url, **req_kwargs)
        except httpx.TimeoutException as e:
            raise RequestFailedError(
                venue=self._venue, message=f"Async request timeout: {e}"
            )
        except httpx.ConnectError as e:
            raise RequestFailedError(
                venue=self._venue, message=f"Async connection error: {e}"
            )

        return self._process_response(response)

    def _process_response(self, response: "httpx.Response") -> Dict[str, Any]:
        """处理响应，统一错误转换"""
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError:
            raise self._handle_error(response)

        try:
            return response.json()
        except Exception:
            return {"text": response.text, "status_code": response.status_code}

    def _handle_error(self, response: "httpx.Response") -> Exception:
        """统一错误处理"""
        status = response.status_code
        try:
            body = response.json()
        except Exception:
            body = {"text": response.text}

        if status == 429:
            return RateLimitError(venue=self._venue, response=body)
        elif status in (401, 403):
            return AuthenticationError(
                venue=self._venue, response=body,
                message=f"HTTP {status}: {body.get('msg', body.get('message', 'Auth error'))}"
            )
        elif status >= 500:
            return ServerError(venue=self._venue, status=status, response=body)
        else:
            return RequestFailedError(
                venue=self._venue, status=status, response=body,
                message=f"HTTP {status}: {body.get('msg', body.get('message', 'Request failed'))}"
            )

    def close(self):
        """关闭同步客户端"""
        if not self._sync_client.is_closed:
            self._sync_client.close()

    async def aclose(self):
        """异步关闭所有客户端"""
        if not self._sync_client.is_closed:
            self._sync_client.close()
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()
