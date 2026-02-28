# Bitbank API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: v1
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://github.com/bitbankinc/bitbank-api-docs>
- 官方公告: <https://blog.bitbank.cc/tag/service>

## 交易所基本信息

- 官方名称: Bitbank (bitbank.cc)
- 官网: <https://bitbank.cc>
- 交易所类型: CEX (中心化交易所)
- 总部: 日本
- 支持的交易对: 30+ (以 JPY 和 BTC 计价为主)
- 支持的交易类型: 现货(Spot)、杠杆(Margin)
- 手续费: Maker -0.02% (返佣), Taker 0.12% (具体因交易对而异)
- 法币支持: JPY (日元)

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| 公共 REST API | `<https://public.bitbank.cc`> | 行情数据（无需认证） |

| 私有 REST API | `<https://api.bitbank.cc/v1`> | 交易和账户（需认证） |

| 实时数据流 | PubNub | 通过 PubNub 服务推送（需获取 channel 和 token） |

## 认证方式

### API 密钥获取

1. 注册 Bitbank 账户并完成 KYC（日本居民）
2. 进入 API 管理页面
3. 创建 API Key 和 API Secret

### 签名方式

Bitbank 支持两种认证方式：**ACCESS-TIME-WINDOW**(推荐) 和**ACCESS-NONCE**。

#### 请求头

| Header | 描述 |

|--------|------|

| ACCESS-KEY | API Key |

| ACCESS-SIGNATURE | HMAC SHA256 签名 |

| ACCESS-REQUEST-TIME | 请求时间戳（毫秒）（TIME-WINDOW 方式） |

| ACCESS-TIME-WINDOW | 请求有效窗口（毫秒，默认 5000，最大 60000） |

| ACCESS-NONCE | 递增整数（NONCE 方式） |

> 如果同时指定 ACCESS-NONCE 和 ACCESS-TIME-WINDOW，TIME-WINDOW 方式优先。

#### ACCESS-TIME-WINDOW 方式（推荐）

- *签名规则**:
- GET: `HMAC-SHA256(ACCESS_REQUEST_TIME + ACCESS_TIME_WINDOW + 完整请求路径(含查询参数))`
- POST: `HMAC-SHA256(ACCESS_REQUEST_TIME + ACCESS_TIME_WINDOW + JSON 请求体)`

#### ACCESS-NONCE 方式

- *签名规则**:
- GET: `HMAC-SHA256(ACCESS_NONCE + 完整请求路径(含/v1 及查询参数))`
- POST: `HMAC-SHA256(ACCESS_NONCE + JSON 请求体)`

### Python 签名示例

```python
import hmac
import time
import json
import requests
from hashlib import sha256

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
PRIVATE_URL = "<https://api.bitbank.cc/v1">
PUBLIC_URL = "<https://public.bitbank.cc">

def signed_get(path, params=None):
    """签名 GET 请求 (ACCESS-TIME-WINDOW 方式)"""
    url = f"{PRIVATE_URL}{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        full_path = f"{path}?{query}"
        url = f"{PRIVATE_URL}{full_path}"
    else:
        full_path = path

    request_time = str(int(time.time() *1000))
    time_window = "5000"
    message = request_time + time_window + full_path
    signature = hmac.new(
        API_SECRET.encode(), message.encode(), sha256
    ).hexdigest()

    headers = {
        "ACCESS-KEY": API_KEY,
        "ACCESS-SIGNATURE": signature,
        "ACCESS-REQUEST-TIME": request_time,
        "ACCESS-TIME-WINDOW": time_window,
    }
    return requests.get(url, headers=headers).json()

def signed_post(path, body):
    """签名 POST 请求 (ACCESS-TIME-WINDOW 方式)"""
    url = f"{PRIVATE_URL}{path}"
    body_str = json.dumps(body)

    request_time = str(int(time.time()*1000))
    time_window = "5000"
    message = request_time + time_window + body_str
    signature = hmac.new(
        API_SECRET.encode(), message.encode(), sha256
    ).hexdigest()

    headers = {
        "ACCESS-KEY": API_KEY,
        "ACCESS-SIGNATURE": signature,
        "ACCESS-REQUEST-TIME": request_time,
        "ACCESS-TIME-WINDOW": time_window,
        "Content-Type": "application/json",
    }
    return requests.post(url, headers=headers, data=body_str).json()

# 测试：获取资产

result = signed_get("/user/assets")
print(result)

```bash

## 市场数据 API

> 公共 API 基础端点: `<https://public.bitbank.cc`，无需认证。>

