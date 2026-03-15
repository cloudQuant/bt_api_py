"""统一限流器.

支持滑动窗口、固定窗口两种限流模式，以及端点级 glob 匹配和权重映射。
对非 HTTP 场所可关闭或替换为场所内置流控。
"""

import asyncio
import contextlib
import enum
import fnmatch
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import unique
from typing import Any

__all__ = [
    "RateLimitType",
    "RateLimitScope",
    "RateLimitRule",
    "SlidingWindowLimiter",
    "FixedWindowLimiter",
    "RateLimiter",
]


@unique
class RateLimitType(enum.StrEnum):
    SLIDING_WINDOW = "sliding_window"
    FIXED_WINDOW = "fixed_window"
    TOKEN_BUCKET = "token_bucket"


@unique
class RateLimitScope(enum.StrEnum):
    GLOBAL = "global"
    ENDPOINT = "endpoint"
    IP = "ip"


@dataclass
class RateLimitRule:
    """限流规则."""

    name: str
    type: RateLimitType
    interval: int  # 时间窗口（秒）
    limit: int  # 限制数量
    scope: RateLimitScope = RateLimitScope.GLOBAL
    endpoint: str | None = None  # 端点匹配（支持 glob）
    weight_map: dict[str, int] | None = None  # {method_or_key: weight}
    weight: int = 1  # 默认权重

    def match(self, method: str, path: str) -> bool:
        """判断请求是否匹配此规则."""
        if self.scope == RateLimitScope.GLOBAL:
            return True
        if self.scope == RateLimitScope.ENDPOINT and self.endpoint:
            return fnmatch.fnmatch(path, self.endpoint)
        return False

    def get_weight(self, method: str, path: str) -> int:
        """获取请求权重."""
        if self.weight_map:
            key = f"{method} {path}"
            if key in self.weight_map:
                return self.weight_map[key]
            if method in self.weight_map:
                return self.weight_map[method]
        return self.weight


