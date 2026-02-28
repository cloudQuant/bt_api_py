# EXMO API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V1.1
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://exmo.me/en/api_doc>
- 数据来源: 官方 Postman 文档 + CCXT 源码验证

## 交易所基本信息

- 官方名称: EXMO
- 官网: <https://exmo.com>
- 交易所类型: CEX (中心化交易所)
- 总部: 立陶宛
- 支持的交易对: 200+ (BTC, USDT, EUR, USD, UAH, RUB 等计价)
- 支持的交易类型: 现货(Spot)、保证金交易(Margin)
- 手续费: Maker 0.4%, Taker 0.4% (基础费率, 阶梯递减)
- 法币支持: EUR, USD, UAH, RUB, GBP, PLN, TRY 等
- 特点: 东欧最大的加密货币交易所之一

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API (Public) | `<https://api.exmo.com/v1.1`> | 公共端点 |

| REST API (Private) | `<https://api.exmo.com/v1.1`> | 私有端点 |

## 认证方式

### API 密钥获取

1. 登录 EXMO 账户
2. 进入 Settings -> API
3. 创建 API Key 并设置权限
4. 保存 API Key 和 Secret

### HMAC SHA512 签名

- *请求头**:

| Header | 描述 |

|--------|------|

| Key | API Key |

| Sign | HMAC-SHA512 签名 |

| Content-Type | application/x-www-form-urlencoded |

- *签名步骤**:
1. 构建 POST body（URL-encoded 格式），包含递增 `nonce`
2. 使用 Secret 对整个 body 进行 HMAC SHA512
3. 签名转为十六进制

### Python 签名示例

```python
import hmac
import hashlib
import time
import requests
from urllib.parse import urlencode

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "<https://api.exmo.com/v1.1">

def exmo_request(method_name, params=None):
    """发送 EXMO 签名请求 (所有私有接口均为 POST)"""
    nonce = int(time.time() *1000)
    body_params = {"nonce": nonce}
    if params:
        body_params.update(params)

    body = urlencode(body_params)
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        body.encode('utf-8'),
        hashlib.sha512
    ).hexdigest()

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Key": API_KEY,
        "Sign": signature,
    }

    resp = requests.post(f"{BASE_URL}/{method_name}", headers=headers, data=body)
    return resp.json()

```bash

## 市场数据 API

> 公共 API 无需认证，均为 GET 请求。

### 1. 获取币种列表

- *端点**: `GET /v1.1/currency`

```python
resp = requests.get(f"{BASE_URL}/currency")
currencies = resp.json()
print(currencies[:10])  # ["BTC", "ETH", "USDT", ...]

```bash

- *扩展币种信息**: `GET /v1.1/currency/list/extended`

### 2. 获取交易对设置

- *端点**: `GET /v1.1/pair_settings`

```python
resp = requests.get(f"{BASE_URL}/pair_settings")
for pair, info in list(resp.json().items())[:5]:
    print(f"{pair}: min_quantity={info['min_quantity']}, "
          f"max_quantity={info['max_quantity']}, "
          f"min_price={info['min_price']}, max_price={info['max_price']}")

```bash

### 3. 获取 Ticker

- *端点**: `GET /v1.1/ticker`

- *响应示例**:

```json
{
    "BTC_USD": {
        "buy_price": "40000.1",
        "sell_price": "40100.5",
        "last_trade": "40050.3",
        "high": "41000",
        "low": "39000",
        "avg": "40000",
        "vol": "123.456",
        "vol_curr": "4940000",
        "updated": 1643374115
    }
}

```bash

```python
resp = requests.get(f"{BASE_URL}/ticker")
for pair, t in list(resp.json().items())[:5]:
    print(f"{pair}: last={t['last_trade']}, buy={t['buy_price']}, "
          f"sell={t['sell_price']}, high={t['high']}, low={t['low']}, vol={t['vol']}")

```bash

### 4. 获取订单簿

- *端点**: `GET /v1.1/order_book`

- *参数**: `pair` (必需, 可多个逗号分隔), `limit` (可选, 默认 100)

```python
resp = requests.get(f"{BASE_URL}/order_book", params={
    "pair": "BTC_USD", "limit": 10
})
data = resp.json()
book = data["BTC_USD"]
for ask in book["ask"][:5]:
    print(f"ASK: price={ask[0]}, quantity={ask[1]}, amount={ask[2]}")
for bid in book["bid"][:5]:
    print(f"BID: price={bid[0]}, quantity={bid[1]}, amount={bid[2]}")

```bash

### 5. 获取最近成交

- *端点**: `GET /v1.1/trades`

- *参数**: `pair` (必需)