### 1. 获取 Ticker

- *端点**: `GET /{pair}/ticker`

- *描述**: 获取指定交易对的 24h 行情数据。

- *参数**: `pair` (路径参数) - 交易对，如 `btc_jpy`, `xrp_jpy`, `eth_btc`

```python
resp = requests.get(f"{PUBLIC_URL}/btc_jpy/ticker")
data = resp.json()["data"]
print(f"BTC/JPY Last: {data['last']}, High: {data['high']}, Low: {data['low']}, Vol: {data['vol']}")

```bash

- *响应示例**:

```json
{
  "success": 1,
  "data": {
    "sell": "9750001",
    "buy": "9750000",
    "high": "9800000",
    "low": "9600000",
    "open": "9700000",
    "last": "9750000",
    "vol": "1234.5678",
    "timestamp": 1700000000000
  }
}

```bash

### 2. 批量获取 Ticker

- *端点**: `GET /tickers` / `GET /tickers_jpy`

- *描述**: 获取所有交易对（或仅 JPY 交易对）的行情。

```python
resp = requests.get(f"{PUBLIC_URL}/tickers")
for ticker in resp.json()["data"]:
    print(f"{ticker['pair']}: {ticker['last']}")

```bash

### 3. 获取深度数据

- *端点**: `GET /{pair}/depth`

- *描述**: 获取指定交易对的订单簿，最多各 200 档。

```python
resp = requests.get(f"{PUBLIC_URL}/btc_jpy/depth")
data = resp.json()["data"]
print(f"Best ask: price={data['asks'][0][0]}, amount={data['asks'][0][1]}")
print(f"Best bid: price={data['bids'][0][0]}, amount={data['bids'][0][1]}")

```bash

- *响应字段**:

| 字段 | 类型 | 描述 |

|------|------|------|

| asks | [price, amount][] | 卖单列表 |

| bids | [price, amount][] | 买单列表 |

| asks_over | string | 超出最高卖价的卖单量 |

| bids_under | string | 低于最低买价的买单量 |

| timestamp | number | 时间戳（毫秒） |

| sequenceId | string | 序列号（单调递增） |

### 4. 获取最近成交

- *端点**: `GET /{pair}/transactions/{YYYYMMDD}`

- *参数**: `YYYYMMDD` (可选) - 省略时返回最新 60 条

```python
resp = requests.get(f"{PUBLIC_URL}/btc_jpy/transactions")
for tx in resp.json()["data"]["transactions"]:
    print(f"ID:{tx['transaction_id']} {tx['side']} price={tx['price']} amount={tx['amount']}")

```bash

### 5. 获取 K 线数据

- *端点**: `GET /{pair}/candlestick/{candle-type}/{YYYY}`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| pair | STRING | 是 | 交易对 |

| candle-type | ENUM | 是 | 1min, 5min, 15min, 30min, 1hour, 4hour, 8hour, 12hour, 1day, 1week, 1month |

| YYYY | STRING | 是 | 日期。短周期(1min~1hour)用 YYYYMMDD，长周期(4hour~1month)用 YYYY |

```python

# 获取 BTC/JPY 1 小时 K 线 (2024 年 1 月 1 日)

resp = requests.get(f"{PUBLIC_URL}/btc_jpy/candlestick/1hour/20240101")
data = resp.json()["data"]["candlestick"][0]
for ohlcv in data["ohlcv"]:
    open_, high, low, close, vol, ts = ohlcv
    print(f"Time:{ts} O:{open_} H:{high} L:{low} C:{close} V:{vol}")

```bash

- *响应格式**: ohlcv 数组 `[open, high, low, close, volume, timestamp_ms]`

### 6. 熔断信息

- *端点**: `GET /{pair}/circuit_break_info`

- *描述**: 获取交易对的熔断状态和估计价格。

## 交易 API

> 基础端点: `<https://api.bitbank.cc/v1`，所有端点均需签名认证。>

### 1. 查询资产

- *端点**: `GET /user/assets`

```python
result = signed_get("/user/assets")
if result["success"] == 1:
    for asset in result["data"]["assets"]:
        free = float(asset["free_amount"])
        if free > 0:
            print(f"{asset['asset']}: free={asset['free_amount']}, locked={asset['locked_amount']}")

```bash

- *响应字段**:

| 字段 | 类型 | 描述 |

|------|------|------|

| asset | string | 资产名称 |

| free_amount | string | 可用余额 |

| onhand_amount | string | 总余额 |

| locked_amount | string | 冻结余额 |

