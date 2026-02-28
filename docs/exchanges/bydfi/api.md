# BYDFi API 文档

## 交易所信息

- **交易所名称**: BYDFi
- **官方网站**: <https://www.bydfi.com>
- **API 文档**: <https://developers.bydfi.com>
- **24h 交易量排名**: #27
- **24h 交易量**: $250M+
- **支持的交易对**: 200+（以官方接口为准）
- **API 版本**: REST/WS（Spot/Swap）

## API 基础信息

### 基础 URL

```text

# REST API

<https://api.bydfi.com/api>

# WebSocket (Swap 公共行情)

wss://stream.bydfi.com/v1/public/swap

```bash

### 请求头（私有接口）

```text
X-API-KEY: {api_key}
X-API-TIMESTAMP: {timestamp}
X-API-SIGNATURE: {signature}
Content-Type: application/json
Accept-Language: en-US

```bash

## 认证方式

### 1. 获取 API 密钥

1. 登录 BYDFi 账户
2. 进入 API Management
3. 申请 API 权限并创建 API Key
4. 保存 API Key / Secret

### 2. 请求签名算法

BYDFi 使用 HMAC SHA256。

- *签名字符串**:

`accessKey + timestamp + queryString + body`

将结果写入 `X-API-SIGNATURE`。

## 市场数据 API（Swap 示例）

- 交易规则与交易对: `GET /v1/swap/market/exchange_info`
- 深度: `GET /v1/swap/market/depth`
- 成交: `GET /v1/swap/market/trades`
- K 线: `GET /v1/swap/market/klines`
- 24h 行情: `GET /v1/swap/market/ticker/24hr`

## 交易 API（Swap 示例）

- 下单: `POST /v1/swap/trade/place_order`

## 账户管理 API（Swap 示例）

- 账户资产: `GET /v1/swap/account/balance`

## 速率限制

- 可通过 `GET /v1/public/api_limit` 查询各接口限频配置
- WebSocket：单 IP 每秒最多 5 次订阅消息、5 分钟最多 300 次订阅消息

## WebSocket 支持

- 行情 WS 示例：`wss://stream.bydfi.com/ws/BTC-USDT@depth`
- 支持多流合并订阅

## 错误代码

- 详见官方文档说明

## 代码示例

```python

# 获取合约交易规则与交易对

import requests

url = "<https://api.bydfi.com/api/v1/swap/market/exchange_info">
print(requests.get(url).json())

```bash