class SlidingWindowLimiter:
    """滑动窗口限流器."""

    def __init__(self, interval: int, limit: int) -> None:
        self.interval = interval
        self.limit = limit
        self._requests: deque[tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def _prune_expired_locked(self, now: float) -> None:
        cutoff = now - self.interval
        while self._requests and self._requests[0][0] < cutoff:
            self._requests.popleft()

    def acquire(self, weight: int = 1) -> bool:
        with self._lock:
            now = time.time()
            self._prune_expired_locked(now)
            current_weight = sum(w for _, w in self._requests)
            if current_weight + weight <= self.limit:
                self._requests.append((now, weight))
                return True
            return False

    def release(self, weight: int = 1) -> None:
        with self._lock:
            now = time.time()
            self._prune_expired_locked(now)
            if self._requests and self._requests[-1][1] == weight:
                self._requests.pop()

    def wait_time(self) -> float:
        with self._lock:
            if not self._requests:
                return 0.0
            oldest, _ = self._requests[0]
            return max(0.0, oldest + self.interval - time.time())

    @property
    def current_usage(self) -> int:
        with self._lock:
            now = time.time()
            self._prune_expired_locked(now)
            return sum(w for _, w in self._requests)


class FixedWindowLimiter:
    """固定窗口限流器."""

    def __init__(self, interval: int, limit: int) -> None:
        self.interval = interval
        self.limit = limit
        self._window_start = 0.0
        self._window_count = 0
        self._lock = threading.Lock()

    def acquire(self, weight: int = 1) -> bool:
        with self._lock:
            now = time.time()
            window_start = int(now // self.interval) * self.interval

            if window_start != self._window_start:
                self._window_start = window_start
                self._window_count = 0

            if self._window_count + weight <= self.limit:
                self._window_count += weight
                return True
            return False

    def release(self, weight: int = 1) -> None:
        with self._lock:
            self._window_count = max(0, self._window_count - weight)

    def wait_time(self) -> float:
        with self._lock:
            now = time.time()
            window_end = self._window_start + self.interval
            return max(0.0, window_end - now)

    @property
    def current_usage(self) -> int:
        with self._lock:
            return self._window_count


class RateLimiter:
    """统一限流器.

    使用方式::

        rules = [
            RateLimitRule(name="global", type=RateLimitType.SLIDING_WINDOW,
                          interval=60, limit=1200, scope=RateLimitScope.GLOBAL),
            RateLimitRule(name="order", type=RateLimitType.FIXED_WINDOW,
                          interval=1, limit=100, scope=RateLimitScope.ENDPOINT,
                          endpoint="/api/v3/order*",
                          weight_map={"POST": 10, "DELETE": 5, "GET": 1}),
        ]
        limiter = RateLimiter(rules)

        # 同步使用
        if limiter.acquire("POST", "/api/v3/order"):
            # 发送请求
            ...
        else:
            limiter.wait_and_acquire("POST", "/api/v3/order")

        # 异步使用
        await limiter.async_acquire("POST", "/api/v3/order")

        # 上下文管理器使用（自动等待并获取许可）
        with limiter:
            # 发送请求
            ...
    """

    def __init__(self, rules: list[RateLimitRule] | None = None) -> None:
        self.rules = rules or []
        self._limiters: dict[str, Any] = {}
        self._lock = threading.Lock()

        for rule in self.rules:
            if rule.type == RateLimitType.SLIDING_WINDOW:
                self._limiters[rule.name] = SlidingWindowLimiter(rule.interval, rule.limit)
            elif rule.type == RateLimitType.FIXED_WINDOW:
                self._limiters[rule.name] = FixedWindowLimiter(rule.interval, rule.limit)

    def _get_matched_rules(self, method: str, path: str) -> list[RateLimitRule]:
        return [rule for rule in self.rules if rule.match(method, path)]

    def _get_max_wait_time(self, method: str, path: str) -> float:
        max_wait = 0.0
        for rule in self._get_matched_rules(method, path):
            limiter = self._limiters.get(rule.name)
            if limiter is not None:
                max_wait = max(max_wait, limiter.wait_time())
        return max_wait

    def __enter__(self):
        """进入上下文管理器（阻塞等待获取许可）."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文管理器."""
        return False

    def acquire(self, method: str = "", path: str = "", weight: int = 1) -> bool:
        """同步获取限流许可（不阻塞）."""
        if not self.rules:
            return True

        matched_rules = self._get_matched_rules(method, path)
        if not matched_rules:
            return True

        with self._lock:
            acquired_limiters: list[tuple[Any, int]] = []
            for rule in matched_rules:
                limiter = self._limiters.get(rule.name)
                if limiter is None:
                    continue
                request_weight = rule.get_weight(method, path) * weight
                if limiter.acquire(request_weight):
                    acquired_limiters.append((limiter, request_weight))
                    continue
                for acquired_limiter, acquired_weight in reversed(acquired_limiters):
                    release = getattr(acquired_limiter, "release", None)
                    if release is not None:
                        release(acquired_weight)
                return False
            return True

    def wait_and_acquire(
        self,
        method: str = "",
        path: str = "",
        weight: int = 1,
        timeout: float | None = None,
    ) -> bool:
        """同步阻塞获取许可."""
        start = time.monotonic()
        while True:
            if self.acquire(method, path, weight):
                return True
            wait_interval = self._get_max_wait_time(method, path)
            effective_wait = wait_interval if wait_interval > 0 else 0.05
            if timeout is not None and (time.monotonic() - start + effective_wait) > timeout:
                return False
            threading.Event().wait(min(effective_wait, 1.0))

    async def async_acquire(
        self,
        method: str = "",
        path: str = "",
        weight: int = 1,
        timeout: float | None = None,
    ) -> bool:
        """异步获取限流许可（自动等待）."""
        start = time.monotonic()
        while True:
            if self.acquire(method, path, weight):
                return True
            wait_interval = self._get_max_wait_time(method, path)
            effective_wait = wait_interval if wait_interval > 0 else 0.05
            if timeout is not None and (time.monotonic() - start + effective_wait) > timeout:
                return False
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.Event().wait(), timeout=effective_wait)

    def get_status(self) -> dict[str, dict]:
        """获取各限流器状态（用于监控）."""
        status = {}
        for rule in self.rules:
            limiter = self._limiters.get(rule.name)
            if limiter:
                status[rule.name] = {
                    "type": rule.type.value,
                    "limit": rule.limit,
                    "interval": rule.interval,
                    "current": limiter.current_usage,
                }
        return status
