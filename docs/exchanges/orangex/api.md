# OrangeX API 文档

## 文档信息

- 文档版本: 1.0.0
- 创建日期: 2026-03-04

## 交易所基本信息

- 官方名称: OrangeX
- 官网: <https://www.orangex.com>
- CMC 衍生品排名: #29
- 交易所类型: CEX (中心化交易所)
- 支持的交易类型: 永续合约(Perpetual Futures)
- 最大杠杆: 150x

## API 基础 URL

| 端点类型 | URL | 说明 |
|---------|-----|------|
| REST | `https://api.orangex.com` | 主端点 |
| WebSocket | `wss://ws.orangex.com` | 行情流 |

## 认证方式

- HMAC SHA256 签名
- API Key + Secret Key + 时间戳

## 市场数据 API

### 1. 获取合约列表
- **端点**: `GET /api/v1/instruments`

### 2. 获取 Ticker
- **端点**: `GET /api/v1/ticker`

### 3. 获取深度
- **端点**: `GET /api/v1/orderbook`
- **参数**: `symbol`, `depth`

### 4. 获取 K 线
- **端点**: `GET /api/v1/klines`
- **参数**: `symbol`, `interval`, `limit`

### 5. 获取成交
- **端点**: `GET /api/v1/trades`

## 交易 API

### 1. 下单 / 2. 撤单 / 3. 查询余额 / 4. 查询持仓
- 标准合约交易接口

## 特殊说明

- 公开文档有限，建议联系官方获取完整 API
- 主要面向合约交易

## 相关资源

- [OrangeX 官网](https://www.orangex.com)
