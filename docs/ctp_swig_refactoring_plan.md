# CTP SWIG 接口模块化重构方案（修订版 v2）

> **修订说明**：v1 方案提出在 SWIG `.i` 层面拆分为多个独立模块，经过对实际代码的
> 深入分析，发现该方案存在根本性缺陷（见下方"v1 方案问题"）。本修订版提出更安全、
> 更务实的 **Python 层后处理拆分** 方案。

## 1. 现状分析

### 1.1 文件结构

当前 `bt_api_py/ctp/` 目录：

```
bt_api_py/ctp/
├── __init__.py                           # from ._ctp import *; from .ctp import*

├── _ctp.cpython-311-darwin.so            # SWIG 生成的 C 扩展（单一 .so）

├── ctp.py                                # SWIG 生成的 Python 包装（12,178 行）

├── ctp.i                                 # SWIG 接口文件（238 行，源头）

├── ctp_wrap.cpp                          # SWIG 生成的 C++ 包装（509,854 行）

├── client.py                             # 高层封装（MdClient / TraderClient）

└── api/6.7.7/                            # CTP SDK（头文件 + 动态库）
    ├── darwin/                            # macOS framework
    └── linux/                             # Linux .so

```

### 1.2 ctp.py 内部结构

| 区段 | 行号范围 | 行数 | 内容 |

|------|----------|------|------|

| SWIG 基础设施 | 1-86 | 86 | 辅助函数、元类、_swig_repr |

| 常量定义 | 87-1395 | 1,309 | 127 个 `THOST_*` 常量 |

| Field 数据结构 | 1396-11165 | 9,770 | 470 个 `CThostFtdc*Field` 类 |

| MdSpi | 11166-11231 | 66 | 行情回调接口 |

| MdApi | 11232-11293 | 62 | 行情 API |

| TraderSpi | 11294-11785 | 492 | 交易回调接口 |

| TraderApi | 11786-12178 | 393 | 交易 API |

### 1.3 实际使用情况

- *项目中仅使用 18 个类型**（共 476 个类 + 127 个常量）：

被 `client.py` 直接导入的：

- `CThostFtdcMdApi`, `CThostFtdcMdSpi`
- `CThostFtdcTraderApi`, `CThostFtdcTraderSpi`
- `CThostFtdcReqUserLoginField`, `CThostFtdcReqAuthenticateField`
- `CThostFtdcSettlementInfoConfirmField`
- `CThostFtdcQryTradingAccountField`, `CThostFtdcQryInvestorPositionField`

被 `live_ctp_feed.py` 间接使用的：

- `CThostFtdcInputOrderField`, `CThostFtdcInputOrderActionField`
- `CThostFtdcDepthMarketDataField`
- `CThostFtdcOrderField`, `CThostFtdcTradeField`
- `CThostFtdcTradingAccountField`, `CThostFtdcInvestorPositionField`
- `CThostFtdcRspUserLoginField`, `CThostFtdcRspInfoField`

### 1.4 构建管道

```
ctp.i  ──SWIG──>  ctp.py (Python 包装) + ctp_wrap.cpp (C++ 包装)
                                           │
                                      gcc/clang
                                           │
                                           v
                                   _ctp.cpython-311-darwin.so

```
关键约束：

- `ctp.i` 使用 `%feature("director")` 使 MdSpi/TraderSpi 可被 Python 子类继承
- `_ctp.so` 是**单一** C 扩展，所有 C++ 绑定都在其中
- `ctp.py` 中所有类都引用同一个 `_ctp` 模块

## 2. v1 方案问题（为什么不能在 SWIG 层拆分）

原方案提出将 `ctp.i` 拆分为 13+ 个独立 SWIG 模块，每个生成独立的 `.so`。
这在实际操作中有**根本性障碍**：

### 2.1 SWIG Director 跨模块不兼容

