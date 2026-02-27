# BeQuant API 文档

## 交易所信息

- **交易所名称**: BeQuant
- **官方网站**: https://bequant.io
- **API文档**: https://api.bequant.io/
- **24h交易量排名**: #34
- **24h交易量**: $80M+
- **支持的交易对**: 200+（以官方列表为准）
- **API版本**: v3（推荐），v2 仍可用

## API基础信息

### 基础URL

```text
# REST API
https://api.bequant.io/api/3

# WebSocket
wss://api.bequant.io/api/3/ws/public
wss://api.bequant.io/api/3/ws/trading
wss://api.bequant.io/api/3/ws/wallet
```

### 认证方式

BeQuant 支持 **Basic** 与 **HS256** 两种鉴权方式。

**Basic**: `Authorization: Basic base64(apiKey:secretKey)`

**HS256**:

1. 组装签名串：`method + path + [?query] + [body] + timestamp + [window]`
2. 用 secretKey 做 HMAC SHA256
3. `Authorization: HS256 base64(apiKey:signature:timestamp[:window])`

## 市场数据API（示例）

- 货币列表: `GET /api/3/public/currency`
- 交易对: `GET /api/3/public/symbol`
- 行情: `GET /api/3/public/ticker`

## 交易API（示例）

- 下单: `POST /api/3/spot/order`
- 撤单: `DELETE /api/3/spot/order`
- 查询订单: `GET /api/3/spot/order`

## 账户管理API（示例）

- 资产: `GET /api/3/wallet/balance`

## 速率限制

- REST 默认：`/*` 20/30，`/public/*` 30/50，`/wallet/*` 10/10
- 交易类接口：`/spot/order/*` 300/450
- WS：`/ws/public`、`/ws/trading`、`/ws/wallet` 10/10
- 账户总挂单上限：全市场 25000，单交易对 2000

## WebSocket支持

- Public/Trading/Wallet 三类 WS 端点
- 支持 Basic / HS256 登录

## 错误代码

- 官方文档提供完整错误码与 HTTP 状态码说明

## 代码示例

```python
# 获取行情
import requests

url = "https://api.bequant.io/api/3/public/ticker"
print(requests.get(url).json())
```
