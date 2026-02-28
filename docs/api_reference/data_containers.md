# 数据容器参考

bt_api_py 使用标准化的数据容器来确保所有交易所返回相同的数据结构。

## 概述

所有数据容器都继承自基类，提供统一的接口方法：

| 方法 | 说明 |

|------|------|

| `init_data()` | 初始化数据，解析原始响应 |

| `get_event()` | 获取事件类型 |

| `get_exchange_name()` | 获取交易所名称 |

| `get_symbol_name()` | 获取交易对名称 |

| `get_server_time()` | 获取服务器时间戳 |

| `get_local_update_time()` | 获取本地更新时间 |

- --

## Ticker (行情)

### TickerData

保存最新行情信息。

- *属性方法：**

| 方法 | 返回类型 | 说明 |

|------|----------|------|

| `get_last_price()` | float | 最新成交价 |

| `get_bid_price()` | float | 买一价 |

| `get_ask_price()` | float | 卖一价 |

| `get_bid_volume()` | float | 买一量 |

| `get_ask_volume()` | float | 卖一量 |

| `get_last_volume()` | float | 最新成交量 |

| `get_ticker_symbol_name()` | str | 交易对名称 |

- *示例：**

```python
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()

print(f"最新价: {ticker.get_last_price()}")
print(f"买一: {ticker.get_bid_price()}")
print(f"卖一: {ticker.get_ask_price()}")

```bash

- --

## Order (订单)

### OrderData

保存订单相关信息。

- *订单状态 (OrderStatus)：**

| 状态 | 说明 |

|------|------|

| `SUBMITTED` | 已提交 |

| `ACCEPTED` / `NEW` | 已接受 |

| `PARTIAL` / `PARTIALLY_FILLED` | 部分成交 |

| `COMPLETED` / `FILLED` | 完全成交 |

| `CANCELED` | 已撤销 |

| `REJECTED` | 已拒绝 |

| `EXPIRED` | 已过期 |

- *属性方法：**

| 方法 | 返回类型 | 说明 |

|------|----------|------|

| `get_order_id()` | str | 订单 ID |

| `get_client_order_id()` | str | 客户端订单 ID |

| `get_trade_id()` | str | 成交 ID |

| `get_symbol_name()` | str | 交易对名称 |

| `get_order_side()` | str | 订单方向 (buy/sell) |

| `get_order_type()` | str | 订单类型 (limit/market) |

| `get_order_size()` | float | 订单数量 |

| `get_order_price()` | float | 订单价格 |

| `get_executed_qty()` | float | 已成交数量 |

| `get_order_status()` | OrderStatus | 订单状态 |

| `get_order_avg_price()` | float | 成交均价 |

| `get_order_time_in_force()` | str | 订单有效期 (GTC/IOC/FOK) |

| `get_position_side()` | str | 持仓方向 (long/short) |

| `get_order_offset()` | str | 开平方向 (open/close) |

| `get_reduce_only()` | bool | 是否只减仓 |

- *示例：**

```python
order = api.make_order("BINANCE___SPOT", "BTCUSDT", 0.001, 50000, "limit")
order.init_data()

print(f"订单 ID: {order.get_order_id()}")
print(f"订单状态: {order.get_order_status()}")
print(f"已成交: {order.get_executed_qty()}")

# 等待订单成交

import time
time.sleep(2)

order = api.query_order("BINANCE___SPOT", "BTCUSDT", order.get_order_id())
order.init_data()
print(f"最新状态: {order.get_order_status()}")

```bash

- --

## Bar (K 线)

### BarData

保存 K 线数据。

- *属性方法：**

| 方法 | 返回类型 | 说明 |

|------|----------|------|

| `get_open_time()` | int | 开盘时间 |

| `get_close_time()` | int | 收盘时间 |

| `get_open_price()` | float | 开盘价 |

| `get_high_price()` | float | 最高价 |

| `get_low_price()` | float | 最低价 |

| `get_close_price()` | float | 收盘价 |

| `get_volume()` | float | 成交量（基础资产） |

| `get_amount()` | float | 成交额（计价资产） |

| `get_quote_asset_volume()` | float | 计价资产成交量 |

| `get_base_asset_volume()` | float | 基础资产成交量 |

| `get_num_trades()` | int | 成交笔数 |

- *示例：**

```python
klines = api.get_kline("BINANCE___SPOT", "BTCUSDT", "1m", count=100)

for bar_data in klines:
    bar_data.init_data()
    print(f"时间: {bar_data.get_open_time()}")
    print(f"OHLCV: {bar_data.get_open_price()}, {bar_data.get_high_price()}, "
          f"{bar_data.get_low_price()}, {bar_data.get_close_price()}, {bar_data.get_volume()}")

```bash

