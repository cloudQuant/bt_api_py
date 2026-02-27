# Phemex API 文档

## 交易所信息

- **交易所名称**: Phemex
- **官方网站**: https://phemex.com
- **API文档**: https://phemex-docs.github.io/
- **24h交易量排名**: #20
- **24h交易量**: $300M+
- **支持的交易对**: 200+ 交易对
- **API版本**: 多产品 REST/WS

## API基础信息

### 基础URL

```text
# 公共用户
REST: https://api.phemex.com
WebSocket: wss://ws.phemex.com

# VIP（白名单 IP）
REST: https://vapi.phemex.com
WebSocket: wss://vapi.phemex.com/ws

# Testnet
REST: https://testnet-api.phemex.com
WebSocket: wss://testnet-api.phemex.com/ws
```

### 请求头（REST）

```text
x-phemex-access-token: {api_key}
x-phemex-request-expiry: {expiry_seconds}
x-phemex-request-signature: {signature}
```

## 认证方式

### 1. 获取API密钥

1. 登录 Phemex 账户
2. 进入 API 管理
3. 创建 API Key 并设置权限
4. 保存 API Key 与 Secret

### 2. 请求签名算法

Phemex 使用 HMAC SHA256。

**签名字符串**:

`URL Path + QueryString + Expiry + body`

使用 Secret 计算 HMAC SHA256 并写入 `x-phemex-request-signature`。

## 市场数据API（示例）

- 行情与深度、K线、成交等（详见官方文档）

## 交易API（示例）

- 下单、撤单、改单等（详见官方文档）

## 账户管理API

- 账户、持仓、资金划转、充值提现（详见官方文档）

## 速率限制

- REST: 用户级 1 分钟窗口限频，IP 5 分钟窗口限频
- IP 限频：5000 次/5 分钟
- Testnet：共享限频 500 次/5 分钟
- 合约接口分组限频：Contract group 5000 次/分钟，Symbol group 500 次/分钟
- WebSocket: 单连接订阅与请求节流限制（详见官方文档）

## WebSocket支持

- Spot/合约 WebSocket 推送（详见官方文档）

## 错误代码

- 官方文档提供错误码表

## 代码示例

```python
# 获取服务器时间
import requests

url = "https://api.phemex.com/exchange/public/md/v2/timestamp"
print(requests.get(url).json())
```