| withdrawing_amount | string | 提现中金额 |

| withdrawal_fee | object | 提现手续费 |

| stop_deposit | boolean | 是否停止充值 |

| stop_withdrawal | boolean | 是否停止提现 |

### 2. 下单

- *端点**: `POST /user/spot/order`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| pair | STRING | 是 | 交易对，如 btc_jpy |

| side | ENUM | 是 | buy / sell |

| type | ENUM | 是 | limit, market, stop, stop_limit, take_profit, stop_loss |

| amount | STRING | 条件 | 数量（除 take_profit/stop_loss 外必需） |

| price | STRING | 条件 | 价格（limit/stop_limit 必需） |

| trigger_price | STRING | 条件 | 触发价格（stop/stop_limit/take_profit/stop_loss 必需） |

| post_only | BOOL | 否 | 仅 Maker（仅 limit 类型可用） |

- *Python 示例**:

```python

# 限价买单

order = signed_post("/user/spot/order", {
    "pair": "btc_jpy",
    "side": "buy",
    "type": "limit",
    "price": "9500000",
    "amount": "0.001"
})
print(f"Order ID: {order['data']['order_id']}, Status: {order['data']['status']}")

# 市价卖单

order = signed_post("/user/spot/order", {
    "pair": "btc_jpy",
    "side": "sell",
    "type": "market",
    "amount": "0.001"
})
print(f"Market order: {order['data']['order_id']}")

```bash

- *订单状态枚举**:
- `INACTIVE` - 未激活（止损单等待触发）
- `UNFILLED` - 未成交
- `PARTIALLY_FILLED` - 部分成交
- `FULLY_FILLED` - 完全成交
- `CANCELED_UNFILLED` - 已撤销（未成交）
- `CANCELED_PARTIALLY_FILLED` - 已撤销（部分成交）
- `REJECTED` - 被拒绝

### 3. 撤单

- *端点**: `POST /user/spot/cancel_order`

```python
result = signed_post("/user/spot/cancel_order", {
    "pair": "btc_jpy",
    "order_id": 12345678
})
print(result)

```bash

### 4. 批量撤单

- *端点**: `POST /user/spot/cancel_orders`

- *参数**: `pair` (必需), `order_ids` (必需，数组)

### 5. 查询订单

- *端点**: `GET /user/spot/order`

- *参数**: `pair` (必需), `order_id` (必需)

> 注意: 无法查询 3 个月前已成交或已撤销的订单（返回 50009 错误）。

### 6. 查询活跃订单

- *端点**: `GET /user/spot/active_orders`

- *参数**: `pair` (必需), `count` (可选，最大 1000), `since` (可选), `end` (可选)

### 7. 查询成交历史

- *端点**: `GET /user/spot/trade_history`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| pair | STRING | 否 | 指定 order_id 时必需 |

| count | INT | 否 | 最大 1000 |

| order_id | INT | 否 | 按订单 ID 查询 |

| since | INT | 否 | 起始时间戳 |

| end | INT | 否 | 结束时间戳 |

| order | ENUM | 否 | asc / desc（默认 desc） |

## 账户管理 API

### 1. 充值地址

- *端点**: `GET /user/withdrawal_account`

- *参数**: `asset` (必需)

### 2. 提币

- *端点**: `POST /user/request_withdrawal`

- *参数**: `asset`, `uuid` (提现账户 ID), `amount`, `otp_token` (可选), `sms_token` (可选)

### 3. 充值历史

- *端点**: `GET /user/deposit_history`

- *参数**: `asset`, `count`, `since`, `end`, `order`

### 4. 提现历史

- *端点**: `GET /user/withdrawal_history`

- *参数**: `asset`, `count`, `since`, `end`, `order`

### 5. 杠杆持仓

- *端点**: `GET /user/margin/positions`

### 6. 交易手续费设置

- *端点**: `GET /user/spot/pair_info`

- *响应包含**: maker/taker 费率、最小/最大订单量、价格精度等。

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| QUERY (查询) | 10 次/秒 | 默认，可申请提高 |

| UPDATE (交易) | 6 次/秒 | 下单/撤单/提现 |

| 全局系统限制 | 较高 | 防止匹配引擎过载 |

- 超过限制返回 **HTTP 429**
- 如需提高限额，联系 `onboarding@bitcoinbank.co.jp`

### 最佳实践

