# 最佳实践指南

bt_api_py 开发和使用的最佳实践。

---

## 代码规范

### 使用类型提示

```python
from bt_api_py import BtApi
from typing import Optional

def get_current_price(api: BtApi, exchange: str, symbol: str) -> Optional[float]:
    """获取当前价格"""
    try:
        ticker = api.get_tick(exchange, symbol)
        ticker.init_data()
        return ticker.get_last_price()
    except Exception:
        return None

```

### 使用常量定义

```python

# 交易所常量

EXCHANGE_BINANCE_SPOT = "BINANCE___SPOT"
EXCHANGE_OKX_SPOT = "OKX___SPOT"
EXCHANGE_CTP_FUTURE = "CTP___FUTURE"

# 订单类型常量

ORDER_TYPE_LIMIT = "limit"
ORDER_TYPE_MARKET = "market"

# 订单方向常量

ORDER_SIDE_BUY = "buy"
ORDER_SIDE_SELL = "sell"

# 使用

ticker = api.get_tick(EXCHANGE_BINANCE_SPOT, "BTCUSDT")

```

### 配置管理

```python

# config.py

import yaml
from dataclasses import dataclass

@dataclass
class ExchangeConfig:
    api_key: str
    secret: str
    passphrase: str = ""
    testnet: bool = True

def load_config(config_file: str) -> dict:
    """加载配置文件"""
    with open(config_file) as f:
        config = yaml.safe_load(f)

    return {
        "BINANCE___SPOT": ExchangeConfig(**config["binance"]),
        "OKX___SPOT": ExchangeConfig(**config["okx"]),
    }

# 使用

exchange_kwargs = load_config("config.yaml")
api = BtApi(exchange_kwargs=exchange_kwargs)

```

---

## API 使用

### 初始化连接

```python

# ❌ 不推荐：每次创建新实例

def get_price():
    api = BtApi(exchange_kwargs={...})
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
    ticker.init_data()
    return ticker

# ✅ 推荐：复用实例

api = BtApi(exchange_kwargs={...})

def get_price():
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
    ticker.init_data()
    return ticker

```

### 连接池管理

```python
class ExchangePool:
    """交易所连接池"""
    def __init__(self):
        self.apis = {}

    def get_api(self, exchange_name):
        """获取 API 实例"""
        if exchange_name not in self.apis:
            self.apis[exchange_name] = self._create_api(exchange_name)
        return self.apis[exchange_name]

    def _create_api(self, exchange_name):
        """创建 API 实例"""

# 从配置加载
        config = load_config(exchange_name)
        return BtApi(exchange_kwargs={exchange_name: config})

# 使用

pool = ExchangePool()
binance_api = pool.get_api("BINANCE___SPOT")

```

### 批量操作优化

```python

# ❌ 不推荐：串行请求

for symbol in ["BTCUSDT", "ETHUSDT", "BNBUSDT"]:
    ticker = api.get_tick("BINANCE___SPOT", symbol)
    ticker.init_data()
    print(ticker.get_last_price())

# ✅ 推荐：使用批量方法

ticks = api.get_all_ticks("BTCUSDT")
for exchange, ticker in ticks.items():
    ticker.init_data()
    print(f"{exchange}: {ticker.get_last_price()}")

```

### 资源清理

```python
from contextlib import contextmanager

@contextmanager
def api_context(exchange_kwargs):
    """API 上下文管理器"""
    api = BtApi(exchange_kwargs=exchange_kwargs)
    try:
        yield api
    finally:

# 清理资源
        for exchange in api.list_exchanges():

# 取消订阅
            api.cancel_all(exchange)
        print("API 连接已关闭")

# 使用

with api_context({...}) as api:
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
    print(ticker.get_last_price())

```

---

## 性能优化

### 使用异步接口

```python
import asyncio

async def get_multiple_prices(api, symbols):
    """并发获取多个价格"""
    loop = asyncio.get_running_loop()
    tasks = []

    for symbol in symbols:
        task = loop.run_in_executor(
            None,
            lambda s=symbol: api.get_tick("BINANCE___SPOT", s)
        )
        tasks.append(task)

    results = await asyncio.gather(*tasks)

    for ticker in results:
        ticker.init_data()
        print(ticker.get_last_price())

# 使用

asyncio.run(get_multiple_prices(api, ["BTCUSDT", "ETHUSDT"]))

```

### 数据缓存

```python
from functools import lru_cache
import time

class CachedTicker:
    """带缓存的行情获取"""
    def __init__(self, api, ttl=5):
        self.api = api
        self.ttl = ttl  # 缓存过期时间（秒）
        self.cache = {}

    def get_tick(self, exchange, symbol):
        """获取行情（带缓存）"""
        key = f"{exchange}:{symbol}"
        now = time.time()

        if key in self.cache:
            data, timestamp = self.cache[key]
            if now - timestamp < self.ttl:
                return data

# 从 API 获取
        ticker = self.api.get_tick(exchange, symbol)
        ticker.init_data()
        self.cache[key] = (ticker, now)
        return ticker

# 使用

cached_ticker = CachedTicker(api, ttl=5)
ticker = cached_ticker.get_tick("BINANCE___SPOT", "BTCUSDT")

```

### WebSocket 代替轮询

