# 性能优化指南

bt_api_py 的性能优化建议和最佳实践。

## 目录

- [网络优化](#网络优化)
- [数据缓存](#数据缓存)
- [并发处理](#并发处理)
- [内存管理](#内存管理)
- [数据库优化](#数据库优化)
- [监控工具](#监控工具)

- --

## 网络优化

### 使用 WebSocket 代替轮询

- *问题：** 轮询会增加网络延迟和服务器负载

- *解决：** 使用 WebSocket 订阅实时数据

```python

# ❌ 轮询方式（延迟高，资源消耗大）

while True:
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
    ticker.init_data()
    print(ticker.get_last_price())
    time.sleep(1)

# ✅ WebSocket 方式（实时，资源消耗低）

def on_ticker(ticker):
    ticker.init_data()
    print(ticker.get_last_price())

api.event_bus.subscribe("ticker", on_ticker)
api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT")
api.run()

```bash

- *性能对比：**

| 方式 | 延迟 | CPU | 网络 |

|------|------|-----|------|

| 轮询 (1 秒) | 0-1000ms | 高 | 高 |

| WebSocket | 0-50ms | 低 | 低 |

### 连接复用

```python

# ❌ 每次请求创建新连接

def get_price():
    api = BtApi(exchange_kwargs={...})  # 每次创建新连接
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
    return ticker

# ✅ 复用连接

api = BtApi(exchange_kwargs={...})  # 创建一次

def get_price():
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
    return ticker

```bash

### 批量请求合并

```python

# ❌ 多次单独请求

for symbol in symbols:
    ticker = api.get_tick("BINANCE___SPOT", symbol)

# ✅ 使用批量方法

ticks = api.get_all_ticks("BTCUSDT")  # 一次获取所有交易所价格

```bash

- --

## 数据缓存

### 内存缓存

```python
from functools import lru_cache
import time

class CachedApi:
    """带缓存的 API"""
    def __init__(self, api, cache_ttl=5):
        self.api = api
        self.cache_ttl = cache_ttl
        self._cache = {}

    def get_tick(self, exchange, symbol):
        """获取行情（带缓存）"""
        key = f"{exchange}:{symbol}"
        now = time.time()

# 检查缓存
        if key in self._cache:
            data, timestamp = self._cache[key]
            if now - timestamp < self.cache_ttl:
                return data

# 从 API 获取
        ticker = self.api.get_tick(exchange, symbol)
        ticker.init_data()
        self._cache[key] = (ticker, now)
        return ticker

# 性能提升：避免频繁 API 调用，减少 90% 重复请求

```bash

### Redis 缓存

```python
import redis
import pickle

class RedisCachedApi:
    """Redis 缓存的 API（多进程共享）"""
    def __init__(self, api, redis_url="redis://localhost:6379"):
        self.api = api
        self.redis = redis.from_url(redis_url)
        self.cache_ttl = 5  # 秒

    def get_tick(self, exchange, symbol):
        """获取行情（Redis 缓存）"""
        key = f"ticker:{exchange}:{symbol}"

# 尝试从缓存获取
        cached = self.redis.get(key)
        if cached:
            return pickle.loads(cached)

# 从 API 获取
        ticker = self.api.get_tick(exchange, symbol)
        ticker.init_data()

# 存入缓存
        self.redis.setex(
            key,
            self.cache_ttl,
            pickle.dumps(ticker)
        )

        return ticker

```bash

- --

## 并发处理

### 多线程并发

```python
import concurrent.futures
import time

def get_price(exchange, symbol):
    """获取单个价格"""
    ticker = api.get_tick(exchange, symbol)
    ticker.init_data()
    return exchange, ticker.get_last_price()

def get_all_prices_concurrent(exchanges, symbol):
    """并发获取所有交易所价格"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(get_price, exchange, symbol)
            for exchange in exchanges
        ]

        results = []
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    return dict(results)

# 性能提升：10 个交易所 5 秒获取完，串行需要 50 秒

```bash

### 异步处理

```python
import asyncio
import time

async def async_get_price(api, exchange, symbol):
    """异步获取价格"""
    loop = asyncio.get_running_loop()
    ticker = await loop.run_in_executor(
        None,
        lambda: api.get_tick(exchange, symbol)
    )
    ticker.init_data()
    return exchange, ticker.get_last_price()

async def get_all_prices_async(exchanges, symbol):
    """异步获取所有价格"""
    tasks = [
        async_get_price(api, exchange, symbol)
        for exchange in exchanges
    ]

    results = await asyncio.gather(*tasks)
    return dict(results)

# 使用

asyncio.run(get_all_prices_async(["BINANCE___SPOT", "OKX___SPOT"], "BTCUSDT"))

```bash

### 批量数据处理

```python
def process_klines_batch(klines):
    """批量处理 K 线数据"""

# 预分配列表大小
    closes = [0] *len(klines)
    volumes = [0]* len(klines)

    for i, bar in enumerate(klines):
        bar.init_data()
        closes[i] = bar.get_close_price()
        volumes[i] = bar.get_volume()

# 使用 numpy 进行高性能计算
    import numpy as np
    np_closes = np.array(closes)
    ma = np.convolve(np_closes, np.ones(20)/20, mode='valid')

    return ma

# 性能提升：批量处理比逐条处理快 3-5 倍

```bash

- --

## 内存管理

### 及时清理数据

```python

# ❌ 不推荐：累积大量数据

all_klines = []
while True:
    klines = api.get_kline("BINANCE___SPOT", "BTCUSDT", "1m", count=100)
    all_klines.extend(klines)

# 内存持续增长

# ✅ 推荐：只保留需要的数据

class CircularBuffer:
    """环形缓冲区"""
    def __init__(self, size):
        self.size = size
        self.buffer = []

    def add(self, items):
        self.buffer.extend(items)
        if len(self.buffer) > self.size:
            self.buffer = self.buffer[-self.size:]

    def get(self):
        return self.buffer

klines_buffer = CircularBuffer(1000)

```bash

### 使用生成器

```python

# ❌ 不推荐：返回大列表

def get_all_klines():
    klines = []
    for i in range(10000):
        kline = api.get_kline(...)
        klines.append(kline)
    return klines  # 占用大量内存

# ✅ 推荐：使用生成器

def get_klines_generator(count):
    """K 线生成器"""
    for i in range(count):
        klines = api.get_kline("BINANCE___SPOT", "BTCUSDT", "1m", count=100)
        for bar in klines:
            bar.init_data()
            yield bar

# 逐条处理，不占用大量内存

for bar in get_klines_generator(100):
    process(bar)

```bash

### 数据分片处理

```python
def process_large_dataset(data, chunk_size=1000):
    """分片处理大数据集"""
    for i in range(0, len(data), chunk_size):
        chunk = data[i:i+chunk_size]

# 处理这一批数据
        results = process_chunk(chunk)

# 释放内存
        del chunk

        yield results

# 使用

for result in process_large_dataset(large_dataset):
    save_to_db(result)

```bash

- --

## 数据库优化

### 批量插入

```python

# ❌ 不推荐：逐条插入

for bar in bars:
    cursor.execute("INSERT INTO klines VALUES (?,?,?,?)", (...))

# ✅ 推荐：批量插入

def batch_insert(cursor, bars, batch_size=1000):
    """批量插入"""
    for i in range(0, len(bars), batch_size):
        chunk = bars[i:i+batch_size]
        cursor.executemany(
            "INSERT INTO klines VALUES (?,?,?,?)",
            [
                (bar.get_open_time(), bar.get_close_price(), ...)
                for bar in chunk
            ]
        )
        conn.commit()

# 性能提升：批量插入比逐条插入快 10-100 倍

```bash

### 索引优化

```sql

- - 为常用查询字段添加索引

CREATE INDEX idx_symbol_time ON klines(symbol, open_time);
CREATE INDEX idx_price ON ticks(price);

- - 定期清理过期数据

DELETE FROM klines WHERE open_time < NOW() - INTERVAL '30 DAY';

```bash

### 连接池

```python
from sqlalchemy.pool import QueuePool

# 创建连接池

pool = QueuePool(
    create_function,
    max_overflow_size=10,
    pool_size=5
)

# 使用连接池

with pool.connect() as conn:
    process_data(conn)

```bash

- --

## 监控工具

### 性能分析器

```python
import cProfile
import pstats
from io import StringIO

def profile_api_call(func):
    """API 调用性能分析"""
    pr = cProfile.Profile()
    pr.enable()

    result = func()

    pr.disable()

# 输出分析结果
    s = StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
    ps.print_stats(10)  # 打印前 10 个最耗时的函数
    print(s.getvalue())

    return result

# 使用

@profile_api_call
def test_function():
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
    return ticker

```bash

### 性能监控装饰器

```python
import time
from functools import wraps

def monitor_performance(func):
    """性能监控装饰器"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        start_memory = get_memory_usage()

        try:
            result = func(*args, **kwargs)
            return result
        finally:
            elapsed = time.time() - start_time
            end_memory = get_memory_usage()
            memory_used = end_memory - start_memory

            print(f"{func.__name__}:")
            print(f"  耗时: {elapsed:.3f}秒")
            print(f"  内存: {memory_used:.2f}MB")

    return wrapper

def get_memory_usage():
    """获取当前内存使用量"""
    import psutil
    return psutil.Process().memory_info().rss / 1024 / 1024

```bash

### 实时性能监控

```python
class PerformanceMonitor:
    """实时性能监控"""

    def __init__(self, api):
        self.api = api
        self.metrics = {
            "api_calls": 0,
            "errors": 0,
            "latencies": [],
        }

    def track_call(self, func, *args, **kwargs):
        """跟踪 API 调用"""
        start_time = time.time()

        try:
            result = func(*args, **kwargs)
            self.metrics["api_calls"] += 1
            return result
        except Exception:
            self.metrics["errors"] += 1
            raise
        finally:
            latency = time.time() - start_time
            self.metrics["latencies"].append(latency)

    def get_stats(self):
        """获取统计信息"""
        latencies = self.metrics["latencies"]
        return {
            "total_calls": self.metrics["api_calls"],
            "errors": self.metrics["errors"],
            "avg_latency": sum(latencies) / len(latencies) if latencies else 0,
            "max_latency": max(latencies) if latencies else 0,
            "p99_latency": sorted(latencies)[int(len(latencies) * 0.99)] if latencies else 0,
        }

# 使用

monitor = PerformanceMonitor(api)
ticker = monitor.track_call(api.get_tick, "BINANCE___SPOT", "BTCUSDT")
print(monitor.get_stats())

```bash

- --

## 性能优化清单

### 网络层优化

- [ ] 使用 WebSocket 代替轮询
- [ ] 启用连接复用
- [ ] 批量请求合并
- [ ] 配置合理的超时时间

### 应用层优化

- [ ] 使用缓存减少 API 调用
- [ ] 并发处理独立请求
- [ ] 异步处理耗时操作
- [ ] 使用数据结构优化算法

### 内存优化

- [ ] 及时清理不需要的数据
- [ ] 使用生成器处理大数据集
- [ ] 避免内存泄漏
- [ ] 监控内存使用情况

### 数据库优化

- [ ] 批量插入代替逐条插入
- [ ] 为常用字段添加索引
- [ ] 定期清理过期数据
- [ ] 使用连接池

### 监控优化

- [ ] 添加性能监控
- [ ] 记录关键指标
- [ ] 设置告警阈值
- [ ] 定期分析性能瓶颈

- --

## 性能基准

### 操作性能参考

| 操作 | 预期耗时 | 优化后 |

|------|----------|--------|

| 获取单个 ticker | 50-200ms | 30-100ms |

| 获取深度 (20 档) | 100-300ms | 50-150ms |

| 获取 K 线 (100 根) | 200-500ms | 100-300ms |

| 下单 | 100-400ms | 50-200ms |

| 撤单 | 50-200ms | 30-100ms |

| 查询订单 | 50-200ms | 30-100ms |

### 并发性能参考

| 并发数 | 吞吐量 (QPS) | 延迟 (P99) |

|--------|--------------|----------|

| 1 | 50 | 100ms |

| 5 | 200 | 150ms |

| 10 | 350 | 200ms |

| 20 | 500 | 300ms |

- --

## 相关文档

- [API 示例](examples/api_examples.md)
- [错误处理](error_handling.md)
- [最佳实践](best_practices.md)
