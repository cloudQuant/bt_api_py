# API 使用示例

本文档提供 bt_api_py 的实际使用示例。

## 目录

- [基础示例](#基础示例)
- [行情数据](#行情数据)
- [交易操作](#交易操作)
- [账户管理](#账户管理)
- [WebSocket 订阅](#websocket-订阅)
- [多交易所操作](#多交易所操作)
- [策略开发](#策略开发)
- [数据处理](#数据处理)

- --

## 基础示例

### 初始化 API

```python
from bt_api_py import BtApi

# 单个交易所

api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "your_api_key",
        "secret": "your_secret",
        "testnet": True,
    }
})

# 多个交易所

api = BtApi(exchange_kwargs={
    "BINANCE___SPOT": {
        "api_key": "binance_key",
        "secret": "binance_secret",
    },
    "OKX___SPOT": {
        "api_key": "okx_key",
        "secret": "okx_secret",
        "passphrase": "okx_passphrase",
    },
})

# 使用配置文件

import yaml
with open("config.yaml") as f:
    config = yaml.safe_load(f)
api = BtApi(exchange_kwargs=config["exchanges"])

```bash

### 基础查询

```python

# 获取行情

ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
ticker.init_data()
print(f"最新价: {ticker.get_last_price()}")

# 获取深度

orderbook = api.get_depth("BINANCE___SPOT", "BTCUSDT", count=10)
orderbook.init_data()
print(f"买一: {orderbook.get_bids()[0]}")

# 获取 K 线

klines = api.get_kline("BINANCE___SPOT", "BTCUSDT", "1m", count=10)
for bar in klines:
    bar.init_data()
    print(f"时间: {bar.get_open_time()}, 收盘: {bar.get_close_price()}")

```bash

- --

## 行情数据

### 获取多个交易对的行情

```python
symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

for symbol in symbols:
    ticker = api.get_tick("BINANCE___SPOT", symbol)
    ticker.init_data()
    print(f"{symbol}: {ticker.get_last_price()}")

```bash

### 监控价格变化

```python
import time

def monitor_price(symbol, interval=5):
    """监控价格变化"""
    last_price = None

    while True:
        ticker = api.get_tick("BINANCE___SPOT", symbol)
        ticker.init_data()
        price = ticker.get_last_price()

        if last_price is not None:
            change = price - last_price
            change_pct = (change / last_price) *100
            print(f"价格: {price}, 变化: {change:+.2f} ({change_pct:+.2f}%)")
        else:
            print(f"初始价格: {price}")

        last_price = price
        time.sleep(interval)

# 使用

monitor_price("BTCUSDT")

```bash

### 批量获取历史 K 线

```python
def download_bars(symbol, period, days=7):
    """下载历史 K 线"""
    from datetime import datetime, timedelta

    end_time = datetime.now()
    start_time = end_time - timedelta(days=days)

    api.download_history_bars(
        "BINANCE___SPOT",
        symbol,
        period,
        start_time=start_time,
        end_time=end_time
    )

# 从队列获取数据
    data_queue = api.get_data_queue("BINANCE___SPOT")
    bars = []
    while not data_queue.empty():
        bar = data_queue.get()
        bar.init_data()
        bars.append(bar)

    return bars

bars = download_bars("BTCUSDT", "1H", days=30)
print(f"下载了 {len(bars)} 根 K 线")

```bash

### 计算技术指标

```python
def calculate_ma(bars, period=20):
    """计算移动平均线"""
    prices = [bar.get_close_price() for bar in bars]
    ma = []
    for i in range(len(prices)):
        if i >= period - 1:
            ma.append(sum(prices[i-period+1:i+1]) / period)
        else:
            ma.append(None)
    return ma

def calculate_rsi(bars, period=14):
    """计算 RSI 指标"""
    prices = [bar.get_close_price() for bar in bars]
    gains = []
    losses = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-change)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi = []

    for i in range(period, len(gains)):
        avg_gain = (avg_gain*(period - 1) + gains[i]) / period
        avg_loss = (avg_loss*(period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else 0
        rsi.append(100 - (100 / (1 + rs)))

    return rsi

# 使用

klines = api.get_kline("BINANCE___SPOT", "BTCUSDT", "1H", count=100)
for bar in klines:
    bar.init_data()

ma_20 = calculate_ma(klines, 20)
rsi_14 = calculate_rsi(klines, 14)

print(f"最新 MA20: {ma_20[-1]:.2f}")
print(f"最新 RSI14: {rsi_14[-1]:.2f}")

```bash

- --

## 交易操作

### 下限价单

```python

# 创建订单

order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    price=45000,
    order_type="limit"
)
order.init_data()

print(f"订单 ID: {order.get_order_id()}")
print(f"订单状态: {order.get_order_status()}")

```bash

### 下市价单

```python

# 市价单（price 传 0）

order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    price=0,
    order_type="market"
)

```bash

### 下条件单（只做 maker）

```python

# Post-only 订单（只做 maker，不立即成交）

order = api.make_order(
    exchange_name="BINANCE___SPOT",
    symbol="BTCUSDT",
    volume=0.001,
    price=45000,
    order_type="limit",
    post_only=True  # 只做 maker

)

```bash

### CTP 下单（指定开平）

```python

# CTP 必须指定开平方向

order = api.make_order(
    exchange_name="CTP___FUTURE",
    symbol="IF2506",
    volume=1,
    price=3500.0,
    order_type="limit",
    offset="open"  # 开仓

)

```bash

### 查询订单状态

```python
def wait_order_filled(api, exchange_name, symbol, order_id, timeout=60):
    """等待订单成交"""
    import time
    start_time = time.time()

    while time.time() - start_time < timeout:
        order = api.query_order(exchange_name, symbol, order_id)
        order.init_data()
        status = order.get_order_status()

        print(f"订单状态: {status}")

        if status in ["filled", "canceled", "rejected"]:
            return order

        time.sleep(2)

    return order

# 使用

order = api.make_order("BINANCE___SPOT", "BTCUSDT", 0.001, 45000, "limit")
order.init_data()

result = wait_order_filled(
    api,
    "BINANCE___SPOT",
    "BTCUSDT",
    order.get_order_id()
)

```bash

### 撤销所有订单

```python

# 撤销特定交易对的订单

api.cancel_all("BINANCE___SPOT", symbol="BTCUSDT")

# 撤销所有订单

api.cancel_all("BINANCE___SPOT")

# 撤销多个交易所的订单

for exchange in api.list_exchanges():
    api.cancel_all(exchange)

```bash

- --

## 账户管理

### 查询账户余额

```python
def print_balance(api, exchange_name):
    """打印账户余额"""
    balance = api.get_balance(exchange_name)
    balance.init_data()

    print(f"\n=== {exchange_name} 余额 ===")
    for asset in balance:
        print(f"{asset.get_currency()}:")
        print(f"  可用: {asset.get_free()}")
        print(f"  冻结: {asset.get_locked()}")
        print(f"  总计: {asset.get_total()}")

print_balance(api, "BINANCE___SPOT")

```bash

### 查询持仓

```python
def print_positions(api, exchange_name):
    """打印持仓信息"""
    positions = api.get_position(exchange_name)

    print(f"\n=== {exchange_name} 持仓 ===")
    total_pnl = 0

    for pos in positions:
        pos.init_data()
        pnl = pos.get_unrealized_profit()
        total_pnl += pnl

        print(f"{pos.get_symbol_name()}:")
        print(f"  持仓: {pos.get_position()}")
        print(f"  开仓价: {pos.get_open_price()}")
        print(f"  未实现盈亏: {pnl:.2f}")

    print(f"总未实现盈亏: {total_pnl:.2f}")

print_positions(api, "BINANCE___SWAP")

```bash

### 更新账户净值

```python

# 更新所有交易所的账户净值

api.update_total_balance()

# 获取总净值和现金

total_value = api.get_total_value()
total_cash = api.get_total_cash()

print("账户总净值:")
for exchange, value in total_value.items():
    cash = total_cash[exchange]
    for currency, val in value.items():
        print(f"  {exchange} {currency}: 净值={val:.2f}, 现金={cash[currency]:.2f}")

```bash

- --

## WebSocket 订阅

### K 线订阅

```python
from bt_api_py import BtApi

api = BtApi(exchange_kwargs={...})
data_queue = api.get_data_queue("BINANCE___SPOT")

# 订阅 K 线

api.subscribe("BINANCE___SPOT___BTCUSDT", [
    {"topic": "kline", "symbol": "BTCUSDT", "period": "1m"},
])

# 处理推送数据

while True:
    bar = data_queue.get()
    bar.init_data()
    print(f"K 线更新: {bar.get_close_price()} - {bar.get_volume()}")

```bash

### 使用回调模式

```python
def on_kline(bar):
    """K 线回调"""
    bar.init_data()
    print(f"K 线: {bar.get_open_time()} - {bar.get_close_price()}")

def on_order(order):
    """订单回调"""
    order.init_data()
    print(f"订单: {order.get_order_id()} - {order.get_order_status()}")

# 注册回调

event_bus = api.get_event_bus()
event_bus.subscribe("kline", on_kline)
event_bus.subscribe("order", on_order)

# 订阅并运行

api.subscribe_kline("BINANCE___SPOT", "BTCUSDT", "1m")
api.run()

```bash

### 简单量化策略

```python
class SimpleStrategy:
    def __init__(self, api):
        self.api = api
        self.event_bus = api.get_event_bus()
        self.position = 0
        self.entry_price = None

    def on_tick(self, ticker):
        """行情回调"""
        ticker.init_data()
        price = ticker.get_last_price()

# 简单的均线策略
        if price > 50000 and self.position == 0:
            self.buy()
        elif price < 45000 and self.position > 0:
            self.sell()

        print(f"价格: {price}, 持仓: {self.position}")

    def buy(self):
        """买入"""
        self.api.make_order("BINANCE___SPOT", "BTCUSDT", 0.001, 0, "market")
        self.position = 1

    def sell(self):
        """卖出"""
        self.api.make_order("BINANCE___SPOT", "BTCUSDT", 0.001, 0, "market")
        self.position = 0

# 使用

api = BtApi(exchange_kwargs={...})
strategy = SimpleStrategy(api)

strategy.event_bus.subscribe("ticker", strategy.on_tick)
api.subscribe_ticker("BINANCE___SPOT", "BTCUSDT")
api.run()

```bash

- --

## 多交易所操作

### 套利交易

```python
def find_arbitrage(api, symbol):
    """寻找套利机会"""
    exchanges = ["BINANCE___SPOT", "OKX___SPOT"]

    prices = {}
    for exchange in exchanges:
        ticker = api.get_tick(exchange, symbol)
        ticker.init_data()
        prices[exchange] = ticker.get_last_price()

# 计算价差
    binance_price = prices["BINANCE___SPOT"]
    okx_price = prices["OKX___SPOT"]
    spread = okx_price - binance_price
    spread_pct = (spread / binance_price)*100

    print(f"Binance: {binance_price}")
    print(f"OKX: {okx_price}")
    print(f"价差: {spread} ({spread_pct:.2f}%)")

# 判断是否有套利机会
    if abs(spread_pct) > 0.5:  # 价差超过 0.5%
        if spread > 0:
            print("OKX 更贵，可低买高卖")
            return {"buy": "BINANCE___SPOT", "sell": "OKX___SPOT"}
        else:
            print("Binance 更贵，可低买高卖")
            return {"buy": "OKX___SPOT", "sell": "BINANCE___SPOT"}

    return None

# 使用

arbitrage = find_arbitrage(api, "BTCUSDT")
if arbitrage:
    print(f"套利机会: 买入 {arbitrage['buy']}, 卖出 {arbitrage['sell']}")

```bash

### 获取最优价格

```python
def get_best_price(api, symbol, side="buy"):
    """获取最优价格"""
    exchanges = ["BINANCE___SPOT", "OKX___SPOT"]
    best_price = None
    best_exchange = None

    for exchange in exchanges:
        ticker = api.get_tick(exchange, symbol)
        ticker.init_data()

        if side == "buy":
            price = ticker.get_ask_price()  # 卖一价（买入价）
        else:
            price = ticker.get_bid_price()  # 买一价（卖出价）

        if best_price is None or price < best_price:
            best_price = price
            best_exchange = exchange

    return best_exchange, best_price

# 使用

exchange, price = get_best_price(api, "BTCUSDT", side="buy")
print(f"最优买入: {exchange} @ {price}")

```bash

### 跨交易所价差监控

```python
import time

def monitor_spread(api, symbol, interval=5):
    """监控跨交易所价差"""
    exchanges = ["BINANCE___SPOT", "OKX___SPOT"]

    while True:
        prices = {}
        for exchange in exchanges:
            try:
                ticker = api.get_tick(exchange, symbol)
                ticker.init_data()
                prices[exchange] = ticker.get_last_price()
            except Exception as e:
                print(f"{exchange} 获取失败: {e}")
                continue

        if len(prices) == len(exchanges):
            price_values = list(prices.values())
            max_price = max(price_values)
            min_price = min(price_values)
            spread = max_price - min_price
            spread_pct = (spread / min_price)*100

            print(f"\n{symbol} 价差监控:")
            for exchange, price in prices.items():
                print(f"  {exchange}: {price}")

            print(f"  最大价差: {spread} ({spread_pct:.4f}%)")

        time.sleep(interval)

monitor_spread(api, "BTCUSDT")

```bash

- --

## 策略开发

### 网格交易策略

```python
class GridStrategy:
    def __init__(self, api, symbol, base_price, grid_count=10, grid_spacing=0.01):
        self.api = api
        self.symbol = symbol
        self.base_price = base_price
        self.grid_count = grid_count
        self.grid_spacing = grid_spacing
        self.grids = []
        self.init_grids()

    def init_grids(self):
        """初始化网格"""
        for i in range(-self.grid_count, self.grid_count + 1):
            price = self.base_price*(1 + i*self.grid_spacing)
            self.grids.append({
                "price": price,
                "filled": False,
                "order_id": None
            })

    def run(self):
        """运行策略"""
        while True:
            ticker = self.api.get_tick("BINANCE___SPOT", self.symbol)
            ticker.init_data()
            current_price = ticker.get_last_price()

# 检查是否触发网格
            for grid in self.grids:
                if not grid["filled"]:

# 买入网格
                    if current_price <= grid["price"]:
                        self.place_order("buy", grid["price"])

# 卖出网格
                    elif current_price >= grid["price"]:
                        self.place_order("sell", grid["price"])

    def place_order(self, side, price):
        """下单"""

# 这里应该有更复杂的订单管理
        order = self.api.make_order(
            "BINANCE___SPOT",
            self.symbol,
            0.001,
            price,
            "limit"
        )
        print(f"网格订单: {side} @ {price}")

```bash

### 动量策略

```python
class MomentumStrategy:
    def __init__(self, api, symbol, period=20):
        self.api = api
        self.symbol = symbol
        self.period = period
        self.prices = []

    def update(self):
        """更新策略"""
        ticker = self.api.get_tick("BINANCE___SPOT", self.symbol)
        ticker.init_data()
        price = ticker.get_last_price()

        self.prices.append(price)
        if len(self.prices) > self.period:
            self.prices.pop(0)

        if len(self.prices) >= self.period:
            ma = sum(self.prices) / len(self.prices)
            momentum = (price - ma) / ma* 100

            print(f"价格: {price}, 均线: {ma:.2f}, 动量: {momentum:.2f}%")

# 动量超过阈值时交易
            if momentum > 2 and self.position == 0:
                self.buy()
            elif momentum < -2 and self.position > 0:
                self.sell()

```bash

- --

## 数据处理

### 保存数据到文件

```python
import json
import csv
from datetime import datetime

def save_klines_to_csv(api, symbol, period, count, filename):
    """保存 K 线到 CSV"""
    klines = api.get_kline("BINANCE___SPOT", symbol, period, count)

    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['时间', '开盘', '最高', '最低', '收盘', '成交量'])

        for bar in klines:
            bar.init_data()
            timestamp = datetime.fromtimestamp(bar.get_open_time() / 1000)
            writer.writerow([
                timestamp,
                bar.get_open_price(),
                bar.get_high_price(),
                bar.get_low_price(),
                bar.get_close_price(),
                bar.get_volume()
            ])

    print(f"已保存 {len(klines)} 条数据到 {filename}")

# 使用

save_klines_to_csv(api, "BTCUSDT", "1H", 1000, "btc_klines.csv")

```bash

### 数据库存储

```python
import sqlite3

def init_db():
    """初始化数据库"""
    conn = sqlite3.connect('trading.db')
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange TEXT,
            symbol TEXT,
            price REAL,
            volume REAL,
            timestamp INTEGER
        )
    ''')

    conn.commit()
    return conn

def save_tick(conn, exchange, ticker):
    """保存 tick 到数据库"""
    ticker.init_data()
    cursor = conn.cursor()

    cursor.execute('''
        INSERT INTO ticks (exchange, symbol, price, volume, timestamp)
        VALUES (?, ?, ?, ?, ?)
    ''', (
        exchange,
        ticker.get_symbol_name(),
        ticker.get_last_price(),
        ticker.get_last_volume(),
        ticker.get_server_time()
    ))

    conn.commit()

# 使用

conn = init_db()
ticker = api.get_tick("BINANCE___SPOT", "BTCUSDT")
save_tick(conn, "BINANCE___SPOT", ticker)

```bash

- --

## 错误处理

```python
from bt_api_py.exceptions import ExchangeNotFoundError

def safe_get_tick(api, exchange_name, symbol):
    """安全获取行情"""
    try:
        ticker = api.get_tick(exchange_name, symbol)
        ticker.init_data()
        return ticker
    except ExchangeNotFoundError:
        print(f"交易所 {exchange_name} 不存在")
        return None
    except Exception as e:
        print(f"获取行情失败: {e}")
        return None

# 使用

ticker = safe_get_tick(api, "BINANCE___SPOT", "BTCUSDT")
if ticker:
    print(ticker.get_last_price())

```bash

- --

## 更多示例

- [错误处理指南](error_handling.md)
- [最佳实践](best_practices.md)
- [性能优化](performance.md)
