# bt_api_py 初级工程师执行版迭代计划

日期：2026-03-08  
适用对象：初级工程师 / 校招生 / 新加入维护者  
计划周期：4 周  
目标：把上一轮分析得到的改进建议，拆成可以稳定交付的小任务

## 1. 这轮迭代要解决什么

这轮不是继续扩交易所数量，而是优先做工程治理，减少后续维护成本。当前最明显的问题是：

- 运行时错误处理不统一，`except Exception`、裸 `except:`、`pass` 过多
- 生产路径仍有不少 `print()`，日志不可观测
- 测试主干已经能过，但 async / integration / network 测试边界还不够清晰
- 文档和真实代码规模已经开始漂移

## 2. 初级工程师执行规则

### 2.1 任务边界

每个任务尽量控制在：

- `1` 个 PR
- `1` 个明确目标
- `3~8` 个主要文件
- 不跨太多子系统

### 2.2 必须遵守

1. 不要修改 CTP 二进制、SWIG 生成物、平台构建逻辑，除非高级工程师明确分配。
2. 不要把“网络失败 / IP 白名单失败”当成代码缺陷去硬修。
3. 每个任务都必须带测试或最少带回归验证命令。
4. 如果需要修改公开 API、异常类型、目录结构，先找高级工程师 review 方案再动手。
5. 当前仓库有大量历史脏改动；每次只 `git add` 本任务文件，提交前先看 `git diff -- <files>`。

### 2.3 升级条件

出现下面任意一种情况，停止自行扩改，交给高级工程师确认：

- 需要改动 `BtApi` 公开接口
- 需要同时改 `feeds`、`containers`、`registry` 三层以上
- 需要引入新第三方依赖
- 目标文件存在大量历史脏改动，无法确认安全范围

## 3. 交付标准

每个任务完成后，至少满足：

- 代码通过 `ruff check`
- 相关目标测试通过
- 没有引入新的 `print()` 到生产路径
- 没有新增裸 `except:` 和静默 `pass`
- PR 描述里写清楚“改了什么、怎么验证、剩余风险”

推荐本地验证顺序：

```bash
ruff check <modified_files>
pytest <related_tests> -q
pytest tests -m 'not network' -q
```

如果最后一步失败，但只剩网络或 IP 白名单类问题，需要在 PR 描述里明确说明。

## 4. 任务总览

| 任务ID | 任务名 | 优先级 | 预计工期 | 适合级别 |
|---|---|---:|---:|---|
| T1 | Async 测试告警清理 | P0 | 0.5 天 | 初级 |
| T2 | Integration 测试返回值写法清理 | P0 | 1 天 | 初级 |
| T3 | Binance/OKX WebSocket `print()` 清理 | P0 | 0.5 天 | 初级 |
| T4 | WebSocket 核心吞错治理第一批 | P0 | 2 天 | 初中级 |
| T5 | Monitoring 生命周期和测试收口 | P1 | 1.5 天 | 初中级 |
| T6 | Container 重复解析试点重构 | P1 | 2 天 | 初中级 |
| T7 | 项目文档与 project-context 刷新 | P1 | 1 天 | 初级 |
| T8 | 交易所能力矩阵与发布检查清单 | P1 | 1.5 天 | 初级 |

## 5. 推荐开发顺序

### 第 1 周

- T1 Async 测试告警清理
- T2 Integration 测试返回值写法清理
- T3 Binance/OKX WebSocket `print()` 清理

### 第 2 周

- T4 WebSocket 核心吞错治理第一批
- T5 Monitoring 生命周期和测试收口

### 第 3 周

- T6 Container 重复解析试点重构
- T7 项目文档与 project-context 刷新

### 第 4 周

- T8 交易所能力矩阵与发布检查清单
- 统一回归、补漏、文档修订

## 5.1 任务依赖和冲突管理

- T5 必须在 T1 合入后再开始，因为两者都会修改 [tests/test_monitoring.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_monitoring.py)。
- T8 必须在 T7 合入后再开始，因为两者都会修改 [docs/index.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/index.md)、[docs/project-overview.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/project-overview.md)、[docs/source-tree-analysis.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/source-tree-analysis.md)。
- T3 开始前先对目标文件跑一次 `rg '\\bprint\\s*\\('`；如果只有 [my_websocket_app.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/my_websocket_app.py) 仍有残留，就把 PR 范围收缩到真实残留文件。
- T7 涉及 [_bmad-output/project-context.md](/Users/yunjinqi/Documents/source_code/bt_api_py/_bmad-output/project-context.md)；如果该文件有生成来源，优先更新来源并同步产物；如果当前没有可复用生成入口，允许手工更新，但 PR 描述里必须注明。

