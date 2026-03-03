# LocalBitcoins API 文档

## 交易所信息

- **交易所名称**: LocalBitcoins
- **官方网站**: <https://localbitcoins.com>
- **API 文档**: <https://localbitcoins.com/api-docs/>
- **24h 交易量排名**: #47
- **24h 交易量**: $15M+
- **支持的交易对**: 以 P2P 交易为主
- **API 版本**: REST

## API 基础信息

### 基础 URL

```text

# REST API

<https://localbitcoins.com/api>

```bash

### 认证方式

LocalBitcoins 使用 HMAC-SHA256（签名 header: `Apiauth`）。

- *签名字符串**:

`nonce + api_key + endpoint + query + body`

签名结果为 Base64，并写入 `Apiauth` 头。

## 市场数据 API（示例）

- 公共报价: `GET /api/advertisement/`（详见官方文档）

## 交易 API（示例）

- 发布/更新广告与交易相关接口详见官方文档

## 账户管理 API

- 钱包/交易/消息等接口详见官方文档

## 速率限制

- 详见官方文档

## WebSocket 支持

- 官方未提供 WebSocket API 说明

## 错误代码

- 官方文档提供错误码说明

## 代码示例

```python

# 获取广告列表（示例）

import requests

url = "<https://localbitcoins.com/api/advertisement/">
print(requests.get(url).json())

```bash
