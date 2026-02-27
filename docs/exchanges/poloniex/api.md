# Poloniex API 文档

## 交易所信息

- **交易所名称**: Poloniex
- **官方网站**: https://poloniex.com
- **API文档**: https://api-docs.poloniex.com/spot
- **24h交易量排名**: #18
- **24h交易量**: $150M+
- **支持的交易对**: 200+ 交易对
- **API版本**: Spot API

## API基础信息

### 基础URL

```text
# REST API
https://api.poloniex.com

# WebSocket (Spot)
wss://ws.poloniex.com/ws/public
wss://ws.poloniex.com/ws/private
```

### 请求头（私有接口）

```text
key: {api_key}
signatureMethod: HmacSHA256
signatureVersion: 2
signTimestamp: {timestamp_ms}
signature: {signature}
Content-Type: application/json
```

## 认证方式

### 1. 获取API密钥

1. 登录 Poloniex 账户
2. 进入 API Keys 管理
3. 创建 API Key 并设置权限
4. 保存 API Key 与 Secret

### 2. 请求签名算法

Poloniex 私有接口使用 HMAC-SHA256 签名，签名结果写入 `signature` 头。

## 市场数据API（示例）

- 市场行情、交易对、币种等公共数据通过 REST API 获取（详见官方文档）

## 交易API（示例）

- 下单、撤单与订单查询通过私有 REST API 完成（详见官方文档）

## 账户管理API

- 账户与资产相关接口需签名访问（详见官方文档）

## 速率限制

- 以官方文档为准（返回 429 表示触发限频）

## WebSocket支持

- Public: `wss://ws.poloniex.com/ws/public`
- Private: `wss://ws.poloniex.com/ws/private`
- 服务器要求 30 秒内有消息或 ping

## 错误代码

- 详见官方错误码说明

## 代码示例

```python
# 获取市场列表（公共接口）
import requests

url = "https://api.poloniex.com/markets"
print(requests.get(url).json())
```
