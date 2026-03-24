# bt_api_py 下一轮迭代计划

## 1. 文档目的

本文件用于承接当前仓库已有的稳定性路线图、代码质量治理计划和 BMAD 产物，形成一份更适合当前阶段审阅与执行的“下一轮迭代计划”。

这份计划强调三点：

1. 不再新增泛泛而谈的长期愿景，而是基于当前仓库证据给出下一轮最值得推进的改进主题。
2. 不与现有 `docs/plans/2026-03-16-stability-roadmap.md` 和 `docs/plans/2026-03-19-bt-api-py-code-quality-iteration-plan.md` 冲突，而是做范围收敛和优先级排序。
3. 方便后续评审后，直接拆成独立小批次继续迭代。

## 2. 来自 `bmad-help` 的当前判断

结合 `_bmad/_config/bmad-help.csv`、`_bmad/bmm/config.yaml`、`_bmad-output/` 和仓库内现有计划文档，可以判断：

1. 当前更适合使用 `bmm` 模块。
2. 仓库已经存在 `planning-artifacts`、`implementation-artifacts/sprint-status.yaml` 等产物，说明项目并不是“从零规划”，而是处于已有规划基础上的收敛与校正阶段。
3. 因此，当前最合理的 BMAD 路径不是重新做一轮大而全的前置规划，而是先做一次面向现状的校准，再进入小批次实施。

### 2.1 推荐的 BMAD 后续动作

- **可选**: `bmad-bmm-sprint-status`
  - Agent: 🏃 Bob (Scrum Master)
  - 用途: 汇总当前已有 sprint/实施状态，帮助判断本计划中的哪些项应优先落地。

- **可选**: `bmad-bmm-correct-course`
  - Agent: 🏃 Bob (Scrum Master)
  - 用途: 当前仓库已经有多份计划、路线图和改进报告，若你准备将本计划转成正式执行入口，建议先做一次 Correct Course，避免并行计划继续扩散。

- **如果沿正式实施流继续推进，下一项必经步骤**: `bmad-bmm-create-story`
  - Agent: 🏃 Bob (Scrum Master)
  - 用途: 将本计划中的单个主题拆成可实施 story，再交给开发工作流执行。

## 3. 本轮计划的输入证据

本计划基于以下事实形成：

1. 仓库已存在多个历史/并行计划文档，包括：
   - `docs/plans/2026-03-16-stability-roadmap.md`
   - `docs/plans/2026-03-19-bt-api-py-code-quality-iteration-plan.md`
   - `_bmad-output/planning-artifacts/optimization-execution-plan.md`
2. 仓库同时存在基础版与增强版 WebSocket 管理实现：
   - `bt_api_py/websocket_manager.py`
   - `bt_api_py/websocket/advanced_websocket_manager.py`
3. `pyproject.toml` 中虽然已配置 `ruff`、`mypy`、`coverage`，但仍保留较大面积的 `ignore_errors = true`，且全局 `disallow_untyped_defs = false`。
4. 仓库内已存在统一 `HttpClient`，但代码中仍可检索到多处 `requests` 直接调用。
5. GitHub Actions 中存在两套测试工作流：
   - `.github/workflows/tests.yml`
   - `.github/workflows/optimized-tests.yml`
6. `tests.yml` 的覆盖率门槛为 `60`，而 `pyproject.toml` 的覆盖率门槛为 `80`，存在质量门禁漂移。
7. README 中仍明确记录若干交易所测试存在 mock 目标路径问题、WebSocket 覆盖不足或测试资产不足。
8. 关键模块已有部分测试，但保护范围仍偏局部，尤其在生命周期、异常恢复和跨模块契约上仍有提升空间。

## 4. 这轮迭代要解决的核心问题

本轮不追求“把所有遗留问题一次性做完”，而是聚焦以下四类最影响后续开发效率和稳定性的主题：

