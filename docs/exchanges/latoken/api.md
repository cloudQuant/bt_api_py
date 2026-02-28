# Latoken API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V2
- 创建日期: 2026-02-27
- 官方文档: <https://api.latoken.com>
- 数据来源: CCXT 源码验证

## 交易所基本信息

- 官方名称: Latoken
- 官网: <https://latoken.com>
- 交易所类型: CEX (中心化交易所)
- 总部: 开曼群岛
- 支持的交易对: 800+ (USDT, BTC, ETH, LA 等计价)
- 支持的交易类型: 现货(Spot)
- 手续费: Maker 0.49%, Taker 0.49%
- 特点: 支持 IEO、Token 上市丰富

## API 基础 URL

| 端点类型 | URL |

|---------|-----|

| REST API | `<https://api.latoken.com`> |

## 认证方式

### HMAC SHA512 签名

- *请求头**:

| Header | 描述 |

|--------|------|

| X-LA-APIKEY | API Key |

| X-LA-SIGNATURE | HMAC-SHA512 签名 |

| X-LA-DIGEST | 签名算法 (HMAC-SHA512) |

| Content-Type | application/json (POST) |

- *签名步骤**:
1. 拼接签名字符串: `METHOD + /v2/path + urlencode(params)`
2. 使用 Secret 进行 HMAC SHA512
3. GET 参数拼到 URL，POST 参数为 JSON body

### Python 签名示例

```python
import hmac
import hashlib
import json
import requests
from urllib.parse import urlencode

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "<https://api.latoken.com">

def latoken_get(path, params=None):
    query = urlencode(params) if params else ""
    auth = "GET" + f"/v2/{path}" + query
    signature = hmac.new(
        API_SECRET.encode(), auth.encode(), hashlib.sha512
    ).hexdigest()
    url = f"{BASE_URL}/v2/{path}"
    if query:
        url += "?" + query
    headers = {
        "X-LA-APIKEY": API_KEY,
        "X-LA-SIGNATURE": signature,
        "X-LA-DIGEST": "HMAC-SHA512",
    }
    return requests.get(url, headers=headers).json()

def latoken_post(path, params=None):
    query = urlencode(params) if params else ""
    auth = "POST" + f"/v2/{path}" + query
    signature = hmac.new(
        API_SECRET.encode(), auth.encode(), hashlib.sha512
    ).hexdigest()
    headers = {
        "X-LA-APIKEY": API_KEY,
        "X-LA-SIGNATURE": signature,
        "X-LA-DIGEST": "HMAC-SHA512",
        "Content-Type": "application/json",
    }
    return requests.post(f"{BASE_URL}/v2/{path}", headers=headers,
                         json=params or {}).json()

```bash

## 市场数据 API (GET, 无需认证)

| 端点 | 描述 |

|------|------|

| `/v2/currency` | 全部币种 |

| `/v2/currency/available` | 可用币种 |

| `/v2/currency/quotes` | 报价币种 |

| `/v2/pair` | 全部交易对 |

| `/v2/pair/available` | 可用交易对 |

| `/v2/ticker` | 全部 Ticker |

| `/v2/ticker/{base}/{quote}` | 单个 Ticker |

| `/v2/book/{currency}/{quote}` | 订单簿 |

| `/v2/trade/history/{currency}/{quote}` | 最近成交 |

| `/v2/trade/fee/{currency}/{quote}` | 交易费率 |

| `/v2/trade/feeLevels` | 费率阶梯 |

| `/v2/chart/week/{currency}/{quote}` | 周 K 线图 |

| `/v2/time` | 服务器时间 |

```python

# 获取 Ticker

resp = requests.get(f"{BASE_URL}/v2/ticker")
for t in resp.json()[:5]:
    print(f"Symbol={t.get('symbol')}, LastPrice={t.get('lastPrice')}, "
          f"Volume={t.get('volume24h')}")

# 获取订单簿 (需要用 UUID 格式的 currency/quote ID)

resp = requests.get(f"{BASE_URL}/v2/book/{base_uuid}/{quote_uuid}")

```bash
> **注意**: Latoken 使用 UUID 作为币种和交易对的内部标识符。需先通过 `/v2/currency` 和 `/v2/pair` 获取映射关系。

## 交易 API (需签名)

| 端点 | 方法 | 描述 |

|------|------|------|

| auth/order/place | POST | 下单 |

| auth/order/cancel | POST | 撤单 |

| auth/order/cancelAll | POST | 全部撤单 |

| auth/order | GET | 全部订单 |

| auth/order/getOrder/{id} | GET | 订单详情 |

| auth/order/pair/{currency}/{quote}/active | GET | 活跃挂单 |

| auth/stopOrder/place | POST | 止损单 |

| auth/stopOrder/cancel | POST | 撤销止损单 |

| auth/trade | GET | 全部成交 |

| auth/trade/pair/{currency}/{quote} | GET | 交易对成交 |

```python

# 下单

order = latoken_post("auth/order/place", {
    "baseCurrency": "btc_uuid",
    "quoteCurrency": "usdt_uuid",
    "side": "BUY",
    "condition": "GOOD_TILL_CANCELLED",
    "type": "LIMIT",
    "clientOrderId": "my_order_001",
    "price": "40000",
    "quantity": "0.001"
})

# 撤单

latoken_post("auth/order/cancel", {"id": "order_uuid"})

```bash

## 账户管理 API

| 端点 | 方法 | 描述 |

|------|------|------|

| auth/account | GET | 全部余额 |

| auth/account/currency/{currency}/{type} | GET | 单币余额 |

| auth/transaction | GET | 交易记录 |

| auth/transaction/depositAddress | POST | 充值地址 |

| auth/transaction/withdraw | POST | 提现 |

| auth/transfer/id | POST | 内部转账 (ID) |

| auth/transfer/email | POST | 内部转账 (Email) |

| auth/transfer | GET | 转账记录 |

## 速率限制

| 类别 | 限制 |

|------|------|

| REST API | 1 次/秒 |

## 错误处理

失败: `{"result": false, "message": "...", "error": "BAD_REQUEST", "status": "FAILURE"}`

## 变更历史

### 2026-02-27

- 基于 CCXT 源码验证完善

- --

## 相关资源

- [Latoken API 文档](<https://api.latoken.com)>
- [CCXT Latoken 实现](<https://github.com/ccxt/ccxt/blob/master/python/ccxt/latoken.py)>

- --

- 本文档由 bt_api_py 项目维护。*