`ctp.i` 使用了 `%feature("director")` 使得 Python 可以继承 `CThostFtdcMdSpi`
和 `CThostFtdcTraderSpi`。Director 机制要求回调参数的结构体类型与 SPI 类在

- *同一个 SWIG 模块**中定义。拆分后，SPI 回调参数（如 `CThostFtdcRspUserLoginField`）

与 SPI 类不在同一模块，Director 无法正确传递参数。

### 2.2 单一 C 扩展是 SWIG 的设计约束

SWIG 的 `%module ctp` 生成一个 `_ctp.so`。拆分为多模块意味着：

- 需要 13+ 个 `.so` 文件
- 类型在不同 `.so` 之间共享需要 `%import`（而非 `%include`）
- `%import` + `director` 的组合在 SWIG 4.x 中有已知 bug
- 编译时间和复杂度会急剧增加

### 2.3 构建系统重写代价极高

当前 `setup.py` 只编译一个 CTP extension。拆分后需要：

- 13+ 个 Extension 定义
- 管理模块间编译顺序和依赖
- 三平台（Linux/macOS/Windows）全部需要适配
- CTP SDK 更新时需要重新调整所有模块边界

### 2.4 向后兼容极难保证

现有代码通过 `from bt_api_py.ctp import CThostFtdcXxxField` 导入。拆分后
即使通过 `__init__.py` 重新导出，`pickle`、`isinstance` 等操作可能因为类的
`__module__` 属性变化而失败。

## 3. 修订方案：Python 层后处理拆分（推荐）

### 核心思路

- *保持 SWIG 生成管道完全不变**（`ctp.i` → `ctp.py` + `_ctp.so`），通过一个

自动化 Python 脚本将 SWIG 生成的 `ctp.py`（12,178 行）拆分为多个按功能分类的
Python 模块。`__init__.py` 将所有符号重新导出，**100% 向后兼容**。

### 3.1 重构后目标结构

```
bt_api_py/ctp/
├── __init__.py                  # 兼容层：重新导出所有符号（不变）

├── _ctp.cpython-311-darwin.so   # C 扩展（不动）

├── ctp.i                        # SWIG 接口文件（不动）

├── ctp_wrap.cpp                 # SWIG 生成的 C++ 包装（不动）

├── ctp.py                       # SWIG 原始输出（保留，作为 _ctp.so 的 Python 伴侣）

├── client.py                    # 高层封装（不动）

│
├── ctp_constants.py             # [新] 127 个 THOST_* 常量

├── ctp_structs_common.py        # [新] 通用结构体（登录、认证、响应等 ~50 类）

├── ctp_structs_order.py         # [新] 订单相关结构体（~60 类）

├── ctp_structs_trade.py         # [新] 成交相关结构体（~30 类）

├── ctp_structs_position.py      # [新] 持仓相关结构体（~40 类）

├── ctp_structs_account.py       # [新] 账户/资金相关结构体（~50 类）

├── ctp_structs_market.py        # [新] 行情/合约相关结构体（~40 类）

├── ctp_structs_query.py         # [新] 查询请求/响应结构体（~80 类）

├── ctp_structs_transfer.py      # [新] 银期转账相关结构体（~40 类）

├── ctp_structs_risk.py          # [新] 风控/保证金相关结构体（~80 类）

├── ctp_md_api.py                # [新] MdSpi + MdApi（~130 行）

├── ctp_trader_api.py            # [新] TraderSpi + TraderApi（~890 行）

│
├── scripts/
│   └── split_ctp_wrapper.py     # [新] 自动化拆分脚本

│
└── api/6.7.7/                   # CTP SDK（不动）

```

### 3.2 关键设计决策

#### 决策 1：拆分的是 Python 代理层，不是 C 扩展

所有拆分出的文件仍然 `from . import _ctp` 引用同一个 C 扩展。
类的 SWIG 注册代码（如 `_ctp.CThostFtdcOrderField_swigregister`）在 `_ctp.so`
加载时已全局注册，各子模块只需 import 即可使用。

#### 决策 2：`ctp.py` 保留不删除