1. WebSocket / Gateway 体系存在并行实现与生命周期复杂度，后续维护成本高。
2. 请求层已经开始统一，但尚未真正收敛，导致异常语义、mock 策略和测试稳定性仍不一致。
3. 质量门禁存在配置漂移，类型系统也仍处于“局部严格、整体宽松”的过渡状态。
4. 仓库已有较多计划和报告，如果不收敛，会持续降低后续执行清晰度。

## 5. 下一轮迭代目标

本轮目标定义为：

1. 收敛核心基础设施层的重复实现与职责边界。
2. 提高请求层、WebSocket 层和核心服务层的可验证性。
3. 对质量门禁做一次“标准统一”，让后续所有改进都能站在同一套基线上。
4. 把当前改进方向整理成单一主计划，降低文档和执行分叉。

## 6. 迭代主题与执行顺序

建议按照以下顺序推进。

### 主题 A：WebSocket / Gateway 架构收敛 ✅ 已完成

> **执行结果 (2026-03-24)**：
> - **架构结论**：`websocket/` 包（AdvancedWebSocketManager）为权威实现，`websocket_manager.py` 保留为 legacy 兼容层并添加弃用提示，`gateway/` 基于 ZMQ 独立运行、无需与 WebSocket 管理器收敛。
> - **异常收窄**：`advanced_connection_manager.py` 6处 `except Exception` 替换为 `ConnectionClosed/OSError/WebSocketException` 具体分支，消除 `"ConnectionClosed" in str(type(e))` 字符串匹配反模式。
> - **回归测试**：新增 `tests/websocket/test_lifecycle_regression.py`（12 个测试），覆盖 connect/disconnect 幂等、重连订阅恢复、压缩帧处理、错误分类计数。

#### 背景

当前至少存在两套路由相近但能力层级不同的 WebSocket 管理实现，且 `gateway/runtime.py`、监控与连接管理链路中仍有较多宽泛异常与复杂生命周期逻辑。继续在并行实现上叠加功能，会持续放大维护成本。

#### 目标

1. 明确 WebSocket 管理的权威实现路径。
2. 收敛连接生命周期、重连、心跳、关闭、订阅恢复的行为定义。
3. 提高异常路径的可见性和可测试性。

#### 建议处理文件

1. `bt_api_py/websocket_manager.py`
2. `bt_api_py/websocket/advanced_websocket_manager.py`
3. `bt_api_py/websocket/advanced_connection_manager.py`
4. `bt_api_py/websocket/monitoring.py`
5. `bt_api_py/gateway/runtime.py`
6. `bt_api_py/gateway/process.py`

#### 具体动作

1. 列出基础版与增强版 WebSocket manager 的职责差异、调用方和重复能力。
2. 选定一条主路径：
   - 要么保留基础实现作为兼容层，增强实现作为主实现。
   - 要么保留基础实现为稳定默认，增强实现拆为实验能力。
3. 补齐连接状态机文档，明确 `connect`、`disconnect`、`reconnect`、`restore_subscriptions` 的预期行为。
4. 替换最关键链路中的宽泛异常为更具体的错误分支，至少附带上下文日志。
5. 补最小回归测试，覆盖：
   - 文本帧/压缩帧处理
   - 重连后订阅恢复
   - 关闭过程幂等
   - gateway command loop 错误回包

#### 验收标准

1. WebSocket 主实现路径有明确结论，并在文档中写清。
2. 至少完成一组连接生命周期回归测试。
3. 不再出现“同类问题要改两个 manager 才能生效”的情况。

### 主题 B：请求层统一与测试确定性增强 ✅ 已完成

> **执行结果 (2026-03-23)**：22个交易所 `request_base.py` 全部收敛到 `_http_client.async_request`，11个模块补充 `extra_data` 防御性修复，新增 20 个异步回归测试，零残留 `async_http_request` 调用。

#### 背景

仓库已经引入 `bt_api_py/feeds/http_client.py`，说明项目正在朝统一请求层收敛。但代码中仍存在 `requests` 直接调用，README 也明确提到若干交易所测试失败与 mock 目标路径相关，说明“共享请求层 + 共享测试策略”尚未完成闭环。

#### 目标