## 5.2 任务与 Epic/Story 映射

| 任务ID | 对应 Epic / Story | 说明 |
|---|---|---|
| T1 | Epic 3 / Story 3.1 | async pytest 告警与 marker 治理 |
| T2 | Epic 3 / Story 3.1 | integration 测试表达方式规范化 |
| T3 | Epic 1 / Story 1.2 | 生产路径 `print()` 收敛到 logger |
| T4 | Epic 1 / Story 1.1 | websocket 核心吞错治理 |
| T5 | Epic 1 / Story 1.3 | monitoring 可选模块与生命周期契约 |
| T6 | Epic 2 / Story 2.2 | 容器层重复解析压缩试点 |
| T7 | Epic 4 / Story 4.1 | 项目文档与 project context 刷新 |
| T8 | Epic 4 / Story 4.2 + 4.3 | 能力矩阵与发布检查清单 |

执行要求：

- PR 描述里同时写 `任务 ID` 和 `Epic/Story ID`。
- 更新 [_bmad-output/implementation-artifacts/sprint-status.yaml](/Users/yunjinqi/Documents/source_code/bt_api_py/_bmad-output/implementation-artifacts/sprint-status.yaml) 时，按上表回写对应 Story 状态。

## 6. 详细任务卡

### T1 Async 测试告警清理

目标：消除 `pytest_asyncio` loop scope 警告，清理未被 pytest 正式管理的 async 测试。

建议修改文件：

- [pyproject.toml](/Users/yunjinqi/Documents/source_code/bt_api_py/pyproject.toml)
- [tests/test_monitoring.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_monitoring.py)

具体动作：

1. 在 pytest 配置中显式设置 `asyncio_default_fixture_loop_scope`
2. 检查 `tests/test_monitoring.py` 中的 async 测试
3. 该加 `@pytest.mark.asyncio` 的加上
4. 不能作为 async 测试存在的，改成同步测试

不要做：

- 不要顺手大改 monitoring 业务逻辑
- 不要引入新的测试插件

验收标准：

- `pytest tests/test_monitoring.py -q` 不再出现 async fixture loop scope 警告
- 原有测试通过

自测命令：

```bash
pytest tests/test_monitoring.py -q
```

### T2 Integration 测试返回值写法清理

目标：把 integration 测试里 `return True/False` 的写法改成标准 pytest 断言或 `pytest.skip()`。

建议修改文件：

- [tests/integration/test_gemini_integration.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/integration/test_gemini_integration.py)
- [tests/integration/test_kraken_integration.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/integration/test_kraken_integration.py)
- [tests/integration/test_mexc_integration.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/integration/test_mexc_integration.py)
- [tests/integration/test_bitfinex_integration.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/integration/test_bitfinex_integration.py)
- [tests/integration/test_hyperliquid.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/integration/test_hyperliquid.py)
- [tests/integration/test_hyperliquid_integration.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/integration/test_hyperliquid_integration.py)

具体动作：

1. 查找 `return True` / `return False`
2. 成功路径改成显式 `assert`
3. 环境不满足时用 `pytest.skip(reason=...)`
4. 预期失败时用 `pytest.raises(...)`

不要做：

- 不要把 live/network 测试直接删除
- 不要改测试目标行为，只改测试表达方式

验收标准：

- 这些文件不再触发 `PytestReturnNotNoneWarning`
- 对应测试在当前环境下要么通过、要么合理 skip

自测命令：

```bash
pytest tests/integration/test_gemini_integration.py \
  tests/integration/test_kraken_integration.py \
  tests/integration/test_mexc_integration.py \
  tests/integration/test_bitfinex_integration.py \
  tests/integration/test_hyperliquid.py \
  tests/integration/test_hyperliquid_integration.py -q
```

### T3 Binance/OKX WebSocket `print()` 清理

目标：把核心 WebSocket 路径中的 `print()` 迁移到统一 logger。

建议修改文件：

- [bt_api_py/feeds/live_binance/market_wss_base.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_binance/market_wss_base.py)
- [bt_api_py/feeds/live_binance/account_wss_base.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_binance/account_wss_base.py)
- [bt_api_py/feeds/live_okx/market_wss_base.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/live_okx/market_wss_base.py)
- [bt_api_py/feeds/my_websocket_app.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/my_websocket_app.py)

