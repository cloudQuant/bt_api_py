from __future__ import annotations

import time

import pytest

from bt_api_py.core.services import CacheService, EventService, RateLimitService


@pytest.mark.asyncio
async def test_event_service_publish_async_falls_back_to_async_handlers() -> None:
    service = EventService()
    received: list[dict[str, str]] = []

    async def async_handler(event: dict[str, str]) -> None:
        received.append(event)

    service.subscribe("topic", async_handler)

    await service.publish_async("topic", {"data": "async"})

    assert received == [{"data": "async"}]


@pytest.mark.asyncio
async def test_cache_service_local_cache_isolated_from_mutation() -> None:
    service = CacheService()
    original = {"outer": {"value": 1}}

    await service.set("key", original)
    original["outer"]["value"] = 2

    cached_first = await service.get("key")
    assert cached_first == {"outer": {"value": 1}}

    assert isinstance(cached_first, dict)
    cached_first["outer"]["value"] = 3

    cached_second = await service.get("key")
    assert cached_second == {"outer": {"value": 1}}


@pytest.mark.asyncio
async def test_rate_limit_service_respects_configured_remaining_tokens_before_first_acquire() -> None:
    service = RateLimitService()

    service.configure_limit("orders", 3, 60.0)

    assert service.get_remaining_tokens("orders") == 3
    assert service.get_reset_time("orders") is None


@pytest.mark.asyncio
async def test_rate_limit_service_acquire_consumes_requested_token_count() -> None:
    service = RateLimitService()

    service.configure_limit("orders", 3, 60.0)

    await service.acquire("orders", tokens=2)

    assert service.get_remaining_tokens("orders") == 1
    assert service.get_reset_time("orders") is not None


@pytest.mark.asyncio
async def test_rate_limit_service_rejects_non_positive_token_request() -> None:
    service = RateLimitService()

    with pytest.raises(ValueError, match="tokens must be > 0"):
        await service.acquire("orders", tokens=0)


@pytest.mark.asyncio
async def test_rate_limit_service_prunes_expired_requests_for_introspection() -> None:
    service = RateLimitService()

    service.configure_limit("orders", 2, 0.5)
    await service.acquire("orders")

    limiter = service._limiters["orders"]
    limiter.requests = [time.time() - 10]

    assert service.get_remaining_tokens("orders") == 2
    assert service.get_reset_time("orders") is None
