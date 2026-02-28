# Binance 合约交易

bt_api_py 支持 Binance 的多种合约交易类型。

## 合约类型

| 类型 | 说明 | 支持状态 |

|------|------|----------|

| **USDT-M 永续**| USDT 本位永续合约 | ✅ |

|**USDT-M 交割**| USDT 本位交割合约 | ✅ |

|**COIN-M 永续**| 币本位永续合约 | ✅ |

|**COIN-M 交割**| 币本位交割合约 | ✅ |

|**杠杆** | 逐仓/全仓杠杆 | ✅ |

## 快速开始

```python
from bt_api_py import BtApi

# USDT 本位合约

api = BtApi(
    exchange='binance',
    market='swap',
    api_key='your_api_key',
    secret='your_secret'
)

# 下单

order = api.limit_order(
    symbol='BTCUSDT',
    side='buy',
    quantity=0.001,
    price=50000
)

```bash

## 杠杆交易

```python

# 设置杠杆

api.set_leverage('BTCUSDT', 10)

# 设置持仓模式

api.set_position_mode('hedge')  # 双向持仓

```bash
详见 [Binance 杠杆交易](../margin_trading/) 文档。