- --

## OrderBook (深度)

### OrderBookData

保存订单簿深度数据。

- *属性方法：**

| 方法 | 返回类型 | 说明 |

|------|----------|------|

| `get_bids()` | list | 买盘列表 |

| `get_asks()` | list | 卖盘列表 |

| `get_timestamp()` | int | 时间戳 |

- *每档深度包含：**

| 属性 | 类型 | 说明 |

|------|------|------|

| `price` | float | 价格 |

| `volume` | float | 数量 |

- *示例：**

```python
orderbook = api.get_depth("BINANCE___SPOT", "BTCUSDT", count=20)
orderbook.init_data()

# 买盘（价格从高到低）

bids = orderbook.get_bids()
for bid in bids[:5]:
    print(f"买: {bid['price']} x {bid['volume']}")

# 卖盘（价格从低到高）

asks = orderbook.get_asks()
for ask in asks[:5]:
    print(f"卖: {ask['price']} x {ask['volume']}")

```bash

- --

## Balance (余额)

### BalanceData

保存账户余额信息。

- *属性方法：**

| 方法 | 返回类型 | 说明 |

|------|----------|------|

| `get_currency()` | str | 币种 |

| `get_free()` | float | 可用余额 |

| `get_locked()` | float | 冻结余额 |

| `get_total()` | float | 总余额 |

- *示例：**

```python
balance = api.get_balance("BINANCE___SPOT")
balance.init_data()

for asset in balance:
    print(f"{asset.get_currency()}: 可用={asset.get_free()}, "
          f"冻结={asset.get_locked()}, 总计={asset.get_total()}")

```bash

- --

## Position (持仓)

### PositionData

保存持仓信息（主要用于合约交易）。

- *属性方法：**

| 方法 | 返回类型 | 说明 |

|------|----------|------|

| `get_symbol_name()` | str | 交易对 |

| `get_position()` | float | 持仓数量 |

| `get_open_price()` | float | 开仓价 |

| `get_leverage()` | float | 杠杆倍数 |

| `get_unrealized_profit()` | float | 未实现盈亏 |

| `get_margin()` | float | 保证金 |

| `get_liquidation_price()` | float | 强平价 |

- *示例：**

```python
positions = api.get_position("BINANCE___SWAP")

for pos in positions:
    pos.init_data()
    print(f"{pos.get_symbol_name()}: {pos.get_position()} 手")
    print(f"  开仓价: {pos.get_open_price()}")
    print(f"  未实现盈亏: {pos.get_unrealized_profit()}")
    print(f"  杠杆: {pos.get_leverage()}x")

```bash

- --

## Trade (成交)

### TradeData

保存成交信息。

- *属性方法：**

| 方法 | 返回类型 | 说明 |

|------|----------|------|

| `get_trade_id()` | str | 成交 ID |

| `get_order_id()` | str | 订单 ID |

| `get_price()` | float | 成交价格 |

| `get_qty()` | float | 成交数量 |

| `get_fee()` | float | 手续费 |

| `get_timestamp()` | int | 成交时间 |

| `get_side()` | str | 买卖方向 |

- --

## 特定交易所容器

### CTP 专用容器

| 容器 | 文件 |

|------|------|

| `CtpTickerData` | `ctp/ctp_ticker.py` |

| `CtpOrderData` | `ctp/ctp_order.py` |

| `CtpPositionData` | `ctp/ctp_position.py` |

| `CtpBarData` | `ctp/ctp_bar.py` |

| `CtpTradeData` | `ctp/ctp_trade.py` |

| `CtpAccountData` | `ctp/ctp_account.py` |

### IB 专用容器

| 容器 | 文件 |

|------|------|

| `IbTickerData` | `ib/ib_ticker.py` |

| `IbOrderData` | `ib/ib_order.py` |

| `IbPositionData` | `ib/ib_position.py` |

| `IbBarData` | `ib/ib_bar.py` |

| `IbTradeData` | `ib/ib_trade.py` |

| `IbAccountData` | `ib/ib_account.py` |

| `IbContractData` | `ib/ib_contract.py` |

- --

## 初始化数据

所有从 API 返回的容器都需要调用 `init_data()` 来初始化：

```python

# 正确用法

ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()  # 必须调用

print(ticker.get_last_price())

# 错误用法

ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
print(ticker.get_last_price())  # 错误：数据未初始化

```bash

- --

## 批量初始化

对于批量数据（如 K 线列表），可以使用辅助函数：

```python
def init_data_list(data_list):
    """批量初始化数据列表"""
    for item in data_list:
        item.init_data()
    return data_list

klines = api.get_kline("BINANCE___SPOT", "BTCUSDT", "1m", count=100)
klines = init_data_list(klines)

```bash
