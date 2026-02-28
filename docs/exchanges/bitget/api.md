# Bitget API 文档

## 交易所信息

- **交易所名称**: Bitget
- **官方网站**: <https://www.bitget.com>
- **API 文档**: <https://www.bitget.com/api-doc>
- **24h 交易量排名**: #11-15
- **24h 交易量**: $2B+
- **支持的交易对**: 600+ 交易对
- **API 版本**: v2 (主推), v1 (旧版)

## API 基础信息

### 基础 URL

```text

# REST API

<https://api.bitget.com>

# WebSocket (v3)

wss://ws.bitget.com/v3/ws/public
wss://ws.bitget.com/v3/ws/private

```bash

### 请求头

```text
ACCESS-KEY: {api_key}
ACCESS-SIGN: {signature}
ACCESS-PASSPHRASE: {passphrase}
ACCESS-TIMESTAMP: {timestamp}
Content-Type: application/json
locale: en-US

```bash

## 认证方式

### 1. 获取 API 密钥

1. 登录 Bitget 账户
2. 进入用户中心 -> API 管理
3. 创建 API Key 并设置 Passphrase
4. 配置权限与 IP 白名单
5. 保存 API Key / Secret / Passphrase

### 2. 请求签名算法

Bitget 使用 HMAC SHA256 签名算法。

- *签名步骤**:

1. 生成时间戳（毫秒字符串）
2. 构建签名字符串: `timestamp + method + requestPath + "?" + queryString + body`
3. 使用 Secret Key 对签名字符串进行 HMAC SHA256
4. 将结果进行 Base64 编码
5. 将签名放入 `ACCESS-SIGN` 请求头

### 3. Python 认证示例

```python
import hmac
import hashlib
import base64
import time
import requests

class BitgetAuth:
    def __init__(self, api_key, api_secret, passphrase):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.base_url = "<https://api.bitget.com">

    def _sign(self, timestamp, method, request_path, body=""):
        message = f"{timestamp}{method.upper()}{request_path}{body}"
        mac = hmac.new(self.api_secret.encode(), message.encode(), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode()

    def _headers(self, timestamp, signature):
        return {
            "ACCESS-KEY": self.api_key,
            "ACCESS-SIGN": signature,
            "ACCESS-PASSPHRASE": self.passphrase,
            "ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json",
            "locale": "en-US",
        }

    def get(self, endpoint, params=None):
        ts = str(int(time.time() * 1000))
        request_path = endpoint
        if params:
            qs = "&".join([f"{k}={v}" for k, v in params.items()])
            request_path = f"{endpoint}?{qs}"
        sig = self._sign(ts, "GET", request_path, "")
        headers = self._headers(ts, sig)
        url = f"{self.base_url}{request_path}"
        return requests.get(url, headers=headers).json()

```bash

## 市场数据 API（Spot v2）

- 服务器时间: `GET /api/v2/public/time`
- 交易对列表: `GET /api/v2/spot/public/symbols`
- 行情列表: `GET /api/v2/spot/market/tickers`
- 深度: `GET /api/v2/spot/market/orderbook`
- K 线: `GET /api/v2/spot/market/candles`
- 最新成交: `GET /api/v2/spot/market/fills-history`
- 币种信息: `GET /api/v2/spot/public/coins`

## 交易 API（Spot v2）

- 下单: `POST /api/v2/spot/trade/place-order`
- 撤单: `POST /api/v2/spot/trade/cancel-order`
- 修改订单（撤单再下单）: `POST /api/v2/spot/trade/cancel-replace-order`

## 账户管理 API（Spot v2）

- 账户资产: `GET /api/v2/spot/account/assets`

## 速率限制

- 以接口说明为准（常见公共接口为 20 次/秒/IP）
- 交易接口常见限制为 10 次/秒/UID

## WebSocket 支持

- 公共频道：行情、深度、K 线、成交
- 私有频道：订单、资产等

## 错误代码

- 官方文档提供 `code`/`msg` 错误码列表

## 代码示例

```python

# 获取服务器时间

import requests

url = "<https://api.bitget.com/api/v2/public/time">
print(requests.get(url).json())

```bash
