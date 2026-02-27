# Bitvavo API 文档

## 交易所信息

- **交易所名称**: Bitvavo
- **官方网站**: https://www.bitvavo.com
- **API文档**: https://docs.bitvavo.com
- **24h交易量排名**: #26
- **24h交易量**: $260M+
- **支持的交易对**: 200+ 交易对（以官方 markets 列表为准）
- **API版本**: v2 (REST/WS)
- **特点**: 欧洲合规交易所，提供 REST 与 WebSocket

## API基础信息

### 基础URL

```text
# REST API
https://api.bitvavo.com/v2/

# WebSocket API
wss://ws.bitvavo.com/v2/
```

### 请求头（私有接口）

```text
Content-Type: application/json
Bitvavo-Access-Key: {api_key}
Bitvavo-Access-Signature: {signature}
Bitvavo-Access-Timestamp: {timestamp_ms}
Bitvavo-Access-Window: {window_ms}  # 可选
```

## 认证方式

### 1. 获取API密钥

1. 登录 Bitvavo 账户
2. 进入设置 -> API
3. 创建 API Key 并设置权限与 IP 白名单
4. 保存 API Key / Secret

### 2. 请求签名算法

Bitvavo 使用 HMAC SHA256。

**签名字符串**:

`timestamp + method + url + body`

结果为十六进制签名，写入 `Bitvavo-Access-Signature`。

## 市场数据API（示例）

- 市场列表: `GET /markets`
- 市场成交: `GET /{market}/trades`
- 24h 行情: `GET /ticker/24h`

## 交易API（示例）

- 下单: `POST /order`

## 账户管理API（示例）

- 私有接口需签名（余额/订单/交易等，详见官方文档）

## 速率限制

- 权重限频：默认 1000 权重/分钟
- 认证请求按 API Key 限频，未认证按 IP 限频
- 超限返回 429，错误码 110
- WebSocket：单连接 5000 消息/秒；Market Data Pro 为 50 消息/秒

## WebSocket支持

- 支持 Action 与 Channel 两类消息
- WebSocket 端点：`wss://ws.bitvavo.com/v2/`

## 错误代码

- 官方文档提供完整错误码列表

## 代码示例

```python
# 获取 24h 行情
import requests

url = "https://api.bitvavo.com/v2/ticker/24h"
print(requests.get(url).json())
```
