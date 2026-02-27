# CoinSpot API 文档

## 文档信息
- 文档版本: 1.0.0
- 创建日期: 2026-02-27
- 官方文档: https://www.coinspot.com.au/api
- 数据来源: CCXT 源码验证

## 交易所基本信息
- 官方名称: CoinSpot
- 官网: https://www.coinspot.com.au
- 交易所类型: CEX (中心化交易所)
- 总部: 澳大利亚
- 支持的交易对: 300+ (AUD, USDT 计价)
- 支持的交易类型: 现货(Spot) - 仅限价单
- 法币支持: AUD (澳元)

## API基础URL

| 端点类型 | URL |
|---------|-----|
| Public V1 | `https://www.coinspot.com.au/pubapi` |
| Private V1 | `https://www.coinspot.com.au/api` |
| Public V2 | `https://www.coinspot.com.au/pubapi/v2` |
| Private V2 | `https://www.coinspot.com.au/api/v2` |

## 认证方式

### HMAC SHA512 签名

**请求头**:

| Header | 描述 |
|--------|------|
| key | API Key |
| sign | HMAC-SHA512 签名 |
| Content-Type | application/json |

**签名步骤**:
1. 构建 JSON body，包含 `nonce` (时间戳)
2. 使用 Secret 对 JSON body 进行 HMAC SHA512

### Python 签名示例

```python
import hmac
import hashlib
import time
import json
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "https://www.coinspot.com.au/api"

def coinspot_request(path, params=None):
    nonce = int(time.time() * 1000)
    body_params = {"nonce": nonce}
    if params:
        body_params.update(params)
    body = json.dumps(body_params)
    signature = hmac.new(
        API_SECRET.encode(), body.encode(), hashlib.sha512
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "key": API_KEY,
        "sign": signature,
    }
    resp = requests.post(f"{BASE_URL}/{path}", headers=headers, data=body)
    return resp.json()
```

## 市场数据API

### V1 Public (GET)

| 端点 | 描述 |
|------|------|
| /pubapi/latest | 全部 Ticker |

### V2 Public (GET)

| 端点 | 描述 |
|------|------|
| /pubapi/v2/latest | 全部最新价 |
| /pubapi/v2/latest/{cointype} | 单币最新价 |
| /pubapi/v2/buyprice/{cointype} | 买入价 |
| /pubapi/v2/sellprice/{cointype} | 卖出价 |
| /pubapi/v2/orders/open/{cointype} | 公开挂单 |
| /pubapi/v2/orders/completed/{cointype} | 已完成订单 |

```python
# 获取全部 Ticker
resp = requests.get("https://www.coinspot.com.au/pubapi/latest")
prices = resp.json().get("prices", {})
for coin, p in list(prices.items())[:5]:
    print(f"{coin}: bid={p['bid']}, ask={p['ask']}, last={p['last']}")
```

## 交易API (POST, 需签名)

### V1 Private

| 端点 | 描述 |
|------|------|
| my/buy | 限价买单 |
| my/sell | 限价卖单 |
| my/buy/cancel | 撤销买单 |
| my/sell/cancel | 撤销卖单 |
| orders | 公开订单簿 |
| orders/history | 成交历史 |
| my/balances | 余额 |
| my/orders | 我的挂单 |

### V2 Private

| 端点 | 描述 |
|------|------|
| my/buy | 限价买单 |
| my/sell | 限价卖单 |
| my/buy/now | 即时买入 (市价) |
| my/sell/now | 即时卖出 (市价) |
| my/swap/now | 即时兑换 |
| my/buy/cancel | 撤销买单 |
| my/sell/cancel | 撤销卖单 |
| my/buy/cancel/all | 撤销全部买单 |
| my/sell/cancel/all | 撤销全部卖单 |
| my/coin/withdraw/send | 提现 |
| ro/my/balances | 余额 (只读) |
| ro/my/orders/completed | 已完成订单 |
| ro/my/deposits | 充值记录 |
| ro/my/withdrawals | 提现记录 |

```python
# 限价买单
order = coinspot_request("my/buy", {
    "cointype": "BTC",
    "amount": 0.001,
    "rate": 50000
})

# 限价卖单
order = coinspot_request("my/sell", {
    "cointype": "BTC",
    "amount": 0.001,
    "rate": 60000
})

# 撤单 (需指定 side)
coinspot_request("my/buy/cancel", {"id": "order_id_here"})

# 余额查询
balance = coinspot_request("my/balances")
```

> **注意**: CoinSpot V1 API 仅支持限价单。V2 API 支持即时（市价）订单。

## 速率限制

| 类别 | 限制 |
|------|------|
| REST API | 1 次/秒 |

## 错误处理

成功: `{"status": "ok", ...}`
失败: `{"status": "error", "message": "..."}`

## 变更历史

### 2026-02-27
- 基于 CCXT 源码验证完善

---

## 相关资源

- [CoinSpot API 文档](https://www.coinspot.com.au/api)
- [CCXT CoinSpot 实现](https://github.com/ccxt/ccxt/blob/master/python/ccxt/coinspot.py)

---

*本文档由 bt_api_py 项目维护。*
