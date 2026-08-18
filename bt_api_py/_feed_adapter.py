"""Feed adapter bridging v1 order requests to venue feeds (Task 2.2)."""

from __future__ import annotations

from typing import Any

from bt_api_py._contracts.models import OrderRequest
from bt_api_py._venue_mappers import OrderMapper


class FeedAdapter:
    """Routes a v1 ``OrderRequest`` through a venue mapper into a feed call."""

    def __init__(self, feed: Any, mapper: OrderMapper) -> None:
        self._feed = feed
        self._mapper = mapper

    def make_order(self, request: OrderRequest) -> Any:
        args = self._mapper(request)
        return self._feed.make_order(
            args["symbol"],
            args["vol"],
            args["price"],
            args["order_type"],
            offset=args["offset"],
            post_only=args["post_only"],
            client_order_id=args["client_order_id"],
        )
