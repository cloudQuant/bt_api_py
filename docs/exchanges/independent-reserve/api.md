# Independent Reserve API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-02-27
- 官方文档: <https://www.independentreserve.com/API>
- 数据来源: CCXT 源码验证

## 交易所基本信息

- 官方名称: Independent Reserve
- 官网: <https://www.independentreserve.com>
- 交易所类型: CEX (中心化交易所)
- 总部: 澳大利亚/新西兰
- 支持的交易类型: 现货(Spot)
- 手续费: Maker 0.5%, Taker 0.5% (基础费率, 阶梯递减)
- 法币支持: AUD, NZD, USD, SGD

## API 基础 URL

| 端点类型 | URL |

|---------|-----|

| Public | `<https://api.independentreserve.com/Public`> |

| Private | `<https://api.independentreserve.com/Private`> |

## 认证方式

### HMAC SHA256 签名

- *签名步骤**:
1. 拼接参数字符串: `url,apiKey={key},nonce={nonce},param1=val1,...`（逗号分隔）
2. 使用 Secret 进行 HMAC SHA256
3. 签名转为大写十六进制
4. POST JSON body 包含 `apiKey`, `nonce`, `signature` 及其他参数

### Python 签名示例

```python
import hmac
import hashlib
import time
import json
import requests

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "<https://api.independentreserve.com">

def ir_request(endpoint, params=None):
    """发送 Independent Reserve 签名请求"""
    url = f"{BASE_URL}/Private/{endpoint}"
    nonce = int(time.time() *1000)

    auth_parts = [url, f"apiKey={API_KEY}", f"nonce={nonce}"]
    body = {"apiKey": API_KEY, "nonce": nonce}

    if params:
        for key, value in params.items():
            auth_parts.append(f"{key}={value}")
            body[key] = value

    message = ",".join(auth_parts)
    signature = hmac.new(
        API_SECRET.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest().upper()

    body["signature"] = signature
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, headers=headers, json=body)
    return resp.json()

```bash

## 市场数据 API (GET, 无需认证)

| 端点 | 描述 |

|------|------|

| GetValidPrimaryCurrencyCodes | 基础币种列表 |

| GetValidSecondaryCurrencyCodes | 计价币种列表 |

| GetMarketSummary | 市场摘要 (Ticker) |

| GetOrderBook | 订单簿 |

| GetRecentTrades | 最近成交 |

| GetAllOrders | 全部订单 |

| GetTradeHistorySummary | 历史成交摘要 |

| GetFxRates | 外汇汇率 |

| GetOrderMinimumVolumes | 最小下单量 |

| GetCryptoWithdrawalFees2 | 提现手续费 |

| GetNetworks | 支持的网络 |

| GetPrimaryCurrencyConfig2 | 币种配置 |

```python

# 获取市场摘要

resp = requests.get(f"{BASE_URL}/Public/GetMarketSummary", params={
    "primaryCurrencyCode": "Xbt",
    "secondaryCurrencyCode": "Aud"
})
t = resp.json()
print(f"BTC/AUD: last={t['LastPrice']}, bid={t['CurrentHighestBidPrice']}, "
      f"ask={t['CurrentLowestOfferPrice']}, vol={t['DayVolumeXbt']}")

# 获取订单簿

resp = requests.get(f"{BASE_URL}/Public/GetOrderBook", params={
    "primaryCurrencyCode": "Xbt",
    "secondaryCurrencyCode": "Aud"
})
book = resp.json()
for o in book["BuyOrders"][:5]:
    print(f"BID: price={o['Price']}, volume={o['Volume']}")

```bash

## 交易 API (POST, 需签名)

| 端点 | 描述 |

|------|------|

| PlaceLimitOrder | 限价单 |

| PlaceMarketOrder | 市价单 |

| CancelOrder | 撤单 |

| GetOpenOrders | 挂单列表 |

| GetClosedOrders | 已完成订单 |

| GetClosedFilledOrders | 已成交订单 |

| GetOrderDetails | 订单详情 |

| GetTrades | 成交记录 |

| GetAccounts | 账户列表 |

| GetTransactions | 交易记录 |

| GetBrokerageFees | 费率查询 |

```python

# 限价买单

order = ir_request("PlaceLimitOrder", {
    "primaryCurrencyCode": "Xbt",
    "secondaryCurrencyCode": "Aud",
    "orderType": "LimitBid",
    "price": 50000,
    "volume": 0.001
})
print(f"Order GUID: {order.get('OrderGuid')}")

# 市价卖单

order = ir_request("PlaceMarketOrder", {
    "primaryCurrencyCode": "Xbt",
    "secondaryCurrencyCode": "Aud",
    "orderType": "MarketOffer",
    "volume": 0.001
})

# 撤单

ir_request("CancelOrder", {"orderGuid": "order-guid-here"})

```bash

- *订单类型**: `LimitBid`, `LimitOffer`, `MarketBid`, `MarketOffer`

## 账户管理 API

| 端点 | 描述 |

|------|------|

| GetDigitalCurrencyDepositAddress2 | 充值地址 |

| WithdrawCrypto | 加密货币提现 |

| RequestFiatWithdrawal | 法币提现 |

| WithdrawFiatCurrency | 法币提现 |

| GetFiatBankAccounts | 银行账户列表 |

| GetDigitalCurrencyWithdrawal | 提现详情 |

## 速率限制

| 类别 | 限制 |

|------|------|

| REST API | 1 次/秒 |

## 变更历史

### 2026-02-27

- 基于 CCXT 源码验证完善

- --

## 相关资源

- [官方 API 文档](<https://www.independentreserve.com/API)>
- [CCXT 实现](<https://github.com/ccxt/ccxt/blob/master/python/ccxt/independentreserve.py)>

- --

- 本文档由 bt_api_py 项目维护。*
