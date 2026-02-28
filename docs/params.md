# 交易所参数参考

各交易所的特定参数说明。

## 目录

- [Binance](#binance)
- [OKX](#okx)
- [CTP](#ctp)
- [Interactive Brokers](#interactive-brokers)

- --

## Binance

### 基础参数

| 参数 | 类型 | 必需 | 默认值 | 说明 |

|------|------|------|--------|------|

| `api_key` | str | ✅ | - | API Key |

| `secret` | str | ✅ | - | Secret Key |

| `testnet` | bool | ❌ | `False` | 是否使用测试网络 |

### 测试网络配置

```python
exchange_kwargs = {
    "BINANCE___SPOT": {
        "api_key": "...",
        "secret": "...",
        "testnet": True,  # 启用测试网络
    }
}

```bash

### 现货/合约通用参数

| 参数 | 类型 | 说明 |

|------|------|------|

| `recvWindow` | int | 请求有效期，默认 5000ms |

| `timestamp` | int | 请求时间戳 |

### 订单参数

| 参数 | 类型 | 说明 |

|------|------|------|

| `newClientOrderId` | str | 客户端自定义订单 ID |

| `postOnly` | bool | 是否只做 maker |

| `reduceOnly` | bool | 是否只减仓 |

| `icebergQty` | float | 冰山订单数量 |

| `timeInForce` | str | 订单有效期: GTC/IOC/FOK |

### 时间周期

| 周期 | 说明 |

|------|------|

| `1m` | 1 分钟 |

| `3m` | 3 分钟 |

| `5m` | 5 分钟 |

| `15m` | 15 分钟 |

| `30m` | 30 分钟 |

| `1h` / `1H` | 1 小时 |

| `4h` | 4 小时 |

| `1d` / `1D` | 1 天 |

### API 端点

| 环境 | Spot REST | Spot WebSocket | Futures REST | Futures WebSocket |

|------|-----------|----------------|-------------|-------------------|

| 生产 | `<https://api.binance.com`> | `wss://stream.binance.com:9443` | `<https://fapi.binance.com`> | `wss://fstream.binance.com` |

| 测试 | `<https://testnet.binance.vision`> | `wss://testnet.binance.vision` | `<https://testnet.binancefuture.com`> | `wss://stream.testnet.binancefuture.com` |

- --

## OKX

### 基础参数

| 参数 | 类型 | 必需 | 默认值 | 说明 |

|------|------|------|--------|------|

| `api_key` | str | ✅ | - | API Key |

| `secret` | str | ✅ | - | Secret Key |

| `passphrase` | str | ✅ | - | API Key Passphrase |

| `testnet` | bool | ❌ | `False` | 是否使用测试网络 |

### 特殊说明

OKX 需要 **passphrase**，在创建 API Key 时设置。

### 模拟交易配置

```python
exchange_kwargs = {
    "OKX___SPOT": {
        "api_key": "...",
        "secret": "...",
        "passphrase": "...",
        "testnet": False,
        "simulate": False,  # 是否启用模拟交易
    }
}

```bash

### 签名方法

OKX 使用 HMAC-SHA256 签名，需要包含 timestamp。

### 订单参数

| 参数 | 类型 | 说明 |

|------|------|------|

| `clientOrderId` | str | 客户端订单 ID |

| `tag` | str | 订单标签 |

| `reduceOnly` | bool | 是否只减仓 |

| `tpTriggerPx` | str | 止盈触发价 |

| `slTriggerPx` | str | 止损触发价 |

### API 端点

| 环境 | REST | WebSocket |

|------|------|-----------|

| 生产 | `<https://www.okx.com`> | `wss://ws.okx.com:8443/ws/v5/public` |

| 模拟 | `<https://www.okx.com`> | `wss://wspap.okx.com:8443/ws/v5/public` |

- --

## CTP

### 基础参数 (CtpAuthConfig)

| 参数 | 类型 | 必需 | 说明 |

|------|------|------|------|

| `broker_id` | str | ✅ | 经纪商代码 (如 SimNow 为 "9999") |

| `user_id` | str | ✅ | 用户 ID |

| `password` | str | ✅ | 密码 |

| `md_front` | str | ✅ | 行情前置地址 |

| `td_front` | str | ✅ | 交易前置地址 |

| `app_id` | str | ❌ | 应用 ID (生产环境) |

| `auth_code` | str | ❌ | 认证码 (生产环境) |

### SimNow 配置

```python
from bt_api_py import CtpAuthConfig

exchange_kwargs = {
    "CTP___FUTURE": {
        "auth_config": CtpAuthConfig(
            broker_id="9999",
            user_id="your_user_id",
            password="your_password",
            md_front="tcp://180.168.146.187:10211",  # 电信行情
            td_front="tcp://180.168.146.187:10201",  # 电信交易
        )
    }
}

```bash

### 前置地址

| 环境 | 电信行情 | 电信交易 | 移动/联通行情 | 移动/联通交易 |

|------|----------|----------|--------------|--------------|

| SimNow | tcp://180.168.146.187:10211 | tcp://180.168.146.187:10201 | tcp://180.168.146.187:10212 | tcp://180.168.146.187:10202 |

### 订单参数

| 参数 | 类型 | 说明 |

|------|------|------|

| `offset` | str | **必需** | 开平方向: `open` / `close` / `close_today` / `close_yesterday` |

| `priceType` | str | 价格类型: `limit_price` / `market_price` |

| `volume` | float | 数量 (手数) |

| `minVolume` | float | 最小成交量 |

| `condition` | dict | 条单触发条件 |

### 合约代码格式

| 合约类型 | 格式 | 示例 |

|----------|------|------|

| 股指期货 | 品种 + 年份(后 2 位) + 月份 | IF2506 (沪深 300 2025 年 6 月) |

| 商品期货 | 代码 + 年份(后 2 位) + 月份 | AU2506 (黄金 2025 年 6 月) |

- --

## Interactive Brokers

### 基础参数 (IbWebAuthConfig)

| 参数 | 类型 | 必需 | 说明 |

|------|------|------|------|

| `account_id` | str | ✅ | IB 账户 ID |

| `base_url` | str | ❌ | API 基础 URL |

### 配置示例

```python
from bt_api_py import IbWebAuthConfig

exchange_kwargs = {
    "IB_WEB___STK": {
        "auth_config": IbWebAuthConfig(
            account_id="DU1234567",  # IB 账户 ID
            base_url="<https://api.interactivebrokers.com">
        )
    }
}

```bash

### 股票代码格式

| 市场 | 格式 | 示例 |

|------|------|------|

| 美股 | 交易对符号 | `AAPL`, `TSLA`, `NVDA` |

| 港股 | 代码 + `.HK` | `700.HK` (腾讯), `9988.HK` (阿里巴巴) |

| A 股 | 代码 + `.STK` | `600519.STK` (贵州茅台) |

| 加密货币 | 交易对 | `BTCUSD` |

### 订单参数

| 参数 | 类型 | 说明 |

|------|------|------|

| `orderType` | str | 订单类型: `LMT` / `MKT` / `STP` |

| `outsideRth` | bool | 是否允许常规交易时间外下单 |

| `hidden | bool | 是否隐藏订单 |

### API 端点

| 环境 | URL |

|------|-----|

| 生产 | `<https://api.interactivebrokers.com`> |

| 测试 | `<https://api.test.interactivebrokers.com`> |

- --

## 参数对比表

### 订单类型映射

| 概念 | Binance | OKX | CTP | IB |

|------|---------|-----|-----|-----|

| 限价单 | `limit` | `limit` | `limit_price` | `LMT` |

| 市价单 | `market` | `market` | `market_price` | `MKT` |

| 止损单 | - | - | - | `STP` |

| 冰山单 | - | `iceberg` | - | - |

### 订单状态映射

| 状态 | Binance | OKX | CTP | IB |

|------|---------|-----|-----|-----|

| 新建 | `new` | `live` | - | `PendingSubmit` |

| 部分成交 | `partially_filled` | `partially_filled` | - | `PartiallyFilled` |

| 完全成交 | `filled` | `filled` | - | `Filled` |

| 已撤销 | `canceled` | `canceled` | - | `Cancelled` |

| 被拒绝 | `rejected` | - | - | - |

### 开平方向

| 概念 | OKX | CTP | IB |

|------|-----|-----|-----|

| 开仓 | `open` | `open` | - |

| 平仓 | `close` | `close` | - |

| 平今 | - | `close_today` | - |

| 平昨 | - | `close_yesterday` | - |

- --

## 参数校验

### Binance 参数校验

```python
def validate_binance_symbol(symbol: str) -> bool:
    """校验 Binance 交易对格式"""
    if not symbol.isupper():
        raise ValueError("Binance SPOT symbol must be uppercase")
    return True

def validate_binance_period(period: str) -> bool:
    """校验 K 线周期"""
    valid_periods = ["1m", "3m", "5m", "15m", "30m", "1h", "1d"]
    if period not in valid_periods:
        raise ValueError(f"Invalid period. Must be one of {valid_periods}")
    return True

```bash

### OKX 参数校验

```python
def validate_okx_symbol(symbol: str) -> bool:
    """校验 OKX 交易对格式"""
    if '-' not in symbol:
        raise ValueError("OKX symbol must contain '-'")
    return True

```bash

### CTP 参数校验

```python
def validate_ctp_offset(offset: str) -> bool:
    """校验 CTP 开平方向"""
    valid_offsets = ["open", "close", "close_today", "close_yesterday"]
    if offset not in valid_offsets:
        raise ValueError(f"Invalid offset. Must be one of {valid_offsets}")
    return True

```bash

- --

## 完整示例

### Binance 完整配置

```python
binance_config = {
    "BINANCE___SPOT": {
        "api_key": os.getenv("BINANCE_API_KEY"),
        "secret": os.getenv("BINANCE_SECRET"),
        "testnet": os.getenv("BINANCE_TESTNET", "false") == "true",
    },
    "BINANCE___SWAP": {
        "api_key": os.getenv("BINANCE_FUTURES_API_KEY"),
        "secret": os.getenv("BINANCE_FUTURES_SECRET"),
        "testnet": os.getenv("BINANCE_TESTNET", "false") == "true",
    }
}

```bash

### OKX 完整配置

```python
okx_config = {
    "OKX___SPOT": {
        "api_key": os.getenv("OKX_API_KEY"),
        "secret": os.getenv("OKX_SECRET"),
        "passphrase": os.getenv("OKX_PASSPHRASE"),
        "testnet": os.getenv("OKX_TESTNET", "false") == "true",
    },
    "OKX___SWAP": {
        "api_key": os.getenv("OKX_API_KEY"),
        "secret": os.getenv("OKX_SECRET"),
        "passphrase": os.getenv("OKX_PASSPHRASE"),
    }
}

```bash

### CTP 完整配置

```python
ctp_config = {
    "CTP___FUTURE": {
        "auth_config": CtpAuthConfig(
            broker_id=os.getenv("CTP_BROKER_ID"),
            user_id=os.getenv("CTP_USER_ID"),
            password=os.getenv("CTP_PASSWORD"),
            md_front=os.getenv("CTP_MD_FRONT"),
            td_front=os.getenv("CTP_TD_FRONT"),
            app_id=os.getenv("CTP_APP_ID", ""),  # 生产环境需要
            auth_code=os.getenv("CTP_AUTH_CODE", ""),  # 生产环境需要
        )
    }
}

```bash

### IB 完整配置

```python
ib_config = {
    "IB_WEB___STK": {
        "auth_config": IbWebAuthConfig(
            account_id=os.getenv("IB_ACCOUNT_ID"),
            base_url="<https://api.interactivebrokers.com">
        )
    },
    "IB_WEB___FUT": {
        "auth_config": IbWebAuthConfig(
            account_id=os.getenv("IB_ACCOUNT_ID"),
        )
    }
}

```bash

- --

## 相关文档

- [API 参考](api_reference.md)
- [Binance API](api_reference/binance.md)
- [OKX API](api_reference/okx.md)
- [CTP API](api_reference/ctp.md)
- [IB API](api_reference/ib.md)
