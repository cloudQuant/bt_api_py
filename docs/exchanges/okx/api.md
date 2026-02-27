# OKX API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: V5
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: https://www.okx.com/docs-v5/en/
- Python SDK: https://github.com/okxapi/python-okx

## 交易所基本信息
- 官方名称: OKX (原 OKEx)
- 官网: https://www.okx.com
- 交易所类型: CEX (中心化交易所)
- 总部: 塞舌尔
- 支持的交易对: 600+ (USDT, USDC, BTC 计价)
- 支持的交易类型: 现货(Spot)、保证金(Margin)、永续合约(Swap)、交割合约(Futures)、期权(Options)
- 手续费: Maker 0.08%, Taker 0.10% (现货基础费率，VIP 阶梯)
- 特点: 全品类衍生品，统一账户，跟单交易，Web3 钱包
- Python SDK: `pip install python-okx`

## API基础URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST API | `https://www.okx.com` | 主端点 |
| REST API (AWS) | `https://aws.okx.com` | AWS 节点 |
| Demo Trading REST | `https://www.okx.com` | 模拟盘（header 标识） |
| WebSocket (公共) | `wss://ws.okx.com:8443/ws/v5/public` | 公共频道 |
| WebSocket (私有) | `wss://ws.okx.com:8443/ws/v5/private` | 私有频道 |
| WebSocket (业务) | `wss://ws.okx.com:8443/ws/v5/business` | 业务频道 |
| WebSocket (AWS 公共) | `wss://wsaws.okx.com:8443/ws/v5/public` | AWS 公共 |
| WebSocket (AWS 私有) | `wss://wsaws.okx.com:8443/ws/v5/private` | AWS 私有 |

> 模拟盘请求需添加 Header: `x-simulated-trading: 1`

## 认证方式

### API密钥获取

1. 登录 OKX 账户
2. 进入 API 管理页面
3. 创建 API Key，获取 API Key、Secret Key 和 Passphrase
4. 设置权限（读取、交易、提现）和 IP 限制
5. 可选择绑定到特定交易账户

### HMAC SHA256 签名

**签名步骤**:
1. 获取 ISO 格式时间戳: `2024-01-01T00:00:00.000Z`
2. 拼接签名字符串: `timestamp + method + requestPath + body`
3. 使用 Secret Key 进行 HMAC SHA256 签名
4. 将签名结果进行 Base64 编码

**请求头**:

| Header | 描述 |
|--------|------|
| OK-ACCESS-KEY | API Key |
| OK-ACCESS-SIGN | Base64(HMAC-SHA256(签名字符串)) |
| OK-ACCESS-TIMESTAMP | ISO 时间戳 |
| OK-ACCESS-PASSPHRASE | API Passphrase |
| Content-Type | application/json |

### Python 签名示例

```python
import hmac
import hashlib
import base64
import datetime
import json
import requests

API_KEY = "your_api_key"
SECRET_KEY = "your_secret_key"
PASSPHRASE = "your_passphrase"
BASE_URL = "https://www.okx.com"

def get_timestamp():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.') + \
           datetime.datetime.utcnow().strftime('%f')[:3] + 'Z'

def sign(timestamp, method, request_path, body=''):
    message = timestamp + method.upper() + request_path + body
    mac = hmac.new(
        SECRET_KEY.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    )
    return base64.b64encode(mac.digest()).decode('utf-8')

def okx_request(method, path, params=None, body=None):
    """发送 OKX 签名请求"""
    timestamp = get_timestamp()

    if params and method == "GET":
        query = "&".join(f"{k}={v}" for k, v in params.items())
        request_path = f"{path}?{query}"
    else:
        request_path = path

    body_str = json.dumps(body) if body else ""
    signature = sign(timestamp, method, request_path, body_str)

    headers = {
        "OK-ACCESS-KEY": API_KEY,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": PASSPHRASE,
        "Content-Type": "application/json",
    }

    url = f"{BASE_URL}{request_path}"
    if method == "GET":
        resp = requests.get(url, headers=headers)
    elif method == "POST":
        resp = requests.post(url, headers=headers, data=body_str)
    else:
        raise ValueError(f"Unsupported: {method}")

    return resp.json()
```

### 使用官方 Python SDK

