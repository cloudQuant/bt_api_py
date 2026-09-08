"""Feed adapter bridging v1 order requests to venue feeds (Task 2.2)."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from bt_api_py._contracts.models import OrderRequest
from bt_api_py._venue_mappers import OrderMapper


class FeedAdapter:
    """Routes a v1 ``OrderRequest`` through a venue mapper into a feed call."""

    def __init__(self, feed: Any, mapper: OrderMapper) -> None:
        self._feed = feed
        self._mapper = mapper

    def _call_arguments(self, request: OrderRequest) -> tuple[tuple[Any, ...], dict[str, Any]]:
        args = self._mapper(request)
        positional_keys = {
            "symbol",
            "vol",
            "price",
            "order_type",
            "offset",
            "post_only",
            "client_order_id",
        }
        options = {key: value for key, value in args.items() if key not in positional_keys}
        positional = (
            args["symbol"],
            args["vol"],
            args["price"],
            args["order_type"],
        )
        options.update(
            offset=args["offset"],
            post_only=args["post_only"],
            client_order_id=args["client_order_id"],
        )
        return positional, options

    def make_order(self, request: OrderRequest) -> Any:
        positional, options = self._call_arguments(request)
        return self._feed.make_order(*positional, **options)

    async def async_make_order(self, request: OrderRequest) -> Any:
        """Await a native coroutine backend, or run its sync twin in a worker."""
        method = getattr(self._feed, "async_make_order", None)
        if not callable(method) or not inspect.iscoroutinefunction(method):
            return await asyncio.to_thread(self.make_order, request)
        positional, options = self._call_arguments(request)
        return await method(*positional, **options)
