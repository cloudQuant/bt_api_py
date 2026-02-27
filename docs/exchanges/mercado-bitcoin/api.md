# Mercado Bitcoin API 文档

## 文档信息
- 文档版本: 1.0.0
- API版本: V3 (Private) / V4 (K线)
- 创建日期: 2026-02-27
- 官方文档: https://www.mercadobitcoin.com.br/api-doc
- 数据来源: CCXT 源码验证

## 交易所基本信息
- 官方名称: Mercado Bitcoin
- 官网: https://www.mercadobitcoin.com.br
- 交易所类型: CEX (中心化交易所)
- 总部: 巴西
- 支持的交易对: 100+ (BRL 计价)
- 支持的交易类型: 现货(Spot)
- 手续费: Maker 0.3%, Taker 0.7%
- 法币支持: BRL (巴西雷亚尔)
- 特点: 拉丁美洲最大的加密货币交易所之一

## API基础URL

| 端点类型 | URL |
|---------|-----|
| Public API | `https://www.mercadobitcoin.net/api` |
| Private API (tapi) | `https://www.mercadobitcoin.net/tapi` |
| V4 K线 | `https://api.mercadobitcoin.net/api/v4` |

## 认证方式

### HMAC SHA512 签名

**请求头**:

| Header | 描述 |
|--------|------|
| TAPI-ID | API Key |
| TAPI-MAC | HMAC-SHA512 签名 |
| Content-Type | application/x-www-form-urlencoded |

**签名步骤**:
1. 构建 URL-encoded POST body，包含 `tapi_method` 和 `tapi_nonce`
2. 签名字符串: `/tapi/v3/?` + body
3. 使用 Secret 进行 HMAC SHA512

### Python 签名示例

```python
import hmac
import hashlib
import time
import requests
from urllib.parse import urlencode

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "https://www.mercadobitcoin.net"

def mb_request(method_name, params=None):
    nonce = int(time.time())
    body_params = {"tapi_method": method_name, "tapi_nonce": nonce}
    if params:
        body_params.update(params)
    body = urlencode(body_params)
    auth = f"/tapi/v3/?{body}"
    signature = hmac.new(
        API_SECRET.encode(), auth.encode(), hashlib.sha512
    ).hexdigest()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "TAPI-ID": API_KEY,
        "TAPI-MAC": signature,
    }
    resp = requests.post(f"{BASE_URL}/tapi/v3/", headers=headers, data=body)
    return resp.json()
```

## 市场数据API (GET, 无需认证)

| 端点 | 描述 |
|------|------|
| `/{coin}/ticker/` | Ticker |
| `/{coin}/orderbook/` | 订单簿 |
| `/{coin}/trades/` | 最近成交 |
| `/{coin}/trades/{from}/` | 指定起始ID成交 |
| `/{coin}/day-summary/{year}/{month}/{day}/` | 日线摘要 |
| `/coins` | 币种列表 |

```python
PUB = "https://www.mercadobitcoin.net/api"

# Ticker
resp = requests.get(f"{PUB}/BTC/ticker/")
t = resp.json()["ticker"]
print(f"BTC/BRL: last={t['last']}, buy={t['buy']}, sell={t['sell']}, "
      f"high={t['high']}, low={t['low']}, vol={t['vol']}")

# 订单簿
resp = requests.get(f"{PUB}/BTC/orderbook/")
book = resp.json()
for ask in book["asks"][:5]:
    print(f"ASK: price={ask[0]}, amount={ask[1]}")
```

### K线数据 (V4)

**端点**: `GET https://api.mercadobitcoin.net/api/v4/candles`

**周期**: `15m, 1h, 3h, 1d, 1w, 1M`

```python
import time as t
resp = requests.get("https://api.mercadobitcoin.net/api/v4/candles", params={
    "symbol": "BTC-BRL",
    "resolution": "1d",
    "from": int(t.time()) - 86400 * 7,
    "to": int(t.time())
})
```

## 交易API (POST to /tapi)

| 方法 | 描述 |
|------|------|
| place_buy_order | 限价买单 |
| place_sell_order | 限价卖单 |
| place_market_buy_order | 市价买单 |
| place_market_sell_order | 市价卖单 |
| cancel_order | 撤单 |
| get_order | 订单详情 |
| list_orders | 订单列表 |
| list_orderbook | 订单簿 |
| get_account_info | 账户信息 |
| withdraw_coin | 提现 |
| get_withdrawal | 提现详情 |

```python
# 限价买单
order = mb_request("place_buy_order", {
    "coin_pair": "BRLBTC",
    "quantity": "0.001",
    "limit_price": "200000"
})

# 市价买单
order = mb_request("place_market_buy_order", {
    "coin_pair": "BRLBTC",
    "cost": "1000"  # 花费 1000 BRL
})

# 撤单
mb_request("cancel_order", {"coin_pair": "BRLBTC", "order_id": 12345})

# 余额
info = mb_request("get_account_info")
```

## 速率限制

| 类别 | 限制 |
|------|------|
| REST API | 1 次/秒 |

## 错误处理

成功: `{"status_code": 100, "response_data": {...}}`
失败: `{"status_code": 4xx, "error_message": "..."}`

## 变更历史

### 2026-02-27
- 基于 CCXT 源码验证完善

---

## 相关资源

- [Mercado Bitcoin API 文档](https://www.mercadobitcoin.com.br/api-doc)
- [CCXT Mercado 实现](https://github.com/ccxt/ccxt/blob/master/python/ccxt/mercado.py)

---

*本文档由 bt_api_py 项目维护。*