当前仓库快照下，前三个文件已经没有 `print()`；本任务默认只在回归时核对这三处，实际代码修改重点放在 [my_websocket_app.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/feeds/my_websocket_app.py)。

具体动作：

1. 识别运行时路径上的 `print()`
2. 改成 `_logger.debug/info/warning/error`
3. 同一类事件保持统一日志级别
4. 尽量附上 `exchange_name`、`symbol`、`topic` 等上下文

不要做：

- 不要清理脚本示例中的演示输出
- 不要顺手重写订阅逻辑

验收标准：

- 上述 4 个文件的生产路径 `print()` 清零
- 不影响现有 WebSocket 相关测试

自测命令：

```bash
rg '\\bprint\\s*\\(' \
  bt_api_py/feeds/live_binance/market_wss_base.py \
  bt_api_py/feeds/live_binance/account_wss_base.py \
  bt_api_py/feeds/live_okx/market_wss_base.py \
  bt_api_py/feeds/my_websocket_app.py
pytest tests/websocket/test_advanced_websocket.py -q
```

如需补跑 `pytest tests -m 'not network' -q`，放到 PR 最后统一回归，不作为本任务的首选自测。

### T4 WebSocket 核心吞错治理第一批

目标：清理 WebSocket 核心连接管理中的第一批高风险吞错点。

建议修改文件：

- [bt_api_py/websocket_manager.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/websocket_manager.py)
- [bt_api_py/websocket/advanced_connection_manager.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/websocket/advanced_connection_manager.py)
- [bt_api_py/websocket/monitoring.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/websocket/monitoring.py)

具体动作：

1. 优先找连接、重连、消息消费、队列处理附近的 `except Exception` / `pass`
2. 将静默失败改成：
   - 记录日志
   - 保留上下文
   - 必要时抛出 `WebSocketError` 或已有明确异常
3. 对“可忽略失败”写清楚注释和降级理由

不要做：

- 不要重构整个 websocket 子系统
- 不要同时改多个不相关适配器

验收标准：

- 这 3 个文件中的裸 `except:` 清零
- 关键路径静默 `pass` 改成“记录日志”或“写清楚降级理由”
- 修改后不新增新的 `except Exception`
- 相关测试无回归

自测命令：

```bash
pytest tests/websocket/test_advanced_websocket.py -q
pytest tests/integration/test_websocket_infrastructure.py -q
```

说明：第二条命令是网络集成测试；如果只因为网络或 IP 白名单失败，不阻塞该任务验收，但要在 PR 描述里明确记录。

### T5 Monitoring 生命周期和测试收口

目标：让 monitoring 模块的初始化、collector 使用和测试行为更稳定。

建议修改文件：

- [bt_api_py/monitoring/system_metrics.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/monitoring/system_metrics.py)
- [bt_api_py/monitoring/collector.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/monitoring/collector.py)
- [bt_api_py/monitoring/exchange_health.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/monitoring/exchange_health.py)
- [tests/test_monitoring.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/test_monitoring.py)

具体动作：

1. 避免隐式全局状态污染测试
2. 明确哪些 collector 是全局单例，哪些是测试内实例
3. 把 exchange health 的 async 测试整理到 pytest 可稳定执行的形式
4. 不要和 T1 放在同一个 PR 中提交

验收标准：

- monitoring 模块不再依赖测试执行顺序
- `tests/test_monitoring.py` 稳定通过

自测命令：

```bash
pytest tests/test_monitoring.py -q
```

### T6 Container 重复解析试点重构

目标：做一次小范围试点，证明容器层可以模板化收敛，不要求一次性铺开。

建议修改文件：

- [bt_api_py/containers/trades/coinbase_trade.py](/Users/yunjinqi/Documents/source_code/bt_api_py/bt_api_py/containers/trades/coinbase_trade.py)
- [tests/feeds/test_coinbase_integration.py](/Users/yunjinqi/Documents/source_code/bt_api_py/tests/feeds/test_coinbase_integration.py)

具体动作：

1. 抽出 `CoinbaseTradeData`、`CoinbaseWssTradeData`、`CoinbaseRequestTradeData` 的共享解析逻辑
2. 允许子类只维护字段差异
3. 不改变对外字段语义

不要做：

- 不要一口气扩散到所有交易所容器
- 不要引入代码生成器

验收标准：

