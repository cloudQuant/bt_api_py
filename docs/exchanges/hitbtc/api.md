# HitBTC API 文档

## 交易所信息

- **交易所名称**: HitBTC
- **官方网站**: https://hitbtc.com
- **API文档**: https://api.hitbtc.com/
- **24h交易量排名**: #32
- **24h交易量**: $85M+
- **支持的交易对**: 500+（以官方列表为准）
- **API版本**: v3（推荐），v2 仍可用

## API基础信息

### 基础URL

```text
# REST API
https://api.hitbtc.com/api/3

# WebSocket
wss://api.hitbtc.com/api/3/ws/public
wss://api.hitbtc.com/api/3/ws/trading
wss://api.hitbtc.com/api/3/ws/wallet

# Demo (Sandbox)
https://api.demo.hitbtc.com/api/3
wss://api.demo.hitbtc.com/api/3/ws/public
wss://api.demo.hitbtc.com/api/3/ws/trading
wss://api.demo.hitbtc.com/api/3/ws/wallet
```

### 认证方式

HitBTC 支持 **Basic** 与 **HS256** 两种鉴权方式。

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

- REST 与 WS 均为 **Rate/Burst** 模式
- 默认 REST：`/*` 20/30，`/public/*` 30/50
- WS：`/ws/public`、`/ws/trading`、`/ws/wallet` 10/10
- 具体以官方文档为准

## WebSocket支持

- Public/Trading/Wallet 三类 WS 端点
- 支持 Basic / HS256 登录

## 错误代码

- 官方文档提供完整错误码与 HTTP 状态码说明

## 代码示例

```python
# 获取行情
import requests

url = "https://api.hitbtc.com/api/3/public/ticker"
print(requests.get(url).json())
```
