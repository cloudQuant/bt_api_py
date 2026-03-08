# bt_api_py 初级工程师执行版迭代计划（Phase 3：质量基线闭环）

日期：2026-03-08  
适用对象：初级工程师 / 初级测试人员  
计划周期：4 周  
目标：基于 2026-03-08 的当前仓库状态，先补齐质量基线、测试边界和运行时可观测性，再决定后续是否进入更深的架构重构

## 1. 这轮计划为什么要做

这轮计划不是继续扩交易所数量，也不是全面清理全部历史技术债，而是先解决“已经确认会影响开发效率和验收稳定性”的问题。当前代码库的核心事实是：

- `bt_api_py/` 目录下共有 `764` 个 Python 源文件
- `tests/` 目录下共有 `212` 个 Python 测试文件
- `pytest tests -m 'not network' -q --maxfail=20` 当前结果为 `3348 passed, 77 skipped, 2697 deselected`
- 同一次回归仍出现 `pytest-benchmark` 与 `xdist` 组合的提示，说明性能测试和常规并行回归边界还不够清晰
- `ruff check bt_api_py tests` 当前一次性报出 `1263` 个问题，其中既有风格问题，也有真实代码错误

这说明项目的主干回归已经具备一定稳定性，但质量门禁还没有真正闭环。继续扩功能会把后续改造成本进一步放大。

## 2. 本轮分析得到的明确问题

### 2.1 已确认的生产代码正确性问题

