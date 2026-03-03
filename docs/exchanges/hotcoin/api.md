# Hotcoin API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04

## 交易所基本信息

- 官方名称: Hotcoin
- 官网: <https://www.hotcoin.com>
- CMC 衍生品排名: #44
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 现货(Spot)、永续合约(Perpetual Futures)

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api.hotcoin.com` | 主端点 |
| WebSocket | `wss://ws.hotcoin.com/ws` | 行情流 |

## 认证方式

- HMAC SHA256 签名
- API Key + Secret Key + 时间戳

## 市场数据 API

### 1. 获取交易对列表
- **端点**: `GET /v1/common/symbols`

### 2. 获取 Ticker
- **端点**: `GET /v1/market/ticker`

### 3. 获取深度
- **端点**: `GET /v1/market/depth`
- **参数**: `symbol`, `type`

### 4. 获取 K 线
- **端点**: `GET /v1/market/kline`
- **参数**: `symbol`, `period`, `size`

### 5. 获取成交
- **端点**: `GET /v1/market/trade`

## 交易 API

### 1. 下单
- **端点**: `POST /v1/order/place`
- **参数**: `symbol`, `side`, `type`, `amount`, `price`

### 2. 撤单
- **端点**: `POST /v1/order/cancel`

### 3. 查询余额
- **端点**: `GET /v1/account/balance`

## 特殊说明

- API 风格类似火币(HTX)
- 公开文档有限

## 相关资源

- [Hotcoin 官网](https://www.hotcoin.com)
