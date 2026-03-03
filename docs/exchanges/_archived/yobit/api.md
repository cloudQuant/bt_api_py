# YoBit API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V3 (Public) / tapi (Private)
- 创建日期: 2026-02-27
- 官方文档: <https://www.yobit.net/en/api/>
- 数据来源: CCXT 源码验证

## 交易所基本信息

- 官方名称: YoBit
- 官网: <https://www.yobit.net>
- 交易所类型: CEX (中心化交易所)
- 总部: 俄罗斯
- 支持的交易对: 8000+ (BTC, ETH, USDT, DOGE, USD, RUR 等计价)
- 支持的交易类型: 现货(Spot) - 仅限价单
- 手续费: Maker 0.2%, Taker 0.2%

## API 基础 URL

| 端点类型 | URL |

|---------|-----|

| Public | `<https://yobit.net/api/3`> |

| Private (tapi) | `<https://yobit.net/tapi`> |

## 认证方式

### HMAC SHA512 签名

- *请求头**: `Key` (API Key), `Sign` (HMAC-SHA512 签名)

```python
import hmac
import hashlib
import time
import requests
from urllib.parse import urlencode

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"

def yobit_private(method_name, params=None):
    nonce = int(time.time())
    body_params = {"method": method_name, "nonce": nonce}
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
    resp = requests.post("<https://yobit.net/tapi",> headers=headers, data=body)
    return resp.json()

```bash

## 市场数据 API (GET)

| 端点 | 描述 |

|------|------|

| `/api/3/info` | 全部交易对信息 |

| `/api/3/ticker/{pair}` | Ticker (可多对，用`-`分隔) |

| `/api/3/depth/{pair}` | 订单簿 |

| `/api/3/trades/{pair}` | 最近成交 |

```python
BASE = "<https://yobit.net/api/3">

# Ticker

resp = requests.get(f"{BASE}/ticker/btc_usdt")
t = resp.json()["btc_usdt"]
print(f"BTC/USDT: last={t['last']}, buy={t['buy']}, sell={t['sell']}, "
      f"high={t['high']}, low={t['low']}, vol={t['vol']}")

# 订单簿

resp = requests.get(f"{BASE}/depth/btc_usdt", params={"limit": 10})
book = resp.json()["btc_usdt"]
for ask in book["asks"][:5]:
    print(f"ASK: price={ask[0]}, amount={ask[1]}")

```bash

## 交易 API (POST to /tapi)

| 方法 | 描述 |

|------|------|

| getInfo | 账户余额 |

| Trade | 下单 (限价) |

| ActiveOrders | 挂单列表 |

| OrderInfo | 订单详情 |

| CancelOrder | 撤单 |

| TradeHistory | 成交历史 |

| GetDepositAddress | 充值地址 |

| WithdrawCoinsToAddress | 提现 |

```python

# 查余额

info = yobit_private("getInfo")
if info.get("success") == 1:
    for coin, amt in info["return"]["funds"].items():
        if float(amt) > 0:
            print(f"{coin}: {amt}")

# 限价买单

order = yobit_private("Trade", {
    "pair": "btc_usdt",
    "type": "buy",
    "rate": 40000,
    "amount": 0.001
})

# 撤单

yobit_private("CancelOrder", {"order_id": 12345})

```bash

## 速率限制

| 类别 | 限制 |

|------|------|

| REST API | 每 2 秒缓存刷新 |

## 错误处理

成功: `{"success": 1, "return": {...}}`
失败: `{"success": 0, "error": "错误信息"}`

## 变更历史

### 2026-02-27

- 基于 CCXT 源码验证完善

- --

## 相关资源

- [YoBit API 文档](<https://www.yobit.net/en/api/)>
- [CCXT YoBit 实现](<https://github.com/ccxt/ccxt/blob/master/python/ccxt/yobit.py)>

- --

- 本文档由 bt_api_py 项目维护。*