```python
# pip install python-okx
import okx.MarketData as MarketData
import okx.Trade as Trade
import okx.Account as Account

# 公共数据（无需认证）
market_api = MarketData.MarketAPI(flag="0")  # 0=实盘, 1=模拟盘

# 私有接口
trade_api = Trade.TradeAPI(API_KEY, SECRET_KEY, PASSPHRASE, False, flag="0")
account_api = Account.AccountAPI(API_KEY, SECRET_KEY, PASSPHRASE, False, flag="0")
```

## 市场数据API

> 公共 API 无需认证。

### 1. 获取交易产品

**端点**: `GET /api/v5/public/instruments`

**参数**: `instType` (必需: SPOT/MARGIN/SWAP/FUTURES/OPTION), `instId` (可选)

```python
resp = requests.get(f"{BASE_URL}/api/v5/public/instruments", params={"instType": "SPOT"})
data = resp.json()
if data["code"] == "0":
    for inst in data["data"][:5]:
        print(f"{inst['instId']}: base={inst['baseCcy']}, quote={inst['quoteCcy']}, "
              f"tickSz={inst['tickSz']}, lotSz={inst['lotSz']}, state={inst['state']}")

# SDK
result = market_api.get_tickers(instType="SPOT")
```

### 2. 获取 Ticker

**端点**: `GET /api/v5/market/ticker` 或 `GET /api/v5/market/tickers`

```python
# 单个
resp = requests.get(f"{BASE_URL}/api/v5/market/ticker", params={"instId": "BTC-USDT"})
data = resp.json()
if data["code"] == "0":
    t = data["data"][0]
    print(f"BTC-USDT: last={t['last']}, bid={t['bidPx']}, ask={t['askPx']}, "
          f"high24h={t['high24h']}, low24h={t['low24h']}, vol24h={t['vol24h']}")

# 全部现货
resp = requests.get(f"{BASE_URL}/api/v5/market/tickers", params={"instType": "SPOT"})
print(f"Total: {len(resp.json()['data'])}")
```

### 3. 获取订单簿

**端点**: `GET /api/v5/market/books`

**参数**: `instId` (必需), `sz` (可选, 深度档数, 默认1, 最大400)

```python
resp = requests.get(f"{BASE_URL}/api/v5/market/books", params={
    "instId": "BTC-USDT", "sz": "10"
})
data = resp.json()
if data["code"] == "0":
    book = data["data"][0]
    for ask in book["asks"][:5]:
        print(f"ASK: price={ask[0]}, qty={ask[1]}, orders={ask[3]}")
    for bid in book["bids"][:5]:
        print(f"BID: price={bid[0]}, qty={bid[1]}, orders={bid[3]}")
```

### 4. 获取最近成交

**端点**: `GET /api/v5/market/trades`

**参数**: `instId` (必需), `limit` (可选, 默认100, 最大500)

```python
resp = requests.get(f"{BASE_URL}/api/v5/market/trades", params={
    "instId": "BTC-USDT", "limit": "10"
})
for t in resp.json()["data"]:
    print(f"Price={t['px']}, Size={t['sz']}, Side={t['side']}, Time={t['ts']}")
```

### 5. 获取K线数据

**端点**: `GET /api/v5/market/candles`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| instId | STRING | 是 | 产品ID |
| bar | STRING | 否 | K线周期: 1m/3m/5m/15m/30m/1H/2H/4H/6H/12H/1D/1W/1M/3M (默认1m) |
| after | STRING | 否 | 此时间之前的数据 (毫秒时间戳) |
| before | STRING | 否 | 此时间之后的数据 |
| limit | STRING | 否 | 数量 (默认100, 最大300) |

```python
resp = requests.get(f"{BASE_URL}/api/v5/market/candles", params={
    "instId": "BTC-USDT",
    "bar": "1H",
    "limit": "24"
})
for c in resp.json()["data"]:
    # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    print(f"T={c[0]} O={c[1]} H={c[2]} L={c[3]} C={c[4]} V={c[5]}")
```

### 6. 获取标记价格

**端点**: `GET /api/v5/public/mark-price`

### 7. 获取资金费率

**端点**: `GET /api/v5/public/funding-rate`