- `_ctp.so` 编译时绑定了模块名 `ctp`，其内部 `PyInit__ctp` 函数会尝试导入

  同包下的 `ctp` 模块来完成 Python 侧注册

- 删除 `ctp.py` 会导致 `_ctp.so` 加载失败
- `ctp.py` 保留为"权威源"，子模块从中按需导入

#### 决策 3：子模块使用"从 ctp.py 按名导入"模式

每个子模块的结构统一为：

```python

# ctp_structs_order.py - 订单相关结构体

"""CTP 订单相关数据结构"""
from .ctp import (
    CThostFtdcInputOrderField,
    CThostFtdcInputOrderActionField,
    CThostFtdcOrderField,
    CThostFtdcOrderActionField,

# ... 更多订单相关类

)

__all__ = [
    'CThostFtdcInputOrderField',
    'CThostFtdcInputOrderActionField',
    'CThostFtdcOrderField',
    'CThostFtdcOrderActionField',

# ...

]

```
这样做的好处：

- **零风险**：类对象仍是 `ctp.py` 中原始的那个，`isinstance` / `pickle` 完全兼容
- **类的 `__module__` 属性不变**：仍然是 `bt_api_py.ctp.ctp`
- **按需导入**：只导入感兴趣的模块，IDE 自动补全更快
- **可读性**：每个文件 100-500 行，功能清晰

#### 决策 4：`__init__.py` 兼容层保持不变

```python

# __init__.py（保持现有行为不变）

from ._ctp import *
from .ctp import *

```
现有代码 `from bt_api_py.ctp import CThostFtdcXxxField` **无需任何修改**。

新代码可以选择更精确的导入路径：

```python

# 新的推荐导入方式（可选，不强制）

from bt_api_py.ctp.ctp_structs_order import CThostFtdcInputOrderField
from bt_api_py.ctp.ctp_trader_api import CThostFtdcTraderApi
from bt_api_py.ctp.ctp_constants import THOST_TERT_RESTART

```

### 3.3 类分类规则

以下是 470 个 Field 类的分类方案（基于 CTP API 的业务领域）：

#### ctp_structs_common.py（通用结构 ~50 类）

匹配规则：

- `*UserLogin*`, `*UserLogout*`, `*Authenticate*`, `*RspInfo*`
- `*Dissemination*`, `*SystemInfo*`, `*FensUserInfo*`
- `*Exchange*`, `*Product*`, `*Instrument*`（合约基础信息）
- `*Broker*`, `*Investor*`, `*TradingCode*`
- `*SettlementInfo*`, `*Notice*`, `*Bulletin*`

#### ctp_structs_order.py（订单相关 ~60 类）

匹配规则：

- `*InputOrder*`, `*OrderAction*`, `*OrderField*`
- `*ParkedOrder*`, `*ParkedOrderAction*`
- `*QryOrder*`, `*ExecOrder*`, `*ForQuote*`
- `*OptionSelfClose*`, `*CombAction*`, `*BatchOrder*`
- `*Quote*`（报价）

#### ctp_structs_trade.py（成交相关 ~30 类）

匹配规则：

- `*TradeField*`, `*QryTrade*`
- `*TradingNotice*`
- `*Commission*`, `*CommRate*`（手续费）

#### ctp_structs_position.py（持仓相关 ~40 类）

匹配规则：

- `*Position*`, `*InvestorPosition*`
- `*PositionDetail*`, `*PositionCombine*`
- `*QryInvestorPosition*`

#### ctp_structs_account.py（账户/资金 ~50 类）

匹配规则：

- `*TradingAccount*`, `*AccountRegister*`
- `*DepositWithdraw*`, `*FundMortgage*`
- `*QueryCFMMCTradingAccountToken*`
- `*SecAgent*`（二级代理）

#### ctp_structs_market.py（行情/合约 ~40 类）

匹配规则：