1. 收敛高频交易所的请求客户端行为。
2. 统一 timeout、异常映射、错误 body 处理和 mock 入口。
3. 降低测试对真实网络和实现细节路径的脆弱依赖。

#### 建议处理文件

1. `bt_api_py/feeds/http_client.py`
2. 高频 `request_base.py` 文件：
   - `live_binance`
   - `live_okx`
   - `live_kucoin`
   - `live_kraken`
   - `live_mexc`
   - `live_coinbase`
3. 对应测试文件和共享 fixture

#### 具体动作

1. 盘点 remaining `requests` 直接调用，按访问频率和测试失败频率排序。
2. 优先让高频交易所请求层走统一客户端或兼容适配层，而不是继续各自维护完整请求细节。
3. 统一 `RequestFailedError`、认证错误、限流错误和 4xx/5xx 语义。
4. 对 README 中已知的 mock 目标路径问题，补出统一约定：
   - mock 应命中共享客户端还是交易所 request base。
   - 如何避免实现迁移后测试整体失效。
5. 整理共享 fixture/工厂，减少测试数据和 patch 方式分散。

#### 验收标准

1. 至少一批高频交易所请求层切换到统一策略。
2. 已知 mock 目标路径问题形成统一修复模式。
3. 请求失败路径的返回/异常契约更稳定。

### 主题 C：质量门禁统一与类型债务压缩 ✅ 已完成

> **执行结果 (2026-03-23)**：`optimized-tests.yml` 瘦身为 Extended CI（去掉重叠的 pr-check/full-test），覆盖率门槛确认对齐为 80，mypy `websocket.*` glob 冲突修复，`websocket/__init__.py` 结构 bug 修复，10 个类型错误清零（785 源文件零错误）。

#### 背景

当前仓库工具链完整，但质量门禁口径不统一：`pyproject.toml` 设定了 `coverage fail_under = 80`，而 GitHub Actions 的 `tests.yml` 仍使用 `60`。同时，`mypy` 配置中仍存在大量 `ignore_errors = true` 和较宽松的全局默认设置。

#### 目标

1. 让本地门禁、CI 门禁和计划文档中的质量标准对齐。
2. 将类型检查从“按点治理”推进到“按层治理”。
3. 优先压缩核心模块的类型豁免面，而不是盲目追求全仓 strict。

#### 建议处理文件

1. `pyproject.toml`
2. `.github/workflows/tests.yml`
3. `.github/workflows/optimized-tests.yml`
4. 核心模块：
   - `bt_api_py/bt_api.py`
   - `bt_api_py/registry.py`
   - `bt_api_py/websocket_manager.py`
   - `bt_api_py/core/services.py`

#### 具体动作

1. 明确覆盖率门槛以哪一套为准：
   - 如果短期保守执行，定义阶段性门槛与升级计划。
   - 如果以仓库标准为准，CI 应与 `80` 对齐。
2. 重新定义两套 CI 工作流的职责边界，避免重复跑、重复汇报或标准不一致。
3. 选择一批核心模块，将 `ignore_errors` 缩为更小范围。
4. 输出“模块级类型治理顺序”，而不是继续按文件零散补丁推进。
5. 把类型治理与测试补强绑定，避免出现“类型更严格但行为未被保护”的局面。

#### 验收标准

1. 本地与 CI 质量门禁口径一致。
2. 两套测试工作流职责清晰，至少减少一处重复定义。
3. 核心模块类型豁免面较当前明显缩小。

### 主题 D：文档与计划收敛 ✅ 已完成

> **执行结果 (2026-03-23)**：`README_IMPROVEMENTS.md` 重构为四层文档索引（当前计划 → 长期路线 → 活跃参考 → 历史归档），55+ 文档全部归类，当前主计划入口明确，历史文档折叠展示，不再新增平行计划。

#### 背景

仓库当前文档丰富，但“当前主计划”并不突出。`docs/README_IMPROVEMENTS.md`、`NEXT_STEPS.md`、历史计划文件、`_bmad-output` 中的规划产物同时存在，容易导致后续执行入口不清楚。

