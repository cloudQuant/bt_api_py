# Coinone API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: Public V2 / Private V2 / V2.1
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://docs.coinone.co.kr>
- 数据来源: 官方文档搜索结果 + CCXT 源码验证

## 交易所基本信息

- 官方名称: CoinOne
- 官网: <https://coinone.co.kr>
- 交易所类型: CEX (中心化交易所)
- 总部: 韩国首尔
- 支持的交易对: 200+ (KRW 计价)
- 支持的交易类型: 现货(Spot) - 仅限价单
- 手续费: Maker 0.2%, Taker 0.2% (基础费率)
- 法币支持: KRW (韩国元)
- 合规: 韩国持牌交易所

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API (V1) | `<https://api.coinone.co.kr`> | 公共/私有 V1 |

| V2 Public | `<https://api.coinone.co.kr/public/v2`> | 公共 V2 |

| V2 Private | `<https://api.coinone.co.kr/v2`> | 私有 V2 |

| V2.1 Private | `<https://api.coinone.co.kr/v2.1`> | 私有 V2.1 (推荐) |

## 认证方式

### API 密钥获取

1. 登录 Coinone 账户
2. 在 Open API 管理中创建个人 API
3. 获取 Access Token 和 Secret Key

### HMAC SHA512 签名

- *请求头**:

| Header | 描述 |

|--------|------|

| X-COINONE-PAYLOAD | Base64(JSON body) |

| X-COINONE-SIGNATURE | HMAC-SHA512(payload, SECRET.upper()) |

| Content-Type | application/json |

- *签名步骤**:
1. 构建请求 JSON，包含 `access_token` (API Key) 和 `nonce`
2. 将 JSON 字符串进行 Base64 编码得到 payload
3. 将 Secret Key 转为大写
4. 使用大写 Secret 对 payload 进行 HMAC SHA512
5. 签名转为十六进制

- *Nonce 规则**:
- V2.0: Unix 时间戳（递增正整数）
- V2.1: UUID v4

### Python 签名示例

```python
import hmac
import hashlib
import base64
import time
import json
import uuid
import requests

API_KEY = "your_access_token"
SECRET_KEY = "your_secret_key"
BASE_URL = "<https://api.coinone.co.kr">

def coinone_request(path, params=None, version="v2"):
    """发送 Coinone 签名请求 (所有私有接口均为 POST)"""
    if version == "v2.1":
        nonce = str(uuid.uuid4())
        url = f"{BASE_URL}/v2.1/{path}"
    elif version == "v2":
        nonce = str(int(time.time() *1000))
        url = f"{BASE_URL}/v2/{path}"
    else:
        nonce = str(int(time.time()*1000))
        url = f"{BASE_URL}/{path}"

    body = {
        "access_token": API_KEY,
        "nonce": nonce,
    }
    if params:
        body.update(params)

    json_str = json.dumps(body)
    payload = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')

# Secret 需转大写
    secret = SECRET_KEY.upper()
    signature = hmac.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha512
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-COINONE-PAYLOAD": payload,
        "X-COINONE-SIGNATURE": signature,
    }

    resp = requests.post(url, headers=headers, data=payload)
    return resp.json()

```bash

## 市场数据 API

> 公共 API 无需认证，均为 GET 请求。

### 1. 获取市场列表

- *端点**: `GET /public/v2/markets/{quote_currency}`

```python
resp = requests.get(f"{BASE_URL}/public/v2/markets/KRW")
data = resp.json()
for m in data.get("markets", [])[:5]:
    print(f"{m['target_currency']}/KRW: "
          f"min_qty={m.get('min_qty')}, max_qty={m.get('max_qty')}")

```bash

### 2. 获取 Ticker

- *端点**: `GET /public/v2/ticker_new/{quote_currency}` (全部)
- *端点**: `GET /public/v2/ticker_new/{quote_currency}/{target_currency}` (单个)

```python

# 全部 KRW 市场

resp = requests.get(f"{BASE_URL}/public/v2/ticker_new/KRW")
for t in resp.json().get("tickers", [])[:5]:
    print(f"{t['target_currency']}: last={t['last']}, "
          f"high={t['high']}, low={t['low']}, volume={t['volume']}")

# 单个

resp = requests.get(f"{BASE_URL}/public/v2/ticker_new/KRW/BTC")
t = resp.json()
print(f"BTC/KRW: last={t.get('last')}, high={t.get('high')}, low={t.get('low')}")

```bash

### 3. 获取订单簿

