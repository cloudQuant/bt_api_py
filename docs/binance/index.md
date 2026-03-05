# Binance API 文档

> bt_api_py 对 Binance 交易所的完整支持文档

## 概览

bt_api_py 提供了对 Binance 交易所全系列产品的统一接口支持，包括现货、合约、杠杆和算法交易。

## 支持的产品线

| 产品线 | 交易所代码 | 状态 | 说明 |
|--------|-----------|:----:|------|
| **现货交易** | `BINANCE___SPOT` | ✅ | REST + WebSocket + 测试通过 |
| **U本位合约** | `BINANCE___SWAP` | ✅ | REST + WebSocket + 测试通过 |
| **币本位合约** | `BINANCE___COIN_SWAP` | ✅ | REST + WebSocket + 测试通过 |
| **期权** | `BINANCE___OPTION` | ✅ | REST + WebSocket + 测试通过 |
| **杠杆交易** | `BINANCE___MARGIN` | ✅ | REST API 支持 |
| **算法交易** | `BINANCE___ALGO` | ✅ | TWAP/VP 算法支持 |

## 快速开始

```python
from bt_api_py import BtApi

exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,  # 使用测试网
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)

# 获取行情
ticker = api.get_ticker("BINANCE___SPOT", "BTCUSDT")
print(f"BTC 价格: {ticker.last_price}")

# 下单
order = api.limit_order(
    exchange="BINANCE___SPOT",
    symbol="BTCUSDT",
    side="buy",
    quantity=0.001,
    price=50000
)
```

## 文档索引

### 现货交易 (SPOT)

- [概览](spot/README.md) — 现货 API 文档索引
- [REST API](spot/rest-api.md) — 完整 REST 接口文档
- [WebSocket 流](spot/websocket-streams.md) — 行情实时推送
- [WebSocket API](spot/websocket-api.md) — WebSocket 方式调用 API
- [用户数据流](spot/user-data-stream.md) — 账户和订单实时推送
- [枚举类型](spot/enums.md) — API 枚举定义
- [错误码](spot/errors.md) — 错误码参考
- [交易规则](spot/filters.md) — 交易过滤器和规则
- [SBE 行情流](spot/sbe-market-data-streams.md) — SBE 格式行情数据

### 合约交易 (DERIVATIVES)

- [概览](derivatives/index.md) — 合约 API 文档索引
- [快速开始](derivatives/quick-start.md) — 合约交易快速入门
- [更新日志](derivatives/change-log.md) — API 变更记录

### 杠杆交易 (MARGIN)

- [概览](margin_trading/README.md) — 杠杆交易 API 文档

### 算法交易 (ALGO)

- [概览](algo/README.md) — 算法交易 API 文档
- [快速开始](algo/quick-start.md) — 算法交易快速入门
- [现货算法](algo/spot-algo/fee-structure.md) — 费率结构
- [合约算法](algo/future-algo/twap-new-order.md) — TWAP 下单

## 相关文档

- [BtApi 统一接口](../api_reference.md) — BtApi 类 API 参考
- [Binance API 参考](../api_reference/binance.md) — bt_api_py 中 Binance 适配层文档
- [WebSocket 订阅](../api_reference/websocket.md) — WebSocket 使用指南
