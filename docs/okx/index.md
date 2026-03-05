# OKX API 文档

OKX（欧易）是全球领先的加密货币交易所，bt_api_py 提供了对 OKX 现货、合约、期权等多种交易产品的支持。

## 支持的产品

| 产品 | 说明 | 状态 |
|------|------|------|
| **SPOT**| 现货交易 | ✅ 完全支持 |
|**SWAP**| 永续合约 | ✅ 完全支持 |
|**FUTURES**| 交割合约 | ✅ 支持 |
|**OPTION** | 期权 | 🚧 开发中 |

## API 文档

| 模块 | 说明 |
|------|------|
| [交易账户](trading_account.md) | 账户余额、持仓查询 |
| [订单交易](order_book_trading_trade.md) | 下单、撤单、订单查询 |
| [行情数据](market_data.md) | K 线、深度、成交记录 |
| [公共数据](public_data.md) | 合约信息、费率 |
| [资金账户](funding_account.md) | 充值、提现、资金划转 |
| [子账户](sub_account.md) | 子账户管理 |
| [算法交易](order_book_trading_algo.md) | 条件单、冰山委托 |
| [网格交易](order_book_trading_grid.md) | 网格策略 |
| [价差交易](spread_trading.md) | 价差策略 |
| [交易统计](trading_statistics.md) | 交易数据分析 |

## 快速开始

```python
from bt_api_py import BtApi

# OKX 现货

api = BtApi(
    exchange='okx',
    market='spot',
    api_key='your_api_key',
    secret='your_secret',
    passphrase='your_passphrase'  # OKX 需要 passphrase

)

# OKX 合约

api_swap = BtApi(
    exchange='okx',
    market='swap',
    api_key='your_api_key',
    secret='your_secret',
    passphrase='your_passphrase'
)

```

## 相关文档

- [OKX 开发计划](../okx_api_todo.md)
