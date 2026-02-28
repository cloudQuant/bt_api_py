# Phemex API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V2
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://phemex-docs.github.io/>

## 交易所基本信息

- 官方名称: Phemex
- 官网: <https://phemex.com>
- 交易所类型: CEX (中心化交易所)
- 总部: 新加坡
- 支持的交易对: 200+ (USDT, USD 计价)
- 支持的交易类型: 现货(Spot)、USDT-M 永续合约(Perpetuals)、反向合约
- 手续费: Maker -0.025%, Taker 0.075% (合约); Maker 0.1%, Taker 0.1% (现货)
- 特点: 由前摩根士丹利高管创立，高性能撮合引擎

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API | `<https://api.phemex.com`> | 主端点 |

| REST API (VIP) | `<https://vapi.phemex.com`> | VIP 白名单 IP |

| REST API (测试网) | `<https://testnet-api.phemex.com`> | 测试网 |

| WebSocket | `wss://ws.phemex.com` | 主端点 |

| WebSocket (VIP) | `wss://vapi.phemex.com/ws` | VIP |

| WebSocket (测试网) | `wss://testnet-api.phemex.com/ws` | 测试网 |

## 认证方式

### API 密钥获取

1. 登录 Phemex 账户
2. 进入 API 管理页面
3. 创建 API Key 并设置权限
4. 保存 API Key 和 Secret

### HMAC SHA256 签名

- *签名步骤**:
1. 获取过期时间（当前秒+60）
2. 拼接签名字符串: `URL_Path + QueryString + Expiry + Body`
3. 使用 Secret 进行 HMAC SHA256 签名
4. 将签名转为十六进制

- *请求头**:

| Header | 描述 |

|--------|------|

| x-phemex-access-token | API Key |

| x-phemex-request-expiry | 过期时间（秒级时间戳） |

| x-phemex-request-signature | HMAC SHA256 签名 |

### Python 签名示例

```python
import hmac
import hashlib
import time
import json
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "<https://api.phemex.com">

def phemex_request(method, path, params=None, body=None):
    """发送 Phemex 签名请求"""
    expiry = str(int(time.time()) + 60)

# 构建查询字符串
    if params:
        query_string = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    else:
        query_string = ""

    body_str = json.dumps(body) if body else ""

# 签名字符串: path + queryString + expiry + body
    sign_str = path + query_string + expiry + body_str
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        sign_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "x-phemex-access-token": API_KEY,
        "x-phemex-request-expiry": expiry,
        "x-phemex-request-signature": signature,
        "Content-Type": "application/json",
    }

    url = f"{BASE_URL}{path}"
    if query_string:
        url += f"?{query_string}"

    if method == "GET":
        resp = requests.get(url, headers=headers)
    elif method == "POST":
        resp = requests.post(url, headers=headers, data=body_str)
    elif method == "PUT":
        resp = requests.put(url, headers=headers, data=body_str)
    elif method == "DELETE":
        resp = requests.delete(url, headers=headers)
    else:
        raise ValueError(f"Unsupported: {method}")

    return resp.json()

```bash

## 市场数据 API

> 公共 API 无需认证。

### 1. 获取交易对信息

- *端点**: `GET /public/products`

```python
resp = requests.get(f"{BASE_URL}/public/products")
data = resp.json()
if data["code"] == 0:
    for p in data["data"]["products"][:5]:
        print(f"{p['symbol']}: type={p['type']}, status={p['status']}")

```bash

### 2. 获取 Ticker (合约)

- *端点**: `GET /md/v2/ticker/24hr`

- *参数**: `symbol` (必需)

```python
resp = requests.get(f"{BASE_URL}/md/v2/ticker/24hr", params={"symbol": "BTCUSDT"})
data = resp.json()
if data["code"] == 0:
    t = data["data"]
    print(f"BTCUSDT: last={t['lastEp']}, high={t['highEp']}, low={t['lowEp']}, "
          f"vol={t['volumeEv']}")

```bash
> **注意**: Phemex 使用缩放精度（Ep/Ev/Er）来表示价格和数量，需要除以对应的 scale factor。

### 3. 获取订单簿

- *端点**: `GET /md/v2/orderbook`

- *参数**: `symbol` (必需)

```python
resp = requests.get(f"{BASE_URL}/md/v2/orderbook", params={"symbol": "BTCUSDT"})
data = resp.json()
if data["code"] == 0:
    book = data["data"]["book"]
    for ask in book["asks"][:5]:
        print(f"ASK: price={ask[0]}, qty={ask[1]}")
    for bid in book["bids"][:5]:
        print(f"BID: price={bid[0]}, qty={bid[1]}")

```bash