```python
import time
from collections import deque

class BitbankRateLimiter:
    def __init__(self, query_per_sec=10, update_per_sec=6):
        self.query_limit = query_per_sec
        self.update_limit = update_per_sec
        self.query_times = deque()
        self.update_times = deque()

    def wait_query(self):
        self._wait(self.query_times, self.query_limit)

    def wait_update(self):
        self._wait(self.update_times, self.update_limit)

    def _wait(self, times, limit):
        now = time.time()
        while times and now - times[0] > 1.0:
            times.popleft()
        if len(times) >= limit:
            sleep_time = 1.0 - (now - times[0])
            if sleep_time > 0:
                time.sleep(sleep_time)
        times.append(time.time())

```bash

## WebSocket / 实时数据流

Bitbank 使用 **PubNub**服务提供实时数据推送。

### 公共频道（无需认证）

| 频道前缀 | 说明 |

|---------|------|

| `ticker_{pair}` | 实时 Ticker |

| `depth_{pair}` | 深度数据 |

| `transactions_{pair}` | 实时成交 |

| `candlestick_{pair}_{type}` | K 线数据 |

### 私有数据流（需认证）

通过 `GET /user/subscribe` 获取 PubNub channel 和 token：

```python

# 获取私有流 channel 和 token

sub_info = signed_get("/user/subscribe")
channel = sub_info["data"]["pubnub_channel"]
token = sub_info["data"]["pubnub_token"]
print(f"Channel: {channel}, Token: {token}")

# Token TTL: 12 小时，过期后需重新获取

```bash

### PubNub 订阅示例

```python
from pubnub.pnconfiguration import PNConfiguration
from pubnub.pubnub import PubNub
from pubnub.callbacks import SubscribeCallback

class BitbankCallback(SubscribeCallback):
    def message(self, pubnub, message):
        print(f"Channel: {message.channel}")
        print(f"Data: {message.message}")

config = PNConfiguration()
config.subscribe_key = "sub-c-e12e9174-dd60-11e6-806b-02ee2ddab7fe"  # Bitbank PubNub key

config.uuid = "my-client"

pubnub = PubNub(config)
pubnub.add_listener(BitbankCallback())
pubnub.subscribe().channels(["ticker_btc_jpy", "depth_btc_jpy"]).execute()

```bash
>**依赖**: `pip install pubnub`

## 错误代码

### 常见错误码

| 错误码 | 描述 |

|--------|------|

| 10000 | URL 不存在 |

| 10001 | 系统错误 |

| 10002 | 参数不正确 |

| 10003 | JSON 格式错误 |

| 10005 | 超过速率限制 |

| 10007 | 签名验证失败 |

| 10008 | ACCESS-NONCE 无效 |

| 20001 | 认证失败 |

| 20002 | API Key 不存在 |

| 20003 | API Key 无相应权限 |

| 30001 | 订单 ID 不存在 |

| 30006 | 价格超出限制 |

| 30007 | 数量超出限制 |

| 30009 | 交易对不可用 |

| 40001 | 余额不足 |

| 50009 | 订单历史过旧（超过 3 个月） |

| 60001 | 提现地址不在白名单 |

| 70020 | 熔断期间市价单被限制 |

### 错误响应格式

```json
{
  "success": 0,
  "data": {
    "code": 20003
  }
}

```bash

### Python 错误处理

```python
def safe_api_call(func, *args, **kwargs):
    """带错误处理的 API 调用"""
    try:
        result = func(*args, **kwargs)
        if result.get("success") == 1:
            return result["data"]

        code = result.get("data", {}).get("code", "unknown")
        print(f"API Error [{code}]")

        if code == 10005:
            print("Rate limited, waiting 1s...")
            time.sleep(1)
            return safe_api_call(func, *args, **kwargs)
        elif code in (10007, 10008):
            print("Authentication error, check API Key/Secret")
        elif code == 40001:
            print("Insufficient balance")
        elif code == 70020:
            print("Market order restricted during circuit break")

        return None

    except requests.exceptions.RequestException as e:
        print(f"Network error: {e}")
        return None

```bash

## 代码示例

### Python 完整交易示例

