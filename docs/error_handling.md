# 错误处理指南

bt_api_py 的错误处理和异常管理最佳实践。

## 目录

- [异常类型](#异常类型)
- [错误捕获](#错误捕获)
- [重试机制](#重试机制)
- [日志记录](#日志记录)
- [常见错误](#常见错误)
- [最佳实践](#最佳实践)

- --

## 异常类型

### 内置异常类

| 异常类 | 继承自 | 说明 |

|--------|--------|------|

| `BtApiError` | `Exception` | 所有 bt_api_py 异常的基类 |

| `ExchangeNotFoundError` | `BtApiError` | 交易所不存在或未初始化 |

| `ConnectionError` | `BtApiError` | 连接错误 |

| `RateLimitError` | `BtApiError` | 请求频率限制 |

| `OrderError` | `BtApiError` | 订单操作错误 |

| `AuthenticationError` | `BtApiError` | 认证失败 |

### 订单状态

| 状态 | 说明 | 处理建议 |

|------|------|----------|

| `new` | 订单已接受 | 等待成交 |

| `partially_filled` | 部分成交 | 可继续等待或撤销 |

| `filled` | 完全成交 | 订单完成 |

| `canceled` | 已撤销 | 订单已取消 |

| `rejected` | 已拒绝 | 检查订单参数 |

| `expired` | 已过期 | 重新下单 |

- --

## 错误捕获

### 基础异常捕获

```python
from bt_api_py import BtApi
from bt_api_py.exceptions import BtApiError, ExchangeNotFoundError

try:
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
    ticker.init_data()
    print(ticker.get_last_price())
except ExchangeNotFoundError as e:
    print(f"交易所不存在: {e}")
except BtApiError as e:
    print(f"API 错误: {e}")
except Exception as e:
    print(f"未知错误: {e}")

```bash

### 精细化错误处理

```python
def safe_api_call(func, *args, **kwargs):
    """安全的 API 调用包装器"""
    try:
        result = func(*args, **kwargs)
        if hasattr(result, 'init_data'):
            result.init_data()
        return result
    except ExchangeNotFoundError:
        print("错误: 交易所未初始化")
        return None
    except ConnectionError:
        print("错误: 连接失败，请检查网络")
        return None
    except RateLimitError:
        print("错误: 请求过于频繁，请稍后重试")
        return None
    except OrderError as e:
        print(f"订单错误: {e}")
        return None
    except Exception as e:
        print(f"未知错误: {e}")
        return None

# 使用

ticker = safe_api_call(api.get_tick, "BINANCE___SPOT", "BTCUSDT")
if ticker:
    print(ticker.get_last_price())

```bash

### 上下文管理器

```python
from contextlib import contextmanager

@contextmanager
def error_handler(api, exchange_name):
    """错误处理上下文管理器"""
    try:
        yield
    except Exception as e:
        api.log(f"操作失败: {e}", level="error")

# 可以在这里添加错误恢复逻辑
        raise

# 使用

with error_handler(api, "BINANCE___SPOT"):
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
    ticker.init_data()

```bash

- --

## 重试机制

### 指数退避重试

```python
import time

def retry_api_call(func, max_retries=3, initial_delay=1, backoff_factor=2):
    """带指数退避的重试装饰器"""
    def wrapper(*args, **kwargs):
        delay = initial_delay
        last_exception = None

        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except (ConnectionError, RateLimitError) as e:
                last_exception = e
                if attempt < max_retries - 1:
                    print(f"请求失败，{delay}秒后重试... (尝试 {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                    delay *= backoff_factor
                else:
                    break
            except Exception:

# 非网络错误不重试
                raise

        raise last_exception

    return wrapper

# 使用

@retry_api_call(max_retries=5)
def get_ticker_with_retry(symbol):
    ticker = api.get_tick("BINANCE___SPOT", symbol)
    ticker.init_data()
    return ticker

```bash

### 条件重试

```python
def retry_until_success(func, condition, max_attempts=10, delay=1):
    """直到满足条件或达到最大尝试次数"""
    for attempt in range(max_attempts):
        try:
            result = func()
            if condition(result):
                return result
            print(f"条件不满足，重试 {attempt + 1}/{max_attempts}")
        except Exception as e:
            print(f"尝试 {attempt + 1} 失败: {e}")

        if attempt < max_attempts - 1:
            time.sleep(delay)

    return None

# 使用

def check_order_filled(order):
    return order.get_order_status() == "filled"

order = retry_until_success(
    lambda: api.query_order("BINANCE___SPOT", "BTCUSDT", "123456"),
    check_order_filled
)

```bash

- --

## 日志记录

### 配置日志

```python
from bt_api_py.functions.log_message import SpdLogManager

# 创建日志记录器

logger = SpdLogManager(
    file_name='bt_api.log',
    logger_name="api",
    print_info=True
).create_logger()

# 添加日志处理器

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

```bash

### 结构化日志

```python
import logging
import json

class StructuredLogger:
    def __init__(self, name):
        self.logger = logging.getLogger(name)

    def log_api_call(self, method, exchange, symbol, **kwargs):
        """记录 API 调用"""
        self.logger.info(json.dumps({
            "event": "api_call",
            "method": method,
            "exchange": exchange,
            "symbol": symbol,
            "params": kwargs
        }))

    def log_error(self, error, context=None):
        """记录错误"""
        self.logger.error(json.dumps({
            "event": "error",
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": context
        }))

# 使用

logger = StructuredLogger("bt_api")
try:
    ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
except Exception as e:
    logger.log_error(e, context={"exchange": "BINANCE___SPOT", "symbol": "BTCUSDT"})

```bash

### 性能日志

```python
import time
from functools import wraps

def log_performance(func):
    """记录函数执行时间"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time
            api.log(f"{func.__name__} 成功，耗时: {elapsed:.3f}秒")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            api.log(f"{func.__name__} 失败，耗时: {elapsed:.3f}秒，错误: {e}")
            raise
    return wrapper

# 使用

@log_performance
def get_ticker(symbol):
    return api.get_tick("BINANCE___SPOT", symbol)

```bash

- --

## 常见错误

### 网络错误

```python

# 错误: ConnectionError / Timeout

# 原因: 网络不稳定或服务器无响应

# 处理: 重试机制

def handle_network_error():
    max_retries = 3
    for attempt in range(max_retries):
        try:
            ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
            return ticker
        except ConnectionError:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 1, 2, 4 秒
            else:
                raise

```bash

### 认证错误

```python

# 错误: AuthenticationError / 401

# 原因: API Key 错误或过期

# 处理: 检查配置并提示用户

def check_auth_config(api, exchange_name):
    """检查认证配置"""
    try:
        api.get_account(exchange_name)
        print("认证成功")
        return True
    except AuthenticationError:
        print(f"认证失败，请检查 {exchange_name} 的 API Key 配置")
        return False

```bash

### 频率限制

```python

# 错误: RateLimitError / 429

# 原因: 请求过于频繁

# 处理: 添加请求限流

from threading import Lock
import time

class RateLimiter:
    def __init__(self, max_calls, time_window):
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = []
        self.lock = Lock()

    def acquire(self):
        with self.lock:
            now = time.time()

# 移除时间窗口外的记录
            self.calls = [t for t in self.calls if now - t < self.time_window]

            if len(self.calls) >= self.max_calls:
                sleep_time = self.time_window - (now - self.calls[0])
                if sleep_time > 0:
                    time.sleep(sleep_time)
                    self.calls = []

            self.calls.append(now)

# 使用

limiter = RateLimiter(max_calls=10, time_window=1)  # 每秒最多 10 次

def rate_limited_get_tick(symbol):
    limiter.acquire()
    return api.get_tick("BINANCE___SPOT", symbol)

```bash

### 订单错误

```python

# 错误: OrderError

# 原因: 余额不足、价格偏离等

# 处理: 检查错误类型并处理

def handle_order_error(order_response):
    """处理订单错误"""
    if order_response.get("code") == -2010:  # 余额不足
        print("余额不足，请检查账户")
    elif order_response.get("code") == -2011:  # 订单不存在
        print("订单不存在")
    elif order_response.get("code") == -2019:  # 超过自成交限制
        print("超过自成交限制")
    else:
        print(f"订单错误: {order_response.get('msg')}")

```bash

### 数据初始化错误

```python

# 错误: 数据未初始化

# 原因: 忘记调用 init_data()

# 处理: 封装安全访问

class SafeDataAccess:
    @staticmethod
    def get_price(ticker):
        """安全获取价格"""
        try:
            ticker.init_data()
            return ticker.get_last_price()
        except AttributeError:
            print("警告: 数据未初始化")
            return None

# 使用

ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
price = SafeDataAccess.get_price(ticker)

```bash

- --

## 最佳实践

### 1. 统一异常处理

```python
class ApiErrorHandler:
    """统一 API 错误处理器"""

    def __init__(self, api):
        self.api = api
        self.error_count = {}
        self.max_retries = 3

    def call(self, func, *args, **kwargs):
        """统一的 API 调用入口"""
        exchange = kwargs.get('exchange_name') or args[0] if args else None

        for attempt in range(self.max_retries):
            try:
                result = func(*args, **kwargs)
                if hasattr(result, 'init_data'):
                    result.init_data()

# 成功后重置错误计数
                if exchange:
                    self.error_count[exchange] = 0
                return result
            except ConnectionError as e:
                if attempt < self.max_retries - 1:
                    self.log_and_retry(exchange, e, attempt)
                    time.sleep(2 ** attempt)
                else:
                    self.handle_final_error(exchange, e)
                    raise
            except RateLimitError as e:
                self.handle_rate_limit(exchange, e)
                time.sleep(1)
            except Exception as e:
                self.handle_final_error(exchange, e)
                raise

    def log_and_retry(self, exchange, error, attempt):
        """记录并重试"""
        if exchange:
            self.error_count[exchange] = self.error_count.get(exchange, 0) + 1
        self.api.log(f"错误 (尝试 {attempt + 1}): {error}")

    def handle_rate_limit(self, exchange, error):
        """处理频率限制"""
        self.api.log(f"频率限制: {exchange}, 暂停 10 秒")
        time.sleep(10)

    def handle_final_error(self, exchange, error):
        """处理最终错误"""
        self.api.log(f"操作失败: {exchange}, 错误: {error}", level="error")

# 使用

handler = ApiErrorHandler(api)
ticker = handler.call(api.get_tick, "BINANCE___SPOT", "BTCUSDT")

```bash

### 2. 断路器模式

```python
class CircuitBreaker:
    """断路器：防止连续失败的系统被继续调用"""

    def __init__(self, failure_threshold=5, timeout=60):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.failures = {}
        self.last_failure_time = {}

    def call(self, func, exchange, *args, **kwargs):
        """通过断路器调用函数"""

# 检查断路器状态
        if self.is_open(exchange):
            raise Exception(f"断路器开启: {exchange}")

        try:
            result = func(*args, **kwargs)
            if hasattr(result, 'init_data'):
                result.init_data()
            self.on_success(exchange)
            return result
        except Exception as e:
            self.on_failure(exchange)
            raise

    def is_open(self, exchange):
        """检查断路器是否开启"""
        if exchange not in self.failures:
            return False

        failures = self.failures[exchange]
        last_time = self.last_failure_time.get(exchange, 0)

        if failures >= self.failure_threshold:
            if time.time() - last_time < self.timeout:
                return True
            else:

# 超时后重置
                self.reset(exchange)
                return False
        return False

    def on_success(self, exchange):
        """成功时重置"""
        if exchange in self.failures:
            del self.failures[exchange]

    def on_failure(self, exchange):
        """失败时增加计数"""
        self.failures[exchange] = self.failures.get(exchange, 0) + 1
        self.last_failure_time[exchange] = time.time()

    def reset(self, exchange):
        """重置断路器"""
        if exchange in self.failures:
            del self.failures[exchange]

# 使用

breaker = CircuitBreaker(failure_threshold=5, timeout=60)

def protected_call(symbol):
    return breaker.call(api.get_tick, "BINANCE___SPOT", symbol)

```bash

### 3. 错误通知

```python
class ErrorNotifier:
    """错误通知器"""

    def __init__(self, api):
        self.api = api
        self.errors = []

    def notify(self, error, context=None):
        """发送通知"""
        error_info = {
            "timestamp": time.time(),
            "error": str(error),
            "type": type(error).__name__,
            "context": context
        }
        self.errors.append(error_info)

# 严重错误立即通知
        if isinstance(error, (AuthenticationError, OrderError)):
            self.send_alert(error_info)

    def send_alert(self, error_info):
        """发送告警（可扩展为邮件、钉钉等）"""
        print(f"⚠️ 告警: {error_info}")

```bash

- --

## 完整示例

```python
from bt_api_py import BtApi
from bt_api_py.exceptions import BtApiError, ExchangeNotFoundError, ConnectionError

class SafeBtApi:
    """带完整错误处理的 API 包装类"""

    def __init__(self, exchange_kwargs):
        self.api = BtApi(exchange_kwargs)
        self.circuit_breaker = CircuitBreaker()
        self.error_handler = ApiErrorHandler(self.api)

    def get_tick(self, exchange_name, symbol):
        """安全获取行情"""
        def _call():
            return self.error_handler.call(
                self.api.get_tick, exchange_name, symbol
            )
        return self.circuit_breaker.call(_call, exchange_name)

    def make_order(self, exchange_name, symbol, volume, price, order_type, **kwargs):
        """安全下单"""
        def _call():
            order = self.error_handler.call(
                self.api.make_order,
                exchange_name, symbol, volume, price, order_type,

                - *kwargs

            )

# 验证订单
            if order.get_order_status() == "rejected":
                raise OrderError(f"订单被拒绝: {order.get_order_id()}")
            return order
        return self.circuit_breaker.call(_call, exchange_name)

# 使用

safe_api = SafeBtApi(exchange_kwargs={...})
ticker = safe_api.get_tick("BINANCE___SPOT", "BTCUSDT")

```bash

## 相关文档

- [API 示例](examples/api_examples.md)
- [最佳实践](best_practices.md)
- [性能优化](performance.md)