### 4. 获取最近成交

- *端点**: `GET /md/v2/trade`

- *参数**: `symbol` (必需)

```python
resp = requests.get(f"{BASE_URL}/md/v2/trade", params={"symbol": "BTCUSDT"})
data = resp.json()
if data["code"] == 0:
    for t in data["data"]["trades"][:5]:
        print(f"Price={t[0]}, Qty={t[1]}, Side={t[2]}, Time={t[3]}")

```bash

### 5. 获取 K 线数据

- *端点**: `GET /md/v2/kline`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | STRING | 是 | 交易对 |

| resolution | INT | 是 | 秒数: 60/300/900/1800/3600/14400/86400/604800/2592000 |

| from | INT | 否 | 开始时间（秒） |

| to | INT | 否 | 结束时间（秒） |

| limit | INT | 否 | 数量 |

```python
import time as t
resp = requests.get(f"{BASE_URL}/md/v2/kline", params={
    "symbol": "BTCUSDT",
    "resolution": 3600,
    "limit": 24
})
data = resp.json()
if data["code"] == 0:
    for c in data["data"]["rows"]:
        print(f"T={c[0]} O={c[1]} H={c[2]} L={c[3]} C={c[4]} V={c[5]}")

```bash

### 6. 获取服务器时间

- *端点**: `GET /exchange/public/md/v2/timestamp`

## 交易 API

### 1. 查询账户（现货）

- *端点**: `GET /spot/wallets`

```python
wallets = phemex_request("GET", "/spot/wallets")
if wallets["code"] == 0:
    for w in wallets["data"]:
        if int(w.get("balanceEv", 0)) > 0:
            print(f"{w['currency']}: balance={w['balanceEv']}")

```bash

### 2. 现货下单

- *端点**: `POST /spot/orders/create`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| symbol | STRING | 是 | 交易对 |

| side | STRING | 是 | Buy / Sell |

| type | STRING | 是 | Limit / Market |

| qtyType | STRING | 是 | ByBase / ByQuote |

| quoteQtyEv | LONG | 条件 | 金额（ByQuote 时） |

| baseQtyEv | LONG | 条件 | 数量（ByBase 时） |

| priceEp | LONG | 条件 | 价格（Limit 必需） |

| timeInForce | STRING | 否 | GoodTillCancel / ImmediateOrCancel / FillOrKill / PostOnly |

```python

# 限价买单

order = phemex_request("POST", "/spot/orders/create", body={
    "symbol": "sBTCUSDT",
    "side": "Buy",
    "type": "Limit",
    "qtyType": "ByBase",
    "baseQtyEv": 100000,  # scaled
    "priceEp": 4000000000,  # scaled
    "timeInForce": "GoodTillCancel"
})
if order["code"] == 0:
    print(f"Order ID: {order['data']['orderID']}")

```bash
> 现货交易对以 `s` 前缀，如 `sBTCUSDT`

### 3. 合约下单

- *端点**: `POST /g-orders/create`

```python
order = phemex_request("POST", "/g-orders/create", body={
    "symbol": "BTCUSDT",
    "side": "Buy",
    "orderType": "Limit",
    "orderQtyRq": "0.01",
    "priceRp": "40000",
    "timeInForce": "GoodTillCancel"
})
if order["code"] == 0:
    print(f"Futures order: {order['data']['orderID']}")

```bash

### 4. 撤单

- *端点**: `DELETE /spot/orders` (现货) / `DELETE /g-orders/cancel` (合约)

```python

# 现货撤单

result = phemex_request("DELETE", "/spot/orders", params={
    "symbol": "sBTCUSDT",
    "orderID": "order_id_here"
})

# 合约撤单

result = phemex_request("DELETE", "/g-orders/cancel", params={
    "symbol": "BTCUSDT",
    "orderID": "order_id_here"
})

```bash

### 5. 查询挂单

- *端点**: `GET /spot/orders/active` (现货) / `GET /g-orders/activeList` (合约)

```python
orders = phemex_request("GET", "/spot/orders/active", params={"symbol": "sBTCUSDT"})
if orders["code"] == 0:
    for o in orders["data"]["rows"]:
        print(f"ID:{o['orderID']} {o['side']} price={o['priceEp']} qty={o['baseQtyEv']}")

```bash

### 6. 查询持仓（合约）

- *端点**: `GET /g-accounts/accountPositions`

```python
positions = phemex_request("GET", "/g-accounts/accountPositions", params={"currency": "USDT"})
if positions["code"] == 0:
    for p in positions["data"]["positions"]:
        if float(p.get("sizeRq", 0)) != 0:
            print(f"{p['symbol']}: size={p['sizeRq']}, avgEntry={p['avgEntryPriceRp']}, "
                  f"unrealizedPnl={p['unrealisedPnlRv']}")

```bash

