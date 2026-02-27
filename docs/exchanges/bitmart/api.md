# BitMart API 文档

## 交易所信息

- **交易所名称**: BitMart
- **官方网站**: https://www.bitmart.com
- **API文档**: https://developer-pro.bitmart.com/en/spot/ （现货）
- **API文档**: https://openapi-doc.bitmart.com/en/futuresv2/ （合约）
- **24h交易量排名**: #21
- **24h交易量**: $250M+
- **支持的交易对**: 1000+ 交易对（以官方列表为准）
- **API版本**: Spot v1/v2/v3/v4，Futures V2

## API基础信息

### 基础URL

```text
# Spot REST
https://api-cloud.bitmart.com

# Futures REST (V2)
https://api-cloud-v2.bitmart.com

# Spot WebSocket
wss://ws-manager-compress.bitmart.com/api?protocol=1.1
wss://ws-manager-compress.bitmart.com/user?protocol=1.1

# Futures WebSocket (V2)
wss://openapi-ws-v2.bitmart.com
```

### 请求头（私有接口）

```text
X-BM-KEY: {api_key}
X-BM-SIGN: {signature}
X-BM-TIMESTAMP: {timestamp_ms}
Content-Type: application/json
```

## 认证方式

### 1. 获取API密钥

1. 登录 BitMart 账户
2. 进入账户设置 -> API 管理
3. 创建 API Key 并设置权限
4. 保存 API Key / Secret / Memo

### 2. 请求签名算法

BitMart 使用 HMAC SHA256。

**签名字符串**:

- `timestamp + "#" + memo + "#" + queryString`
- 或 `timestamp + "#" + memo + "#" + body`

使用 Secret 进行 HMAC SHA256，写入 `X-BM-SIGN`。

### 3. 认证类型

- `NONE`: 公共接口
- `KEYED`: 仅需 `X-BM-KEY`
- `SIGNED`: 需要 `X-BM-KEY` + `X-BM-SIGN`

## 市场数据API（示例）

- 交易对与行情接口详见官方 Spot 文档
- WebSocket 频道支持 ticker、depth、kline、trade 等（详见官方文档）

## 交易API（示例）

- Spot 下单/撤单/查询订单（v2/v4）
- Futures V2 下单/撤单/持仓与委托管理

## 账户管理API

- 钱包资产与充值提现接口详见官方文档

## 速率限制

- 超频会返回 `429`，严重时返回 `418` 并封禁 IP（详见官方说明）

## WebSocket支持

- Spot 公共与私有通道
- Futures V2 WebSocket 独立域名

## 错误代码

- 官方文档提供完整错误码与说明

## 代码示例

```python
# 获取现货交易对行情（示例）
import requests

url = "https://api-cloud.bitmart.com/spot/quotation/v3/tickers"
print(requests.get(url).json())
```
