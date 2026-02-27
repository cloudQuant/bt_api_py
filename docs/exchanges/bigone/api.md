# BigONE API 文档

## 交易所信息

- **交易所名称**: BigONE
- **官方网站**: https://big.one
- **API文档**: https://open.big.one/docs/
- **24h交易量排名**: #25
- **24h交易量**: $440M+
- **支持的交易对**: 200+ 交易对（以官方列表为准）
- **API版本**: v3（现货/钱包）、合约 v2

## API基础信息

### 基础URL

```text
# Spot / Wallet REST
https://api.big.one/api/v3

# Convert REST
https://api.big.one/sapi/v1/convert

# Contract REST
https://api.big.one/api/contract/v2

# WebSocket
wss://api.big.one/ws/v2
```

### 请求头（私有接口）

```text
Authorization: Bearer {jwt_token}
Content-Type: application/json
```

## 认证方式

### 1. 获取API密钥

1. 登录 BigONE 账户
2. 进入 User Center -> API 管理
3. 创建 API Key 与 Secret
4. 保存密钥

### 2. JWT 签名算法

BigONE 使用 JWT（HS256）。

**JWT Payload 必须包含**:

- `type`: 固定为 `OpenAPIV2`
- `sub`: API Key
- `nonce`: 纳秒级时间戳

将生成的 JWT 放入 `Authorization: Bearer {token}`。

## 市场数据API

- 交易对、行情、深度、成交、K线等（详见官方文档）

## 交易API

- 下单、撤单、订单查询等（详见官方文档）

## 账户管理API

- 资产与资金接口、充值提现等（详见官方文档）

## 速率限制

- 官方文档提供限频规则（以接口说明为准）

## WebSocket支持

- 行情与账户数据推送（详见官方文档）

## 错误代码

- 官方文档提供错误码表

## 代码示例

```python
# REST: 获取账户资产
import requests

url = "https://api.big.one/api/v3/viewer/accounts"
print(requests.get(url).json())
```