- `*DepthMarketData*`, `*MarketData*`
- `*InstrumentField*`（非基础部分，如 `*InstrumentMarginRate*`）
- `*InstrumentTradingRight*`, `*InstrumentOrderComm*`
- `*Multicast*`

#### ctp_structs_query.py（查询请求/响应 ~80 类）

匹配规则：

- `*Qry*`（所有查询请求结构）中未归入其他分类的
- `*QueryResult*`, `*QryClassifiedInstrument*`

#### ctp_structs_transfer.py（银期转账 ~40 类）

匹配规则：

- `*Transfer*`, `*BankToFuture*`, `*FutureToBank*`
- `*NotifyQueryAccount*`, `*Accountregister*`
- `*ContractBank*`, `*OpenAccount*`, `*CancelAccount*`
- `*ChangeAccount*`

#### ctp_structs_risk.py（风控/保证金 ~80 类）

匹配规则：

- `*SPBM*`, `*SPMM*`, `*RCAMS*`, `*RULE*`（各保证金模型）
- `*Margin*`, `*RiskSettle*`, `*SyncDelta*`
- `*LimitAmount*`, `*LimitPosi*`

#### ctp_md_api.py（行情接口）

- `CThostFtdcMdSpi`
- `CThostFtdcMdApi`

#### ctp_trader_api.py（交易接口）

- `CThostFtdcTraderSpi`
- `CThostFtdcTraderApi`

#### ctp_constants.py（常量）

- 全部 127 个 `THOST_*` 常量

### 3.4 自动化拆分脚本 `scripts/split_ctp_wrapper.py`

