# 拆分方案卡：`bt_api_py/bt_api.py`

> 职责定位：主门面 BtApi　|　现状 **8864 行**（迭代07 M5-Task14 产出，本轮仅出方案不改代码）

## 1. 现状结构

- 顶层类 10 个、顶层函数 22 个

| 类 | 行数 | 方法数 |
|----|------|--------|
| `BtApi` | 7994 | 196 |
| `_CtpPrivateIngressQueue` | 59 | 8 |
| `_CtpExecutionArmAuthorization` | 56 | 1 |
| `_CtpMarketConsumerQueue` | 49 | 11 |
| `_CtpSettlementAuthorization` | 26 | 1 |
| `_CtpMarketIngressQueue` | 25 | 6 |
| `_CtpMarketIngressEnvelope` | 18 | 2 |
| `_RuntimeRegistrar` | 15 | 4 |
| `_CtpPrivateIngressFence` | 11 | 1 |
| `_CtpControlledTestAuthority` | 9 | 1 |

| 顶层函数 | 行数 |
|---------|------|
| `_pin_verified_ctp_front_selection` | 48 |
| `_dependency_source_manifest_digest` | 36 |
| `_execution_credential_fingerprints` | 35 |
| `_package_source_manifest_digest` | 33 |
| `_approval_material_digest` | 26 |
| `_require_ctp_read_only_settings` | 20 |
| `_credential_alias_value` | 20 |
| `_ctp_managed_quote_v2_receipt` | 18 |
| `_serialized_ctp_execution_transition` | 18 |
| `_is_native_ctp_quote_v2_ticker` | 13 |

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