#### 目标

1. 形成一份明确的当前主计划。
2. 明确历史计划与当前计划的关系。
3. 让 README、改进索引、BMAD 产物和测试现状的描述保持一致。

#### 具体动作

1. 以本文件作为当前主计划入口。
2. 在 `docs/README_IMPROVEMENTS.md` 中增加本文件索引。
3. 标注哪些文档属于：
   - 当前执行计划
   - 长期路线图
   - 历史完成报告
   - 背景分析材料
4. 后续新一轮改进若不是替换当前主计划，不再新增同级“平行计划”。
5. 将 README 中交易所支持状态、测试结论和计划优先级尽量对齐到共享事实来源。

#### 验收标准

1. 任何新参与者都能快速看出“当前该从哪一份计划开始”。
2. 历史文档不删除，但关系更清晰。
3. 后续 planning review 可以围绕本文件直接做增删改，而不是另起炉灶。

## 7. 建议的分批执行方式

为了降低回归风险，建议拆成四个小批次推进：

### 批次 1

主题：WebSocket / Gateway 收敛审查与第一轮修复

交付：

1. WebSocket 权威路径结论
2. 生命周期问题清单
3. 第一批回归测试

### 批次 2

主题：请求层统一与 mock 稳定化

交付：

1. 高优先级交易所请求层收敛清单
2. mock 目标路径统一规则
3. 第一批失败测试修复

### 批次 3

主题：质量门禁统一与核心类型治理

交付：

1. CI 与本地门禁对齐方案
2. 工作流职责边界说明
3. 核心模块类型治理结果

### 批次 4

主题：文档收敛与下一轮 backlog 整理

交付：

1. 当前主计划入口稳定化
2. 历史文档关系说明
3. 下一轮 story/backlog 清单

## 8. 本轮建议暂不处理的事项

本轮不建议优先做以下内容：

1. CTP 生成代码的大规模重构。
2. 一次性统一所有 70+ 交易所实现。
3. 新增大型功能模块来掩盖基础设施问题。
4. 在尚未收敛 WebSocket / 请求层基础问题前，继续扩展更多实验性能力。

## 9. 建议追踪指标

建议本轮至少跟踪以下指标：

1. WebSocket / gateway 关键测试通过率。
2. 已收敛到统一请求层的高频交易所数量。
3. `mypy ignore_errors` 涉及模块数量变化。
4. CI 与本地覆盖率门槛是否已统一。
5. 历史计划文档是否已完成“当前 / 历史 / 背景”分层。

## 10. 审阅时重点关注的问题

后续审阅本计划时，建议优先确认以下问题：

1. WebSocket 体系是否真的需要双实现并存，还是应尽快收敛成主干 + 兼容层。
2. 请求层统一是否应先聚焦少数高频交易所，而不是追求全覆盖。
3. 覆盖率门槛应先按现状分阶段提升，还是直接对齐到 `80`。
4. 是否接受“本文件作为当前主计划”，并停止继续新增同级平行计划。

## 11. 推荐的下一步

如果要基于本计划继续往下执行，建议顺序如下：

1. 先运行一次 `bmad-bmm-sprint-status`，确认当前已有实施状态与阻塞点。
2. 再使用 `bmad-bmm-correct-course`，把本文件正式纳入当前执行上下文。
3. 评审通过后，把“主题 A”拆成第一批 story，优先治理 WebSocket / Gateway。
4. 第一批稳定后，再进入请求层统一与质量门禁对齐。

## 12. 结论

`bt_api_py` 当前最大的问题不是“缺少改进方向”，而是已经有足够多的方向、计划和产物，但仍需要一份更聚焦、更收敛、可直接评审落地的当前计划。

因此，下一轮最值得做的不是再扩写愿景，而是围绕以下四件事建立执行闭环：

1. WebSocket / Gateway 架构收敛。
2. 请求层统一与测试确定性增强。
3. 质量门禁统一与类型债务压缩。
4. 文档与主计划收敛。

本文件建议作为当前阶段的主迭代计划使用。