```python

# !/usr/bin/env python3

"""
自动化拆分 SWIG 生成的 ctp.py 为多个按功能分类的 Python 模块。

用法:
    python scripts/split_ctp_wrapper.py

功能:

    1. 解析 ctp.py，提取所有常量和类定义
    2. 按预定义规则将类分配到各子模块
    3. 生成子模块文件（每个文件只包含 from .ctp import ... 和 __all__）
    4. 验证无遗漏、无重复

设计原则:

    - ctp.py 保持不变（SWIG 原始输出）
    - 子模块只做 re-export，不包含任何逻辑
    - __init__.py 兼容层不变

"""
import re
import os
import sys

# 类名 -> 子模块 的映射规则（正则表达式，优先级从上到下）

CLASSIFICATION_RULES = [

# (模块名, [匹配模式列表])
    ("ctp_md_api", [
        r"^CThostFtdcMdSpi$",
        r"^CThostFtdcMdApi$",
    ]),
    ("ctp_trader_api", [
        r"^CThostFtdcTraderSpi$",
        r"^CThostFtdcTraderApi$",
    ]),
    ("ctp_structs_transfer", [
        r"Transfer", r"BankToFuture", r"FutureToBank",
        r"ContractBank", r"OpenAccount", r"CancelAccount",
        r"ChangeAccount", r"NotifyQueryAccount",
        r"Accountregister", r"AccountRegister",
    ]),
    ("ctp_structs_risk", [
        r"SPBM", r"SPMM", r"RCAMS", r"RULE",
        r"SyncDelta", r"RiskSettle",
        r"LimitAmount", r"LimitPosi",
    ]),
    ("ctp_structs_order", [
        r"InputOrder", r"OrderAction", r"^CThostFtdcOrderField$",
        r"ParkedOrder", r"ExecOrder", r"ForQuote",
        r"OptionSelfClose", r"CombAction", r"BatchOrder",
        r"Quote",  # 报价
    ]),
    ("ctp_structs_trade", [
        r"Trade(?!r)", r"Commission", r"CommRate",
        r"TradingNotice",
    ]),
    ("ctp_structs_position", [
        r"Position", r"PositionDetail", r"PositionCombine",
    ]),
    ("ctp_structs_account", [
        r"TradingAccount", r"DepositWithdraw", r"FundMortgage",
        r"CFMMC", r"SecAgent", r"UserRight",
    ]),
    ("ctp_structs_market", [
        r"DepthMarketData", r"MarketData",
        r"MarginRate", r"TradingRight", r"OrderComm",
        r"Multicast",
    ]),
    ("ctp_structs_common", [
        r"UserLogin", r"UserLogout", r"Authenticate",
        r"RspInfo", r"Dissemination", r"SystemInfo",
        r"FensUserInfo", r"Exchange(?!Data)", r"Product",
        r"Instrument", r"Broker", r"Investor",
        r"TradingCode", r"SettlementInfo", r"Notice",
        r"Bulletin", r"Trader(?!Api|Spi)",

        r"PartBroker", r"SuperUser", r"FrontInfo",
    ]),

# 兜底：未匹配的全部归入 query
    ("ctp_structs_query", [r".*"]),
]


def parse_ctp_py(filepath):
    """解析 ctp.py，提取常量和类名"""
    with open(filepath, 'r') as f:
        content = f.read()

# 提取常量
    constants = re.findall(r'^(THOST_\w+) = _ctp\.\w+', content, re.MULTILINE)

# 提取类名
    classes = re.findall(r'^class (\w+)\(', content, re.MULTILINE)

# 排除 SWIG 内部类
    classes = [c for c in classes if not c.startswith('_Swig')]

    return constants, classes


def classify(class_name, rules):
    """将类名分配到子模块"""
    for module_name, patterns in rules:
        for pattern in patterns:
            if re.search(pattern, class_name):
                return module_name
    return "ctp_structs_query"  # 兜底


def generate_module(module_name, symbols, is_constants=False):
    """生成子模块文件内容"""
    if not symbols:
        return None

    lines = []
    if is_constants:
        lines.append(f'"""CTP 常量定义（自动生成，勿手动编辑）"""')
    else:
        desc_map = {
            "ctp_md_api": "CTP 行情 API 接口",
            "ctp_trader_api": "CTP 交易 API 接口",
            "ctp_structs_common": "CTP 通用数据结构",
            "ctp_structs_order": "CTP 订单相关数据结构",
            "ctp_structs_trade": "CTP 成交相关数据结构",
            "ctp_structs_position": "CTP 持仓相关数据结构",
            "ctp_structs_account": "CTP 账户/资金相关数据结构",
            "ctp_structs_market": "CTP 行情/合约相关数据结构",
            "ctp_structs_query": "CTP 查询请求/响应数据结构",
            "ctp_structs_transfer": "CTP 银期转账相关数据结构",
            "ctp_structs_risk": "CTP 风控/保证金相关数据结构",
        }
        desc = desc_map.get(module_name, f"CTP {module_name}")
        lines.append(f'"""{desc}（自动生成，勿手动编辑）"""')

    lines.append("from .ctp import (")
    for sym in sorted(symbols):
        lines.append(f"    {sym},")
    lines.append(")")
    lines.append("")
    lines.append("__all__ = [")
    for sym in sorted(symbols):
        lines.append(f"    '{sym}',")
    lines.append("]")
    lines.append("")

    return "\n".join(lines)


def main():
    ctp_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ctp_py = os.path.join(ctp_dir, "ctp.py")

    if not os.path.exists(ctp_py):
        print(f"Error: {ctp_py} not found")
        sys.exit(1)

    constants, classes = parse_ctp_py(ctp_py)
    print(f"Parsed: {len(constants)} constants, {len(classes)} classes")

# 分类
    module_classes = {}
    for cls in classes:
        mod = classify(cls, CLASSIFICATION_RULES)
        module_classes.setdefault(mod, []).append(cls)

# 生成常量模块
    content = generate_module("ctp_constants", constants, is_constants=True)
    if content:
        path = os.path.join(ctp_dir, "ctp_constants.py")
        with open(path, 'w') as f:
            f.write(content)
        print(f"  Generated: ctp_constants.py ({len(constants)} constants)")

# 生成类模块
    total = 0
    for mod_name, cls_list in sorted(module_classes.items()):
        content = generate_module(mod_name, cls_list)
        if content:
            path = os.path.join(ctp_dir, f"{mod_name}.py")
            with open(path, 'w') as f:
                f.write(content)
            print(f"  Generated: {mod_name}.py ({len(cls_list)} classes)")
            total += len(cls_list)

    print(f"\nTotal: {total} classes classified into {len(module_classes)} modules")

# 验证无遗漏
    all_classified = sum(module_classes.values(), [])
    missing = set(classes) - set(all_classified)
    if missing:
        print(f"WARNING: {len(missing)} classes not classified: {missing}")
    else:
        print("Validation OK: all classes classified, no duplicates")


if __name__ == "__main__":
    main()

```

