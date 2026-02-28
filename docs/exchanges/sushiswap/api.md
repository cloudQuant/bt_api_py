# SushiSwap API 文档

## 交易所信息

- **交易所名称**: SushiSwap
- **官方网站**: <https://www.sushi.com>
- **API 文档**: <https://docs.sushi.com>
- **24h 交易量排名**: #7（DEX）
- **区块链**: 多链

## API 基础信息

### 基础 URL

```text

# Price API

<https://api.sushi.com/price/v1/{chainId}>

# Quote API

<https://api.sushi.com/quote/v7/{chainId}>

# Swap API

<https://api.sushi.com/swap/v7/{chainId}>

```bash

### 认证方式

- API Key 可在 Sushi Portal 获取
- API Key 可作为查询参数 `apiKey` 或请求头（Authorization）提供

## Price API

- `GET /price/v1/{chainId}`  获取链上所有 token 的 USD 价格（映射）
- `GET /price/v1/{chainId}/{address}`  获取指定 token 的 USD 价格

## Quote API

### 说明

- Quote API 用于生成报价（不执行交易）

### 常用查询参数（示例）

- `tokenIn` / `tokenOut`
- `amount`
- `maxSlippage`

### 示例请求

```bash
GET <https://api.sushi.com/quote/v7/1?tokenIn=0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE&tokenOut=0x6B3595068778DD592e39A122f4f5a5cF09C90fE2&amount=1000000000000000&maxSlippage=0.005>

```bash

## Swap API

### 说明

- Swap API 生成交易 calldata（用于链上执行）

### 常用查询参数（示例）

- `tokenIn` / `tokenOut`
- `amount`
- `maxSlippage`
- `sender`
- `apiKey`（可选）

### 示例请求

```bash
GET <https://api.sushi.com/swap/v7/1?tokenIn=0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE&tokenOut=0x6B3595068778DD592e39A122f4f5a5cF09C90fE2&amount=1000000000000000&maxSlippage=0.005&sender=0xYOUR_WALLET>

```bash

## 错误代码（示例）

- `invalid-api-key` (401)
- `unauthorized` (403)
- `ratelimit-exceeded` (429)
- `insufficient-allowance` (422)
- `insufficient-balance` (422)
- `estimate-gas` (422)
- `not-found` (404)
- `service-unavailable` (503)

## 代码示例

```python

# 获取报价

import requests

url = "<https://api.sushi.com/quote/v7/1">
params = {
    "tokenIn": "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE",
    "tokenOut": "0x6B3595068778DD592e39A122f4f5a5cF09C90fE2",
    "amount": "1000000000000000",
    "maxSlippage": "0.005",
}
print(requests.get(url, params=params).json())

```bash
