# CoinDCX API 文档

## 文档信息
- 文档版本: 1.0.0
- 创建日期: 2026-02-27
- 官方文档: https://docs.coindcx.com
- 数据来源: 官方文档

## 交易所基本信息
- 官方名称: CoinDCX
- 官网: https://coindcx.com
- 交易所类型: CEX (中心化交易所)
- 总部: 印度
- 支持的交易对: 500+ (INR, USDT, BTC 等计价)
- 支持的交易类型: 现货(Spot)、杠杆(Margin)、合约(Futures)
- 法币支持: INR (印度卢比)
- 特点: 印度最大加密货币交易所之一，支持 HFT API

## API基础URL

| 端点类型 | URL |
|---------|-----|
| REST API | `https://api.coindcx.com` |
| WebSocket (Socket.io) | `https://stream.coindcx.com` |

## 认证方式

### HMAC SHA256 签名

**请求头**:

| Header | 描述 |
|--------|------|
| X-AUTH-APIKEY | API Key |
| X-AUTH-SIGNATURE | HMAC-SHA256 签名 |
| Content-Type | application/json |

**签名步骤**:
1. 构建 JSON body (包含 `timestamp` 毫秒时间戳)
2. JSON 序列化 (使用 `separators=(',', ':')` 去除空格)
3. 使用 Secret 对 JSON 字符串进行 HMAC SHA256

### Python 签名示例

```python
import hmac
import hashlib
import json
import time
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "https://api.coindcx.com"

def coindcx_request(path, body_params):
    secret_bytes = bytes(API_SECRET, encoding='utf-8')
    timestamp = int(round(time.time() * 1000))
    body_params["timestamp"] = timestamp

    json_body = json.dumps(body_params, separators=(',', ':'))
    signature = hmac.new(
        secret_bytes, json_body.encode(), hashlib.sha256
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": API_KEY,
        "X-AUTH-SIGNATURE": signature,
    }
    resp = requests.post(f"{BASE_URL}/{path}", data=json_body, headers=headers)
    return resp.json()
```

## 市场数据API (GET, 无需认证)

| 端点 | 描述 |
|------|------|
| /exchange/ticker | 全部 Ticker |
| /exchange/v1/markets | 市场列表 |
| /exchange/v1/markets_details | 市场详细信息 |
| /market_data/trade_history | 最近成交 |
| /market_data/orderbook | 订单簿 |
| /market_data/candles | K线数据 |

```python
# Ticker
resp = requests.get(f"{BASE_URL}/exchange/ticker")
for t in resp.json()[:5]:
    print(f"{t['market']}: last={t['last_price']}, bid={t['bid']}, "
          f"ask={t['ask']}, vol={t['volume']}")

# 订单簿
resp = requests.get(f"{BASE_URL}/market_data/orderbook", params={
    "pair": "B-BTC_USDT", "count": 20
})

# K线
resp = requests.get(f"{BASE_URL}/market_data/candles", params={
    "pair": "B-BTC_USDT", "interval": "1m"
})
```

## 交易API (POST, 需签名)

| 端点 | 描述 |
|------|------|
| /exchange/v1/orders/create | 下单 |
| /exchange/v1/orders/create_multiple | 批量下单 |
| /exchange/v1/orders/status | 订单状态 |
| /exchange/v1/orders/status_multiple | 批量订单状态 |
| /exchange/v1/orders/active_orders | 活跃订单 |
| /exchange/v1/orders/active_orders_count | 活跃订单计数 |
| /exchange/v1/orders/trade_history | 成交历史 |
| /exchange/v1/orders/cancel | 撤单 |
| /exchange/v1/orders/cancel_by_ids | 批量撤单 |
| /exchange/v1/orders/cancel_all | 全部撤单 |
| /exchange/v1/orders/edit | 修改价格 |

```python
# 限价买单
order = coindcx_request("exchange/v1/orders/create", {
    "side": "buy",
    "order_type": "limit_order",
    "market": "BTCUSDT",
    "price_per_unit": 40000,
    "total_quantity": 0.001,
    "client_order_id": "my_order_001"
})
print(f"Order: {order}")

# 市价买单
order = coindcx_request("exchange/v1/orders/create", {
    "side": "buy",
    "order_type": "market_order",
    "market": "BTCUSDT",
    "total_quantity": 0.001
})

# 撤单
coindcx_request("exchange/v1/orders/cancel", {
    "id": "order_id_here"
})

# 查余额
balance = coindcx_request("exchange/v1/users/balances", {})
```

**订单类型**: `limit_order`, `market_order`, `stop_limit` (部分市场)

## 杠杆交易API (Margin)

| 端点 | 描述 |
|------|------|
| /exchange/v1/margin/create | 杠杆下单 |
| /exchange/v1/margin/cancel | 撤销杠杆单 |
| /exchange/v1/margin/exit | 平仓 |
| /exchange/v1/margin/edit_target | 修改止盈 |
| /exchange/v1/margin/edit_sl | 修改止损 |
| /exchange/v1/margin/add_margin | 追加保证金 |
| /exchange/v1/margin/remove_margin | 减少保证金 |
| /exchange/v1/margin/fetch_orders | 查询杠杆订单 |

## 合约交易API (Futures)

| 端点类型 | 描述 |
|---------|------|
| 合约市场数据 | instruments, orderbook, trades, candlesticks |
| 合约交易 | create order, cancel order, edit order |
| 合约仓位 | list positions, update leverage, add/remove margin, exit |
| 合约钱包 | wallet details, wallet transfer, wallet transactions |

## 账户管理API

| 端点 | 描述 |
|------|------|
| /exchange/v1/users/balances | 余额查询 |
| /exchange/v1/users/info | 用户信息 |
| /exchange/v1/funding/wallet_transfer | 钱包转账 |
| /exchange/v1/lending/place | 借贷下单 |
| /exchange/v1/lending/settle | 借贷结算 |

## WebSocket (Socket.io)

CoinDCX 使用 Socket.io 协议实现 WebSocket。

### 公共频道

| 事件 | 描述 |
|------|------|
| depth-update | 订单簿更新 |
| new-trade | 最新成交 |
| price-change | 价格变化 (LTP) |
| candlestick | K线更新 |
| current-prices | 当前价格 |
| price-stats | 价格统计 |

### 私有频道 (需认证)

| 事件 | 描述 |
|------|------|
| balance-update | 余额更新 |
| order-update | 订单更新 |
| trade-update | 成交更新 |

### Python WebSocket 示例

```python
import socketio

sio = socketio.Client()
sio.connect('https://stream.coindcx.com', transports='websocket')

@sio.on('depth-update')
def on_depth(data):
    print("Depth update:", data)

@sio.event
def connect():
    sio.emit('join', {'channelName': 'B-BTC_USDT@depth'})
    print("Connected!")

@sio.event
def connect_error(data):
    print("Connection failed!")
```

## 速率限制

| 类别 | 限制 |
|------|------|
| REST API | 有频率限制，超限返回错误 |
| HFT API | 需联系官方申请，提供静态 IP |

## 错误处理

常见错误原因:
- 余额不足
- 频率超限
- 订单类型不支持 (如 BTCINR 不支持 stop_limit)
- 必填参数缺失
- 订单已成交/已取消无法再取消

## 变更历史

### 2026-02-27
- 基于官方文档 (docs.coindcx.com) 完善

---

## 相关资源

- [CoinDCX 官方 API 文档](https://docs.coindcx.com)
- [CoinDCX 开发者页面](https://coindcx.com/api/)

---

*本文档由 bt_api_py 项目维护。*
