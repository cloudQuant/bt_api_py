# bt_api_py 开发者指南

本文档面向希望扩展 bt_api_py 的开发者，介绍如何添加新交易所、新数据容器、编写测试等。

---

## 开发环境搭建

```bash

# 克隆仓库

git clone <https://github.com/cloudQuant/bt_api_py>
cd bt_api_py

# 安装依赖

pip install -r requirements.txt
pip install -e ".[dev]"

# 运行测试

pytest tests -n 4

```

---

## 添加新交易所

添加新交易所需要完成以下 5 个步骤，**无需修改任何核心文件**（`bt_api.py`、`registry.py` 等）。

### 步骤概览

1. 创建 ExchangeData 类 (`containers/exchanges/`)
2. 创建 Feed 类 (`feeds/live_{exchange}/` 或 `feeds/live_{exchange}_feed.py`)
3. 创建 WebSocket Stream 类（可选）
4. 创建注册模块 (`feeds/register_{exchange}.py`)
5. 在 `bt_api.py` 中添加自动导入

### 步骤 1：创建 ExchangeData 类

在 `bt_api_py/containers/exchanges/` 目录下创建交易所配置类：

```python

# bt_api_py/containers/exchanges/myex_exchange_data.py

from bt_api_py.containers.exchanges.exchange_data import ExchangeData


class MyExExchangeDataSwap(ExchangeData):
    """MyEx 永续合约的交易所配置"""

    def __init__(self):
        super().__init__()
        self.exchange_name = "MYEX___SWAP"

    def get_exchange_name(self):
        return self.exchange_name

# 定义交易所特有的配置方法，例如：

# - 符号格式转换

# - K 线周期映射

# - 订单类型映射

```

### 步骤 2：创建 Feed 类

Feed 类负责 REST API 调用，继承 `Feed` 基类并实现 `AbstractVenueFeed` 协议方法：

```python

# bt_api_py/feeds/live_myex_feed.py

from bt_api_py.feeds.feed import Feed


class MyExRequestDataSwap(Feed):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.public_key = kwargs.get("public_key", "")
        self.private_key = kwargs.get("private_key", "")
        self.base_url = "<https://api.myex.com">
        self.exchange_name = "MYEX___SWAP"

# ── 行情查询 ──

    def get_tick(self, symbol, extra_data=None, **kwargs):
        """获取最新价格"""

# 1. 构造请求
        url = f"{self.base_url}/v1/ticker?symbol={symbol}"

# 2. 发送请求
        response = self.request("GET", url)

# 3. 返回 Container 对象
        return MyExTickerData(response, extra_data=extra_data)

    def get_depth(self, symbol, count=20, extra_data=None, **kwargs):
        """获取深度"""
        ...

    def get_kline(self, symbol, period, count=20, extra_data=None, **kwargs):
        """获取 K 线"""
        ...

# ── 交易操作 ──

    def make_order(self, symbol, volume, price, order_type,
                   offset="open", post_only=False, client_order_id=None,
                   extra_data=None, **kwargs):
        """下单"""
        ...

    def cancel_order(self, symbol, order_id, extra_data=None, **kwargs):
        """撤单"""
        ...

    def cancel_all(self, symbol=None, extra_data=None, **kwargs):
        """撤销所有订单"""
        ...

    def query_order(self, symbol, order_id, extra_data=None, **kwargs):
        """查询订单"""
        ...

    def get_open_orders(self, symbol=None, extra_data=None, **kwargs):
        """查询挂单"""
        ...

# ── 账户查询 ──

    def get_balance(self, symbol=None, extra_data=None, **kwargs):
        """查询余额"""
        ...

    def get_account(self, symbol="ALL", extra_data=None, **kwargs):
        """查询账户"""
        ...

    def get_position(self, symbol=None, extra_data=None, **kwargs):
        """查询持仓"""
        ...

```

- *关键规则**：
- 方法签名必须与 `AbstractVenueFeed` 协议一致
- 返回值为 Container 对象（如 `TickerData`、`OrderData`）
- 异步方法以 `async_` 为前缀，可通过 `AsyncWrapperMixin` 自动包装

### 步骤 3：创建 WebSocket Stream 类（可选）

如果需要实时数据推送，继承 `BaseDataStream`：

```python

# bt_api_py/feeds/live_myex/myex_market_wss.py

from bt_api_py.feeds.base_stream import BaseDataStream, ConnectionState
import websocket
import json


class MyExMarketWssData(BaseDataStream):
    def __init__(self, data_queue, **kwargs):
        super().__init__(data_queue, **kwargs)
        self.wss_url = kwargs.get("wss_url", "wss://ws.myex.com/ws")
        self.topics = kwargs.get("topics", [])
        self.ws = None

    def connect(self):
        self.state = ConnectionState.CONNECTING
        self.ws = websocket.WebSocketApp(
            self.wss_url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open,
        )

    def disconnect(self):
        if self.ws:
            self.ws.close()
        self.state = ConnectionState.DISCONNECTED

    def subscribe_topics(self, topics):
        for topic in topics:
            msg = json.dumps({"op": "subscribe", "args": [topic]})
            self.ws.send(msg)

    def _run_loop(self):
        self.connect()
        self.ws.run_forever()

    def _on_open(self, ws):
        self.state = ConnectionState.CONNECTED
        self.subscribe_topics(self.topics)

    def _on_message(self, ws, message):
        data = json.loads(message)

# 解析数据并推送到队列
        container = self._parse_message(data)
        if container:
            self.push_data(container)

    def _on_error(self, ws, error):
        self.logger.warn(f"WebSocket error: {error}")
        self.state = ConnectionState.ERROR

    def _on_close(self, ws, close_status_code, close_msg):
        self.state = ConnectionState.DISCONNECTED

    def _parse_message(self, data):
        """将原始消息解析为 Container 对象"""

# 根据消息类型返回对应的 Container
        ...

```