```python
resp = requests.get(f"{BASE_URL}/trades", params={"pair": "BTC_USD"})
for t in resp.json()["BTC_USD"][:5]:
    print(f"ID={t['trade_id']} type={t['type']} price={t['price']} "
          f"quantity={t['quantity']} amount={t['amount']} date={t['date']}")

```bash

### 6. 获取 K 线数据

- *端点**: `GET /v1.1/candles_history`

- *参数**: `symbol` (必需), `resolution` (必需), `from` / `to` (Unix 时间戳)

- *支持周期**: `1, 5, 15, 30, 45, 60, 120, 180, 240, D, W, M`

```python
import time as t
now = int(t.time())
resp = requests.get(f"{BASE_URL}/candles_history", params={
    "symbol": "BTC_USD",
    "resolution": "60",  # 1h
    "from": now - 86400,
    "to": now
})
data = resp.json()
for i in range(min(5, len(data.get("candles", [])))):
    c = data["candles"][i]
    print(f"T={c['t']} O={c['o']} H={c['h']} L={c['l']} C={c['c']} V={c['v']}")

```bash

### 7. 计算所需数量

- *端点**: `GET /v1.1/required_amount`

```python
resp = requests.get(f"{BASE_URL}/required_amount", params={
    "pair": "BTC_USD",
    "quantity": "1"
})
print(resp.json())  # {"quantity": "1", "amount": "40050.3", "avg_price": "40050.3"}

```bash

## 交易 API

### 1. 查询用户信息 (含余额)

- *端点**: `POST /v1.1/user_info`

- *响应示例**:

```json
{
    "uid": 12345,
    "server_date": 1643374115,
    "balances": {
        "BTC": "0.5",
        "USD": "10000.0",
        "USDT": "5000.0"
    },
    "reserved": {
        "BTC": "0.01",
        "USD": "500.0"
    }
}

```bash

```python
info = exmo_request("user_info")
for currency, balance in info.get("balances", {}).items():
    reserved = info.get("reserved", {}).get(currency, "0")
    if float(balance) > 0 or float(reserved) > 0:
        print(f"{currency}: available={balance}, reserved={reserved}")

```bash

### 2. 下单

- *端点**: `POST /v1.1/order_create`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| pair | STRING | 是 | 交易对 (BTC_USD) |

| quantity | STRING | 是 | 数量 |

| price | STRING | 条件 | 价格 (limit 必需) |

| type | STRING | 是 | buy/sell/market_buy/market_sell/market_buy_total/market_sell_total |

| client_id | INT | 否 | 客户端订单 ID |

- *订单类型说明**:
- `buy` / `sell` — 限价买/卖
- `market_buy` / `market_sell` — 市价买/卖（按数量）
- `market_buy_total` / `market_sell_total` — 市价买/卖（按金额）

```python

# 限价买单

order = exmo_request("order_create", {
    "pair": "BTC_USDT",
    "quantity": "0.001",
    "price": "40000",
    "type": "buy"
})
if order.get("result"):
    print(f"Order ID: {order['order_id']}")

# 限价卖单

order = exmo_request("order_create", {
    "pair": "BTC_USDT",
    "quantity": "0.001",
    "price": "50000",
    "type": "sell"
})

# 市价买单（按数量）

order = exmo_request("order_create", {
    "pair": "BTC_USDT",
    "quantity": "0.001",
    "price": "0",
    "type": "market_buy"
})

# 市价买单（按金额）

order = exmo_request("order_create", {
    "pair": "BTC_USDT",
    "quantity": "100",  # 花费 100 USDT
    "price": "0",
    "type": "market_buy_total"
})

```bash

### 3. 止损/止盈单

- *端点**: `POST /v1.1/stop_market_order_create`

```python

# 止损卖单

order = exmo_request("stop_market_order_create", {
    "pair": "BTC_USDT",
    "quantity": "0.001",
    "trigger_price": "38000",
    "type": "sell"
})

```bash

### 4. 撤单

- *端点**: `POST /v1.1/order_cancel`

```python
result = exmo_request("order_cancel", {"order_id": "12345678"})
if result.get("result"):
    print("Order cancelled successfully")

# 撤销止损单

result = exmo_request("stop_market_order_cancel", {"parent_order_id": "12345678"})

```bash

### 5. 查询挂单

- *端点**: `POST /v1.1/user_open_orders`

```python
orders = exmo_request("user_open_orders")
for pair, pair_orders in orders.items():
    for o in pair_orders:
        print(f"{pair}: ID={o['order_id']} type={o['type']} "
              f"price={o['price']} quantity={o['quantity']} "
              f"amount={o['amount']} created={o['created']}")

```bash

### 6. 查询成交记录

- *端点**: `POST /v1.1/user_trades`

