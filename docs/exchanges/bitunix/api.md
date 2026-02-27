# Bitunix API 文档

## 交易所信息

- **交易所名称**: Bitunix
- **官方网站**: https://www.bitunix.com
- **API文档**: https://api-doc.bitunix.com/en_us/
- **24h交易量排名**: #24
- **24h交易量**: $450M+
- **支持的交易对**: 200+ 交易对（以官方列表为准）
- **API版本**: OpenAPI

## API基础信息

### 基础URL

```text
# REST API (Primary Domain)
https://openapi.bitunix.com

# WebSocket
wss://openapi.bitunix.com:443/ws-api/v1
```

> 注：另有文档说明 REST 域名为 `https://fapi.bitunix.com`，请以官方最新文档为准。

### 请求头（私有接口）

```text
api-key: {api_key}
nonce: {nonce}
timestamp: {timestamp_ms}
sign: {signature}
```

## 认证方式

### 1. 获取API密钥

1. 登录 Bitunix 账户
2. 进入 API 管理
3. 创建 API Key 与 Secret
4. 保存密钥

### 2. 请求签名算法

Bitunix 使用双重 SHA256。

**REST 签名步骤**:

1. query 参数按 ASCII 升序拼接
2. body 参数压缩为字符串（去空格）
3. `digest = SHA256(nonce + timestamp + api-key + queryParams + body)`
4. `sign = SHA256(digest + secretKey)`

**WebSocket 签名步骤**:

1. params 字段按 ASCII 升序拼接
2. `digest = SHA256(nonce + timestamp + apiKey + params)`
3. `sign = SHA256(digest + secretKey)`

## 市场数据API

- 公共接口提供市场与行情数据（详见官方文档）

## 交易API

- 私有接口支持下单、撤单与订单查询（详见官方文档）

## 账户管理API

- 账户与资产信息（详见官方文档）

## 速率限制

- 官方文档提供限频说明（以接口说明为准）

## WebSocket支持

- 公共频道与私有频道均支持
- 连接有效期 24 小时，需处理重连

## 错误代码

- 官方文档提供错误码说明

## 代码示例

```python
# WebSocket 连接示例
import websocket

ws = websocket.WebSocket()
ws.connect("wss://openapi.bitunix.com:443/ws-api/v1")
```