### 步骤 4：创建注册模块

这是最关键的一步，将所有组件注册到全局 `ExchangeRegistry`：

```python

# bt_api_py/feeds/register_myex.py

from bt_api_py.registry import ExchangeRegistry
from bt_api_py.feeds.live_myex_feed import MyExRequestDataSwap
from bt_api_py.containers.exchanges.myex_exchange_data import MyExExchangeDataSwap
from bt_api_py.balance_utils import simple_balance_handler


def _myex_swap_subscribe_handler(data_queue, exchange_params, topics, bt_api):
    """MyEx SWAP 订阅处理函数"""
    exchange_data = MyExExchangeDataSwap()
    kwargs = {key: v for key, v in exchange_params.items()}
    kwargs['wss_name'] = 'myex_market_data'
    kwargs["wss_url"] = 'wss://ws.myex.com/ws'
    kwargs["exchange_data"] = exchange_data
    kwargs['topics'] = topics
    MyExMarketWssData(data_queue, **kwargs).start()


def register_myex():
    """注册 MyEx 到全局 ExchangeRegistry"""
    ExchangeRegistry.register_feed("MYEX___SWAP", MyExRequestDataSwap)
    ExchangeRegistry.register_exchange_data("MYEX___SWAP", MyExExchangeDataSwap)
    ExchangeRegistry.register_balance_handler("MYEX___SWAP", simple_balance_handler)
    ExchangeRegistry.register_stream("MYEX___SWAP", "subscribe", _myex_swap_subscribe_handler)


# 模块导入时自动注册

register_myex()

```

### 步骤 5：在 bt_api.py 中添加自动导入

在 `bt_api_py/bt_api.py` 的注册导入区域添加：

```python
try:
    import bt_api_py.feeds.register_myex  # noqa: F401

except ImportError as e:
    _reg_logger.debug(f"MyEx register skipped: {e}")

```
完成！现在用户可以使用 `"MYEX___SWAP"` 标识来连接新交易所了。

---

## 添加新数据容器

### 步骤 1：创建基类

在 `bt_api_py/containers/{type}/` 目录下创建抽象基类：

```python

# bt_api_py/containers/mydata/mydata.py

from bt_api_py.containers.auto_init_mixin import AutoInitMixin


class MyData(AutoInitMixin):
    """自定义数据容器基类"""

    def __init__(self, input_data, extra_data=None, has_been_json_encoded=False):
        self._input_data = input_data
        self._extra_data = extra_data
        self.has_been_json_encoded = has_been_json_encoded
        self._initialized = False

# 定义需要解析的字段
        self._field1 = None
        self._field2 = None

    def init_data(self):
        """解析原始数据，子类必须实现"""
        raise NotImplementedError

    def get_input_data(self):
        return self._input_data

    def get_extra_data(self):
        return self._extra_data

    def get_field1(self):
        self._ensure_init()
        return self._field1

    def get_field2(self):
        self._ensure_init()
        return self._field2

```

### 步骤 2：创建交易所实现

```python

# bt_api_py/containers/mydata/binance_mydata.py

from bt_api_py.containers.mydata.mydata import MyData


class BinanceMyData(MyData):
    """Binance 的 MyData 实现"""

    def init_data(self):
        if self.has_been_json_encoded:
            data = self._input_data
        else:
            data = self._input_data  # 根据 Binance API 响应格式解析
        self._field1 = data.get("field1")
        self._field2 = data.get("field2")
        self._initialized = True

```

### 关键规则

- 基类定义 `get_*()` 抽象方法
- 交易所子类实现 `init_data()` 完成数据解析
- 使用 `has_been_json_encoded` 标志区分数据来源
- 使用 `AutoInitMixin` 的 `_ensure_init()` 实现延迟初始化

---

## 编写测试

### 测试文件组织

测试文件镜像包结构：

```
tests/
├── containers/
│   ├── orders/test_binance_order.py
│   ├── orders/test_okx_order.py
│   └── tickers/test_binance_ticker.py
├── feeds/
│   ├── test_live_binance_spot_request_data.py
│   ├── test_live_binance_swap_request_data.py
│   └── test_live_okx_swap_request_data.py
├── functions/
│   └── test_*.py
├── test_bt_api.py
├── test_registry_and_balance.py
└── test_stage0_infrastructure.py

```

### 命名规范