```python
resp = requests.get(f"{BASE_URL}/api/v5/public/funding-rate", params={"instId": "BTC-USDT-SWAP"})
data = resp.json()
if data["code"] == "0":
    fr = data["data"][0]
    print(f"Current: {fr['fundingRate']}, Next: {fr['nextFundingRate']}, Time: {fr['fundingTime']}")
```

## 交易API

> 以下端点均需签名认证。

### 1. 查询账户余额

**端点**: `GET /api/v5/account/balance`

**参数**: `ccy` (可选, 逗号分隔)

```python
balance = okx_request("GET", "/api/v5/account/balance")
if balance["code"] == "0":
    for detail in balance["data"][0]["details"]:
        eq = float(detail.get("eq", 0))
        if eq > 0:
            print(f"{detail['ccy']}: eq={detail['eq']}, availEq={detail['availEq']}, "
                  f"frozenBal={detail['frozenBal']}")

# SDK
result = account_api.get_account_balance()
```

### 2. 下单

**端点**: `POST /api/v5/trade/order`

**参数**:

| 参数 | 类型 | 必需 | 描述 |
|------|------|------|------|
| instId | STRING | 是 | 产品ID，如 BTC-USDT |
| tdMode | STRING | 是 | 交易模式: cash(现货)/cross(全仓)/isolated(逐仓) |
| side | STRING | 是 | buy / sell |
| ordType | STRING | 是 | market / limit / post_only / fok / ioc / optimal_limit_ioc |
| sz | STRING | 是 | 数量 |
| px | STRING | 条件 | 价格（limit 必需） |
| ccy | STRING | 条件 | 保证金币种（tdMode=cross/isolated 时） |
| clOrdId | STRING | 否 | 客户端订单ID |
| tag | STRING | 否 | 订单标签 |
| tgtCcy | STRING | 否 | 市价单指定: base_ccy / quote_ccy |

```python
# 现货限价买单
order = okx_request("POST", "/api/v5/trade/order", body={
    "instId": "BTC-USDT",
    "tdMode": "cash",
    "side": "buy",
    "ordType": "limit",
    "sz": "0.001",
    "px": "40000"
})
if order["code"] == "0":
    print(f"Order ID: {order['data'][0]['ordId']}, "
          f"Client ID: {order['data'][0]['clOrdId']}")

# 现货市价买单（按金额）
order = okx_request("POST", "/api/v5/trade/order", body={
    "instId": "BTC-USDT",
    "tdMode": "cash",
    "side": "buy",
    "ordType": "market",
    "sz": "100",
    "tgtCcy": "quote_ccy"  # 100 USDT
})

# 合约开多（全仓）
order = okx_request("POST", "/api/v5/trade/order", body={
    "instId": "BTC-USDT-SWAP",
    "tdMode": "cross",
    "side": "buy",
    "ordType": "limit",
    "sz": "1",  # 张数
    "px": "40000"
})

# SDK
result = trade_api.place_order(
    instId="BTC-USDT", tdMode="cash", side="buy",
    ordType="limit", sz="0.001", px="40000"
)
```

### 3. 批量下单

**端点**: `POST /api/v5/trade/batch-orders`

```python
orders = okx_request("POST", "/api/v5/trade/batch-orders", body=[
    {"instId": "BTC-USDT", "tdMode": "cash", "side": "buy", "ordType": "limit", "sz": "0.001", "px": "39000"},
    {"instId": "BTC-USDT", "tdMode": "cash", "side": "buy", "ordType": "limit", "sz": "0.001", "px": "38000"},
])
```

### 4. 撤单

**端点**: `POST /api/v5/trade/cancel-order`

```python
result = okx_request("POST", "/api/v5/trade/cancel-order", body={
    "instId": "BTC-USDT",
    "ordId": "12345678"
})

# 或使用 clOrdId
result = okx_request("POST", "/api/v5/trade/cancel-order", body={
    "instId": "BTC-USDT",
    "clOrdId": "my_order_001"
})
```

### 5. 批量撤单

**端点**: `POST /api/v5/trade/cancel-batch-orders`

### 6. 修改订单

**端点**: `POST /api/v5/trade/amend-order`

```python
result = okx_request("POST", "/api/v5/trade/amend-order", body={
    "instId": "BTC-USDT",
    "ordId": "12345678",
    "newSz": "0.002",
    "newPx": "41000"
})
```