### 3.5 对 `__init__.py` 的改进（可选）

当前 `__init__.py` 做 `from .ctp import *` 会一次性加载全部 476 个类的引用。
可以改为延迟导入以加速启动：

```python

# __init__.py（可选优化版本）

from ._ctp import *
from .ctp import *

# 提供子模块级别的访问（可选）

def __getattr__(name):
    """支持 from bt_api_py.ctp import ctp_structs_order 等子模块导入"""
    import importlib
    submodules = {
        'ctp_constants', 'ctp_structs_common', 'ctp_structs_order',
        'ctp_structs_trade', 'ctp_structs_position', 'ctp_structs_account',
        'ctp_structs_market', 'ctp_structs_query', 'ctp_structs_transfer',
        'ctp_structs_risk', 'ctp_md_api', 'ctp_trader_api',
    }
    if name in submodules:
        return importlib.import_module(f'.{name}', __package__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

```

### 3.6 对 `client.py` 的改进（可选）

当前 `client.py` 从 `. import ...` 导入（走 `__init__.py`），可以改为更精确的导入：

```python

# 当前

from . import (
    CThostFtdcMdApi, CThostFtdcMdSpi,
    CThostFtdcTraderApi, CThostFtdcTraderSpi,
    CThostFtdcReqUserLoginField, ...
)

# 改进后（可选，更清晰的依赖）

from .ctp_md_api import CThostFtdcMdApi, CThostFtdcMdSpi
from .ctp_trader_api import CThostFtdcTraderApi, CThostFtdcTraderSpi
from .ctp_structs_common import CThostFtdcReqUserLoginField, CThostFtdcReqAuthenticateField
from .ctp_structs_common import CThostFtdcSettlementInfoConfirmField
from .ctp_structs_query import CThostFtdcQryTradingAccountField
from .ctp_structs_position import CThostFtdcQryInvestorPositionField

```

## 4. 实施步骤

### 阶段 1：创建拆分脚本（半天）

1. 创建 `bt_api_py/ctp/scripts/split_ctp_wrapper.py`（上述 3.4 节代码）
2. 运行脚本，生成所有子模块
3. 检查输出：确认无遗漏、无重复

```
cd bt_api_py/ctp
python scripts/split_ctp_wrapper.py

```
预期输出：

```
Parsed: 127 constants, 476 classes
  Generated: ctp_constants.py (127 constants)
  Generated: ctp_md_api.py (2 classes)
  Generated: ctp_trader_api.py (2 classes)
  Generated: ctp_structs_common.py (~50 classes)
  Generated: ctp_structs_order.py (~60 classes)
  Generated: ctp_structs_trade.py (~30 classes)
  Generated: ctp_structs_position.py (~40 classes)
  Generated: ctp_structs_account.py (~50 classes)
  Generated: ctp_structs_market.py (~40 classes)
  Generated: ctp_structs_query.py (~80 classes)
  Generated: ctp_structs_transfer.py (~40 classes)
  Generated: ctp_structs_risk.py (~80 classes)

Total: 476 classes classified into 12 modules
Validation OK: all classes classified, no duplicates

```

### 阶段 2：验证（半天）

1. **导入测试**：确认所有子模块可以正常导入

