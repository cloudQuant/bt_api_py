# Luno API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V1
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://www.luno.com/en/api>
- 数据来源: CCXT 源码验证

## 交易所基本信息

- 官方名称: Luno
- 官网: <https://www.luno.com>
- 交易所类型: CEX (中心化交易所)
- 总部: 英国/新加坡/南非
- 支持的交易对: 50+ (ZAR, NGN, MYR, IDR, EUR, GBP 等法币计价)
- 支持的交易类型: 现货(Spot)
- 手续费: Maker 0%, Taker 0.1% (基础费率, 阶梯递减)
- 法币支持: ZAR, NGN, MYR, IDR, EUR, GBP, UGX 等
- 特点: 非洲和东南亚领先加密货币交易所

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| REST API | `<https://api.luno.com/api/1`> | 主端点 |

| Exchange API | `<https://api.luno.com/api/exchange/1`> | 交易所特定端点 |

## 认证方式

### HTTP Basic Auth

Luno 使用 **HTTP Basic Authentication**（非 HMAC 签名），简单直接。

- *Authorization 头**: `Basic Base64(api_key_id:api_key_secret)`

### Python 示例

```python
import requests
from requests.auth import HTTPBasicAuth

API_KEY = "your_api_key_id"
API_SECRET = "your_api_key_secret"
BASE_URL = "<https://api.luno.com/api/1">

def luno_get(path, params=None):
    resp = requests.get(f"{BASE_URL}/{path}", params=params,
                        auth=HTTPBasicAuth(API_KEY, API_SECRET))
    return resp.json()

def luno_post(path, data=None):
    resp = requests.post(f"{BASE_URL}/{path}", data=data,
                         auth=HTTPBasicAuth(API_KEY, API_SECRET))
    return resp.json()

def luno_delete(path):
    resp = requests.delete(f"{BASE_URL}/{path}",
                           auth=HTTPBasicAuth(API_KEY, API_SECRET))
    return resp.json()

```bash

## 市场数据 API

### 1. 获取市场列表

- *端点**: `GET /api/exchange/1/markets`

```python
resp = requests.get("<https://api.luno.com/api/exchange/1/markets")>
for m in resp.json().get("markets", [])[:5]:
    print(f"{m['market_id']}: base={m['base_currency']}, counter={m['counter_currency']}")

```bash

### 2. 获取 Ticker

- *端点**: `GET /api/1/ticker` (单个) / `GET /api/1/tickers` (全部)

```python

# 单个

resp = requests.get(f"{BASE_URL}/ticker", params={"pair": "XBTZAR"})
t = resp.json()
print(f"BTC/ZAR: last={t['last_trade']}, bid={t['bid']}, ask={t['ask']}, "
      f"vol={t['rolling_24_hour_volume']}")

# 全部

resp = requests.get(f"{BASE_URL}/tickers")
for t in resp.json().get("tickers", [])[:5]:
    print(f"{t['pair']}: last={t['last_trade']}, bid={t['bid']}, ask={t['ask']}")

```bash

### 3. 获取订单簿

- *端点**: `GET /api/1/orderbook` / `GET /api/1/orderbook_top` (仅最优)

```python
resp = requests.get(f"{BASE_URL}/orderbook", params={"pair": "XBTZAR"})
book = resp.json()
for ask in book["asks"][:5]:
    print(f"ASK: price={ask['price']}, volume={ask['volume']}")
for bid in book["bids"][:5]:
    print(f"BID: price={bid['price']}, volume={bid['volume']}")

```bash

### 4. 获取最近成交

- *端点**: `GET /api/1/trades`

```python
resp = requests.get(f"{BASE_URL}/trades", params={"pair": "XBTZAR"})
for t in resp.json().get("trades", [])[:5]:
    print(f"Price={t['price']}, Volume={t['volume']}, "
          f"is_buy={t['is_buy']}, timestamp={t['timestamp']}")

```bash

### 5. 获取 K 线数据

- *端点**: `GET /api/exchange/1/candles`

- *参数**: `pair`, `since` (ms 时间戳), `duration` (秒: 60/300/900/1800/3600/10800/14400/86400/259200/604800)