### 7. 查询订单

**端点**: `GET /api/v5/trade/order`

```python
order = okx_request("GET", "/api/v5/trade/order", params={
    "instId": "BTC-USDT", "ordId": "12345678"
})
if order["code"] == "0":
    o = order["data"][0]
    print(f"State: {o['state']}, Filled: {o['accFillSz']}/{o['sz']}, "
          f"AvgPx: {o['avgPx']}, Fee: {o['fee']}")
```

**订单状态**: `live`(挂单中), `partially_filled`, `filled`, `canceled`

### 8. 查询挂单列表

**端点**: `GET /api/v5/trade/orders-pending`

```python
orders = okx_request("GET", "/api/v5/trade/orders-pending", params={
    "instType": "SPOT"
})
if orders["code"] == "0":
    for o in orders["data"]:
        print(f"{o['instId']}: {o['side']} {o['ordType']} px={o['px']} sz={o['sz']}")
```

### 9. 查询历史订单

**端点**: `GET /api/v5/trade/orders-history-archive`

### 10. 查询成交明细

**端点**: `GET /api/v5/trade/fills`

## 账户管理API

| 端点 | 方法 | 描述 |
|------|------|------|
| /api/v5/account/balance | GET | 账户余额 |
| /api/v5/account/positions | GET | 持仓信息 |
| /api/v5/account/config | GET | 账户配置 |
| /api/v5/account/set-leverage | POST | 设置杠杆 |
| /api/v5/account/set-position-mode | POST | 设置持仓模式 |
| /api/v5/asset/balances | GET | 资金账户余额 |
| /api/v5/asset/transfer | POST | 资金划转 |
| /api/v5/asset/deposit-address | GET | 充值地址 |
| /api/v5/asset/withdrawal | POST | 提现 |
| /api/v5/asset/deposit-history | GET | 充值记录 |
| /api/v5/asset/withdrawal-history | GET | 提现记录 |

```python
# 查询持仓
positions = okx_request("GET", "/api/v5/account/positions")
if positions["code"] == "0":
    for p in positions["data"]:
        if float(p.get("pos", 0)) != 0:
            print(f"{p['instId']}: pos={p['pos']}, avgPx={p['avgPx']}, "
                  f"upl={p['upl']}, lever={p['lever']}")

# 设置杠杆
result = okx_request("POST", "/api/v5/account/set-leverage", body={
    "instId": "BTC-USDT-SWAP",
    "lever": "10",
    "mgnMode": "cross"
})
```

## 速率限制

| 类别 | 限制 | 说明 |
|------|------|------|
| 公共端点 | 20 次/2秒 | 按 IP |
| 下单 | 60 次/2秒 | 按账户+产品 |
| 批量下单 | 300 次/2秒 | 按账户 |
| 撤单 | 60 次/2秒 | 按账户+产品 |
| 查询挂单 | 60 次/2秒 | 按账户 |
| 查询余额 | 10 次/2秒 | 按账户 |

### 最佳实践

- 使用 WebSocket 获取实时行情
- 使用 `clOrdId` 管理订单
- 统一账户下各产品共享保证金
- 使用模拟盘测试: 添加 `x-simulated-trading: 1` 头
- 使用 AWS 节点获得更低延迟（亚太地区）

## WebSocket支持

### 连接信息

| URL | 用途 |
|-----|------|
| `wss://ws.okx.com:8443/ws/v5/public` | 公共频道 |
| `wss://ws.okx.com:8443/ws/v5/private` | 私有频道（需认证） |
| `wss://ws.okx.com:8443/ws/v5/business` | 业务频道（K线等） |

### 公共频道

| 频道 | 描述 |
|------|------|
| `tickers` | Ticker |
| `books` / `books5` / `books50-l2-tbt` | 订单簿 |
| `trades` | 实时成交 |
| `mark-price` | 标记价格 |
| `funding-rate` | 资金费率 |
| `index-tickers` | 指数 Ticker |

### 业务频道

| 频道 | 描述 |
|------|------|
| `candle1m` ~ `candle1M` | K线数据 |

### Python WebSocket 示例