- **文件命名**：`test_{exchange}_{feature}.py`
- **类命名**：`Test{Exchange}{Feature}`
- **方法命名**：`test_{exchange}_{method_name}`

### 测试示例

```python

# tests/feeds/test_live_myex_swap_request_data.py

import pytest
import queue
from bt_api_py.feeds.live_myex_feed import MyExRequestDataSwap


class TestMyExSwapRequestData:
    @classmethod
    def setup_class(cls):
        data_queue = queue.Queue()
        kwargs = {
            "public_key": "TEST_KEY",
            "private_key": "TEST_SECRET",
        }
        cls.api = MyExRequestDataSwap(data_queue, **kwargs)
        cls.data_queue = data_queue

    @pytest.mark.xdist_group("mixed_exchange_api")
    def test_myex_get_tick(self):
        result = self.api.get_tick("BTC-USDT")
        result.init_data()
        assert result.get_last_price() > 0

    @pytest.mark.xdist_group("mixed_exchange_api")
    def test_myex_get_kline(self):
        result = self.api.get_kline("BTC-USDT", "1m", count=10)
        result.init_data()
        bars = result.get_data()
        assert len(bars) > 0

```

### 运行测试

```bash

# 运行所有测试（4 并行）

pytest tests -n 4

# 运行特定交易所测试

pytest tests/feeds/test_live_myex_*.py -v

# 仅运行新增/失败的测试

pytest tests -n 4 --picked

# 带覆盖率报告

pytest tests --cov=bt_api_py --cov-report=html

```

### 测试标记

对需要实时 API 访问的测试使用 `xdist_group` 标记：

```python
@pytest.mark.xdist_group("mixed_exchange_api")
def test_live_api():
    ...

```

---

## 代码规范

### 文件命名

- `snake_case` — 如 `binance_order.py`、`okx_ticker.py`

### 类命名

- `PascalCase` + 交易所前缀 — 如 `BinanceOrderData`、`OkxTickerData`

### 方法命名

- `snake_case` — 如 `get_exchange_name()`、`init_data()`
- 异步方法前缀 `async_` — 如 `async_get_tick()`

### 常量

- `UPPER_CASE` — 如 `BINANCE___SWAP`

### 注释语言

- **内部注释**：中文
- **API 文档**：英文

### 异常处理

- 始终使用 `bt_api_py.exceptions` 中的自定义异常
- 禁止使用裸 `Exception` 或 `assert` 处理错误

### 交易所命名

- 使用三下划线格式：`EXCHANGE___ASSET_TYPE`
- 示例：`BINANCE___SWAP`、`OKX___SPOT`、`CTP___FUTURE`

---

## 目录结构约定

```
bt_api_py/
├── containers/
│   └── {type}/
│       ├── {type}.py              # 抽象基类

│       ├── binance_{type}.py      # Binance 实现

│       ├── okx_{type}.py          # OKX 实现

│       ├── ctp/                   # CTP 子目录

│       └── ib/                    # IB 子目录

├── feeds/
│   ├── live_{exchange}/           # 交易所 REST/WebSocket 实现

│   ├── live_{exchange}_feed.py    # 交易所 Feed 入口

│   └── register_{exchange}.py     # 交易所注册模块

├── configs/
│   └── {exchange}.yaml            # 交易所配置文件

└── ctp/                           # CTP 专用（SWIG 绑定）

```

### 添加新文件的检查清单

- [ ] 文件放置在正确的目录
- [ ] 文件命名符合 `snake_case` 规范
- [ ] 类命名符合 `PascalCase` + 交易所前缀规范
- [ ] 继承正确的基类
- [ ] 在 `__init__.py` 中添加导出（如需要）
- [ ] 创建对应的测试文件
- [ ] 异常使用自定义异常类

---

## 常见问题

### Q: 如何调试 WebSocket 连接？

启用 debug 模式并检查日志文件：

```python
bt_api = BtApi(exchange_kwargs, debug=True)

# 日志默认保存在 ./logs/ 目录下

```

### Q: 如何处理交易所 API 限速？

bt_api_py 提供 `RateLimiter` 模块（`bt_api_py/rate_limiter.py`），支持滑动窗口、固定窗口和令牌桶算法。在 Feed 类中使用：

```python
from bt_api_py.rate_limiter import RateLimiter
limiter = RateLimiter(max_requests=10, interval=1.0)
limiter.wait()  # 等待直到可以发送请求

```

### Q: 为什么 Container 数据返回 None？

确保调用了 `init_data()` 方法：

```python
data = api.get_tick("BTC-USDT")
data.init_data()       # 必须调用！

price = data.get_last_price()

```
或者使用 `AutoInitMixin` 的 `_ensure_init()` 方法在 `get_*()` 中自动初始化。

### Q: 如何验证 Feed 是否符合协议？

使用 `check_protocol_compliance` 函数：

```python
from bt_api_py.feeds.abstract_feed import check_protocol_compliance

missing = check_protocol_compliance(MyExRequestDataSwap)
if missing:
    print(f"缺失方法: {missing}")
else:
    print("完全符合协议")

```

---

- 最后更新: 2026-02-28*
