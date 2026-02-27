# Upbit API 文档

## 交易所信息

- **交易所名称**: Upbit
- **官方网站**: https://upbit.com
- **API文档**: https://global-docs.upbit.com
- **24h交易量排名**: #14
- **24h交易量**: $1B+
- **支持的交易对**: 200+ 交易对
- **API版本**: v1

## API基础信息

### 基础URL

```text
# REST API (区域节点)
https://sg-api.upbit.com
https://id-api.upbit.com
https://th-api.upbit.com

# WebSocket
wss://api.upbit.com/websocket/v1
wss://api.upbit.com/websocket/v1/private
```

### 请求头

```text
Authorization: Bearer {jwt_token}
Content-Type: application/json
```

## 认证方式

### 1. 获取API密钥

1. 登录 Upbit 账户
2. 进入账户设置 -> API管理
3. 创建 API Key 并设置权限
4. 设置 IP 白名单
5. 保存 Access Key 和 Secret Key

### 2. 请求签名算法

Upbit 使用 JWT (JSON Web Token)。

**JWT 生成要点**:

- payload 必须包含 `access_key` 与 `nonce`
- 如果包含查询参数，需要 `query_hash` (SHA512) 与 `query_hash_alg`
- 通过 Secret Key 签名生成 JWT
- 将 token 写入 `Authorization: Bearer {token}`

## 市场数据API

- 市场列表: `GET /v1/market/all`
- 行情: `GET /v1/ticker`

## 交易API

- 查询订单: `GET /v1/order`
- 查询未成交订单: `GET /v1/orders/open`

## 速率限制

- REST API: **10 次/秒/IP**（Exchange API）
- WebSocket 连接请求: **5 次/秒**
- WebSocket 数据请求: **5 次/秒、100 次/分钟**
- 响应头包含 `Remaining-Req`

## WebSocket支持

- Public/Private 数据均通过 WebSocket 获取
- 连接鉴权通过 `Authorization` 头
- 请求格式为 JSON 数组

## 错误代码

- 超限返回 429

## 代码示例

```python
# REST: 获取行情
import requests

url = "https://sg-api.upbit.com/v1/ticker?markets=KRW-BTC"
print(requests.get(url).json())
```
