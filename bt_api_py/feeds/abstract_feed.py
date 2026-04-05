"""统一场所协议 (AbstractVenueFeed) 与异步包装混入 (AsyncWrapperMixin).

设计原则：
1. 方法签名必须兼容现有 Feed 基类（extra_data + **kwargs 模式不变）
2. 异步方法提供默认 run_in_executor 包装，HTTP 场所可覆盖为真异步
3. connect/disconnect 对 HTTP 场所默认为 no-op
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Protocol, TypeVar, cast, runtime_checkable

T = TypeVar("T")


@runtime_checkable
class AbstractVenueFeed(Protocol):
    """统一场所协议.

    所有场所 Feed（CEX/DEX/CTP/IB/QMT）都应满足此协议。
    使用 Protocol 而非 ABC，以便现有 Feed 子类无需修改继承链即可通过类型检查。
    """

    def connect(self) -> None:
        """建立连接（HTTP 场所可为 no-op）."""
        ...

    def disconnect(self) -> None:
        """断开连接."""
        ...

    def is_connected(self) -> bool:
        """检查连接状态."""
        ...

    def get_tick(self, symbol: str, extra_data: Any = None, **kwargs: Any) -> Any:
        """获取最新价格."""
        ...

    def get_depth(self, symbol: str, count: int = 20, extra_data: Any = None, **kwargs: Any) -> Any:
        """获取深度."""
        ...

    def get_kline(
        self,
        symbol: str,
        period: str,
        count: int = 20,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """获取K线."""
        ...

    def make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        offset: str = "open",
        post_only: bool = False,
        client_order_id: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        """下单."""
        ...

    def cancel_order(
        self, symbol: str, order_id: str, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """撤单."""
        ...

    def cancel_all(self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any) -> Any:
        """撤销所有订单（可选能力）."""
        ...

    def query_order(self, symbol: str, order_id: str, extra_data: Any = None, **kwargs: Any) -> Any:
        """查询订单."""
        ...

    def get_open_orders(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        """查询挂单."""
        ...

    def get_balance(self, symbol: Any = None, extra_data: Any = None, **kwargs: Any) -> Any:
        """查询余额."""
        ...

    def get_account(self, symbol: str = "ALL", extra_data: Any = None, **kwargs: Any) -> Any:
        """查询账户信息."""
        ...

    def get_position(self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any) -> Any:
        """查询持仓（期货/期权）."""
        ...

    def async_get_tick(self, symbol: str, extra_data: Any = None, **kwargs: Any) -> Any: ...

    def async_make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        offset: str = "open",
        post_only: bool = False,
        client_order_id: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def async_cancel_order(
        self, symbol: str, order_id: str, extra_data: Any = None, **kwargs: Any
    ) -> Any: ...

    def async_get_balance(
        self, symbol: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> Any: ...

    @property
    def capabilities(self) -> set[str]:
        """返回该 Feed 支持的能力集合."""
        ...


class AsyncWrapperMixin:
    """为非 HTTP 场所（CTP/IB/QMT）提供默认的异步包装.

    HTTP 场所应覆盖这些方法为真正的 httpx 异步实现。
    非 HTTP 场所继承此 Mixin，自动将同步方法包装为异步。
    """

    def _sync_feed(self) -> AbstractVenueFeed:
        return cast("AbstractVenueFeed", self)

    async def async_get_tick(self, symbol: str, extra_data: Any = None, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        feed = self._sync_feed()
        return await loop.run_in_executor(
            None, functools.partial(feed.get_tick, symbol, extra_data=extra_data, **kwargs)
        )

    async def async_get_depth(
        self, symbol: str, count: int = 20, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        loop = asyncio.get_running_loop()
        feed = self._sync_feed()
        return await loop.run_in_executor(
            None,
            functools.partial(feed.get_depth, symbol, count=count, extra_data=extra_data, **kwargs),
        )

    async def async_get_kline(
        self,
        symbol: str,
        period: str,
        count: int = 20,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        loop = asyncio.get_running_loop()
        feed = self._sync_feed()
        return await loop.run_in_executor(
            None,
            functools.partial(
                feed.get_kline, symbol, period, count=count, extra_data=extra_data, **kwargs
            ),
        )

    async def async_make_order(
        self,
        symbol: str,
        volume: float,
        price: float,
        order_type: str,
        offset: str = "open",
        post_only: bool = False,
        client_order_id: str | None = None,
        extra_data: Any = None,
        **kwargs: Any,
    ) -> Any:
        loop = asyncio.get_running_loop()
        feed = self._sync_feed()
        return await loop.run_in_executor(
            None,
            functools.partial(
                feed.make_order,
                symbol,
                volume,
                price,
                order_type,
                offset=offset,
                post_only=post_only,
                client_order_id=client_order_id,
                extra_data=extra_data,
                **kwargs,
            ),
        )

    async def async_cancel_order(
        self, symbol: str, order_id: str, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        loop = asyncio.get_running_loop()
        feed = self._sync_feed()
        return await loop.run_in_executor(
            None,
            functools.partial(feed.cancel_order, symbol, order_id, extra_data=extra_data, **kwargs),
        )

    async def async_cancel_all(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        loop = asyncio.get_running_loop()
        feed = self._sync_feed()
        return await loop.run_in_executor(
            None, functools.partial(feed.cancel_all, symbol, extra_data=extra_data, **kwargs)
        )

    async def async_query_order(
        self, symbol: str, order_id: str, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        loop = asyncio.get_running_loop()
        feed = self._sync_feed()
        return await loop.run_in_executor(
            None,
            functools.partial(feed.query_order, symbol, order_id, extra_data=extra_data, **kwargs),
        )

    async def async_get_open_orders(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        loop = asyncio.get_running_loop()
        feed = self._sync_feed()
        return await loop.run_in_executor(
            None, functools.partial(feed.get_open_orders, symbol, extra_data=extra_data, **kwargs)
        )

    async def async_get_balance(
        self, symbol: Any = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        loop = asyncio.get_running_loop()
        feed = self._sync_feed()
        return await loop.run_in_executor(
            None, functools.partial(feed.get_balance, symbol, extra_data=extra_data, **kwargs)
        )

    async def async_get_account(
        self, symbol: str = "ALL", extra_data: Any = None, **kwargs: Any
    ) -> Any:
        loop = asyncio.get_running_loop()
        feed = self._sync_feed()
        return await loop.run_in_executor(
            None, functools.partial(feed.get_account, symbol, extra_data=extra_data, **kwargs)
        )

    async def async_get_position(
        self, symbol: str | None = None, extra_data: Any = None, **kwargs: Any
    ) -> Any:
        loop = asyncio.get_running_loop()
        feed = self._sync_feed()
        return await loop.run_in_executor(
            None, functools.partial(feed.get_position, symbol, extra_data=extra_data, **kwargs)
        )


def check_protocol_compliance(feed_class: type[Any]) -> list[str]:
    """检查 feed_class 是否符合 AbstractVenueFeed 协议.

    :param feed_class: Feed 类（非实例）
    :return: 缺失方法列表，空列表表示完全符合
    """
    required_methods = [
        "connect",
        "disconnect",
        "is_connected",
        "get_tick",
        "get_depth",
        "get_kline",
        "make_order",
        "cancel_order",
        "cancel_all",
        "query_order",
        "get_open_orders",
        "get_balance",
        "get_account",
        "get_position",
        "async_get_tick",
        "async_make_order",
        "async_cancel_order",
        "async_get_balance",
    ]
    missing = [
        method_name
        for method_name in required_methods
        if not hasattr(feed_class, method_name)
        or not callable(getattr(feed_class, method_name, None))
    ]
    return missing
