# IBKR Web API 快速参考

> 最后更新: 2026-02-26

本文档提供IBKR Web API的快速参考，包括常用端点、参数和响应格式。

## 目录

- [基础信息](#基础信息)
- [认证](#认证)
- [交易API端点](#交易api端点)
- [账户管理API端点](#账户管理api端点)
- [常见错误码](#常见错误码)

---

## 基础信息

### 环境URL

| 环境 | URL |
|------|-----|
| 生产环境 | `https://api.interactivebrokers.com` |
| 测试环境 | `https://api.test.interactivebrokers.com` |

### 请求格式

- **协议**: 仅支持HTTPS
- **内容类型**: `application/json`
- **编码**: UTF-8

### 速率限制

| 类型 | 限制 |
|------|------|
| 全局（交易） | 50 请求/秒/用户名 |
| CP Gateway | 10 请求/秒 |
| 账户管理 | 10 请求/秒/端点 |

---

## 认证

### OAuth 2.0 认证流程

IBKR使用 `private_key_jwt` 客户端认证方式。

**Token端点**:
```
POST /oauth/token
```

**请求体**:
```json
{
  "grant_type": "client_credentials",
  "client_id": "YOUR_CLIENT_ID",
  "client_assertion": "SIGNED_JWT_TOKEN",
  "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
}
```

**响应**:
```json
{
  "access_token": "eyJhbGci...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

---

## 交易API端点

### 会话管理

#### 验证会话状态
```
POST /iserver/auth/status
```

#### 重新认证
```
POST /iserver/reauthenticate
```

### 合约搜索

#### 搜索股票
```
GET /trsrv/stocks?symbols={symbol}
```

**示例**: `/trsrv/stocks?symbols=AAPL`

#### 搜索期货
```
GET /trsrv/futures?symbols={symbol}
```

#### 通用合约搜索
```
GET /iserver/secdef/search?symbol={symbol}&secType={type}
```

**参数**:
- `symbol`: 交易品种代码
- `secType`: STK, OPT, FUT, CASH等

### 市场数据

#### 获取快照数据
```
GET /iserver/marketdata/snapshot?conids={conids}&fields={fields}
```

**常用字段**:
- `31`: 最新价
- `84`: 买价
- `85`: 买量
- `86`: 卖价
- `88`: 卖量
- `7059`: 成交量

**示例**:
```
GET /iserver/marketdata/snapshot?conids=265598&fields=31,84,85,86,88
```

#### WebSocket实时数据流

**订阅**:
```
smd+{CONID}+{"fields":["31","84","85","86","88"]}
```

**取消订阅**:
```
umd+{CONID}+{}
```

### 订单管理

#### 提交新订单
```
POST /iserver/account/{accountId}/orders
```

**请求体**:
```json
{
  "orders": [
    {
      "conid": 265598,
      "side": "BUY",
      "orderType": "LMT",
      "price": 165.00,
      "quantity": 100,
      "tif": "DAY"
    }
  ]
}
```

**订单类型**:
- `MKT`: 市价单
- `LMT`: 限价单
- `STP`: 止损单
- `STP_LMT`: 止损限价单

**有效期**:
- `DAY`: 当日有效
- `GTC`: 撤销前有效
- `IOC`: 立即成交否则取消
- `GTD`: 指定日期前有效

#### 修改订单
```
POST /iserver/account/{accountId}/order/{orderId}
```

#### 取消订单
```
DELETE /iserver/account/{accountId}/order/{orderId}
```

#### 查询订单
```
GET /iserver/account/orders?accountId={accountId}
```

**查询参数**:
- `filters`: filled, inactive, pending, submitted
- `force`: true/false (强制刷新)

### 持仓和账户

#### 获取账户列表
```
GET /portfolio/accounts
```

#### 获取持仓
```
GET /portfolio/{accountId}/positions
```

#### 获取账户摘要
```
GET /portfolio/{accountId}/summary
```

#### 获取账户余额
```
GET /portfolio/{accountId}/ledger
```

---

## 账户管理API端点

### 账户查询

#### 获取账户列表
```
GET /gw/api/v1/accounts
```

**查询参数**:
- `status`: 账户状态过滤 (O=开户, C=关闭, P=待审核)
- `limit`: 返回数量限制
- `offset`: 分页偏移

#### 获取账户详情
```
GET /gw/api/v1/accounts/{accountId}
```

#### 更新账户信息
```
PATCH /gw/api/v1/accounts/{accountId}
```

### 资金和银行

#### 获取银行指令
```
GET /gw/api/v1/bank-instructions/query?accountId={accountId}
```

#### 创建提款请求
```
POST /gw/api/v1/withdraw-request
```

**请求体**:
```json
{
  "accountId": "U1234567",
  "amount": 1000.00,
  "currency": "USD",
  "instructionId": "12345"
}
```

#### 创建存款请求
```
POST /gw/api/v1/deposit-request
```

#### 内部转账
```
POST /gw/api/v1/internal-transfer
```

**现金转账**:
```json
{
  "fromAccountId": "U1234567",
  "toAccountId": "U7654321",
  "transferType": "CASH",
  "amount": 1000.00,
  "currency": "USD"
}
```

**持仓转账**:
```json
{
  "fromAccountId": "U1234567",
  "toAccountId": "U7654321",
  "transferType": "POSITION",
  "transfers": [
    {
      "conid": 265598,
      "quantity": 100
    }
  ]
}
```

### 报告

#### 获取账户报表
```
GET /gw/api/v1/statements?accountId={accountId}&startDate={date}&endDate={date}
```

**日期格式**: YYYY-MM-DD

#### 获取税务文档
```
GET /gw/api/v1/tax-documents/available?accountId={accountId}&taxYear={year}
```

#### 获取交易确认
```
GET /gw/api/v1/trade-confirmations?accountId={accountId}&startDate={date}&endDate={date}
```

### 单点登录(SSO)

#### 生成SSO URL
```
GET /gw/api/v1/sso/url?accountId={accountId}&targetUrl={url}
```

---

## 常见错误码

### HTTP状态码

| 状态码 | 含义 |
|--------|------|
| 200 | 成功 |
| 400 | 请求错误 |
| 401 | 未授权 |
| 403 | 禁止访问 |
| 404 | 未找到 |
| 429 | 超过速率限制 |
| 500 | 服务器内部错误 |

### 业务错误码

| 错误码 | 描述 |
|--------|------|
| INVALID_REQUEST | 请求格式错误 |
| UNAUTHORIZED | 认证失败 |
| FORBIDDEN | 权限不足 |
| NOT_FOUND | 资源不存在 |
| RATE_LIMIT_EXCEEDED | 超过速率限制 |
| ACCOUNT_NOT_ELIGIBLE | 账户不符合条件 |
| VALIDATION_ERROR | 输入验证失败 |
| SYSTEM_ERROR | 系统错误 |

### 错误响应格式

```json
{
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Invalid account ID format",
    "details": {
      "field": "accountId",
      "reason": "Must start with 'U' followed by digits"
    }
  },
  "timestamp": "2026-02-26T10:30:00Z",
  "path": "/gw/api/v1/accounts/INVALID"
}
```

---

## 维护时间窗口

### 交易API维护

| 地区 | 维护时间 |
|------|----------|
| 北美 | 01:00 US/Eastern |
| 欧洲 | 01:00 CEST |
| 亚洲 | 01:00 HKT |

### 账户管理API维护

| 端点组 | 维护时间 (ET) |
|--------|---------------|
| 账户管理 | 每日 18:00 - 18:05 |
| 资金/银行 | 每日 23:45 - 00:30 |
| 报表 | 周日和周二 18:00 - 18:30 |

---

## 支持联系方式

- **交易API支持**: api@interactivebrokers.com
- **账户管理API支持**: am-api@interactivebrokers.com
- **文档**: https://www.interactivebrokers.com/campus/ibkr-api-page/
- **API参考**: https://www.interactivebrokers.com/api/doc.html

---

## 相关文档

- [概览](./overview.md) - API概览和入门指南
- [交易API详细文档](./trading.md) - 交易功能详细说明
- [账户管理API详细文档](./account_management.md) - 账户管理功能详细说明
