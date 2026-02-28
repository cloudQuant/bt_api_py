# Zaif API 文档

## 文档信息

- 文档版本: 1.0.0
- API 版本: V1
- 创建日期: 2026-02-27
- 最后更新: 2026-02-27
- 官方文档: <https://zaif-api-document.readthedocs.io/ja/latest/>
- 数据来源: CCXT 源码验证

## 交易所基本信息

- 官方名称: Zaif
- 官网: <https://zaif.jp>
- 交易所类型: CEX (中心化交易所)
- 总部: 日本
- 支持的交易对: 30+ (JPY 计价)
- 支持的交易类型: 现货(Spot)、保证金(Margin via tlapi)、期货(fapi)
- 手续费: Maker 0%, Taker 0.1%
- 法币支持: JPY (日本円)
- 合规: 日本金融厅 (FSA) 注册

## API 基础 URL

| 端点类型 | URL | 说明 |

|---------|-----|------|

| Public API | `<https://api.zaif.jp/api/1`> | 公共数据 |

| Trading API (tapi) | `<https://api.zaif.jp/tapi`> | 现货交易 |

| Margin API (tlapi) | `<https://api.zaif.jp/tlapi`> | 保证金交易 |

| Futures API (fapi) | `<https://api.zaif.jp/fapi/1`> | 期货数据 |

## 认证方式

### HMAC SHA512 签名

- *请求头**:

| Header | 描述 |

|--------|------|

| Key | API Key |

| Sign | HMAC-SHA512 签名 |

| Content-Type | application/x-www-form-urlencoded |

- *签名步骤**:
1. 构建 POST body（URL-encoded），包含 `method` 和 `nonce`
2. nonce 使用 `milliseconds / 1000`，精确到小数点后 8 位
3. 使用 Secret 对 body 进行 HMAC SHA512

### Python 签名示例

```python
import hmac
import hashlib
import time
import requests
from urllib.parse import urlencode

API_KEY = "your_api_key"
API_SECRET = "your_api_secret"
BASE_URL = "<https://api.zaif.jp">

def zaif_private(method_name, params=None):
    """发送 Zaif 私有 API 请求"""
    nonce = format(time.time(), '.8f')
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

    resp = requests.post(f"{BASE_URL}/tapi", headers=headers, data=body)
    return resp.json()

```bash

## 市场数据 API

### 1. 获取交易对列表

- *端点**: `GET /api/1/currency_pairs/all`

```python
resp = requests.get(f"{BASE_URL}/api/1/currency_pairs/all")
for p in resp.json()[:5]:
    print(f"{p['currency_pair']}: name={p['name']}")

```bash

### 2. 获取 Ticker

- *端点**: `GET /api/1/ticker/{pair}`

```python
resp = requests.get(f"{BASE_URL}/api/1/ticker/btc_jpy")
t = resp.json()
print(f"BTC/JPY: last={t['last']}, bid={t['bid']}, ask={t['ask']}, "
      f"high={t['high']}, low={t['low']}, vwap={t['vwap']}, volume={t['volume']}")

```bash

### 3. 获取订单簿

- *端点**: `GET /api/1/depth/{pair}`

```python
resp = requests.get(f"{BASE_URL}/api/1/depth/btc_jpy")
book = resp.json()
for ask in book["asks"][:5]:
    print(f"ASK: price={ask[0]}, amount={ask[1]}")
for bid in book["bids"][:5]:
    print(f"BID: price={bid[0]}, amount={bid[1]}")

```bash

### 4. 获取最近成交

- *端点**: `GET /api/1/trades/{pair}`

```python
resp = requests.get(f"{BASE_URL}/api/1/trades/btc_jpy")
for t in resp.json()[:5]:
    print(f"ID={t['tid']} price={t['price']} amount={t['amount']} "
          f"type={t['trade_type']} date={t['date']}")

```bash

### 5. 获取最新价

- *端点**: `GET /api/1/last_price/{pair}`

## 交易 API (POST to /tapi)

### 1. 查询余额

- *方法**: `get_info2`

```python
info = zaif_private("get_info2")
if info.get("success") == 1:
    for currency, amount in info["return"]["funds"].items():
        if float(amount) > 0:
            print(f"{currency}: {amount}")

```bash

### 2. 下单

- *方法**: `trade`

| 参数 | 类型 | 必需 | 描述 |

|------|------|------|------|

| currency_pair | STRING | 是 | 交易对 (btc_jpy) |

| action | STRING | 是 | bid (买) / ask (卖) |

| price | FLOAT | 是 | 价格 |

| amount | FLOAT | 是 | 数量 |

| limit | FLOAT | 否 | 止盈价 |

| comment | STRING | 否 | 备注 |

```python

# 限价买单

order = zaif_private("trade", {
    "currency_pair": "btc_jpy",
    "action": "bid",
    "price": 4000000,
    "amount": 0.001
})
if order.get("success") == 1:
    print(f"Order ID: {order['return']['order_id']}")

```bash

### 3. 撤单

- *方法**: `cancel_order`

```python
result = zaif_private("cancel_order", {"order_id": 12345})

```bash

### 4. 查询挂单

- *方法**: `active_orders`

```python
orders = zaif_private("active_orders", {"currency_pair": "btc_jpy"})
if orders.get("success") == 1:
    for oid, o in orders["return"].items():
        print(f"ID:{oid} {o['action']} price={o['price']} amount={o['amount']}")

```bash

### 5. 查询成交历史

- *方法**: `trade_history`

## 账户管理 API

| 方法 | 描述 |

|------|------|

| get_info | 完整账户信息 (含权限) |

| get_info2 | 余额信息 |

| get_personal_info | 个人信息 |

| get_id_info | KYC 信息 |

| deposit_history | 充值历史 |

| withdraw | 提现 |

| withdraw_history | 提现历史 |

## 速率限制

| 端点 | 限制 |

|------|------|

| Public API | 10 次/秒 |

| get_info | 1 次/秒 |

| get_info2 | 2 次/秒 |

| active_orders / cancel_order | 2 次/秒 |

| trade | 2 次/秒 |

| trade_history | 0.2 次/秒 |

## 错误处理

成功: `{"success": 1, "return": {...}}`
失败: `{"success": 0, "error": "错误信息"}`

## 变更历史

### 2026-02-27

- 基于 CCXT 源码验证完善
- 添加 HMAC SHA512 签名（含特殊 nonce 格式）
- 添加完整端点列表和 Python 示例

- --

## 相关资源

- [Zaif API 文档](<https://zaif-api-document.readthedocs.io/ja/latest/)>
- [CCXT Zaif 实现](<https://github.com/ccxt/ccxt/blob/master/python/ccxt/zaif.py)>

- --

- 本文档由 bt_api_py 项目维护，内容基于 CCXT 源码验证整理。*
