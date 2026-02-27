# Bitso API 文档

## 交易所信息

- **交易所名称**: Bitso
- **官方网站**: https://bitso.com
- **API文档**: https://docs.bitso.com/bitso-api/
- **24h交易量排名**: #22
- **24h交易量**: $100M+
- **支持的交易对**: 100+ 交易对（以官方列表为准）
- **API版本**: v3

## API基础信息

### 基础URL

```text
# REST API (生产环境)
https://bitso.com/api/v3

# REST API (测试环境)
https://stage.bitso.com/api/v3

# REST API (Sandbox)
https://api-sandbox.bitso.com/api/v3

# WebSocket
wss://ws.bitso.com
```

### 请求头（私有接口）

```text
Authorization: Bitso {key}:{nonce}:{signature}
Content-Type: application/json
```

## 认证方式

### 1. 获取API密钥

1. 登录 Bitso 账户
2. 进入 Profile -> API
3. 创建 API Key 并设置权限（交易/余额/账户信息等）
4. 保存 API Key / Secret（仅显示一次）

### 2. 请求签名算法

Bitso 使用 HMAC SHA256。

**签名字符串**:

`nonce + HTTP method + request path + JSON payload`

将结果十六进制编码，并放入 `Authorization` 头。

## 市场数据API

- 公共接口提供交易对、Ticker、订单簿、成交等信息（详见官方文档）

## 交易API

- 私有接口支持下单、撤单与订单查询（详见官方文档）

## 账户管理API

- 账户余额、充值与提现等接口（详见官方文档）

## 速率限制

- 公共接口：60 RPM / IP
- 私有接口：300 RPM / 账户（需完成 KYC）
- 超限会被锁定 1 分钟，持续超限可能被限制 24 小时

## WebSocket支持

- 频道：trades / orders / diff-orders
- 支持订阅指定交易对

## 错误代码

- 官方文档提供错误信息与处理建议

## 代码示例

```python
# WebSocket 连接示例
import websocket

ws = websocket.WebSocket()
ws.connect("wss://ws.bitso.com")
```
