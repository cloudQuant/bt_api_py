# Zebpay API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V2 (Spot) / V1 (Futures)
- 创建日期: 2026-02-27
- 官方文档: <https://github.com/zebpay/zebpay-api-references>
- 数据来源: CCXT 源码验证

## 交易所基本信息

- 官方名称: Zebpay
- 官网: <https://www.zebpay.com>
- 交易所类型: CEX (中心化交易所)
- 总部: 印度
- 支持的交易对: 100+ (INR, USDT 等计价)
- 支持的交易类型: 现货(Spot)、永续合约(Swap)
- 法币支持: INR (印度卢比)

## API 基础 URL

| 端点类型 | URL |

|---------|-----|

| Spot REST | `<https://sapi.zebpay.com`> |

| Swap REST | `<https://futuresbe.zebpay.com`> |

| Spot 测试 | `<https://www.zebstage.com`> |

| Swap 测试 | `<https://dev-futuresbe.zebstage.com`> |

## 认证方式

### HMAC SHA256 签名

- *请求头**:

| Header | 描述 |

|--------|------|

| X-AUTH-APIKEY | API Key |

| X-AUTH-SIGNATURE | HMAC-SHA256 签名 |

| Content-Type | application/json |

- *签名规则**:
- **GET/DELETE**: 对 URL query string 进行 HMAC SHA256
- **POST/PUT**: 对 JSON body 进行 HMAC SHA256

### Python 签名示例

```python
import hmac
import hashlib
import json
import time
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
SPOT_URL = "<https://sapi.zebpay.com">

def zebpay_get(path, params=None):
    timestamp = str(int(time.time() *1000))
    if params is None:
        params = {}
    params["timestamp"] = timestamp
    query = "&".join(f"{k}={v}" for k, v in params.items())
    signature = hmac.new(
        API_SECRET.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": API_KEY,
        "X-AUTH-SIGNATURE": signature,
    }
    url = f"{SPOT_URL}/api/{path}?{query}"
    return requests.get(url, headers=headers).json()

def zebpay_post(path, params=None):
    timestamp = str(int(time.time()*1000))
    if params is None:
        params = {}
    params["timestamp"] = timestamp
    body = json.dumps(params)
    signature = hmac.new(
        API_SECRET.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-AUTH-APIKEY": API_KEY,
        "X-AUTH-SIGNATURE": signature,
    }
    return requests.post(f"{SPOT_URL}/api/{path}", headers=headers, data=body).json()

```bash

## 现货市场数据 API (GET, 无需认证)

| 端点 | 描述 |

|------|------|

| /api/v2/market/ticker | 单个 Ticker |

| /api/v2/market/allTickers | 全部 Tickers |

| /api/v2/market/orderbook | 订单簿 |

| /api/v2/market/trades | 最近成交 |

| /api/v2/market/klines | K 线数据 |

| /api/v2/ex/exchangeInfo | 交易所信息 |

| /api/v2/ex/currencies | 币种列表 |

| /api/v2/ex/tradefees | 费率列表 |

| /api/v2/system/time | 服务器时间 |

| /api/v2/system/status | 系统状态 |

- *K 线周期**: `1m, 5m, 15m, 30m, 1h, 2h, 4h, 12h, 1d, 1w`

```python

# Ticker (公开)

resp = requests.get(f"{SPOT_URL}/api/v2/market/allTickers")
for t in resp.json()[:5]:
    print(f"{t['symbol']}: last={t['last']}, high={t['high']}, low={t['low']}")

```bash

## 现货交易 API (需签名)

| 端点 | 方法 | 描述 |

|------|------|------|

| /api/v2/ex/orders | POST | 下单 |

| /api/v2/ex/orders | GET | 订单列表 |

| /api/v2/ex/order | GET | 订单详情 |

| /api/v2/ex/order | DELETE | 撤单 |

| /api/v2/ex/orders | DELETE | 批量撤单 |

| /api/v2/ex/orders/cancelAll | DELETE | 全部撤单 |

| /api/v2/ex/order/fills | GET | 成交明细 |

| /api/v2/account/balance | GET | 余额 |

| /api/v2/ex/tradefee | GET | 交易费率 |

```python

# 下单

order = zebpay_post("v2/ex/orders", {
    "symbol": "BTC-INR",
    "side": "buy",
    "type": "limit",
    "price": "5000000",
    "quantity": "0.001"
})

# 查余额

balance = zebpay_get("v2/account/balance")

```bash

## 合约交易 API (Swap)

### 合约市场数据 (GET, 无需认证)

| 端点 | 描述 |

|------|------|

| /api/v1/market/ticker24Hr | 24h Ticker |

| /api/v1/market/orderBook | 订单簿 |

| /api/v1/market/markets | 市场列表 |

| /api/v1/market/aggTrade | 聚合成交 |

| /api/v1/market/klines | K 线 (POST) |

### 合约交易 (需签名)

| 端点 | 方法 | 描述 |

|------|------|------|

| /api/v1/trade/order | POST | 下单 |

| /api/v1/trade/order | GET | 订单详情 |

| /api/v1/trade/order | DELETE | 撤单 |

| /api/v1/trade/order/open-orders | GET | 活跃订单 |

| /api/v1/trade/order/addTPSL | POST | 设置止盈止损 |

| /api/v1/trade/positions | GET | 持仓列表 |

| /api/v1/trade/position/close | POST | 平仓 |

| /api/v1/trade/addMargin | POST | 追加保证金 |

| /api/v1/trade/reduceMargin | POST | 减少保证金 |

| /api/v1/trade/update/userLeverage | POST | 修改杠杆 |

| /api/v1/trade/userLeverages | GET | 杠杆列表 |

| /api/v1/wallet/balance | GET | 合约余额 |

| /api/v1/trade/history | GET | 历史成交 |

## 速率限制

| 类别 | 限制 |

|------|------|

| REST API | ~20 次/秒 (50ms 间隔) |

## 错误码

| 错误码 | 描述 |

|--------|------|

| 400 | 请求格式错误 |

| 401 | 认证失败 |

| 403 | 禁止访问 |

| 404 | 资源未找到 |

| 429 | 频率超限 |

| 500 | 服务器内部错误 |

| 77 | 订单无效 |

| 3013 | 订单未找到 |

## 变更历史

### 2026-02-27

- 基于 CCXT 源码验证完善

- --

## 相关资源

- [Zebpay API References (GitHub)](<https://github.com/zebpay/zebpay-api-references)>
- [CCXT Zebpay 实现](<https://github.com/ccxt/ccxt/blob/master/python/ccxt/zebpay.py)>

- --

- 本文档由 bt_api_py 项目维护。*