- [bitget_account.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/containers/accounts/bitget_account.py#L147) 使用了未定义的 `BitgetBalanceData`
- [upbit_balance.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/containers/balances/upbit_balance.py#L96) 使用了未定义变量 `currency`
- `ruff` 还指出一批生产路径中的裸 `except`、`raise ... from` 缺失和导入顺序问题，这些不只是“好不好看”，而是会影响调试质量和部分运行时行为

### 2.2 运行时日志与吞错热点仍然存在

按当前扫描，热点包括：

- [request_base.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_binance/request_base.py) 仍有 `16` 个 `print()`
- [spot.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_binance/spot.py) 仍有 `8` 个 `print()`
- [my_websocket_app.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/my_websocket_app.py) 仍有 `5` 个 `print()`
- [monitoring.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/websocket/monitoring.py) 有 `11` 处 `except Exception|except:`
- [advanced_connection_manager.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/websocket/advanced_connection_manager.py) 有 `10` 处 `except Exception|except:`
- [services.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/core/services.py) 有 `9` 处 `except Exception|except:`

### 2.3 测试边界没有完全和 network/live 场景分开

- 共有 `27` 个测试文件调用了 `read_account_config()`
- 其中至少 `26` 个文件没有显式 `@pytest.mark.network`
- 这会让“依赖真实账号、网络或 IP 白名单”的测试继续混入日常回归判断

### 2.4 一批测试仍保留脚本风格，不适合长期维护

当前 `print()` 最多的测试文件包括：

- [test_okx_swap_req_funding.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_okx_swap_req_funding.py) `102` 个 `print()`
- [test_okx_swap_req_account_config.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_okx_swap_req_account_config.py) `84` 个 `print()`
- [test_hyperliquid_integration.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/integration/test_hyperliquid_integration.py) `60` 个 `print()`
- [test_live_ib_web_request_data.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_live_ib_web_request_data.py) `38` 个 `print()`
- [test_gemini_integration.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/integration/test_gemini_integration.py) `45` 个 `print()`

这类测试更像手工调试脚本，而不是稳定、可复跑、可验收的自动化测试。

### 2.5 关键模块仍缺少更直接的失败路径或生命周期测试

- 当前不存在 [test_services.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/core/test_services.py) 这样的核心服务直接测试文件
- [test_security_compliance.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_security_compliance.py) 目前几乎都是正向路径
- [test_risk_management.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_risk_management.py) 也以正向路径为主

### 2.6 文档和真实状态已经再次漂移

- [source-tree-analysis.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/source-tree-analysis.md) 仍写着 `201` 个测试文件，但当前真实数量是 `212`
- [testing_optimization_summary.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/testing_optimization_summary.md) 仍写着 `204` 个测试文件，且部分“已完成”表述和当前质量状态不完全一致

## 3. 本轮迭代目标

1. 修掉一批已经确认的真实正确性问题，先把质量基线拉回可控状态。
2. 把 network/live 测试和确定性回归进一步分开，降低误判成本。
3. 清理最明显的运行时 `print()` 和脚本式测试噪音。
4. 给核心服务补上第一批直接测试，让后续异常治理不再“盲改”。
5. 刷新关键文档，使开发、测试和维护人员看到的是当前事实。

## 4. 非目标

本轮不要做下面这些事情：

1. 不要做“全仓 1263 个 lint 问题一次性清零”这种大扫除。
2. 不要修改 CTP 生成物、大体量 SWIG 文件和平台构建链路。
3. 不要同时发起 registry、container、feed 三层联动重构。
4. 不要把网络失败、白名单失败、第三方接口抖动硬修成“本地绿”。
5. 不要在没有高级工程师确认的情况下改公开 API 签名。

## 5. 任务总览

| 任务ID | 任务名 | 优先级 | 预计工期 | 适合级别 |
|---|---|---:|---:|---|
| P3-T1 | 生产代码真实错误修复第一批 | P0 | 1 天 | 初级 |
| P3-T2 | network/live 测试边界补标 | P0 | 1.5 天 | 初级 |
| P3-T3 | 脚本风格测试收敛第一批 | P0 | 2 天 | 初级 |
| P3-T4 | 运行时日志噪音清理第一批 | P1 | 1.5 天 | 初级 |
| P3-T5 | Core Services 生命周期测试补齐 | P1 | 2 天 | 初中级 |
| P3-T6 | 文档与验收口径刷新 | P1 | 1 天 | 初级 |

## 6. 推荐执行顺序

### 第 1 周

- P3-T1 生产代码真实错误修复第一批
- P3-T2 network/live 测试边界补标

### 第 2 周

- P3-T3 脚本风格测试收敛第一批
- P3-T4 运行时日志噪音清理第一批

### 第 3 周

- P3-T5 Core Services 生命周期测试补齐

### 第 4 周

- P3-T6 文档与验收口径刷新
- 整体验收回归

## 7. 详细任务卡

### P3-T1 生产代码真实错误修复第一批

目标：先修掉已经被静态检查明确指出、且可能影响运行时行为的真实错误。

建议修改文件：

- [bitget_account.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/containers/accounts/bitget_account.py)
- [upbit_balance.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/containers/balances/upbit_balance.py)
- [upbit_ticker.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/containers/tickers/upbit_ticker.py)
- [order.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/containers/orders/order.py)
- 相关的容器测试文件；如果缺失，允许新增最小测试文件

开发动作：

1. 修复未定义名称和明显变量引用错误。
2. 将裸 `except:` 改成窄异常或至少带上下文日志。
3. 在 `except` 中重新抛出时补 `raise ... from err`。
4. 只处理“真实错误”和低风险的 lint-correctness 问题，不要扩散成全目录重构。

自测命令：

```bash
ruff check \
  bt_api_py/containers/accounts/bitget_account.py \
  bt_api_py/containers/balances/upbit_balance.py \
  bt_api_py/containers/tickers/upbit_ticker.py \
  bt_api_py/containers/orders/order.py
```

初级测试验收：

1. 复跑上述 `ruff check`，确认这 4 个文件不再报 `F821`、`E722`、`B904`。
2. 如果开发补了测试，执行对应目标测试并截图结果。
3. 检查修复没有引入新的 `print()`。

验收标准：

- 目标文件中的明确正确性错误被消除。
- 至少覆盖到 `Bitget` 与 `Upbit` 对应修复点的验证路径。

### P3-T2 network/live 测试边界补标

目标：把依赖真实账号、真实网络或 IP 白名单的测试从确定性回归中清楚分离出来。

优先处理文件：

- [test_bt_api.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_bt_api.py)
- [test_live_binance_spot_request_data.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_live_binance_spot_request_data.py)
- [test_live_binance_swap_request_data.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_live_binance_swap_request_data.py)
- [test_live_okx_spot_request_data.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_live_okx_spot_request_data.py)
- [test_live_ib_web_request_data.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_live_ib_web_request_data.py)
- 一批 `test_okx_swap_req_*.py` 文件

开发动作：

1. 对调用 `read_account_config()` 的测试补显式 `pytestmark = [pytest.mark.integration, pytest.mark.network]` 或等价标记。
2. 对必须依赖凭据的场景，统一改成“缺配置就 `pytest.skip()`”。
3. 保持现有测试意图不变，只调整测试边界和跳过逻辑。

自测命令：

```bash
pytest tests -m 'not network' -q
pytest tests/feeds/test_live_binance_spot_request_data.py -m network --co
```

初级测试验收：

1. `pytest tests -m 'not network' -q` 结果不能比当前主干更差。
2. 用 `--co` 检查目标文件确实被标记为 `network`。
3. 人工确认“缺凭据时 skip，不是 fail”。

验收标准：

- 第一批高频 live/request-data 测试已完成网络边界补标。
- 确定性回归和真实网络回归的职责更清晰。

### P3-T3 脚本风格测试收敛第一批

目标：把一批高噪音测试从“打印脚本”改成“可重复的自动化测试”。

建议修改文件：

- [test_okx_swap_req_funding.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_okx_swap_req_funding.py)
- [test_okx_swap_req_account_config.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_okx_swap_req_account_config.py)
- [test_live_ib_web_request_data.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_live_ib_web_request_data.py)
- [test_gemini_integration.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/integration/test_gemini_integration.py)
- [test_hyperliquid_integration.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/integration/test_hyperliquid_integration.py)

开发动作：

1. 用明确断言替换“只打印不判断”的逻辑。
2. 把 `return True/False` 改成 `assert`、`pytest.skip()` 或 `pytest.raises(...)`。
3. 重复的打印输出收敛成测试辅助函数或调试注释，不保留在常规执行路径。
4. 只处理第一批高噪音文件，不要一次扫完整个 `tests/feeds/`。

自测命令：

```bash
rg '\bprint\s*\(' \
  tests/feeds/test_okx_swap_req_funding.py \
  tests/feeds/test_okx_swap_req_account_config.py \
  tests/feeds/test_live_ib_web_request_data.py \
  tests/integration/test_gemini_integration.py \
  tests/integration/test_hyperliquid_integration.py
pytest \
  tests/feeds/test_okx_swap_req_funding.py \
  tests/feeds/test_okx_swap_req_account_config.py \
  tests/feeds/test_live_ib_web_request_data.py \
  tests/integration/test_gemini_integration.py \
  tests/integration/test_hyperliquid_integration.py -q
```

初级测试验收：

1. 确认目标文件的 `print()` 数量明显下降。
2. 目标测试在当前环境下要么通过、要么合理 skip。
3. 不接受“仍然依赖人工肉眼读打印结果”的测试。

验收标准：

- 第一批高噪音测试已经具备自动化断言。
- 不再触发 `PytestReturnNotNoneWarning`。

### P3-T4 运行时日志噪音清理第一批

目标：清理最明显的生产路径 `print()`，把故障定位信息迁移到统一 logger。

建议修改文件：

- [request_base.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_binance/request_base.py)
- [spot.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_binance/spot.py)
- [my_websocket_app.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/my_websocket_app.py)

开发动作：

1. 把运行时 `print()` 改成 `_logger.debug/info/warning/error`。
2. 同类事件使用统一日志级别。
3. 尽量带上 `exchange_name`、`symbol`、`request_type` 等上下文。
4. 不要重写协议实现，不要顺手改 unrelated 行为。

自测命令：

```bash
rg '\bprint\s*\(' \
  bt_api_py/feeds/live_binance/request_base.py \
  bt_api_py/feeds/live_binance/spot.py \
  bt_api_py/feeds/my_websocket_app.py
pytest \
  tests/feeds/test_live_binance_spot_request_data.py \
  tests/feeds/test_live_binance_swap_request_data.py -q
```

初级测试验收：

1. 目标运行时文件生产路径 `print()` 清零。
2. 请求数据相关回归不出现新的失败。
3. 核对日志输出仍能定位到交易所、请求类型或 symbol。

验收标准：

- 第一批运行时噪音文件完成日志化改造。

### P3-T5 Core Services 生命周期测试补齐

目标：给核心服务补直接测试，降低后续异常治理的盲区。

建议修改文件：

- [services.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/core/services.py)
- `tests/core/test_services.py`（新增）

开发动作：

1. 给 `EventService` 增加 `start()`、`stop()`、`publish_async()`、`unsubscribe()` 的测试。
2. 给 `ConnectionService` 增加 `get_connection()`、`release_connection()`、`close_all()` 的测试。
3. 给 `CacheService` 增加“无 Redis 时回退本地缓存”的测试。
4. 如果发现某些分支无法观测，允许把静默行为改成可断言的状态或日志。

自测命令：

```bash
pytest tests/core/test_services.py -q
```

初级测试验收：

1. 连续执行两次 `pytest tests/core/test_services.py -q`，确认结果稳定。
2. 检查没有遗留 pending task、未关闭连接或事件循环告警。
3. 人工核对测试是否覆盖到 start/stop/release/fallback 四类动作。

验收标准：

- 新增核心服务直接测试文件。
- 核心生命周期路径具备可重复验证能力。

### P3-T6 文档与验收口径刷新

目标：让项目文档、测试现状和本轮交付口径重新对齐。

建议修改文件：

- [source-tree-analysis.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/source-tree-analysis.md)
- [testing_optimization_summary.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/testing_optimization_summary.md)
- [project-overview.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/project-overview.md)
- 本计划文档本身

开发动作：

1. 把代码和测试文件数量更新到当前真实值。
2. 修正“已完成”但和当前状态不一致的测试优化表述。
3. 在文档里显式区分“当前稳定事实”和“目标/愿景表述”。
4. 把本轮回归结果与允许保留的已知提示写清楚。

自测命令：

```bash
rg -n "201个测试文件|204 test files" docs
pytest tests -m 'not network' -q --maxfail=20
```

初级测试验收：

1. 文档中的关键数字与当前扫描结果一致。
2. 文档提到的测试状态能被命令复现。
3. 已知例外项写明原因，不允许模糊表述。

验收标准：

- 项目总览、测试现状和本轮计划对齐。

## 8. 初级工程师执行规则

1. 每个任务只做一个清晰目标，不要把 P3-T1 到 P3-T6 混进同一个 PR。
2. 每个 PR 最多改 `3~8` 个主要文件；超出就要拆分。
3. 当前仓库有历史脏改动，提交前只 `git add` 本任务文件。
4. 提交前必须看 `git diff -- <files>`，确认没有带入无关修改。
5. 如果需要改公开 API、目录结构或引入新依赖，先找高级工程师确认。

## 9. 初级测试人员统一验收口径

每个任务都按下面的顺序验收：

1. 先跑任务卡里的 `ruff check` 或 `rg` 命令，确认目标问题真的消失。
2. 再跑任务卡里的目标 `pytest` 命令，确认通过或合理 skip。
3. 最后补一轮 `pytest tests -m 'not network' -q`，确认主干没有被拖坏。

统一判定规则：

- 可以接受：`skip`，前提是写明缺凭据、缺网络或白名单受限。
- 不可以接受：依赖肉眼看 `print()` 输出判断结果。
- 不可以接受：新增裸 `except:`、静默 `pass`、生产路径 `print()`。
- 不可以接受：把网络问题伪装成业务逻辑成功。

## 10. 本轮整体退出标准

本轮计划完成时，至少满足：

1. `pytest tests -m 'not network' -q` 仍保持全绿。
2. P3-T1 目标文件不再报关键 correctness 类 lint 错误。
3. P3-T2 第一批 network/live 测试完成明确补标。
4. P3-T3 第一批高噪音测试完成断言化。
5. P3-T4 目标运行时文件生产路径 `print()` 清零。
6. P3-T5 新增核心服务直接测试。
7. P3-T6 文档中的关键数字和当前仓库状态一致。

## 11. 下轮候选，不纳入本轮强制范围

下面这些方向有价值，但不建议直接交给初级工程师在本轮独立推进：

- [monitoring.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/websocket/monitoring.py) 和 [advanced_connection_manager.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/websocket/advanced_connection_manager.py) 的宽泛异常治理
- [encryption_manager.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/security_compliance/core/encryption_manager.py) 与 [policy_engine.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/risk_management/core/policy_engine.py) 的失败契约补测
- 全仓 `ruff` 历史债务清零
- registry / container / feed 的跨层统一重构

这类工作建议等本轮质量基线收口后，再由中高级工程师牵头拆成下一轮计划。