- 三个 Trade 容器类共享同一个解析入口或共享 helper
- 相关测试保持通过
- 新增少量注释说明模板化模式

自测命令：

```bash
pytest tests/feeds/test_coinbase_integration.py -q
```

### T7 项目文档与 project-context 刷新

目标：让文档重新反映当前真实代码规模和模块布局。

建议修改文件：

- [docs/project-overview.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/project-overview.md)
- [docs/source-tree-analysis.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/source-tree-analysis.md)
- [docs/index.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/index.md)
- [_bmad-output/project-context.md](/Users/yunjinqi/Documents/source_code/bt_api_py/_bmad-output/project-context.md)

具体动作：

1. 更新文件数、测试数、模块列表
2. 把 `monitoring`、`risk_management`、`security_compliance`、`websocket` 纳入说明
3. 修正文档与当前实现不一致的部分
4. 如果只更新了生成产物，没有说明来源，PR 不能通过

验收标准：

- 文档中的关键统计与当前仓库一致
- 项目索引可以找到新增模块

自测命令：

```bash
find bt_api_py -type f -name '*.py' | wc -l
find tests -type f -name '*.py' | wc -l
rg "monitoring|risk_management|security_compliance|websocket" \
  docs/project-overview.md docs/source-tree-analysis.md docs/index.md _bmad-output/project-context.md
```

结果应与文档中的当前统计一致；如果历史文档中仍出现旧数字，需要在正文里明确“历史统计”标签，不能冒充当前口径。

### T8 交易所能力矩阵与发布检查清单

目标：补一份维护者可用的“交换所能力矩阵”和“发布前检查清单”。

建议修改或新建文件：

- [docs/exchanges/EXCHANGE_CAPABILITY_MATRIX.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/exchanges/EXCHANGE_CAPABILITY_MATRIX.md)
- [docs/release-checklist.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/release-checklist.md)

可参考文件：

- [docs/index.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/index.md)
- [docs/project-overview.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/project-overview.md)
- [docs/source-tree-analysis.md](/Users/yunjinqi/Documents/source_code/bt_api_py/docs/source-tree-analysis.md)

具体动作：

1. 形成能力矩阵列：
   - exchange
   - REST
   - WebSocket
   - tests
   - docs
   - risk note
2. 形成发布清单列：
   - lint
   - targeted tests
   - `not network` regression
   - docs updated
   - optional dependency notes
   - packaging sanity check

验收标准：

- 新维护者可以照着清单完成一次发布前检查
- 能从矩阵中快速找到文档缺口和测试缺口

自测命令：

```bash
rg "^#|REST|WebSocket|tests|docs|risk note" docs/exchanges/EXCHANGE_CAPABILITY_MATRIX.md -n
rg "^#|lint|targeted tests|not network|docs updated|optional dependency|packaging" docs/release-checklist.md -n
rg "EXCHANGE_CAPABILITY_MATRIX|release-checklist" docs/index.md -n
```

## 7. 建议任务分配

如果有 2 个初级工程师，建议这样分：

### 工程师 A

- T1 Async 测试告警清理
- T3 Binance/OKX WebSocket `print()` 清理
- T7 项目文档与 project-context 刷新
- T8 交易所能力矩阵与发布检查清单

### 工程师 B

- T2 Integration 测试返回值写法清理
- T4 WebSocket 核心吞错治理第一批

### 由高级工程师带着做

- T5 Monitoring 生命周期和测试收口
- T6 Container 重复解析试点重构

## 8. PR 要求

每个 PR 标题建议使用：

- `test: ...`
- `refactor: ...`
- `docs: ...`
- `chore: ...`

每个 PR 描述必须包含：

1. 任务 ID
2. Epic / Story ID
3. 修改文件
4. 验证命令
5. 是否存在网络/IP 白名单类未解决问题
6. 只暂存了哪些文件

## 9. 交付完成定义

本轮迭代完成，指的是：

- T1~T8 全部合入
- `pytest tests -m 'not network' -q` 持续通过
- 关键 async 告警与 pytest return 告警清理完成
- 生产路径 `print()` 显著下降
- 新文档和发布清单可直接给后续维护者使用

## 10. 对初级工程师的最后要求

你不是在“顺手清理代码风格”，你是在降低整个项目未来的维护成本。所以每个改动都要满足两个要求：

1. 改动范围可控，能解释清楚
2. 改完以后，别人更容易定位问题、更容易继续开发

如果做不到这两点，就说明任务拆得还不够小，需要继续拆。