```python
import hmac
import time
import json
import requests
from hashlib import sha256

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
PRIVATE_URL = "<https://api.bitbank.cc/v1">
PUBLIC_URL = "<https://public.bitbank.cc">

def signed_get(path, params=None):
    url = f"{PRIVATE_URL}{path}"
    full_path = path
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        full_path = f"{path}?{query}"
        url = f"{PRIVATE_URL}{full_path}"
    rt = str(int(time.time() *1000))
    tw = "5000"
    sig = hmac.new(API_SECRET.encode(), (rt + tw + full_path).encode(), sha256).hexdigest()
    headers = {"ACCESS-KEY": API_KEY, "ACCESS-SIGNATURE": sig,
               "ACCESS-REQUEST-TIME": rt, "ACCESS-TIME-WINDOW": tw}
    return requests.get(url, headers=headers).json()

def signed_post(path, body):
    url = f"{PRIVATE_URL}{path}"
    body_str = json.dumps(body)
    rt = str(int(time.time()* 1000))
    tw = "5000"
    sig = hmac.new(API_SECRET.encode(), (rt + tw + body_str).encode(), sha256).hexdigest()
    headers = {"ACCESS-KEY": API_KEY, "ACCESS-SIGNATURE": sig,
               "ACCESS-REQUEST-TIME": rt, "ACCESS-TIME-WINDOW": tw,
               "Content-Type": "application/json"}
    return requests.post(url, headers=headers, data=body_str).json()

# ===== 公共接口 =====

def get_ticker(pair):
    return requests.get(f"{PUBLIC_URL}/{pair}/ticker").json()

def get_depth(pair):
    return requests.get(f"{PUBLIC_URL}/{pair}/depth").json()

def get_transactions(pair):
    return requests.get(f"{PUBLIC_URL}/{pair}/transactions").json()

def get_candlestick(pair, candle_type, date):
    return requests.get(f"{PUBLIC_URL}/{pair}/candlestick/{candle_type}/{date}").json()

# ===== 私有接口 =====

def get_assets():
    return signed_get("/user/assets")

def place_order(pair, side, order_type, amount, price=None, trigger_price=None):
    body = {"pair": pair, "side": side, "type": order_type, "amount": amount}
    if price:
        body["price"] = price
    if trigger_price:
        body["trigger_price"] = trigger_price
    return signed_post("/user/spot/order", body)

def cancel_order(pair, order_id):
    return signed_post("/user/spot/cancel_order", {"pair": pair, "order_id": order_id})

def get_active_orders(pair):
    return signed_get("/user/spot/active_orders", {"pair": pair})

def get_trade_history(pair):
    return signed_get("/user/spot/trade_history", {"pair": pair})

# ===== 使用示例 =====

# 获取 BTC/JPY 行情

ticker = get_ticker("btc_jpy")
if ticker["success"] == 1:
    d = ticker["data"]
    print(f"BTC/JPY Last: {d['last']}, High: {d['high']}, Low: {d['low']}")

# 获取深度

depth = get_depth("btc_jpy")
if depth["success"] == 1:
    d = depth["data"]
    print(f"Best ask: {d['asks'][0]}, Best bid: {d['bids'][0]}")

# 获取 K 线

candles = get_candlestick("btc_jpy", "1hour", "20240101")
if candles["success"] == 1:
    for ohlcv in candles["data"]["candlestick"][0]["ohlcv"][:3]:
        print(f"O:{ohlcv[0]} H:{ohlcv[1]} L:{ohlcv[2]} C:{ohlcv[3]} V:{ohlcv[4]}")

# 查询余额

assets = get_assets()
if assets["success"] == 1:
    for a in assets["data"]["assets"]:
        if float(a["free_amount"]) > 0:
            print(f"{a['asset']}: {a['free_amount']}")

# 下限价买单

order = place_order("btc_jpy", "buy", "limit", "0.001", price="9500000")
print(f"Order: {order}")

```bash

## 变更历史

### 2026-02-27

- 完善文档，添加详细公共/私有 REST API 端点说明
- 添加 ACCESS-TIME-WINDOW 和 ACCESS-NONCE 两种认证方式的完整 Python 示例
- 添加市场数据 API（Ticker、深度、K 线、成交）详细说明和响应格式
- 添加交易 API（下单、撤单、查询）完整示例
- 添加 PubNub 实时数据流订阅说明
- 添加速率限制（QUERY/UPDATE 分离限制）
- 添加错误码表和熔断机制说明

- --

## 相关资源

- [Bitbank 官方 API 文档 (GitHub)](<https://github.com/bitbankinc/bitbank-api-docs)>
- [Bitbank 官网](<https://bitbank.cc)>
- [交易对列表](<https://github.com/bitbankinc/bitbank-api-docs/blob/master/pairs.md)>
- [资产列表](<https://github.com/bitbankinc/bitbank-api-docs/blob/master/assets.md)>
- [错误码完整列表](<https://github.com/bitbankinc/bitbank-api-docs/blob/master/errors.md)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 Bitbank 官方 API 文档整理。*