```python
import time
resp = requests.get("<https://api.luno.com/api/exchange/1/candles",> params={
    "pair": "XBTZAR",
    "since": int((time.time() - 86400) *1000),
    "duration": 3600
}, auth=HTTPBasicAuth(API_KEY, API_SECRET))
for c in resp.json().get("candles", [])[:5]:
    print(f"T={c['timestamp']} O={c['open']} H={c['high']} L={c['low']} C={c['close']} V={c['volume']}")

```bash

## 交易 API

### 1. 查询余额

- *端点**: `GET /api/1/balance`

```python
balance = luno_get("balance")
for b in balance.get("balance", []):
    if float(b.get("balance", 0)) > 0:
        print(f"{b['asset']}: balance={b['balance']}, reserved={b['reserved']}, "
              f"available={float(b['balance']) - float(b['reserved'])}")

```bash

### 2. 限价单

- *端点**: `POST /api/1/postorder`

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| pair | STRING | 是 | 交易对 (XBTZAR) |

| type | STRING | 是 | BID (买) / ASK (卖) |

| volume | STRING | 是 | 数量 |

| price | STRING | 是 | 价格 |

| post_only | BOOL | 否 | 仅 Maker |

```python
order = luno_post("postorder", data={
    "pair": "XBTZAR",
    "type": "BID",
    "volume": "0.001",
    "price": "500000"
})
print(f"Order ID: {order.get('order_id')}")

```bash

### 3. 市价单

- *端点**: `POST /api/1/marketorder`

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| pair | STRING | 是 | 交易对 |

| type | STRING | 是 | BUY / SELL |

| counter_volume | STRING | 条件 | 花费金额 (BUY) |

| base_volume | STRING | 条件 | 卖出数量 (SELL) |

```python

# 市价买 (按金额)

order = luno_post("marketorder", data={
    "pair": "XBTZAR",
    "type": "BUY",
    "counter_volume": "1000"  # 花费 1000 ZAR

})

# 市价卖 (按数量)

order = luno_post("marketorder", data={
    "pair": "XBTZAR",
    "type": "SELL",
    "base_volume": "0.001"
})

```bash

### 4. 止损单

- *端点**: `POST /api/1/stoporder`

### 5. 撤单

- *端点**: `POST /api/1/stoporder` (使用 order_id 参数取消)

### 6. 查询订单

- *端点**: `GET /api/1/listorders` / `GET /api/1/orders/{id}`

```python

# 查询挂单

orders = luno_get("listorders", params={"pair": "XBTZAR", "state": "PENDING"})
for o in orders.get("orders", []):
    print(f"ID:{o['order_id']} type={o['type']} price={o['limit_price']} "
          f"volume={o['limit_volume']} state={o['state']}")

```bash

### 7. 查询成交记录

- *端点**: `GET /api/1/listtrades`

```python
trades = luno_get("listtrades", params={"pair": "XBTZAR"})
for t in trades.get("trades", []):
    print(f"Price={t['price']}, Volume={t['volume']}, "
          f"is_buy={t['is_buy']}, timestamp={t['timestamp']}")

```bash

## 账户管理 API

| 端点 | 方法 | 描述 |

|------|------|------|

| balance | GET | 余额查询 |

| fee_info | GET | 费率查询 |

| funding_address | GET/POST | 充值地址 (查询/创建) |

| send | POST | 发送加密货币 |

| send_fee | GET | 发送手续费查询 |

| send/networks | GET | 支持的发送网络 |

| withdrawals | GET/POST | 法币提现 (查询/发起) |

| withdrawals/{id} | DELETE | 取消提现 |

| accounts | POST | 创建子账户 |

| accounts/{id}/transactions | GET | 账户交易记录 |

## 速率限制

| 类别 | 限制 |

|------|------|

| REST API | 300 次/分钟 (5 次/秒) |

## 错误处理

```json
{"error": "ErrInvalidAPIKey", "error_code": "ErrInvalidAPIKey"}

```bash

## 变更历史

### 2026-02-27

- 基于 CCXT 源码验证完善
- 添加 HTTP Basic Auth 认证示例
- 添加市场数据、交易、账户管理 API 详细说明

- --

## 相关资源

- [Luno 官方 API 文档](<https://www.luno.com/en/api)>
- [CCXT Luno 实现](<https://github.com/ccxt/ccxt/blob/master/python/ccxt/luno.py)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 CCXT 源码验证整理。*
