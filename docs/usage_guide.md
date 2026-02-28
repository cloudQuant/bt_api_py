# bt_api_py 使用指南

本文档提供从安装配置到实际交易的完整使用教程。

- --

## 目录

- [环境准备](#环境准备)
- [账户配置](#账户配置)
- [初始化 BtApi](#初始化-btapi)
- [统一接口（推荐）](#统一接口推荐)
- [REST 同步请求（Feed 模式）](#rest-同步请求 feed-模式)
- [异步请求](#异步请求)
- [WebSocket 实时订阅](#websocket-实时订阅)
- [余额与持仓管理](#余额与持仓管理)
- [历史数据下载](#历史数据下载)
- [事件总线回调模式](#事件总线回调模式)
- [多交易所同时使用](#多交易所同时使用)
- [CTP 期货使用](#ctp-期货使用)
- [Interactive Brokers 使用](#interactive-brokers-使用)
- [错误处理](#错误处理)
- [代理配置](#代理配置)

- --

## 环境准备

### 系统要求

- Python 3.11+
- 支持平台：Linux (x86_64)、Windows (x64)、macOS (arm64/x86_64)

### 安装

```bash

# 从 PyPI 安装

pip install bt_api_py

# 或从源码安装

git clone <https://github.com/cloudQuant/bt_api_py>
cd bt_api_py
pip install -r requirements.txt
pip install .

```bash

### 可选依赖

- **CTP 期货**：需要在对应平台编译 CTP SWIG 绑定
- **Interactive Brokers TWS**：需要安装 `ib_insync` 并运行 TWS/Gateway
- **IB Web API**：需要运行 Client Portal Gateway 或配置 OAuth 2.0

- --

## 账户配置

### 方式一：YAML 配置文件

复制示例文件并编辑：

```bash
cp bt_api_py/configs/account_config_example.yaml bt_api_py/configs/account_config.yaml

```bash
完整配置示例：

```yaml

# 加密货币交易所

binance:
  public_key: 'YOUR_BINANCE_API_KEY'
  private_key: 'YOUR_BINANCE_SECRET_KEY'

okx:
  public_key: 'YOUR_OKX_API_KEY'
  private_key: 'YOUR_OKX_SECRET_KEY'
  passphrase: 'YOUR_OKX_PASSPHRASE'

# CTP 期货 (中国期货市场)

ctp:
  broker_id: "9999"                              # 期货公司代号
  user_id: "YOUR_USER_ID"                        # 资金账号
  password: "YOUR_PASSWORD"                      # 密码
  auth_code: "YOUR_AUTH_CODE"                    # 认证码
  app_id: "simnow_client_test"                   # 产品名称
  md_front: "tcp://180.168.146.187:10131"        # 行情前置地址
  td_front: "tcp://180.168.146.187:10130"        # 交易前置地址

# Interactive Brokers TWS

ib:
  host: "127.0.0.1"
  port: 7497                                     # TWS=7497, Gateway=4001
  client_id: 1

# IB Web API

ib_web:
  base_url: "<https://localhost:5000">
  account_id: "U1234567"
  verify_ssl: false
  timeout: 10

```bash

### 方式二：代码中直接传参

```python
exchange_kwargs = {
    "BINANCE___SWAP": {
        "public_key": "YOUR_API_KEY",
        "private_key": "YOUR_SECRET_KEY",
    },
}

```bash

### 方式三：使用 AuthConfig 类

```python
from bt_api_py.auth_config import CryptoAuthConfig

config = CryptoAuthConfig(
    exchange="BINANCE",
    asset_type="SWAP",
    public_key="YOUR_API_KEY",
    private_key="YOUR_SECRET_KEY",
)

exchange_kwargs = {
    config.get_exchange_name(): config.to_dict(),
}

```bash

- --

## 初始化 BtApi

```python
from bt_api_py.bt_api import BtApi

# 定义要连接的交易所和参数

exchange_kwargs = {
    "BINANCE___SWAP": {
        "public_key": "YOUR_API_KEY",
        "private_key": "YOUR_SECRET_KEY",
    },
    "OKX___SWAP": {
        "public_key": "YOUR_API_KEY",
        "private_key": "YOUR_SECRET_KEY",
        "passphrase": "YOUR_PASSPHRASE",
    },
}

# 创建 BtApi 实例（自动初始化所有交易所连接）

bt_api = BtApi(exchange_kwargs, debug=True)

# 查看已连接的交易所

print(bt_api.list_exchanges())

# 输出: ['BINANCE___SWAP', 'OKX___SWAP']

# 查看所有可用（已注册）的交易所

print(BtApi.list_available_exchanges())

# 输出: ['BINANCE___SWAP', 'BINANCE___SPOT', 'OKX___SWAP', 'OKX___SPOT', ...]

```bash

### 动态添加交易所

```python

# 初始化后动态添加新交易所

bt_api.add_exchange("BINANCE___SPOT", {
    "public_key": "YOUR_API_KEY",
    "private_key": "YOUR_SECRET_KEY",
})

```bash

- --

## 统一接口（推荐）

BtApi 提供统一接口，直接在 `BtApi` 实例上调用方法，第一个参数传入交易所标识即可自动路由到对应交易所。

### 行情查询

```python

# 获取最新行情

tick = bt_api.get_tick("BINANCE___SWAP", "BTC-USDT")
tick.init_data()
print(f"价格: {tick.get_last_price()}")

# 获取深度

depth = bt_api.get_depth("BINANCE___SWAP", "BTC-USDT", count=20)
depth.init_data()
print(f"买一价: {depth.get_bid_price(0)}")

# 获取 K 线

kline = bt_api.get_kline("BINANCE___SWAP", "BTC-USDT", "1m", count=100)

```bash

### 交易操作

```python

# 下单 — 所有交易所使用完全相同的接口

order = bt_api.make_order("BINANCE___SWAP", "BTC-USDT",
                          volume=0.001, price=50000.0, order_type="limit")
order.init_data()
print(f"订单 ID: {order.get_order_id()}")

# 撤单

bt_api.cancel_order("BINANCE___SWAP", "BTC-USDT", order_id="123456789")

# 撤销所有挂单

bt_api.cancel_all("BINANCE___SWAP", "BTC-USDT")

# 查询订单

order = bt_api.query_order("BINANCE___SWAP", "BTC-USDT", order_id="123456789")

# 查询挂单

open_orders = bt_api.get_open_orders("BINANCE___SWAP", "BTC-USDT")

```bash

### 账户查询

```python

# 查询余额

balance = bt_api.get_balance("BINANCE___SWAP")

# 查询账户

account = bt_api.get_account("BINANCE___SWAP")

# 查询持仓

position = bt_api.get_position("BINANCE___SWAP", "BTC-USDT")

```bash

### 异步统一接口

异步方法以 `async_` 前缀，结果推送到对应的 data_queue：

```python
bt_api.async_get_tick("BINANCE___SWAP", "BTC-USDT")
bt_api.async_make_order("OKX___SWAP", "BTC-USDT", 0.001, 50000.0, "limit")
bt_api.async_cancel_order("BINANCE___SWAP", "BTC-USDT", "123456789")
bt_api.async_get_balance("OKX___SWAP")

```bash

### 批量操作（跨交易所）

```python

# 从所有已连接交易所获取 BTC-USDT 行情

all_ticks = bt_api.get_all_ticks("BTC-USDT")

# 返回: {"BINANCE___SWAP": tick_data, "OKX___SWAP": tick_data, ...}

# 查询所有交易所余额

all_balances = bt_api.get_all_balances()

# 查询所有交易所持仓

all_positions = bt_api.get_all_positions("BTC-USDT")

# 撤销所有交易所的所有订单

bt_api.cancel_all_orders("BTC-USDT")

```bash

### 统一接口完整方法列表

| 分类 | 同步方法 | 异步方法 |

|------|---------|---------|

| 行情 | `get_tick`, `get_depth`, `get_kline` | `async_get_tick`, `async_get_depth`, `async_get_kline` |

| 交易 | `make_order`, `cancel_order`, `cancel_all`, `query_order`, `get_open_orders` | `async_make_order`, `async_cancel_order`, `async_cancel_all`, `async_query_order`, `async_get_open_orders` |

| 账户 | `get_balance`, `get_account`, `get_position` | `async_get_balance`, `async_get_account`, `async_get_position` |

| 批量 | `get_all_ticks`, `get_all_balances`, `get_all_positions`, `cancel_all_orders` | — |

- --

## REST 同步请求（Feed 模式）

> **注意**: 推荐使用上面的统一接口。Feed 模式保留向后兼容。

### 获取 Feed API

```python

# 获取某个交易所的 Feed 实例

api = bt_api.get_request_api("BINANCE___SWAP")

```bash

### 获取行情

```python

# 获取最新价格

tick = api.get_tick("BTC-USDT")
tick.init_data()
print(f"价格: {tick.get_last_price()}")
print(f"交易量: {tick.get_volume()}")

# 获取深度数据

depth = api.get_depth("BTC-USDT", count=20)
depth.init_data()
print(f"买一价: {depth.get_bid_price(0)}")
print(f"卖一价: {depth.get_ask_price(0)}")

```bash

### 获取 K 线

```python

# 获取最近 100 根 1 分钟 K 线

kline = api.get_kline("BTC-USDT", "1m", count=100)
kline.init_data()
bars = kline.get_data()
for bar in bars:
    bar.init_data()
    print(f"时间: {bar.get_datetime()}, 开: {bar.get_open()}, "
          f"高: {bar.get_high()}, 低: {bar.get_low()}, 收: {bar.get_close()}")

```bash

### 下单

```python

# 限价买入

order = api.make_order(
    symbol="BTC-USDT",
    volume=0.001,
    price=50000.0,
    order_type="limit",
    offset="open",            # 开仓

)
order.init_data()
print(f"订单 ID: {order.get_order_id()}")

# 市价买入

order = api.make_order(
    symbol="BTC-USDT",
    volume=0.001,
    price=0,
    order_type="market",
    offset="open",
)

```bash

### 撤单

```python

# 撤销指定订单

result = api.cancel_order("BTC-USDT", order_id="123456789")

# 撤销所有挂单

result = api.cancel_all("BTC-USDT")

```bash

### 查询订单

```python

# 查询单个订单

order = api.query_order("BTC-USDT", order_id="123456789")
order.init_data()
print(f"状态: {order.get_order_status()}")

# 查询所有挂单

open_orders = api.get_open_orders("BTC-USDT")

```bash

- --

## 异步请求

异步方法以 `async_` 为前缀，结果推送到数据队列。

```python

# 获取数据队列

data_queue = bt_api.get_data_queue("BINANCE___SWAP")

# 发送异步请求

api = bt_api.get_request_api("BINANCE___SWAP")
api.async_get_tick("BTC-USDT", extra_data={"custom_key": "value"})

# 从队列获取结果

import queue
try:
    data = data_queue.get(timeout=10)
    data.init_data()
    print(f"异步获取价格: {data.get_last_price()}")
except queue.Empty:
    print("请求超时")

```bash

### 异步下单

```python
api.async_make_order(
    symbol="BTC-USDT",
    volume=0.001,
    price=50000.0,
    order_type="limit",
    offset="open",
    extra_data={"strategy": "grid_trading"},
)

# 从队列获取下单结果

result = data_queue.get(timeout=10)
result.init_data()
print(f"异步下单结果: {result.get_order_id()}")

```bash

- --

## WebSocket 实时订阅

### 订阅行情数据

```python

# 订阅 K 线和深度数据

bt_api.subscribe("BINANCE___SWAP___BTC-USDT", [
    {"topic": "kline", "symbol": "BTC-USDT", "period": "1m"},
    {"topic": "depth", "symbol": "BTC-USDT"},
    {"topic": "ticker", "symbol": "BTC-USDT"},
])

```bash

### 消费推送数据

```python
import queue

data_queue = bt_api.get_data_queue("BINANCE___SWAP")

while True:
    try:
        data = data_queue.get(timeout=10)
        data.init_data()

# 根据数据类型分别处理
        from bt_api_py.containers import BarData, TickerData, OrderBookData, OrderData

        if isinstance(data, BarData):
            print(f"K 线: {data.get_symbol()} {data.get_close()}")
        elif isinstance(data, TickerData):
            print(f"行情: {data.get_symbol()} {data.get_last_price()}")
        elif isinstance(data, OrderBookData):
            print(f"深度: {data.get_symbol()} 买一={data.get_bid_price(0)}")
        elif isinstance(data, OrderData):
            print(f"订单: {data.get_order_id()} {data.get_order_status()}")

    except queue.Empty:
        print("等待数据中...")

```bash

### 订阅账户数据

订阅行情时，系统会自动订阅账户和订单流（account/order/trade），无需单独处理。

- --

## 余额与持仓管理

### 查询余额

```python

# 更新所有交易所余额

bt_api.update_total_balance()

# 获取特定交易所的现金余额

cash = bt_api.get_cash("BINANCE___SWAP", "USDT")
print(f"可用余额: {cash}")

# 获取特定交易所的总净值

value = bt_api.get_value("BINANCE___SWAP", "USDT")
print(f"账户净值: {value}")

# 获取所有交易所的余额信息

all_cash = bt_api.get_total_cash()
all_value = bt_api.get_total_value()

```bash

### 更新单个交易所余额

```python

# 仅更新指定交易所的余额

bt_api.update_balance("BINANCE___SWAP", currency="USDT")

```bash

### 查询持仓

```python
api = bt_api.get_request_api("BINANCE___SWAP")
position = api.get_position("BTC-USDT")
position.init_data()
positions = position.get_data()
for pos in positions:
    pos.init_data()
    print(f"品种: {pos.get_symbol()}, 方向: {pos.get_position_side()}, "
          f"数量: {pos.get_position_amount()}, 盈亏: {pos.get_unrealized_profit()}")

```bash

- --

## 历史数据下载

### 按数量下载

```python

# 下载最近 100 根 1 分钟 K 线

bt_api.download_history_bars("BINANCE___SWAP", "BTC-USDT", "1m", count=100)

# 从数据队列消费下载的数据

data_queue = bt_api.get_data_queue("BINANCE___SWAP")
while not data_queue.empty():
    bar = data_queue.get()
    bar.init_data()
    print(f"{bar.get_datetime()}: O={bar.get_open()} C={bar.get_close()}")

```bash

### 按时间范围下载

```python
from datetime import datetime

# 下载指定时间范围的数据（自动分批请求）

bt_api.download_history_bars(
    "BINANCE___SWAP",
    "BTC-USDT",
    "1m",
    start_time="2025-01-01T00:00:00+08:00",
    end_time="2025-01-02T00:00:00+08:00",
)

```bash
支持的周期：`1m`, `3m`, `5m`, `15m`, `30m`, `1H`, `1D`

- --

## 事件总线回调模式

除了从数据队列轮询，还可以使用 EventBus 注册回调函数：

```python
from bt_api_py.event_bus import EventBus

# 创建带事件总线的 BtApi

event_bus = EventBus()
bt_api = BtApi(exchange_kwargs, event_bus=event_bus)

# 注册事件回调

def on_bar(data):
    data.init_data()
    print(f"收到 K 线: {data.get_symbol()} {data.get_close()}")

def on_order(data):
    data.init_data()
    print(f"订单更新: {data.get_order_id()} -> {data.get_order_status()}")

event_bus.on("BarEvent", on_bar)
event_bus.on("OrderEvent", on_order)

# 移除回调

event_bus.off("BarEvent", on_bar)

# 移除某事件的所有回调

event_bus.off("OrderEvent")

```bash

- --

## 多交易所同时使用

```python

# 同时连接多个交易所

exchange_kwargs = {
    "BINANCE___SWAP": {
        "public_key": "BINANCE_KEY",
        "private_key": "BINANCE_SECRET",
    },
    "BINANCE___SPOT": {
        "public_key": "BINANCE_KEY",
        "private_key": "BINANCE_SECRET",
    },
    "OKX___SWAP": {
        "public_key": "OKX_KEY",
        "private_key": "OKX_SECRET",
        "passphrase": "OKX_PASS",
    },
}

bt_api = BtApi(exchange_kwargs)

# 跨交易所操作

for exchange_name in bt_api.list_exchanges():
    api = bt_api.get_request_api(exchange_name)
    tick = api.get_tick("BTC-USDT")
    tick.init_data()
    print(f"{exchange_name}: BTC-USDT = {tick.get_last_price()}")

```bash

- --

## CTP 期货使用

CTP 使用中国期货市场的专用协议，需要额外配置：

```python
from bt_api_py.auth_config import CtpAuthConfig

ctp_config = CtpAuthConfig(
    broker_id="9999",
    user_id="YOUR_USER_ID",
    password="YOUR_PASSWORD",
    auth_code="YOUR_AUTH_CODE",
    app_id="simnow_client_test",
    md_front="tcp://180.168.146.187:10131",
    td_front="tcp://180.168.146.187:10130",
)

exchange_kwargs = {
    ctp_config.get_exchange_name(): ctp_config.to_dict(),

# 返回 "CTP___FUTURE": {...}

}

bt_api = BtApi(exchange_kwargs)
api = bt_api.get_request_api("CTP___FUTURE")

# CTP 下单必须指定 offset 和 exchange_id

order = api.make_order(
    symbol="au2506",
    volume=1,
    price=500.0,
    order_type="limit",
    offset="open",           # open/close/close_today/close_yesterday

)

```bash
> **注意**：CTP 需要平台特定的 SWIG 绑定：macOS (.framework)、Linux (.so)、Windows (.dll)

- --

## Interactive Brokers 使用

### TWS/Gateway 原生 API

```python
from bt_api_py.auth_config import IbAuthConfig

ib_config = IbAuthConfig(
    host="127.0.0.1",
    port=7497,        # TWS=7497, Gateway=4001
    client_id=1,
)

exchange_kwargs = {
    ib_config.get_exchange_name(): ib_config.to_dict(),
}

bt_api = BtApi(exchange_kwargs)

```bash

### IB Web API

```python
from bt_api_py.auth_config import IbWebAuthConfig

ib_web_config = IbWebAuthConfig(
    base_url="<https://localhost:5000",>
    account_id="U1234567",
    verify_ssl=False,
    timeout=10,
)

exchange_kwargs = {
    ib_web_config.get_exchange_name(): ib_web_config.to_dict(),
}

bt_api = BtApi(exchange_kwargs)

```bash

- --

## 错误处理

bt_api_py 提供完整的异常体系：

```python
from bt_api_py.exceptions import (
    BtApiError,
    ExchangeNotFoundError,
    ExchangeConnectionError,
    AuthenticationError,
    RequestTimeoutError,
    RequestError,
    OrderError,
    SubscribeError,
    DataParseError,
)

try:
    api = bt_api.get_request_api("BINANCE___SWAP")
    order = api.make_order("BTC-USDT", 0.001, 50000.0, "limit")
except AuthenticationError as e:
    print(f"认证失败: {e}")
except RequestTimeoutError as e:
    print(f"请求超时: {e}")
except OrderError as e:
    print(f"下单失败: {e}")
except ExchangeConnectionError as e:
    print(f"连接错误: {e}")
except BtApiError as e:
    print(f"API 错误: {e}")

```bash

- --

## 代理配置

如果需要通过代理访问交易所 API：

```python
exchange_kwargs = {
    "BINANCE___SWAP": {
        "public_key": "YOUR_KEY",
        "private_key": "YOUR_SECRET",
        "proxies": {
            "http": "<http://127.0.0.1:7890",>
            "https": "<http://127.0.0.1:7890",>
        },
        "async_proxy": "<http://127.0.0.1:7890",>
    },
}

```bash

- --

- 最后更新: 2026-02-28*
