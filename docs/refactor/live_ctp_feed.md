# 拆分方案卡：`bt_api/bt_api_ctp/src/bt_api_ctp/feeds/live_ctp_feed.py`

> 职责定位：CTP 行情 Feed　|　现状 **2025 行**（迭代07 M5-Task14 产出，本轮仅出方案不改代码）

## 1. 现状结构

- 顶层类 6 个、顶层函数 13 个

| 类 | 行数 | 方法数 |
|----|------|--------|
| `CtpRequestData` | 1080 | 53 |
| `CtpMarketStream` | 334 | 15 |
| `CtpTradeStream` | 107 | 10 |
| `CtpVolumeDeltaTracker` | 40 | 2 |
| `_CtpManagedQuoteV2Receipt` | 27 | 0 |
| `CtpRequestDataFuture` | 5 | 1 |

| 顶层函数 | 行数 |
|---------|------|
| `_resolve_ctp_runtime_kwargs` | 129 |
| `_get_ctp_managed_quote_v2_receipt` | 58 |
| `_validate_ctp_quote` | 32 |
| `_ctp_field_to_dict` | 18 |
| `_query_evidence` | 14 |
| `_positive_ctp_price` | 13 |
| `_positive_int_lot` | 10 |
| `_valid_ctp_quote_number` | 10 |
| `_safe_ctp_quote_number` | 9 |
| `_as_bool` | 6 |

## 2. 风险

- 该文件被广泛导入，任何移动都会影响调用方与子仓测试；
- 巨型文件内部状态耦合（实例属性/闭包），拆分前需先锁定『哪些状态属于同一职责』；
- 既有测试是保护但**非全覆盖**，必须逐段推进而非一次性重写。

## 3. 拆分边界（建议，待实施阶段评审）

按上表把职责相近的类/函数分组，抽出为独立模块，并在原文件保留**再导出**（re-export），
使调用方零改动；每抽出一组即跑一次回归，绿了再抽下一组。

## 4. 回归测试

- 根测试：`pytest tests -q -n 8 -m "not network and not integration and not performance and not e2e and not ctp"`
- 相关子仓测试（CTP 文件：`bt_api/bt_api_ctp/tests`）；
- 每个抽取批次后必须全绿，且 `make quality-ratchet` 不回退。

## 5. 是否本期执行

**否**。本轮（迭代07）只产出方案、不动该文件——它是工作代码，可维护性收益与回归面风险
不成比例，应单独立项、按批次推进。
