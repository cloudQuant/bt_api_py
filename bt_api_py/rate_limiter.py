"""
统一限流器

支持滑动窗口、固定窗口两种限流模式，以及端点级 glob 匹配和权重映射。
对非 HTTP 场所可关闭或替换为场所内置流控。
"""

import asyncio
import fnmatch
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, Dict, List, Optional


@unique
class RateLimitType(str, Enum):
    SLIDING_WINDOW = "sliding_window"
    FIXED_WINDOW = "fixed_window"
    TOKEN_BUCKET = "token_bucket"


@unique
class RateLimitScope(str, Enum):
    GLOBAL = "global"
    ENDPOINT = "endpoint"
    IP = "ip"


@dataclass
class RateLimitRule:
    """限流规则"""

    name: str
    type: RateLimitType
    interval: int  # 时间窗口（秒）
    limit: int  # 限制数量
    scope: RateLimitScope = RateLimitScope.GLOBAL
    endpoint: Optional[str] = None  # 端点匹配（支持 glob）
    weight_map: Optional[Dict[str, int]] = None  # {method_or_key: weight}
    weight: int = 1  # 默认权重

    def match(self, method: str, path: str) -> bool:
        """判断请求是否匹配此规则"""
        if self.scope == RateLimitScope.GLOBAL:
            return True
        if self.scope == RateLimitScope.ENDPOINT and self.endpoint:
            return fnmatch.fnmatch(path, self.endpoint)
        return False

    def get_weight(self, method: str, path: str) -> int:
        """获取请求权重"""
        if self.weight_map:
            key = f"{method} {path}"
            if key in self.weight_map:
                return self.weight_map[key]
            if method in self.weight_map:
                return self.weight_map[method]
        return self.weight


class SlidingWindowLimiter:
    """滑动窗口限流器"""

    def __init__(self, interval: int, limit: int):
        self.interval = interval
        self.limit = limit
        self._requests: deque = deque()  # (timestamp, weight)
        self._lock = threading.Lock()

    def acquire(self, weight: int = 1) -> bool:
        with self._lock:
            now = time.time()
            cutoff = now - self.interval
            while self._requests and self._requests[0][0] < cutoff:
                self._requests.popleft()

            current_weight = sum(w for _, w in self._requests)
            if current_weight + weight <= self.limit:
                self._requests.append((now, weight))
                return True
            return False

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
            cutoff = now - self.interval
            return sum(w for t, w in self._requests if t >= cutoff)


class FixedWindowLimiter:
    """固定窗口限流器"""

    def __init__(self, interval: int, limit: int):
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
    """统一限流器

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
    """

    def __init__(self, rules: Optional[List[RateLimitRule]] = None):
        self.rules = rules or []
        self._limiters: Dict[str, Any] = {}

        for rule in self.rules:
            if rule.type == RateLimitType.SLIDING_WINDOW:
                self._limiters[rule.name] = SlidingWindowLimiter(rule.interval, rule.limit)
            elif rule.type == RateLimitType.FIXED_WINDOW:
                self._limiters[rule.name] = FixedWindowLimiter(rule.interval, rule.limit)

    def acquire(self, method: str = "", path: str = "", weight: int = 1) -> bool:
        """同步获取限流许可（不阻塞）"""
        if not self.rules:
            return True

        matched_rules = [r for r in self.rules if r.match(method, path)]
        if not matched_rules:
            return True

        for rule in matched_rules:
            limiter = self._limiters.get(rule.name)
            if limiter is None:
                continue
            request_weight = rule.get_weight(method, path) * weight
            if not limiter.acquire(request_weight):
                return False
        return True

    def wait_and_acquire(
        self,
        method: str = "",
        path: str = "",
        weight: int = 1,
        timeout: Optional[float] = None,
    ) -> bool:
        """同步阻塞获取许可"""
        start = time.time()
        while True:
            if self.acquire(method, path, weight):
                return True
            # 找到最长等待时间
            max_wait = 0.0
            matched_rules = [r for r in self.rules if r.match(method, path)]
            for rule in matched_rules:
                limiter = self._limiters.get(rule.name)
                if limiter:
                    max_wait = max(max_wait, limiter.wait_time())
            if timeout is not None and (time.time() - start + max_wait) > timeout:
                return False
            time.sleep(min(max_wait, 1.0) if max_wait > 0 else 0.05)

    async def async_acquire(self, method: str = "", path: str = "", weight: int = 1) -> bool:
        """异步获取限流许可（自动等待）"""
        while True:
            if self.acquire(method, path, weight):
                return True
            await asyncio.sleep(0.05)

    def get_status(self) -> Dict[str, Dict]:
        """获取各限流器状态（用于监控）"""
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