- *端点**: `GET /public/v2/orderbook/{quote_currency}/{target_currency}`

- *参数**: `size` (可选, 默认 15)

```python
resp = requests.get(f"{BASE_URL}/public/v2/orderbook/KRW/BTC", params={"size": 15})
data = resp.json()
for ask in data.get("asks", [])[:5]:
    print(f"ASK: price={ask['price']}, qty={ask['qty']}")
for bid in data.get("bids", [])[:5]:
    print(f"BID: price={bid['price']}, qty={bid['qty']}")

```bash

### 4. 获取最近成交

- *端点**: `GET /public/v2/trades/{quote_currency}/{target_currency}`

```python
resp = requests.get(f"{BASE_URL}/public/v2/trades/KRW/BTC")
for t in resp.json().get("trades", [])[:5]:
    print(f"Price={t['price']}, Qty={t['qty']}, "
          f"is_seller_maker={t.get('is_seller_maker')}, timestamp={t['timestamp']}")

```bash

### 5. 获取 K 线数据

- *端点**: `GET /public/v2/chart/{quote_currency}/{target_currency}`

```python
resp = requests.get(f"{BASE_URL}/public/v2/chart/KRW/BTC", params={
    "interval": "1h",
    "limit": 24
})

```bash

### 6. 获取币种信息

- *端点**: `GET /public/v2/currencies` (全部) / `GET /public/v2/currencies/{currency}` (单个)

```python
resp = requests.get(f"{BASE_URL}/public/v2/currencies")
for c in resp.json().get("currencies", [])[:5]:
    print(f"{c['currency']}: name={c.get('name')}")

```bash

### 7. 获取价格档位

- *端点**: `GET /public/v2/range_units`

## 交易 API

> Coinone 所有私有接口均为 **POST** 请求，仅支持限价单。

### 1. 查询余额

- *V2.1 端点**: `POST /v2.1/account/balance/all` (全部) / `POST /v2.1/account/balance` (单币)

```python

# 查询全部余额

balance = coinone_request("account/balance/all", version="v2.1")
if balance.get("result") == "success":
    for currency, info in balance.get("balances", {}).items():
        avail = float(info.get("available", 0))
        if avail > 0:
            print(f"{currency}: available={info['available']}, "
                  f"limit={info.get('limit')}")

# V2 查询余额

balance = coinone_request("account/balance", version="v2")

```bash

### 2. 下单

- *V2.1 端点**: `POST /v2.1/order/limit`

- *参数**:

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| quote_currency | STRING | 是 | 计价币 (KRW) |

| target_currency | STRING | 是 | 基础币 (BTC) |

| type | STRING | 是 | bid (买) / ask (卖) |

| price | STRING | 是 | 价格 |

| qty | STRING | 是 | 数量 |

| post_only | BOOL | 否 | 仅 Maker |

```python

# 限价买单 (V2.1)

order = coinone_request("order/limit", params={
    "quote_currency": "KRW",
    "target_currency": "BTC",
    "type": "bid",
    "price": "50000000",
    "qty": "0.001"
}, version="v2.1")
if order.get("result") == "success":
    print(f"Order ID: {order.get('orderId')}")

# 限价卖单 (V2.1)

order = coinone_request("order/limit", params={
    "quote_currency": "KRW",
    "target_currency": "BTC",
    "type": "ask",
    "price": "60000000",
    "qty": "0.001"
}, version="v2.1")

# V2 限价买单 (旧版)

order = coinone_request("order/limit_buy", params={
    "currency": "btc",
    "price": "50000000",
    "qty": "0.001"
}, version="v2")

```bash

### 3. 撤单

- *V2.1 端点**: `POST /v2.1/order/cancel`
- *V2.1 全部撤单**: `POST /v2.1/order/cancel/all`

```python

# 撤销单个订单 (V2 需要提供 price, qty, is_ask)

result = coinone_request("order/cancel", params={
    "order_id": "68665943-1eb5-4e4b-9d76-845fc54f5489",
    "currency": "btc",
    "price": "444000",
    "qty": "0.3456",
    "is_ask": 1  # 0=买, 1=卖

}, version="v2")

```bash

### 4. 查询挂单

- *V2.1 端点**: `POST /v2.1/order/open_orders` (指定交易对) / `POST /v2.1/order/open_orders/all` (全部)

- *响应示例 (V1)**:

