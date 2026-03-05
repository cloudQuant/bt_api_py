# HTX (Huobi) API 文档

> bt_api_py 对 HTX 交易所的完整支持文档

## 概览

HTX（原 Huobi）是全球领先的数字资产交易平台之一，bt_api_py 提供了对 HTX 全系列产品的统一接口支持，包括现货、杠杆、U本位合约、币本位合约和期权。

## 交易所基本信息

- ***官方名称**: HTX (原 Huobi)
- ***官网**: https://www.htx.com
- ***交易所类型**: CEX (中心化交易所)
- ***支持的币种数量**: 600+
- ***官方文档**: https://www.htx.com/en-us/opend/newApiPages/

## 支持的产品线

| 产品线 | 交易所代码 | REST | WebSocket | 说明 |
|--------|-----------|:----:|:---------:|------|
| **现货交易** | `HTX___SPOT` | ✅ | ✅ | 现货买卖 |
| **杠杆交易** | `HTX___MARGIN` | ✅ | ✅ | 杠杆借贷交易 |
| **U本位合约** | `HTX___USDT_SWAP` | ✅ | ✅ | USDT 永续合约 |
| **币本位合约** | `HTX___COIN_SWAP` | ✅ | ✅ | 反向永续合约 |
| **期权** | `HTX___OPTION` | ✅ | ✅ | 期权交易 |

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API | `https://api.huobi.pro` | 主端点 |
| REST API (AWS) | `https://api-aws.huobi.pro` | AWS 区域 |
| WebSocket (市场) | `wss://api.huobi.pro/ws` | 市场数据 |
| WebSocket (账户) | `wss://api.huobi.pro/ws/v2` | 账户数据 |

## 快速开始

### 1. 安装 bt_api_py

```bash
pip install bt_api_py
```

### 2. 获取 API 密钥

1. 登录 [HTX 官网](https://www.htx.com)
2. 进入 API Management 页面
3. 创建 API 密钥 (Access Key + Secret Key)
4. 配置权限和 IP 白名单

### 3. 现货交易示例

```python
from bt_api_py import BtApi

api = BtApi(exchange_kwargs={
    "HTX___SPOT": {
        "api_key": "your_access_key",
        "secret": "your_secret_key",
    }
})

# 获取行情
ticker = api.get_tick("HTX___SPOT", "btcusdt")
ticker.init_data()
print(f"BTC 价格: {ticker.get_last_price()}")

# 获取深度
depth = api.get_depth("HTX___SPOT", "btcusdt", count=10)
depth.init_data()

# 下限价单
order = api.make_order(
    exchange_name="HTX___SPOT",
    symbol="btcusdt",
    volume=0.001,
    price=50000,
    order_type="limit"
)

# 查询订单
order_info = api.query_order("HTX___SPOT", "btcusdt", order_id="xxx")

# 撤单
api.cancel_order("HTX___SPOT", "btcusdt", order_id="xxx")
```

### 4. U本位合约示例

```python
api = BtApi(exchange_kwargs={
    "HTX___USDT_SWAP": {
        "api_key": "your_access_key",
        "secret": "your_secret_key",
    }
})

# 获取合约行情
ticker = api.get_tick("HTX___USDT_SWAP", "BTC-USDT")
ticker.init_data()
print(f"合约价格: {ticker.get_last_price()}")

# 获取持仓
positions = api.get_position("HTX___USDT_SWAP")

# 合约下单
order = api.make_order(
    exchange_name="HTX___USDT_SWAP",
    symbol="BTC-USDT",
    volume=1,
    price=50000,
    order_type="limit",
    offset="open"  # 开仓
)
```

### 5. WebSocket 实时订阅

```python
api = BtApi(exchange_kwargs={
    "HTX___SPOT": {
        "api_key": "your_access_key",
        "secret": "your_secret_key",
    }
})

# 订阅 K 线
api.subscribe("HTX___SPOT___btcusdt", [
    {"topic": "kline", "symbol": "btcusdt", "period": "1m"},
])

# 从数据队列读取
data_queue = api.get_data_queue("HTX___SPOT")
while True:
    data = data_queue.get()
    data.init_data()
    print(f"K线更新: {data.get_close_price()}")
```

### 6. 多产品线同时使用

```python
api = BtApi(exchange_kwargs={
    "HTX___SPOT": {
        "api_key": "your_key",
        "secret": "your_secret",
    },
    "HTX___USDT_SWAP": {
        "api_key": "your_key",
        "secret": "your_secret",
    },
    "HTX___OPTION": {
        "api_key": "your_key",
        "secret": "your_secret",
    },
})

# 同时查询现货和合约行情
spot_ticker = api.get_tick("HTX___SPOT", "btcusdt")
swap_ticker = api.get_tick("HTX___USDT_SWAP", "BTC-USDT")
```

## 认证方式

HTX 使用 **HMAC SHA256** 签名算法，bt_api_py 内部自动处理签名过程，用户只需提供 Access Key 和 Secret Key。

**签名流程** (由框架自动完成):

1. 构建规范化请求字符串
2. 按字母顺序排序参数
3. 使用 Secret Key 进行 HMAC SHA256 签名
4. 将签名进行 Base64 编码

## 数据容器

HTX 返回的数据会被自动转换为标准化数据容器：

| 数据类型 | 容器类 | 主要方法 |
|---------|--------|---------|
| 行情 | `HtxTicker` | `get_last_price()`, `get_bid_price()`, `get_ask_price()` |
| 深度 | `HtxOrderBook` | `get_bids()`, `get_asks()` |
| K线 | `HtxBar` | `get_open_price()`, `get_close_price()`, `get_volume()` |
| 订单 | `HtxOrder` | `get_order_id()`, `get_order_status()` |
| 账户 | `HtxAccount` | `get_margin()`, `get_available_margin()` |
| 余额 | `HtxBalance` | `get_free()`, `get_locked()` |
| 成交 | `HtxTrade` | `get_trade_price()`, `get_trade_volume()` |

## 相关文档

- [BtApi 统一接口](../../reference/bt_api.md) — BtApi 类完整 API 参考
- [WebSocket 订阅](../../reference/websocket.md) — WebSocket 使用指南
- [代码示例](../../guides/examples/api_examples.md) — 更多实战示例