```python

# ❌ 不推荐：轮询获取价格

while True:
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
    ticker.init_data()
    print(ticker.get_last_price())
    time.sleep(1)

# ✅ 推荐：WebSocket 订阅

def on_ticker(ticker):
    ticker.init_data()
    print(ticker.get_last_price())

api.event_bus.subscribe("ticker", on_ticker)
api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT")
api.run()

```

---

## 安全实践

### API Key 管理

```python

# ❌ 不推荐：硬编码 API Key

api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "abc123...",
        "secret": "xyz789...",
    }
})

# ✅ 推荐：使用环境变量

import os

api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": os.getenv("BINANCE_API_KEY"),
        "secret": os.getenv("BINANCE_SECRET"),
    }
})

# 或使用配置文件（不提交到 Git）

# config.yaml (在 .gitignore 中)

# binance:

# api_key: abc123...

# secret: xyz789...

```

### 使用测试网络

```python

# 开发时使用测试网络

api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "...",
        "secret": "...",
        "testnet": True,  # 开发时使用测试网
    }
})

# 生产环境通过环境变量控制

TESTNET = os.getenv("TESTNET", "true").lower() == "true"

```

### 订单金额限制

```python
MAX_ORDER_SIZE = 0.1  # BTC

MAX_ORDER_VALUE = 10000  # USDT

def validate_order(symbol, volume, price):
    """验证订单参数"""
    order_value = volume * price

    if volume > MAX_ORDER_SIZE:
        raise ValueError(f"订单数量过大: {volume} > {MAX_ORDER_SIZE}")

    if order_value > MAX_ORDER_VALUE:
        raise ValueError(f"订单金额过大: {order_value} > {MAX_ORDER_VALUE}")

    return True

# 使用

if validate_order("BTCUSDT", 0.001, 50000):
    api.make_order("BINANCE___SPOT", "BTCUSDT", 0.001, 50000, "limit")

```

---

## 测试策略

### 单元测试

```python
import pytest
from bt_api_py import BtApi

@pytest.fixture
def test_api():
    """测试用 API"""
    return BtApi(exchange_kwargs={
        "BINANCE___SPOT": {
            "api_key": "test_key",
            "secret": "test_secret",
            "testnet": True,
        }
    })

def test_get_tick(test_api):
    """测试获取行情"""
    ticker = test_api.get_tick("BINANCE___SPOT", "BTCUSDT")
    assert ticker is not None

def test_api_error_handling(test_api):
    """测试错误处理"""
    with pytest.raises(ExchangeNotFoundError):
        test_api.get_tick("INVALID_EXCHANGE", "BTCUSDT")

```

### Mock 测试

```python
from unittest.mock import Mock, patch

def test_with_mock():
    """使用 mock 测试"""

# Mock ticker 数据
    mock_ticker = Mock()
    mock_ticker.get_last_price.return_value = 50000

    with patch.object(api, 'get_tick', return_value=mock_ticker):
        ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
        assert ticker.get_last_price() == 50000

```

---

## 部署建议

### Docker 容器化

```dockerfile

# Dockerfile

FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
CMD ["python", "main.py"]

```

### 健康检查

```python
def health_check(api):
    """健康检查"""
    try:

# 检查所有交易所连接
        for exchange in api.list_exchanges():

# 尝试获取账户信息
            api.get_account(exchange)
        return True
    except Exception as e:
        print(f"健康检查失败: {e}")
        return False

# 定期健康检查

import schedule

def run_health_check():
    while True:
        if not health_check(api):
            print("检测到异常，尝试重新连接...")
        schedule.run_pending()
        time.sleep(60)

# 后台运行

import threading
threading.Thread(target=run_health_check, daemon=True).start()

```

### 日志轮转

```python
import logging
from logging.handlers import RotatingFileHandler

# 设置日志轮转

handler = RotatingFileHandler(
    'bt_api.log',
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5
)

logger = logging.getLogger("bt_api")
logger.addHandler(handler)
logger.setLevel(logging.INFO)

```

---

## 代码组织

### 项目结构

```
project/
├── config/
│   ├── __init__.py
│   └── exchanges.py        # 交易所配置

├── strategies/
│   ├── __init__.py
│   ├── base.py             # 策略基类

│   └── grid.py             # 网格策略

├── utils/
│   ├── __init__.py
│   ├── error_handler.py    # 错误处理

│   └── logger.py           # 日志工具

└── main.py                 # 主程序

```

### 策略基类

```python
class BaseStrategy:
    """策略基类"""

    def __init__(self, api, exchange, symbol):
        self.api = api
        self.exchange = exchange
        self.symbol = symbol
        self.event_bus = api.get_event_bus()
        self.running = False

    def start(self):
        """启动策略"""
        self.running = True
        self.setup()

    def stop(self):
        """停止策略"""
        self.running = False
        self.teardown()

    def setup(self):
        """策略初始化（子类实现）"""
        pass

    def teardown(self):
        """策略清理（子类实现）"""
        pass

    def on_tick(self, ticker):
        """行情回调（子类实现）"""
        pass

    def on_order(self, order):
        """订单回调（子类实现）"""
        pass

```

---

## 相关文档

- [API 示例](examples/api_examples.md)
- [错误处理](error_handling.md)
- [性能优化](performance.md)
