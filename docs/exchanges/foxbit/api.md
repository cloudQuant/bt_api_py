# Foxbit API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V3
- 创建日期: 2026-02-27
- 官方文档: <https://docs.foxbit.com.br>
- 数据来源: CCXT 源码验证

## 交易所基本信息

- 官方名称: Foxbit
- 官网: <https://app.foxbit.com.br>
- 交易所类型: CEX (中心化交易所)
- 总部: 巴西
- 支持的交易对: 50+ (BRL 计价)
- 支持的交易类型: 现货(Spot)
- 法币支持: BRL (巴西雷亚尔)
- 特点: 巴西领先加密货币交易所

## API 基础 URL

| 端点类型 | URL |

|---------|-----|

| REST API | `<https://api.foxbit.com.br`> |

## 认证方式

### HMAC SHA256 签名

- *请求头**:

| Header | 描述 |

|--------|------|

| X-FB-ACCESS-KEY | API Key |

| X-FB-ACCESS-TIMESTAMP | 毫秒时间戳 |

| X-FB-ACCESS-SIGNATURE | HMAC-SHA256 签名 |

| Content-Type | application/json |

- *签名步骤**:
1. 拼接: `timestamp + method + path + query_string + body`
2. 使用 Secret 进行 HMAC SHA256

### Python 签名示例

```python
import hmac
import hashlib
import time
import json
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "<https://api.foxbit.com.br">

def foxbit_get(path, params=None):
    timestamp = str(int(time.time() *1000))
    query = ""
    url = f"{BASE_URL}/rest/v3/{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url += "?" + query
    pre_hash = timestamp + "GET" + f"/rest/v3/{path}" + query
    signature = hmac.new(
        API_SECRET.encode(), pre_hash.encode(), hashlib.sha256
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-FB-ACCESS-KEY": API_KEY,
        "X-FB-ACCESS-TIMESTAMP": timestamp,
        "X-FB-ACCESS-SIGNATURE": signature,
    }
    return requests.get(url, headers=headers).json()

def foxbit_post(path, params=None):
    timestamp = str(int(time.time()*1000))
    body = json.dumps(params or {})
    pre_hash = timestamp + "POST" + f"/rest/v3/{path}" + body
    signature = hmac.new(
        API_SECRET.encode(), pre_hash.encode(), hashlib.sha256
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-FB-ACCESS-KEY": API_KEY,
        "X-FB-ACCESS-TIMESTAMP": timestamp,
        "X-FB-ACCESS-SIGNATURE": signature,
    }
    return requests.post(f"{BASE_URL}/rest/v3/{path}", headers=headers, data=body).json()

```bash

## 市场数据 API (GET, 无需认证)

| 端点 | 限频 | 描述 |

|------|------|------|

| /rest/v3/currencies | 6/s | 币种列表 |

| /rest/v3/markets | 6/s | 市场列表 |

| /rest/v3/markets/ticker/24hr | 0.5/s | 全部 Ticker |

| /rest/v3/markets/{market}/ticker/24hr | 2/s | 单个 Ticker |

| /rest/v3/markets/{market}/orderbook | ~5/s | 订单簿 |

| /rest/v3/markets/{market}/candlesticks | ~2.5/s | K 线 |

| /rest/v3/markets/{market}/trades/history | ~2.5/s | 成交历史 |

- *K 线周期**: `1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 1w, 2w, 1M`

```python

# Ticker (公开)

resp = requests.get(f"{BASE_URL}/rest/v3/markets/btcbrl/ticker/24hr")
t = resp.json()
print(f"BTC/BRL: last={t.get('last_price')}, vol={t.get('vol')}")

# 订单簿

resp = requests.get(f"{BASE_URL}/rest/v3/markets/btcbrl/orderbook")
book = resp.json()

```bash

## 交易 API (需签名)

| 端点 | 方法 | 限频 | 描述 |

|------|------|------|------|

| /rest/v3/orders | POST | 15/s | 下单 |

| /rest/v3/orders/batch | POST | 4/s | 批量下单 |

| /rest/v3/orders/cancel-replace | POST | 10/s | 改单 |

| /rest/v3/orders | GET | 15/s | 订单列表 |

| /rest/v3/orders/by-order-id/{id} | GET | 15/s | 订单详情 |

| /rest/v3/trades | GET | 5/s | 成交记录 |

```python

# 限价买单

order = foxbit_post("orders", {
    "market_symbol": "btcbrl",
    "side": "BUY",
    "type": "LIMIT",
    "price": "200000",
    "quantity": "0.001"
})

# 市价买单

order = foxbit_post("orders", {
    "market_symbol": "btcbrl",
    "side": "BUY",
    "type": "MARKET",
    "quantity": "0.001"
})

```bash

## 账户管理 API

| 端点 | 方法 | 描述 |

|------|------|------|

| accounts | GET | 余额列表 |

| accounts/{symbol}/transactions | GET | 交易记录 |

| deposits/address | GET | 充值地址 |

| deposits | GET | 充值记录 |

| withdrawals | GET/POST | 提现记录/发起提现 |

| me/fees/trading | GET | 费率查询 |

## 速率限制

| 类别 | 限制 |

|------|------|

| 全局 | 300 次/10 秒 |

| 各端点独立限频 | 详见上方端点表 |

## 错误码

| 错误码 | 描述 |

|--------|------|

| 2001 | 认证错误 |

| 2002 | 签名无效 |

| 2003 | API Key 无效 |

| 4001 | 验证错误 |

| 4002 | 余额不足 |

| 4004 | 交易对无效 |

| 4011 | 挂单数量超限 |

| 429 | 频率超限 |

| 5001 | 服务不可用 |

| 5002 | 维护中 |

## 变更历史

### 2026-02-27

- 基于 CCXT 源码验证完善

- --

## 相关资源

- [Foxbit API 文档](<https://docs.foxbit.com.br)>
- [CCXT Foxbit 实现](<https://github.com/ccxt/ccxt/blob/master/python/ccxt/foxbit.py)>

- --

- 本文档由 bt_api_py 项目维护。*