```python
import websocket
import json
import time
import hmac
import hashlib
import base64

def on_message(ws, message):
    data = json.loads(message)
    event = data.get("event", "")
    arg = data.get("arg", {})

    if event == "subscribe":
        print(f"Subscribed: {arg}")
    elif "data" in data:
        channel = arg.get("channel", "")
        if channel == "tickers":
            for t in data["data"]:
                print(f"Ticker {t['instId']}: last={t['last']}, vol={t['vol24h']}")
        elif channel == "trades":
            for t in data["data"]:
                print(f"Trade {t['instId']}: px={t['px']}, sz={t['sz']}, side={t['side']}")
        elif "books" in channel:
            for b in data["data"]:
                print(f"Book: asks={len(b['asks'])}, bids={len(b['bids'])}")

def on_open(ws):
    ws.send(json.dumps({
        "op": "subscribe",
        "args": [
            {"channel": "tickers", "instId": "BTC-USDT"},
            {"channel": "trades", "instId": "BTC-USDT"},
            {"channel": "books5", "instId": "BTC-USDT"}
        ]
    }))

ws = websocket.WebSocketApp(
    "wss://ws.okx.com:8443/ws/v5/public",
    on_open=on_open,
    on_message=on_message,
    on_error=lambda ws, err: print(f"Error: {err}"),
    on_close=lambda ws, code, msg: print("Closed")
)
ws.run_forever(ping_interval=25)
```

### WebSocket 认证（私有频道）

```python
def ws_login(ws):
    timestamp = str(int(time.time()))
    sign_str = timestamp + "GET" + "/users/self/verify"
    sig = base64.b64encode(
        hmac.new(SECRET_KEY.encode(), sign_str.encode(), hashlib.sha256).digest()
    ).decode()

    ws.send(json.dumps({
        "op": "login",
        "args": [{
            "apiKey": API_KEY,
            "passphrase": PASSPHRASE,
            "timestamp": timestamp,
            "sign": sig
        }]
    }))
```

## 错误代码

### 响应格式

```json
{
  "code": "0",
  "msg": "",
  "data": [...]
}
```

> `code` 为 `"0"` 表示成功

### 常见错误码

| 错误码 | 描述 |
|--------|------|
| 0 | 成功 |
| 50000 | 系统繁忙 |
| 50001 | 系统维护 |
| 50004 | API endpoint 请求超时 |
| 50011 | 请求频率超限 |
| 50013 | 系统繁忙，请稍后再试 |
| 50014 | 请求参数不能为空 |
| 50102 | 时间戳无效 |
| 50103 | 签名无效 |
| 50104 | IP 不在白名单 |
| 50105 | API Key 对应的权限不足 |
| 51000 | 参数错误 |
| 51001 | instId 不存在 |
| 51004 | 订单不存在 |
| 51008 | 余额不足 |
| 51010 | 超过最大挂单数 |
| 51020 | 订单价格超出范围 |

### Python 错误处理

```python
def safe_okx_request(method, path, params=None, body=None):
    try:
        result = okx_request(method, path, params, body)
        if result.get("code") == "0":
            return result.get("data")
        code = result.get("code")
        msg = result.get("msg", "Unknown")
        print(f"OKX Error [{code}]: {msg}")
        if code == "50011":
            print("Rate limited, waiting...")
            time.sleep(1)
        return None
    except Exception as e:
        print(f"Request failed: {e}")
        return None
```

## 变更历史

### 2026-02-27
- 创建完整 V5 API 文档
- 添加 HMAC SHA256 + Base64 签名认证 Python 示例
- 添加官方 Python SDK 使用示例
- 添加市场数据 API（Ticker、订单簿、成交、K线、资金费率）
- 添加交易 API（下单、批量下单、撤单、改单、查询）
- 添加账户管理（余额、持仓、杠杆、资金划转）
- 添加 WebSocket (公共/私有/业务) 订阅和认证示例
- 添加错误代码表和错误处理

---

## 相关资源

- [OKX API V5 文档](https://www.okx.com/docs-v5/en/)
- [OKX Python SDK](https://github.com/okxapi/python-okx)
- [OKX 官网](https://www.okx.com)
- [CCXT OKX 实现](https://github.com/ccxt/ccxt)

---

*本文档由 bt_api_py 项目维护，内容基于 OKX 官方 API V5 文档整理。*
