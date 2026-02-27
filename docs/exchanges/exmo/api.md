# Exmo API 文档

## 交易所信息

- **交易所名称**: EXMO
- **官方网站**: https://exmo.com
- **API文档**: https://documenter.getpostman.com/view/10287440/SzYXWKPi
- **24h交易量排名**: #33
- **24h交易量**: $83M+
- **支持的交易对**: 200+（以官方列表为准）
- **API版本**: v1.1（REST）

## API基础信息

### 基础URL

```text
# REST API
https://api.exmo.com/v1.1/{api_name}
```

### 请求头（私有接口）

```text
Key: {api_key}
Sign: {signature}
Content-Type: application/x-www-form-urlencoded
```

## 认证方式

EXMO 使用 HMAC SHA512。

**签名步骤**:

1. 构造 POST 数据字符串 `post_data`
2. `sign = HMAC_SHA512(post_data, secret)`
3. 将 `Key` 与 `Sign` 作为请求头发送

## 市场数据API

- 详见官方 Postman 文档（Public API）

## 交易API

- 详见官方 Postman 文档（Authenticated API）

## 账户管理API

- 详见官方 Postman 文档（Wallet API / EX-CODE API）

## 速率限制

- API 请求限制：10 次/秒（按 IP 或用户）

## WebSocket支持

- 支持 Public 与 Authenticated WebSocket 方法
- 频道类型详见官方文档

## 错误代码

- 官方文档提供错误码说明

## 代码示例

```python
# 官方文档提供完整示例
```
