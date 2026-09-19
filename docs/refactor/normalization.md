# 拆分方案卡：`bt_api_py/_normalization.py`

> 职责定位：数据归一化　|　现状 **2511 行**（迭代07 M5-Task14 产出，本轮仅出方案不改代码）

## 1. 现状结构

- 顶层类 1 个、顶层函数 62 个

| 类 | 行数 | 方法数 |
|----|------|--------|
| `_CtpQuoteV2ParentAttestation` | 20 | 0 |

| 顶层函数 | 行数 |
|---------|------|
| `_ctp_quote_v2_fields` | 182 |
| `normalize_event` | 181 |
| `instrument_spec` | 149 |
| `fee_schedule` | 137 |
| `order` | 124 |
| `funding_snapshot` | 123 |
| `normalize_result` | 96 |
| `_timestamps` | 89 |
| `_issue_ctp_quote_v2_parent_attestation` | 79 |
| `metadata` | 79 |

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