```python

# test_ctp_split.py

from bt_api_py.ctp import CThostFtdcTraderApi          # 原路径仍可用

from bt_api_py.ctp.ctp_trader_api import CThostFtdcTraderApi  # 新路径也可用

from bt_api_py.ctp.ctp_structs_order import CThostFtdcInputOrderField
from bt_api_py.ctp.ctp_constants import THOST_TERT_RESTART

# 验证是同一个对象

from bt_api_py.ctp import CThostFtdcInputOrderField as A
from bt_api_py.ctp.ctp_structs_order import CThostFtdcInputOrderField as B
assert A is B, "Must be the same object!"

print("All imports OK")

```

1. **回归测试**：运行现有测试套件

```
pytest tests/ -x -v

```

1. **功能验证**：确认 `client.py` 和 `live_ctp_feed.py` 无影响

### 阶段 3：可选改进（按需）

- 更新 `client.py` 使用更精确的导入（3.6 节）
- 添加 `__init__.py` 延迟加载优化（3.5 节）
- 更新 `setup.py` 的 `package_data`，包含新文件

## 5. 风险评估

### 风险 1：分类规则不完美

- *概率**：中
- *影响**：低
- *应对**：兜底规则确保无遗漏；分类错误仅影响可读性，不影响功能。

脚本可反复运行调整规则。

### 风险 2：CTP SDK 版本更新后类名变化

- *概率**：低（CTP API 很稳定）
- *影响**：低
- *应对**：重新运行 SWIG 生成 `ctp.py`，再运行拆分脚本即可。

全流程：`swig → ctp.py → split_ctp_wrapper.py → 子模块`。

### 风险 3：第三方代码使用 `from bt_api_py.ctp.ctp import XxxField`

- *概率**：低
- *影响**：零
- *应对**：`ctp.py` 完全保留，此导入路径永远有效。

### 风险对比（v1 vs v2）

| 风险维度 | v1（SWIG 层拆分） | v2（Python 层拆分） |

|----------|-------------------|---------------------|

| 破坏 C++ 绑定 | **高**|**零**|

| 破坏现有导入 |**中**|**零**|

| 构建系统改动 |**大量**|**无**|

| Director 兼容 |**有 bug 风险**|**无影响**|

| 实施工时 | 10-15 天 |**半天-1 天**|

| 可回滚性 | 困难 |**删除子模块即可**|

## 6. 总结

### 方案对比

| 维度 | v1 (SWIG 层拆分) | v2 (Python 层后处理) |

|------|-------------------|----------------------|

| 改动范围 | ctp.i, setup.py, _ctp.so | 仅新增 Python 文件 |

| 风险等级 | 高 |**极低**|

| 向后兼容 | 需要兼容层 |**100% 兼容**|

| 实施周期 | 10-15 工作日 |**0.5-1 工作日**|

| C 扩展改动 | 13+ 个 .so |**不动**|

| 构建系统改动 | 大量 |**无**|

| CTP 升级成本 | 重新调整模块边界 |**重新运行脚本**|

### 核心原则

1.**不动 SWIG 生成管道**：`ctp.i` → `ctp.py` + `_ctp.so` 完全不变

1. **不动 C 扩展**：`_ctp.cpython-311-darwin.so` 完全不变
2. **不删 ctp.py**：保留为权威源，子模块从中导入
3. **100% 向后兼容**：所有现有导入路径不变
4. **自动化可重复**：CTP SDK 更新后重新运行脚本即可

### 推荐

- *采用 v2 方案**。半天即可完成，零风险，完全可逆。

---

## 附录

### A. 参考资源

- [SWIG 官方文档](https://www.swig.org/Doc4.0/SWIGDocumentation.html)
- [CTP API 文档](http://www.sfit.com.cn/DocumentDown/api_3/index.htm)
- [openctp 项目](<https://github.com/openctp/openctp)>
- [SWIG Director 机制文档](https://www.swig.org/Doc4.0/Python.html#Python_nn36)