## 账户管理 API

| 端点 | 方法 | 描述 |

|------|------|------|

| /spot/wallets | GET | 现货钱包余额 |

| /g-accounts/accountPositions | GET | 合约账户与持仓 |

| /exchange/wallets/depositAddress | GET | 充值地址 |

| /exchange/wallets/depositList | GET | 充值记录 |

| /exchange/wallets/withdrawList | GET | 提现记录 |

| /assets/transfer | POST | 资产划转 |

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| IP 限频 | 5000 次/5 分钟 | 按 IP |

| 用户限频 | 按端点不同 | 1 分钟窗口 |

| 合约组 | 5000 次/分钟 | Contract group |

| 交易对组 | 500 次/分钟 | Symbol group |

| 测试网 | 500 次/5 分钟 | 共享限频 |

### 最佳实践

- 使用 WebSocket 获取实时行情
- 使用测试网进行开发: `<https://testnet-api.phemex.com`>
- 注意 Phemex 使用缩放精度（Ep/Ev/Er/Rp/Rq/Rv）
- VIP 用户可使用专属端点获得更高限额

## WebSocket 支持

### 连接信息

- *URL**: `wss://ws.phemex.com`

### 公共频道

| 频道 | 描述 |

|------|------|

| `tick.v2.{symbol}` | 合约 Ticker |

| `spot_market24h.v2.{symbol}` | 现货 24h 行情 |

| `orderbook.v2.{symbol}` | 订单簿 |

| `trade.v2.{symbol}` | 实时成交 |

| `kline.v2.{resolution}.{symbol}` | K 线 |

### Python WebSocket 示例

```python
import websocket
import json

def on_message(ws, message):
    data = json.loads(message)

    if "tick_v2" in data:
        t = data["tick_v2"]
        print(f"Ticker: last={t.get('lastEp')}")
    elif "trades_v2" in data:
        for t in data["trades_v2"]:
            print(f"Trade: price={t[0]}, qty={t[1]}, side={t[2]}")
    elif "book" in data:
        book = data["book"]
        print(f"Book: asks={len(book.get('asks', []))}, bids={len(book.get('bids', []))}")

def on_open(ws):

# 订阅 Ticker
    ws.send(json.dumps({
        "id": 1,
        "method": "tick_v2.subscribe",
        "params": ["BTCUSDT"]
    }))

# 订阅成交
    ws.send(json.dumps({
        "id": 2,
        "method": "trade_v2.subscribe",
        "params": ["BTCUSDT"]
    }))

# 订阅订单簿
    ws.send(json.dumps({
        "id": 3,
        "method": "orderbook_v2.subscribe",
        "params": ["BTCUSDT"]
    }))

ws = websocket.WebSocketApp(
    "wss://ws.phemex.com",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever(ping_interval=30)

```bash

## 错误代码

### 常见错误码

| 错误码 | 描述 |

|--------|------|

| 0 | 成功 |

| 10001 | 参数错误 |

| 10002 | 签名验证失败 |

| 10003 | API Key 无效 |

| 10005 | 请求过期 |

| 11004 | 余额不足 |

| 11006 | 订单不存在 |

| 11008 | 超过最大持仓 |

| 11009 | 订单价格超出范围 |

| 11010 | 超过最大订单数 |

| 39999 | 系统错误 |

### Python 错误处理

```python
def safe_phemex_request(method, path, **kwargs):
    try:
        result = phemex_request(method, path, **kwargs)
        if result.get("code") == 0:
            return result.get("data")
        print(f"Phemex Error [{result.get('code')}]: {result.get('msg', 'Unknown')}")
        return None
    except Exception as e:
        print(f"Request failed: {e}")
        return None

```bash

## 变更历史

### 2026-02-27

- 完善文档，添加详细 REST API 端点说明
- 添加 HMAC SHA256 签名认证完整 Python 示例
- 添加现货和合约市场数据 API 详细说明
- 添加现货和合约交易 API 完整示例
- 添加 WebSocket 频道订阅示例
- 说明缩放精度（Ep/Ev/Er/Rp/Rq/Rv）机制
- 添加错误代码表和错误处理

- --

## 相关资源

- [Phemex API 文档](<https://phemex-docs.github.io/)>
- [Phemex 官网](<https://phemex.com)>
- [Phemex 测试网](<https://testnet.phemex.com)>
- [CCXT Phemex 实现](<https://github.com/ccxt/ccxt)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 Phemex 官方 API 文档整理。*
