# GMX API 文档

## 交易所信息

- **交易所名称**: GMX
- **官方网站**: https://gmx.io
- **API文档**: https://docs.gmx.io/docs/api/rest/
- **24h交易量排名**: #9（DEX）
- **区块链**: Arbitrum / Avalanche / Botanix

## API基础信息

### REST（Oracle / Pricing）

```text
# Ping
https://arbitrum-api.gmxinfra.io/ping
https://avalanche-api.gmxinfra.io/ping
https://botanix-api.gmxinfra.io/ping

# Tickers
https://arbitrum-api.gmxinfra.io/prices/tickers
https://avalanche-api.gmxinfra.io/prices/tickers
https://botanix-api.gmxinfra.io/prices/tickers

# Signed Prices
https://arbitrum-api.gmxinfra.io/signed_prices/latest
https://avalanche-api.gmxinfra.io/signed_prices/latest
https://botanix-api.gmxinfra.io/signed_prices/latest
```

### Candles

```text
# Candlesticks
https://arbitrum-api.gmxinfra.io/prices/candles?tokenSymbol=ETH&period=1d
https://avalanche-api.gmxinfra.io/prices/candles?tokenSymbol=AVAX&period=1d
https://botanix-api.gmxinfra.io/prices/candles?tokenSymbol=BTC&period=1d
```

- `tokenSymbol` 必填
- `period` 必填：1m / 5m / 15m / 1h / 4h / 1d
- `limit` 可选：1–10000（默认 1000）

### Tokens / APY / Performance / GLV

```text
# Tokens
https://arbitrum-api.gmxinfra.io/tokens
https://avalanche-api.gmxinfra.io/tokens
https://botanix-api.gmxinfra.io/tokens

# APY
https://arbitrum-api.gmxinfra.io/apy?period=total
https://avalanche-api.gmxinfra.io/apy?period=total
https://botanix-api.gmxinfra.io/apy?period=total

# Performance
https://arbitrum-api.gmxinfra.io/performance/annualized?period=total
https://avalanche-api.gmxinfra.io/performance/annualized?period=total
https://botanix-api.gmxinfra.io/performance/annualized?period=total

# GLV Tokens
https://arbitrum-api.gmxinfra.io/glvs/
https://avalanche-api.gmxinfra.io/glvs/
https://botanix-api.gmxinfra.io/glvs/

# GLV Info
https://arbitrum-api.gmxinfra.io/glvs/info
https://avalanche-api.gmxinfra.io/glvs/info
https://botanix-api.gmxinfra.io/glvs/info
```

- APY `period`: 1d / 7d / 30d / 90d / 180d / 1y / total（默认 30d）
- Performance `period`: 7d / 30d / 90d / 180d / 1y / total（默认 90d）
- Performance `address` 可选：指定 GM/GLV 地址

### 备用域名（Fallback）

```text
# Arbitrum
https://arbitrum-api-fallback.gmxinfra.io
https://arbitrum-api-fallback.gmxinfra2.io

# Avalanche
https://avalanche-api-fallback.gmxinfra.io
https://avalanche-api-fallback.gmxinfra2.io

# Botanix
https://botanix-api-fallback.gmxinfra.io
https://botanix-api-fallback.gmxinfra2.io
```

## 代码示例

```python
# 获取 GMX Arbitrum 价格 Tickers
import requests

url = "https://arbitrum-api.gmxinfra.io/prices/tickers"
print(requests.get(url).json())
```
