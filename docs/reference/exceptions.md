# 异常体系

bt_api_py 使用统一的自定义异常体系，所有异常均继承自 `BtApiError`。

## 异常层次结构

```
BtApiError
├── ExchangeNotFoundError       # 交易所未注册或未添加
├── ExchangeConnectionError     # 连接失败
│   └── AuthenticationError    # 认证失败
├── RequestTimeoutError         # 请求超时
├── RequestError                # 请求失败（非超时）
│   └── RequestFailedError     # HTTP 请求失败
├── OrderError                  # 下单/撤单失败
│   ├── InsufficientBalanceError # 余额不足
│   ├── InvalidOrderError       # 订单参数无效
│   └── OrderNotFoundError      # 订单不存在
├── SubscribeError              # 订阅失败
├── DataParseError              # 数据解析失败
├── RateLimitError              # 速率限制
├── NetworkError                # 网络错误
├── InvalidSymbolError          # 无效交易对
├── ConfigurationError          # 配置错误
└── WebSocketError              # WebSocket 错误
```

## 使用示例

```python
from bt_api_py.exceptions import (
    BtApiError,
    ExchangeNotFoundError,
    RateLimitError,
    RequestTimeoutError,
)

try:
    ticker = api.get_tick("UNKNOWN___SPOT", "BTCUSDT")
except ExchangeNotFoundError as e:
    print(f"交易所未找到: {e}")
except RateLimitError as e:
    print(f"频率限制，{e.retry_after}s 后重试")
except RequestTimeoutError as e:
    print(f"请求超时: {e}")
except BtApiError as e:
    print(f"其他错误: {e}")
```

---

::: bt_api_py.exceptions
    options:
      show_root_heading: false
      show_source: false
      members_order: source
      heading_level: 2
      show_if_no_docstring: true
      filters:
        - "!^_"
