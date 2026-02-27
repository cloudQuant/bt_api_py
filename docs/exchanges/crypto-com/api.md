# Crypto.com Exchange API 文档

## 交易所信息

- **交易所名称**: Crypto.com Exchange
- **官方网站**: https://crypto.com/exchange
- **API文档**: https://exchange-docs.crypto.com/exchange/v1/rest-ws/index.html
- **24h交易量排名**: #12-15
- **24h交易量**: $1.5B+
- **支持的交易对**: 250+ 交易对
- **API版本**: v1

## API基础信息

### 基础URL

```text
# REST API
https://api.crypto.com/exchange/v1/{method}

# WebSocket
wss://stream.crypto.com/exchange/v1/market
wss://stream.crypto.com/exchange/v1/user

# UAT (测试环境)
https://uat-api.3ona.co/exchange/v1/{method}
wss://uat-stream.3ona.co/exchange/v1/market
wss://uat-stream.3ona.co/exchange/v1/user
```

### 请求结构

```json
{
  "id": 1,
  "method": "private/create-order",
  "api_key": "your_api_key",
  "params": {"instrument_name": "BTC_USD", "side": "BUY", "type": "LIMIT", "price": "30000", "quantity": "0.01"},
  "nonce": 1700000000000,
  "sig": "signature"
}
```

## 认证方式

### 1. 获取API密钥

1. 登录 Crypto.com Exchange
2. 进入账户设置 -> API管理
3. 创建 API Key 并选择权限
4. 保存 API Key 和 Secret

### 2. 请求签名算法

Crypto.com 使用 HMAC SHA256。

**签名步骤**:

1. 对 `params` 按 key 升序排序
2. 拼接成 `key + value` 的字符串（无分隔符）
3. 构建签名字符串: `method + id + api_key + paramString + nonce`
4. 使用 Secret 进行 HMAC SHA256
5. 输出十六进制签名并写入 `sig`

## 市场数据API

- `public/get-instruments`
- `public/get-book`
- `public/get-candlestick`
- `public/get-trades`
- `public/get-tickers`
- `public/get-valuations`
- `public/get-insurance`

## 交易API

- `private/create-order`
- `private/amend-order`
- `private/cancel-order`
- `private/cancel-all-orders`
- `private/get-open-orders`
- `private/get-order-detail`

## 账户与资金API

- `private/user-balance`
- `private/get-accounts`
- `private/get-positions`
- `private/get-deposit-address`
- `private/create-withdrawal`
- `private/get-deposit-history`
- `private/get-withdrawal-history`

## 速率限制

- 私有 REST 接口按方法限速（如 `private/create-order` 为 15 次/100ms）
- 公共行情接口按方法限速（如 `public/get-book` 等为 100 次/秒/IP）
- WebSocket: 用户通道 150 次/秒，行情通道 100 次/秒

## WebSocket支持

- 用户频道示例: `user.order.{instrument_name}`、`user.trade.{instrument_name}`、`user.balance`
- 行情频道示例: `book.{instrument_name}.{depth}`、`ticker.{instrument_name}`、`trade.{instrument_name}`、`candlestick.{time_frame}.{instrument_name}`

## 错误代码

- `TOO_MANY_REQUESTS` 等错误码与原因说明见官方文档

## 代码示例

```python
# REST: 获取交易对列表
import requests

url = "https://api.crypto.com/exchange/v1/public/get-instruments"
print(requests.get(url).json())
```
