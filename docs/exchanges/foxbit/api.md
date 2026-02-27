# Foxbit API 文档

## 交易所信息

- **交易所名称**: Foxbit
- **官方网站**: https://foxbit.com.br
- **API文档**: https://docs.foxbit.com.br/ （API 1.0 / 3.0）
- **24h交易量排名**: #49
- **24h交易量**: $25M+
- **支持的交易对**: 50+（以官方列表为准）
- **API版本**: API 3.0（REST/WS） / API 1.0（Legacy）

## API基础信息

### 基础URL

```text
# API 3.0 REST
https://api.foxbit.com.br/rest

# API 3.0 WebSocket
wss://api.foxbit.com.br/ws

# API 1.0 REST
https://api.foxbit.com.br
```

## 认证方式

- API 3.0 提供基于 API Key 的鉴权方式（详见官方文档）
- API 1.0 使用 `apikey` 与 `signature` 等头字段（详见官方文档）

## 市场数据API（示例）

- 行情、成交、深度等详见官方文档

## 交易API（示例）

- 下单、撤单与订单管理详见官方文档

## 账户管理API

- 余额与资金接口详见官方文档

## 速率限制

- 详见官方文档

## WebSocket支持

- 提供 API 3.0 WebSocket 文档

## 错误代码

- 官方文档提供错误码说明

## 代码示例

```python
# 官方文档提供完整示例
```
