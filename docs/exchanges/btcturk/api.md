# BTCTurk API 文档

## 交易所信息

- **交易所名称**: BTCTurk
- **官方网站**: https://www.btcturk.com
- **API文档**: https://docs.btcturk.com/
- **24h交易量排名**: #31
- **24h交易量**: $110M+
- **支持的交易对**: 100+（以官方列表为准）
- **API版本**: Public v2 / Private v1

## API基础信息

### 基础URL

```text
# REST API
https://api.btcturk.com

# WebSocket
wss://ws-feed-pro.btcturk.com
```

### 请求头（私有接口）

```text
X-PCK: {public_key}
X-Stamp: {timestamp_ms}
X-Signature: {signature}
Content-Type: application/json
```

## 认证方式

### 1. 获取API密钥

1. 登录 BTCTurk | Kripto
2. 进入 Account -> API Access
3. 创建 API Key 并设置权限
4. 保存 Public Key / Private Key

### 2. 请求签名算法

BTCTurk 使用 HMAC SHA256。

**签名步骤**:

1. 生成 `nonce`（毫秒时间戳）
2. 计算 `message = publicKey + nonce`
3. 使用 Private Key（Base64 解码）做 HMAC SHA256
4. 将结果 Base64 编码，作为 `X-Signature`

## 市场数据API（示例）

- 交易所信息: `GET /api/v2/server/exchangeinfo`
- OrderBook: `GET /api/v2/orderbook?pairSymbol=BTCUSDT&limit=25`

## 交易API（示例）

- 下单: `POST /api/v1/order`
- 撤单: `DELETE /api/v1/order`
- 查询订单: `GET /api/v1/order/{orderId}`
- 当前挂单: `GET /api/v1/openOrders`

## 账户管理API（示例）

- 余额: `GET /api/v1/users/balances`
- 用户成交: `GET /api/v1/users/transactions/trade`

## 速率限制

- WebSocket 连接：每分钟最多 15 次连接请求
- 私有/公共接口限频按端点配置（详见官方 Rate Limits 页面）

## WebSocket支持

- 公共频道：orderbook / trade / ticker 等
- 需 HMAC 鉴权时使用 `wss://ws-feed-pro.btcturk.com`

## 错误代码

- 官方文档提供错误码说明

## 代码示例

```python
# 获取交易所信息
import requests

url = "https://api.btcturk.com/api/v2/server/exchangeinfo"
print(requests.get(url).json())
```
