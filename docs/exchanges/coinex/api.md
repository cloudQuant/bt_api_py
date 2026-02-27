# CoinEx API 文档

## 交易所信息

- **交易所名称**: CoinEx
- **官方网站**: https://www.coinex.com
- **API文档**: https://docs.coinex.com/api/v2/
- **24h交易量排名**: #23
- **24h交易量**: $100M+
- **支持的交易对**: 1000+ 交易对（以官方列表为准）
- **API版本**: v2

## API基础信息

### 基础URL

```text
# REST API
https://api.coinex.com/v2

# WebSocket
wss://socket.coinex.com/v2/spot
wss://socket.coinex.com/v2/futures
```

### 请求头（私有接口）

```text
X-COINEX-KEY: {api_key}
X-COINEX-SIGN: {signature}
X-COINEX-TIMESTAMP: {timestamp_ms}
X-COINEX-WINDOWTIME: {window_ms}  # 可选
Content-Type: application/json
```

## 认证方式

### 1. 获取API密钥

1. 登录 CoinEx 账户
2. 进入 API 管理
3. 创建 API Key 并设置权限
4. 保存 API Key / Secret

### 2. 请求签名算法

- 需要签名的接口在文档中标记为 `signature required`
- 签名与鉴权详细流程见官方 Authentication

## 市场数据API

- 市场列表、行情、深度、K线、成交等（详见官方文档）

## 交易API

- 下单、撤单、批量订单、订单查询等（详见官方文档）

## 账户管理API

- 资产、充值提现、资金划转、子账户等（详见官方文档）

## 速率限制

- IP 维度限频：400 次/秒
- 用户限频：短周期与长周期两级限频（详见官方说明）

## WebSocket支持

- Spot 与 Futures 分离的 WS 域名

## 错误代码

- 官方文档提供错误码列表与处理建议

## 代码示例

```python
# 获取服务器时间
import requests

url = "https://api.coinex.com/v2/time"
print(requests.get(url).json())
```
