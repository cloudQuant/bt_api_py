# WazirX API 文档

## 交易所信息

- **交易所名称**: WazirX
- **官方网站**: <https://wazirx.com>
- **API 文档**: <https://github.com/wazirx/wazirx-api>
- **24h 交易量排名**: #56
- **24h 交易量**: $40M+
- **支持的交易对**: 200+（以官方列表为准）
- **API 版本**: REST / WebSocket

## API 基础信息

### 基础 URL

```text

# REST API

<https://api.wazirx.com>

# WebSocket

wss://stream.wazirx.com/stream

```bash

## 认证方式

- 私有 API 采用 HMAC-SHA256 签名（详见官方文档）

## 市场数据 API（示例）

- 市场列表: `GET /sapi/v1/exchangeInfo`
- 行情: `GET /sapi/v1/tickers/24hr`
- 深度: `GET /sapi/v1/depth?symbol=btcinr`

## 交易 API（示例）

- 下单: `POST /sapi/v1/order`
- 撤单: `DELETE /sapi/v1/order`

## 账户管理 API

- 账户信息: `GET /sapi/v1/account`

## 速率限制

- 详见官方文档

## WebSocket 支持

- `wss://stream.wazirx.com/stream` 支持多流订阅

## 错误代码

- 官方文档提供错误码说明

## 代码示例

```python

# 获取行情

import requests

url = "<https://api.wazirx.com/sapi/v1/tickers/24hr">
print(requests.get(url).json())

```bash
