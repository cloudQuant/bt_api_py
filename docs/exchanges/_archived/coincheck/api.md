# Coincheck API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: REST / WebSocket
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://coincheck.com/documents/exchange/api>
- 数据来源: 官方文档 + CCXT 源码验证

## 交易所基本信息

- 官方名称: Coincheck
- 官网: <https://coincheck.com>
- 交易所类型: CEX (中心化交易所)
- 总部: 日本东京
- 母公司: Monex Group (东京证交所上市)
- 支持的交易对: 30+ (JPY 计价为主, 少量 BTC 计价)
- 支持的交易类型: 现货(Spot)
- 手续费: Maker 0%, Taker 0% (主要交易对，以官方为准)
- 法币支持: JPY (日本円)
- 合规: 日本金融厅 (FSA) 注册

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API | `<https://coincheck.com/api`> | 主端点 |

| WebSocket | `wss://ws-api.coincheck.com` | 实时数据 |

## 认证方式

### API 密钥获取

1. 登录 Coincheck 账户
2. 进入 设定 -> API
3. 创建 API Key，设置权限（查看/交易/提现）
4. 保存 Access Key 和 Secret Key

### HMAC SHA256 签名

- *请求头**:

| Header | 描述 |

|--------|------|

| ACCESS-KEY | API Key |

| ACCESS-NONCE | 递增整数 (毫秒时间戳) |

| ACCESS-SIGNATURE | HMAC-SHA256 签名 |

| Content-Type | application/x-www-form-urlencoded |

- *签名步骤**:
1. 生成递增 nonce（毫秒时间戳）
2. 拼接签名字符串: `nonce + URL(完整 URL 含查询参数) + body`
3. 使用 Secret Key 进行 HMAC SHA256
4. 签名转为十六进制

### Python 签名示例

```python
import hmac
import hashlib
import time
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "<https://coincheck.com">

def coincheck_request(method, path, params=None):
    """发送 Coincheck 签名请求"""
    nonce = str(int(time.time() *1000))

    if method == "GET" and params:
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        url = f"{BASE_URL}{path}?{query}"
        body = ""
    elif method in ("POST", "DELETE"):
        url = f"{BASE_URL}{path}"
        if params:
            body = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        else:
            body = ""
    else:
        url = f"{BASE_URL}{path}"
        body = ""

# 签名: nonce + url + body
    message = nonce + url + body
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "ACCESS-KEY": API_KEY,
        "ACCESS-NONCE": nonce,
        "ACCESS-SIGNATURE": signature,
    }

    if method == "GET":
        resp = requests.get(url, headers=headers)
    elif method == "POST":
        resp = requests.post(url, headers=headers, data=body)
    elif method == "DELETE":
        resp = requests.delete(url, headers=headers)
    else:
        raise ValueError(f"Unsupported: {method}")

    return resp.json()

```bash

## 市场数据 API

> 公共 API 无需认证。

### 1. 获取 Ticker

- *端点**: `GET /api/ticker`

- *响应**:

```json
{
    "last": 4192632.0,
    "bid": 4192496.0,
    "ask": 4193749.0,
    "high": 4332000.0,
    "low": 4101047.0,
    "volume": 2313.43191762,
    "timestamp": 1643374115
}

```bash

```python
resp = requests.get(f"{BASE_URL}/api/ticker")
t = resp.json()
print(f"BTC/JPY: last={t['last']}, bid={t['bid']}, ask={t['ask']}, "
      f"high={t['high']}, low={t['low']}, volume={t['volume']}")

```bash

### 2. 获取汇率

- *端点**: `GET /api/rate/{pair}`

```python

# 获取 BTC/JPY 汇率

resp = requests.get(f"{BASE_URL}/api/rate/btc_jpy")
print(resp.json())  # {"rate": "4192632.0"}

```bash

### 3. 获取订单簿

- *端点**: `GET /api/order_books`

```python
resp = requests.get(f"{BASE_URL}/api/order_books")
book = resp.json()
for ask in book["asks"][:5]:
    print(f"ASK: price={ask[0]}, amount={ask[1]}")
for bid in book["bids"][:5]:
    print(f"BID: price={bid[0]}, amount={bid[1]}")

```bash

### 4. 获取最近成交

- *端点**: `GET /api/trades`

- *参数**: `pair` (如 `btc_jpy`), `limit` (可选)

- *响应示例**:

```json
{
    "data": [
        {
            "id": "206849494",
            "amount": "0.01",
            "rate": "5598346.0",
            "pair": "btc_jpy",
            "order_type": "sell",
            "created_at": "2021-12-08T14:10:33.000Z"
        }
    ]
}

```bash

