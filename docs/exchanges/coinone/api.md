# Coinone API 文档

## 交易所信息

- **交易所名称**: Coinone
- **官方网站**: https://coinone.co.kr
- **API文档**: https://docs.coinone.co.kr
- **24h交易量排名**: #30
- **24h交易量**: $130M+
- **支持的交易对**: 200+（以官方列表为准）
- **API版本**: Public v2、Private v2 / v2.1

## API基础信息

### 基础URL

```text
# REST API
https://api.coinone.co.kr
```

### 请求头（私有接口）

```text
Content-Type: application/json
X-COINONE-PAYLOAD: {base64_payload}
X-COINONE-SIGNATURE: {signature}
```

## 认证方式

### 1. 获取API密钥

1. 登录 Coinone 账户
2. 在 Open API 管理中创建个人 API
3. 获取 Access Token 与 Secret Key

### 2. 请求签名算法

1. 请求体 JSON 字符串 Base64 编码为 `X-COINONE-PAYLOAD`
2. 使用 Secret Key 对 payload 进行 HMAC SHA512，得到 `X-COINONE-SIGNATURE`
3. v2.1 nonce 使用 UUID v4；v2 nonce 使用递增时间戳

## 市场数据API（示例）

- 价格档位: `GET /public/v2/range_units`
- 订单簿: `GET /public/v2/orderbook/{quote_currency}/{target_currency}?size=15`

## 交易API（示例）

- 下单（v2.1）: `POST /v2.1/order`

## 账户管理API

- 余额/订单/成交等私有接口（详见官方文档）

## 速率限制

- Public v2：1200 次/分钟（IP）
- Public v1：600 次/分钟（IP）
- Private v2.1：订单相关 40 次/秒，其他 80 次/秒（按 Portfolio）
- Private v2：订单相关 40 次/秒，其他 40 次/秒（按 Portfolio）

## WebSocket支持

- 官方开发者中心提供 WebSocket 说明

## 错误代码

- 官方文档提供错误码表

## 代码示例

```python
# 获取订单簿
import requests

url = "https://api.coinone.co.kr/public/v2/orderbook/KRW/BTC?size=15"
print(requests.get(url).json())
```
