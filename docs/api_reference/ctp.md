# CTP API 参考

CTP (Comprehensive Transaction Platform) 是中国期货市场专用的交易接口。

## 交易所标识

| 标识 | 说明 |

|------|------|

| `CTP___FUTURE` | 中国期货市场 |

## 认证配置

```python
from bt_api_py import CtpAuthConfig

exchange_kwargs = {
    "CTP___FUTURE": {
        "auth_config": CtpAuthConfig(
            broker_id="9999",           # 经纪商代码
            user_id="your_user_id",     # 用户 ID
            password="your_password",   # 密码
            md_front="tcp://...",       # 行情前置地址
            td_front="tcp://...",       # 交易前置地址
            app_id="your_app_id",       # 应用 ID（生产环境）
            auth_code="your_auth_code", # 认证码（生产环境）
        )
    }
}

```

## SimNow 仿真环境

SimNow 提供免费的仿真环境供测试使用：

| 参数 | 电信 | 移动/联通 |

|------|------|----------|

| 行情前置 | `tcp://180.168.146.187:10211` | `tcp://180.168.146.187:10212` |

| 交易前置 | `tcp://180.168.146.187:10201` | `tcp://180.168.146.187:10202` |

| 经纪商代码 | `9999` | `9999` |

注册地址: <https://www.simnow.com.cn/>

## 合约代码格式

CTP 使用合约代码来标识交易品种：

| 市场 | 合约代码示例 | 说明 |

|------|--------------|------|

| 沪深 300 股指期货 | `IF2506`, `IF2509` | IF + 年份 + 月份 |

| 上证 50 股指期货 | `IH2506`, `IH2509` | IH + 年份 + 月份 |

| 中证 500 股指期货 | `IC2506`, `IC2509` | IC + 年份 + 月份 |

| 中证 1000 股指期货 | `IM2506`, `IM2509` | IM + 年份 + 月份 |

| 黄金期货 | `AU2506`, `AU2512` | AU + 年份 + 月份 |

| 原油期货 | `SC2506`, `SC2510` | SC + 年份 + 月份 |

| 白银期货 | `AG2506`, `AG2512` | AG + 年份 + 月份 |

| 铜期货 | `CU2506`, `CU2512` | CU + 年份 + 月份 |

## 特殊参数

### 开平方向

CTP 下单必须指定开平方向：

| 值 | 说明 |

|------|------|

| `open` | 开仓 |

| `close` | 平仓（自动判断今昨） |

| `close_today` | 平今仓 |

| `close_yesterday` | 平昨仓 |

### 价格类型

| 值 | 说明 |

|------|------|

| `limit_price` | 限价单，指定价格 |

| `market_price` | 市价单，市价执行 |

## 常用 API

### 查询合约信息

```python
api = api.get_request_api("CTP___FUTURE")
instruments = api.get_instruments()

```

### 查询投资者持仓

```python
positions = api.get_position()

```

### 查询资金账户

```python
account = api.get_account()

```

## CTP 特有功能

### 查询合约保证金率

```python
margin_rate = api.get_margin_rate("IF2506")

```

### 查询合约手续费率

```python
commission_rate = api.get_commission_rate("IF2506")

```

### 查询行情

```python
tick = api.get_tick("CTP___FUTURE", "IF2506")

```

## 交易时间

CTP 有严格的交易时段，非交易时段无法下单：

| 类型 | 时段 |

|------|------|

| 日盘 | 09:00-10:15, 10:30-11:30, 13:30-15:00 |

| 夜盘 | 21:00-23:00（部分品种） |

## 注意事项

1. **登录前准备**- 首次使用需要登录认证

2.**交易时间**- 必须在交易时段内下单
3.**合约到期**- 注意合约到期日，及时移仓
4.**保证金**- 确保账户有足够保证金
5.**涨跌停** - 价格涨跌停时可能无法成交

## 相关文档

- [CTP SWIG 重构计划](../ctp_swig_refactoring_plan.md)
- [CTP 快速入门](../ctp_quickstart.md)