```python
resp = requests.get(f"{BASE_URL}/api/trades", params={"pair": "btc_jpy"})
for t in resp.json().get("data", [])[:5]:
    print(f"ID={t['id']} rate={t['rate']} amount={t['amount']} "
          f"side={t['order_type']} time={t['created_at']}")

```bash

### 5. 获取换算汇率

- *端点**: `GET /api/exchange/orders/rate`

- *参数**: `pair`, `order_type` (buy/sell), `amount` 或 `price`

```python
resp = requests.get(f"{BASE_URL}/api/exchange/orders/rate", params={
    "pair": "btc_jpy",
    "order_type": "buy",
    "amount": "0.01"
})
print(resp.json())  # {"success": true, "rate": "...", "price": "...", "amount": "..."}

```bash

## 交易 API

> 以下端点均需签名认证。

### 1. 查询余额

- *端点**: `GET /api/accounts/balance`

- *响应示例**:

```json
{
    "success": true,
    "jpy": "12345.0",
    "btc": "0.123",
    "jpy_reserved": "0.0",
    "btc_reserved": "0.001",
    "jpy_lend_in_use": "0.0",
    "btc_lend_in_use": "0.0",
    "jpy_lent": "0.0",
    "btc_lent": "0.0",
    "jpy_debt": "0.0",
    "btc_debt": "0.0"
}

```bash

```python
balance = coincheck_request("GET", "/api/accounts/balance")
if balance.get("success"):
    print(f"JPY: available={balance['jpy']}, reserved={balance['jpy_reserved']}")
    print(f"BTC: available={balance['btc']}, reserved={balance['btc_reserved']}")

```bash

### 2. 下单

- *端点**: `POST /api/exchange/orders`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| pair | STRING | 是 | 交易对 (btc_jpy) |

| order_type | STRING | 是 | buy/sell/market_buy/market_sell |

| rate | DECIMAL | 条件 | 价格 (limit 必需) |

| amount | DECIMAL | 条件 | 数量 (buy/sell/market_sell 时) |

| market_buy_amount | DECIMAL | 条件 | 花费金额 (market_buy 时, JPY) |

| stop_loss_rate | DECIMAL | 否 | 止损价格 |

```python

# 限价买单

order = coincheck_request("POST", "/api/exchange/orders", params={
    "pair": "btc_jpy",
    "order_type": "buy",
    "rate": "4000000",
    "amount": "0.001"
})
if order.get("success"):
    print(f"Order ID: {order['id']}")

# 限价卖单

order = coincheck_request("POST", "/api/exchange/orders", params={
    "pair": "btc_jpy",
    "order_type": "sell",
    "rate": "5000000",
    "amount": "0.001"
})

# 市价买单 (按金额)

order = coincheck_request("POST", "/api/exchange/orders", params={
    "pair": "btc_jpy",
    "order_type": "market_buy",
    "market_buy_amount": "10000"  # 花费 10000 JPY

})

# 市价卖单 (按数量)

order = coincheck_request("POST", "/api/exchange/orders", params={
    "pair": "btc_jpy",
    "order_type": "market_sell",
    "amount": "0.001"
})

```bash

### 3. 撤单

- *端点**: `DELETE /api/exchange/orders/{id}`

```python
result = coincheck_request("DELETE", "/api/exchange/orders/12345")
if result.get("success"):
    print(f"Cancelled order: {result['id']}")

```bash

### 4. 查询挂单

- *端点**: `GET /api/exchange/orders/opens`

- *响应示例**:

```json
{
    "success": true,
    "orders": [
        {
            "id": 202835,
            "order_type": "buy",
            "rate": 26890,
            "pair": "btc_jpy",
            "pending_amount": "0.5527",
            "pending_market_buy_amount": null,
            "stop_loss_rate": null,
            "created_at": "2015-01-10T05:55:38.000Z"
        }
    ]
}

```bash

```python
orders = coincheck_request("GET", "/api/exchange/orders/opens")
if orders.get("success"):
    for o in orders["orders"]:
        print(f"ID:{o['id']} {o['order_type']} rate={o['rate']} "
              f"pending={o['pending_amount']} pair={o['pair']}")

```bash

### 5. 查询成交记录 (分页)

- *端点**: `GET /api/exchange/orders/transactions_pagination`

- *响应示例**:

```json
{
    "success": true,
    "data": [
        {
            "id": 38,
            "order_id": 49,
            "created_at": "2015-11-18T07:02:21.000Z",
            "funds": {"btc": "0.1", "jpy": "-4096.135"},
            "pair": "btc_jpy",
            "rate": "40900.0",
            "fee_currency": "JPY",
            "fee": "6.135",
            "liquidity": "T",
            "side": "buy"
        }
    ]
}

```bash

