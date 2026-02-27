# Bithumb API 文档

## 交易所信息

- **交易所名称**: Bithumb
- **官方网站**: https://www.bithumb.com
- **API文档**: https://apidocs.bithumb.com
- **24h交易量排名**: #15
- **24h交易量**: $500M+
- **支持的交易对**: 200+ 交易对
- **API版本**: v1

## API基础信息

### 基础URL

```text
# REST API
https://api.bithumb.com

# WebSocket (公共)
wss://pubwss.bithumb.com/pub/ws
```

### 请求头（私有接口）

```text
Api-Key: {api_key}
Api-Sign: {signature}
Api-Nonce: {nonce}
api-client-type: {0|1|2}
Content-Type: application/x-www-form-urlencoded
```

## 认证方式

### 1. 获取API密钥

1. 登录 Bithumb 账户
2. 进入设置 -> API管理
3. 创建 API Key 并设置权限
4. 设置 IP 白名单
5. 保存 API Key 和 Secret

### 2. 请求签名算法

Bithumb 使用 HMAC SHA512。

**签名步骤**:

1. 构建请求参数并进行 URL 编码
2. 拼接签名字符串: `endpoint + delimiter + request_params + delimiter + nonce`
3. 使用 Secret 进行 HMAC SHA512
4. 将结果 Base64 编码
5. 写入 `Api-Sign`

> `api-client-type` 决定分隔符：0 为 ASCII 0，1 为 ASCII 1，2 为分号

## 市场数据API（Public）

- 行情: `GET /public/ticker/{order_currency}_{payment_currency}`
- 行情（全部）: `GET /public/ticker/ALL_{payment_currency}`
- 深度: `GET /public/orderbook/{order_currency}_{payment_currency}`
- 深度（全部）: `GET /public/orderbook/ALL_{payment_currency}`
- 成交: `GET /public/transaction_history/{order_currency}_{payment_currency}`
- K线: `GET /public/candlestick/{order_currency}_{payment_currency}/{chart_intervals}`

## 交易API（Private）

- 订单列表: `GET /v1/orders`
- 账户信息: `POST /info/account`
- 资产余额: `POST /info/balance`
- 用户成交: `POST /info/user_transactions`
- 交易信息: `POST /info/ticker`

## 账户管理API

- 账户信息: `POST /info/account`
- 资产余额: `POST /info/balance`

## 速率限制

- REST 与 WebSocket 限频以官方文档为准

## WebSocket支持

- 当前价、成交、호가(ticker/transaction/orderbookdepth)
- 订阅格式为 JSON，支持批量订阅

## 错误代码

- 结果状态码 `status` 字段参考官方文档

## 代码示例

```python
# REST: 获取行情
import requests

url = "https://api.bithumb.com/public/ticker/BTC_KRW"
print(requests.get(url).json())
```