- *参数**: `pair` (必需), `offset` (可选), `limit` (可选, 默认 100, 最大 10000)

```python
trades = exmo_request("user_trades", {"pair": "BTC_USDT", "limit": 10})
for pair, pair_trades in trades.items():
    for t in pair_trades:
        print(f"{pair}: ID={t['trade_id']} type={t['type']} "
              f"price={t['price']} quantity={t['quantity']} "
              f"amount={t['amount']} date={t['date']}")

```bash

### 7. 查询已取消订单

- *端点**: `POST /v1.1/user_cancelled_orders`

### 8. 查询订单成交明细

- *端点**: `POST /v1.1/order_trades`

```python
trades = exmo_request("order_trades", {"order_id": "12345678"})

```bash

## 保证金交易 API

| 端点 | 描述 |

|------|------|

| margin/user/info | 保证金账户信息 |

| margin/currency/list | 支持的保证金币种 |

| margin/pair/list | 支持的保证金交易对 |

| margin/settings | 保证金设置 |

| margin/user/order/create | 创建保证金订单 |

| margin/user/order/update | 修改保证金订单 |

| margin/user/order/cancel | 取消保证金订单 |

| margin/user/order/list | 保证金挂单列表 |

| margin/user/order/history | 保证金订单历史 |

| margin/user/position/list | 持仓列表 |

| margin/user/position/close | 平仓 |

| margin/user/position/margin_add | 追加保证金 |

| margin/user/position/margin_remove | 减少保证金 |

| margin/user/wallet/list | 保证金钱包列表 |

| margin/user/wallet/history | 保证金钱包历史 |

| margin/user/trade/list | 保证金成交列表 |

## 账户管理 API

| 端点 | 方法 | 描述 |

|------|------|------|

| user_info | POST | 用户信息+余额 |

| deposit_address | POST | 充值地址 |

| withdraw_crypt | POST | 加密货币提现 |

| withdraw_get_txid | POST | 获取提现 TXID |

| wallet_history | POST | 钱包历史 |

| wallet_operations | POST | 钱包操作记录 |

| excode_create | POST | 创建 EXMO Code |

| excode_load | POST | 兑换 EXMO Code |

| payments/providers/crypto/list | GET | 加密支付方式列表 |

```python

# 获取充值地址

address = exmo_request("deposit_address")
for currency, addr in address.items():
    if addr:
        print(f"{currency}: {addr}")

# 提现

result = exmo_request("withdraw_crypt", {
    "amount": "0.01",
    "currency": "BTC",
    "address": "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
    "transport": "BTC"  # 网络

})

```bash

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| 全局 | 10 次/秒 | 按 IP 或用户 |

| K 线数据 | 较低频率 | 建议间隔请求 |

## 错误处理

### 响应格式

成功: `{"result": true, "order_id": 12345}`
失败: `{"result": false, "error": "Error 50052: Insufficient funds"}`

### 常见错误码

| 错误码 | 描述 |

|--------|------|

| 40001 | 授权错误 |

| 40003 | 请求方法不支持 |

| 40004 | 缺少参数 |

| 40005 | 参数值错误 |

| 40016 | 维护中 |

| 40017 | API 被禁用 |

| 40030 | Nonce 已使用 |

| 50052 | 余额不足 |

| 50054 | 订单不存在 |

| 50277 | 交易对不存在 |

| 50304 | 价格超出范围 |

| 50319 | 市价单金额不足 |

### Python 错误处理

```python
def safe_exmo_request(method_name, params=None):
    try:
        result = exmo_request(method_name, params)
        if isinstance(result, dict):
            if result.get("result") == False:
                error = result.get("error", "Unknown error")
                print(f"EXMO Error: {error}")
                return None
        return result
    except Exception as e:
        print(f"Request failed: {e}")
        return None

```bash

## 变更历史

### 2026-02-27

- 基于 CCXT 源码验证和官方 Postman 文档完善
- 添加 HMAC SHA512 签名认证完整 Python 示例（已验证签名逻辑）
- 添加市场数据 API（币种、交易对设置、Ticker、订单簿、成交、K 线）
- 添加交易 API（下单所有类型、止损单、撤单、查询）
- 添加保证金交易 API 端点列表
- 添加账户管理 API（充值地址、提现、EXMO Code）
- 添加错误代码表

- --

## 相关资源

- [EXMO 官方 API 文档 (Postman)](<https://documenter.getpostman.com/view/10287440/SzYXWKPi)>
- [CCXT EXMO 实现](<https://github.com/ccxt/ccxt/blob/master/python/ccxt/exmo.py)>
- [EXMO 官网](<https://exmo.com)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 EXMO 官方 API 文档及 CCXT 源码验证整理。*