```python
trades = coincheck_request("GET", "/api/exchange/orders/transactions_pagination", params={
    "limit": 25
})
if trades.get("success"):
    for t in trades["data"]:
        print(f"ID:{t['id']} {t['side']} rate={t['rate']} fee={t['fee']} {t['fee_currency']}")

```bash

## 账户管理 API

| 端点 | 方法 | 描述 |

|------|------|------|

| /api/accounts | GET | 账户信息 (含费率) |

| /api/accounts/balance | GET | 余额查询 |

| /api/accounts/leverage_balance | GET | 杠杆余额 |

| /api/deposit_money | GET | 充值记录 |

| /api/deposit_money/{id}/fast | POST | 快速充值 |

| /api/send_money | POST | 发送加密货币 |

| /api/send_money | GET | 发送记录 |

| /api/withdraws | POST | 法币提现 |

| /api/withdraws | GET | 提现记录 |

| /api/withdraws/{id} | DELETE | 取消提现 |

| /api/bank_accounts | GET/POST | 银行账户管理 |

| /api/bank_accounts/{id} | DELETE | 删除银行账户 |

```python

# 查询账户信息（含费率）

account = coincheck_request("GET", "/api/accounts")

# 响应: {"success": true, "id": "...", "taker_fee": "0.0", "maker_fee": "0.0",

# "exchange_fees": {"btc_jpy": {"taker_fee": "0.0", "maker_fee": "0.0"}, ...}}

# 充值记录

deposits = coincheck_request("GET", "/api/deposit_money")

# 响应: {"success": true, "deposits": [{"id": 2, "amount": "0.05", "currency": "BTC",

# "address": "...", "status": "confirmed", ...}]}

# 提现记录

withdrawals = coincheck_request("GET", "/api/withdraws")

```bash

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| 下单 | 4 次/秒 | 超过返回 429 |

| 通用 REST | ~1.5 秒间隔 | 按 CCXT rateLimit |

> 超限返回 `429: too_many_requests`

## WebSocket 支持

### 连接信息

- *URL**: `wss://ws-api.coincheck.com`

> 数据约每 0.1 秒在有成交时推送。建议实现自动重连逻辑。

### 频道

| 频道 | 描述 | 示例 |

|------|------|------|

| `{pair}-orderbook` | 订单簿 | `btc_jpy-orderbook` |

| `{pair}-trades` | 实时成交 | `btc_jpy-trades` |

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)
    if isinstance(data, list):

# trades: [trade_id, pair, rate, amount, order_type]
        print(f"Trade: pair={data[1]}, rate={data[2]}, amount={data[3]}, side={data[4]}")
    elif isinstance(data, dict):

# orderbook snapshot/update
        print(f"Orderbook update: asks={len(data.get('asks', []))}, "
              f"bids={len(data.get('bids', []))}")

def on_open(ws):

# 订阅订单簿
    ws.send(json.dumps({
        "type": "subscribe",
        "channel": "btc_jpy-orderbook"
    }))

# 订阅成交
    ws.send(json.dumps({
        "type": "subscribe",
        "channel": "btc_jpy-trades"
    }))

ws = websocket.WebSocketApp(
    "wss://ws-api.coincheck.com",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever(ping_interval=30)

```bash

## 错误处理

### 常见错误

| 错误 | 描述 |

|------|------|

| `disabled API Key` | API Key 已禁用 |

| `invalid authentication` | 认证失败（签名错误） |

| `too_many_requests` (429) | 请求频率过高 |

### 响应格式

成功: `{"success": true, ...}`
失败: `{"success": false, "error": "错误描述"}`

### Python 错误处理

```python
def safe_coincheck_request(method, path, params=None):
    try:
        result = coincheck_request(method, path, params)
        if result.get("success"):
            return result
        print(f"Coincheck Error: {result.get('error', 'Unknown')}")
        return None
    except Exception as e:
        print(f"Request failed: {e}")
        return None

```bash

## 变更历史

### 2026-02-27

- 基于 CCXT 源码和官方文档搜索结果完善
- 添加 HMAC SHA256 签名认证完整 Python 示例（已验证签名逻辑）
- 添加市场数据 API（Ticker、汇率、订单簿、成交）及真实响应格式
- 添加交易 API（下单/撤单/查询）含所有订单类型参数说明
- 添加账户管理 API 完整端点列表
- 添加 WebSocket 订阅示例
- 添加错误处理

- --

## 相关资源

- [Coincheck 官方 API 文档](<https://coincheck.com/documents/exchange/api)>
- [CCXT Coincheck 实现](<https://github.com/ccxt/ccxt/blob/master/python/ccxt/coincheck.py)>
- [coincheck Python 库](<https://github.com/kmn/coincheck)>
- [Coincheck 官网](<https://coincheck.com)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 Coincheck 官方 API 文档及 CCXT 源码验证整理。*