```json
{
    "result": "success",
    "errorCode": "0",
    "limitOrders": [
        {
            "index": "0",
            "orderId": "68665943-1eb5-4e4b-9d76-845fc54f5489",
            "timestamp": "1449037367",
            "price": "444000.0",
            "qty": "0.3456",
            "type": "ask",
            "feeRate": "-0.0015"
        }
    ]
}

```bash

```python
orders = coinone_request("order/open_orders", params={
    "quote_currency": "KRW",
    "target_currency": "BTC"
}, version="v2.1")

```bash

### 5. 查询订单详情

- *V2.1 端点**: `POST /v2.1/order/info`

### 6. 查询成交记录

- *V2.1 端点**: `POST /v2.1/order/complete_orders` / `POST /v2.1/order/complete_orders/all`

- *响应示例 (V2)**:

```json
{
    "result": "success",
    "errorCode": "0",
    "completeOrders": [
        {
            "timestamp": "1416561032",
            "price": "419000.0",
            "type": "bid",
            "qty": "0.001",
            "feeRate": "-0.0015",
            "fee": "-0.0000015",
            "orderId": "E84A1AC2-8088-4FA0-B093-A3BCDB9B3C85"
        }
    ]
}

```bash

### 7. 查询费率

- *V2.1 端点**: `POST /v2.1/account/trade_fee/{quote_currency}/{target_currency}`

## 账户管理 API

| 端点 (V2.1) | 描述 |

|-------------|------|

| account/balance/all | 全部余额 |

| account/balance | 单币余额 |

| account/trade_fee | 费率查询 |

| 端点 (V2) | 描述 |

|-----------|------|

| account/balance | 余额查询 |

| account/deposit_address | 充值地址 |

| account/user_info | 用户信息 |

| account/virtual_account | 虚拟账户 |

| transaction/btc | BTC 转账 |

| transaction/coin | 币种转账 |

| transaction/history | 交易历史 |

| transaction/krw/history | KRW 交易历史 |

| 端点 (V2.1) | 描述 |

|-------------|------|

| transaction/krw/history | KRW 历史 |

| transaction/coin/history | 币种历史 |

| transaction/coin/withdrawal/limit | 提现限额 |

## 速率限制

| 类别 | 限制 | 说明 |

|------|------|------|

| Public V2 | 1200 次/分钟 | 按 IP |

| Public V1 | 600 次/分钟 | 按 IP |

| Private V2.1 (订单) | 40 次/秒 | 按 Portfolio |

| Private V2.1 (其他) | 80 次/秒 | 按 Portfolio |

| Private V2 (订单) | 40 次/秒 | 按 Portfolio |

| Private V2 (其他) | 40 次/秒 | 按 Portfolio |

## 错误代码

### 响应格式

成功: `{"result": "success", "errorCode": "0", ...}`
失败: `{"result": "error", "error_code": "107", "error_msg": "Parameter value is wrong"}`

### 常见错误码

| 错误码 | 描述 |

|--------|------|

| 0 | 成功 |

| 4 | 请求被禁止 |

| 11 | Access Token 无效 |

| 12 | 签名无效 |

| 40 | Nonce 无效 |

| 51 | 订单 ID 无效 |

| 100 | Session 错误 |

| 107 | 参数值错误 |

| 108 | 未知加密货币 |

| 111 | 订单不存在 |

| 141 | 价格超出范围 |

### Python 错误处理

```python
def safe_coinone_request(path, params=None, version="v2.1"):
    try:
        result = coinone_request(path, params, version)
        if result.get("result") == "success":
            return result
        error_code = result.get("error_code", result.get("errorCode", ""))
        error_msg = result.get("error_msg", result.get("errorMsg", "Unknown"))
        print(f"Coinone Error [{error_code}]: {error_msg}")
        return None
    except Exception as e:
        print(f"Request failed: {e}")
        return None

```bash

## 变更历史

### 2026-02-27

- 基于 CCXT 源码和官方文档搜索结果完善
- 添加 HMAC SHA512 签名认证完整 Python 示例（已验证签名逻辑）
- 添加 V2 Public / V2 Private / V2.1 Private 完整端点列表
- 添加市场数据、交易、账户管理 API 详细说明
- 添加错误代码表和错误处理

- --

## 相关资源

- [Coinone 官方 API 文档](<https://docs.coinone.co.kr)>
- [CCXT Coinone 实现](<https://github.com/ccxt/ccxt/blob/master/python/ccxt/coinone.py)>
- [pycoinone Python 库](<https://github.com/gwangyi/pycoinone)>
- [Coinone 官网](<https://coinone.co.kr)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 Coinone 官方 API 文档及 CCXT 源码验证整理。*
