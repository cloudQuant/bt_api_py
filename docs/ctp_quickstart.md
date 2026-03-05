# CTP 期货交易指南

CTP (Comprehensive Transaction Platform) 是中国期货市场专用的交易接口。

## 前置条件

### 安装依赖

```bash
pip install ctp-python

```

### 获取仿真账户

CTP 交易需要连接到 CTP 服务器，推荐先使用 SimNow 仿真环境：

| 环境 | 行情前置 | 交易前置 |
|------|----------|----------|
| 电信 | tcp://180.168.146.187:10211 | tcp://180.168.146.187:10201 |
| 移动/联通 | tcp://180.168.146.187:10212 | tcp://180.168.146.187:10202 |

SimNow 注册: https://www.simnow.com.cn/

## 快速开始

### 1. 创建 CTP API 实例

```python
from bt_api_py import BtApi, CtpAuthConfig

exchange_kwargs = {
    "CTP___FUTURE": {
        "auth_config": CtpAuthConfig(
            broker_id="9999",           # SimNow 经纪商代码
            user_id="your_user_id",     # 你的用户 ID
            password="your_password",   # 你的密码
            md_front="tcp://180.168.146.187:10211",  # 行情前置
            td_front="tcp://180.168.146.187:10201",  # 交易前置
            app_id="your_app_id",       # 认证 App ID (生产环境需要)
            auth_code="your_auth_code", # 认证码 (生产环境需要)
        )
    }
}

api = BtApi(exchange_kwargs=exchange_kwargs)

```

### 2. 查询行情

```python

# 获取最新 tick

ticker = api.get_ticker("CTP___FUTURE", "IF2506")
print(f"最新价: {ticker.last_price}")
print(f"买一: {ticker.bid_price1}, 卖一: {ticker.ask_price1}")
print(f"成交量: {ticker.volume}")

```

### 3. 下单交易

```python

# 下限价单

order = api.limit_order(
    exchange="CTP___FUTURE",
    symbol="IF2506",
    side="buy",
    quantity=1,
    price=3500.0
)
print(f"订单 ID: {order.order_id}")

```

### 4. 查询持仓

```python
positions = api.get_positions("CTP___FUTURE")
for pos in positions:
    print(f"{pos.symbol}: {pos.position} 手")

```

### 5. 查询账户

```python
balance = api.get_balance("CTP___FUTURE")
print(f"可用资金: {balance.available}")
print(f"保证金: {balance.margin}")

```

## WebSocket 订阅

```python
def on_tick(tick):
    print(f"{tick.symbol} 最新价: {tick.last_price}")

def on_order(order):
    print(f"订单状态更新: {order.order_id} - {order.status}")

# 订阅行情推送

api.subscribe_ticker("CTP___FUTURE", "IF2506", on_tick)
api.run()

```

## 合约代码

| 合约 | 代码示例 |
|------|----------|
| 沪深 300 股指期货 | IF2506, IF2509 |
| 上证 50 股指期货 | IH2506, IH2509 |
| 中证 500 股指期货 | IC2506, IC2509 |
| 中证 1000 股指期货 | IM2506, IM2509 |
| 黄金期货 | AU2506, AU2512 |
| 原油期货 | SC2506, SC2510 |

## CTP 特有功能

### 查询合约信息

```python
instruments = api.get_instruments("CTP___FUTURE")
for inst in instruments:
    print(f"{inst.symbol}: {inst.name} - {inst.volume_multiple} 手/手")

```

### 查询手续费率

```python
commission = api.get_commission("CTP___FUTURE", "IF2506")
print(f"开仓手续费: {commission.open_commission}")
print(f"平仓手续费: {commission.close_commission}")

```

## 注意事项

1. ***交易时间**: CTP 有严格的交易时段，非交易时段无法下单
2. ***合约到期**: 注意合约到期日，及时移仓
3. ***保证金**: 确保账户有足够保证金
4. ***涨跌停板**: 价格涨跌停时可能无法成交

## 相关文档

- [CTP SWIG 重构计划](ctp_swig_refactoring_plan.md)
- [交易所列表](exchanges/) - 更多 CTP 相关文档
