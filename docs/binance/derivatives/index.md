# Binance 合约交易 (Derivatives) API

> 来源: [Binance 官方文档](https://developers.binance.com/docs/derivatives)
>
> 最后更新: 2026-03

## 概览

Binance 衍生品 API 提供 REST 和 WebSocket 接口，支持以下合约产品：

| 产品 | Base URL | 说明 |
|------|----------|------|
| **USDⓈ-M 合约** | `/fapi` | U 本位永续/交割合约 |
| **COIN-M 合约** | `/dapi` | 币本位永续/交割合约 |
| **欧式期权** | `/eapi` | 期权交易 |
| **投资组合保证金** | `/papi` | 组合保证金账户 |

## bt_api_py 中的使用

```python
from bt_api_py import BtApi

exchange_kwargs = {
    "BINANCE___SWAP": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)

# 获取合约行情
ticker = api.get_ticker("BINANCE___SWAP", "BTCUSDT")

# 获取持仓信息
positions = api.get_positions("BINANCE___SWAP")

# 合约下单
order = api.limit_order(
    exchange="BINANCE___SWAP",
    symbol="BTCUSDT",
    side="buy",
    quantity=0.01,
    price=50000
)
```

## 文档索引

| 文档 | 说明 |
|------|------|
| [快速开始](quick-start.md) | 合约交易快速入门指南 |
| [更新日志](change-log.md) | API 变更记录 |

## 各产品模块

每个产品目录下通常包含以下子目录/文件：

| 子目录/文件 | 说明 |
|-------------|------|
| `general-info.md` | 基本信息、Base URL、签名方式、频率限制 |
| `common-definition.md` | 枚举定义、公共参数 |
| `error-code.md` | 错误码 |
| `market-data/` | 行情数据接口 (REST + WebSocket) |
| `trade/` | 交易接口 (下单/撤单/改单等) |
| `account/` | 账户信息接口 (余额/持仓/收入历史等) |

## 相关链接

- [Binance 合约官方文档](https://developers.binance.com/docs/derivatives)
- [BtApi 统一接口](../../api_reference.md)
- [Binance API 参考](../../api_reference/binance.md)
