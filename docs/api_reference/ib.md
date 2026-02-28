# Interactive Brokers API 参考

Interactive Brokers (IB) Web API 用于访问全球股票、期货、外汇等市场。

## 交易所标识

| 标识 | 说明 |

|------|------|

| `IB_WEB___STK` | 股票交易 |

| `IB_WEB___FUT` | 期货交易 |

## 认证配置

```python
from bt_api_py import IbWebAuthConfig

exchange_kwargs = {
    "IB_WEB___STK": {
        "auth_config": IbWebAuthConfig(
            account_id="your_account_id",  # IB 账户 ID
            base_url="<https://api.interactivebrokers.com">
        )
    }
}

```bash

## API 端点

| 环境 | URL |

|------|-----|

| 生产环境 | `<https://api.interactivebrokers.com`> |

| 测试环境 | `<https://api.test.interactivebrokers.com`> |

## 特殊参数

### 股票代码格式

| 市场 | 代码格式 | 示例 |

|------|----------|------|

| 美股 | 交易对符号 | `AAPL`, `TSLA` |

| 港股 | 代码 + 交易所后缀 | `700.HK`, `9988.HK` |

| A 股 | 代码 + STK 后缀 | `600519.STK` |

| 加密货币 | 交易对 | `BTCUSD` |

### 交易所后缀

| 后缀 | 说明 |

|------|------|

| `.STK` | 股票 |

| `.HK` | 香港交易所 |

| `.L` | 伦敦交易所 |

| `.T` | 东京交易所 |

| `.DE` | 德国交易所 |

| `.PA` | 泛欧交易所 |

| `.AS` | 澳大利亚证券交易所 |

## 常用 API

### 获取报价

```python
ticker = api.get_tick("IB_WEB___STK", "AAPL")

```bash

### 查询账户

```python
account = api.get_account("IB_WEB___STK")

```bash

### 查询持仓

```python
positions = api.get_position("IB_WEB___STK")

```bash

### 下单

```python
order = api.make_order(
    exchange_name="IB_WEB___STK",
    symbol="AAPL",
    volume=100,
    price=150.0,
    order_type="limit"
)

```bash

## 订单类型

| 类型 | 说明 |

|------|------|

| `LMT` | 限价单 |

| `MKT` | 市价单 |

| `STP` | 止损单 |

| `STP_LMT` | 限价止损单 |

## 订单状态

| 状态 | 说明 |

|------|------|

| `PendingSubmit` | 等待提交 |

| `PendingCancel` | 等待撤销 |

| `PreSubmitted` | 预提交 |

| `Submitted` | 已提交 |

| `ApiPending` | API 等待中 |

| `ApiCancelled` | API 已撤销 |

| `Filled` | 已成交 |

| `Inactive` | 不活跃 |

## 注意事项

1. **市场时间**- 不同市场有不同交易时间

2.**货币转换**- 跨币种交易需要注意汇率
3.**数据延迟**- 部分数据可能有延迟
4.**API 限制** - 注意请求频率限制

## 相关文档

- [IB Web API 概览](../ib_web_api/overview.md)
- [IB API 快速参考](../ib_web_api/api_reference_quick.md)
- [IB 快速入门](../ib_quickstart.md)
