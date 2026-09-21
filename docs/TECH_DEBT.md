# 技术债与例外清单

> 迭代：`docs/迭代计划/迭代07-代码质量提升/`
> 基线快照：`docs/acceptance/2026-09-19-quality-ratchet.json`（**10 项**，初始 1301）
> format 基线快照：`docs/acceptance/2026-09-20-format-ratchet.json`（**15 仓 / 452 文件**）
> 最后更新：2026-09-21

本文件是**唯一台账**：lint 债、结构债、配置债、例外豁免、遗留测试都登记在此。
配置级 `ignore` / 路径例外必须同时在 `pyproject.toml`（如有）与本文件出现；精确行级 `noqa` 必须在源码行、
本文件和对应迭代设计/验收证据三处出现，且有可执行回归测试。

## 0. 总览

| 指标 | 迭代07 开始 | 现在 | 变化 |
|------|-----------|------|------|
| ruff 违规（完整门禁范围，34 路径） | **1301** | **10** | **-1291（-99.2%）** |
| 其中 `F821`（真实潜在缺陷） | 135 | **0** | 清零 |
| 其中 `W291` / `F401` / `I001` / `E701` / `F541` / `PIE790` | 712/164/154/36/21/9 | 全部 **0** | 清零 |
| 真实账号泄漏 | 9 处 | **0** | 清零 |
| 上版诊断子集（`-n 8`，显式排除旧工件失败） | 1321 passed / 882s | **1489 passed / 2 warnings / 42.97s** | 仅为根因定位留下的历史子集；不替代完整 marker，也不构成外部 CTP/SimNow 证据 |
| 本版完整根 marker（`PIP_NO_INDEX=1`） | 不适用 | **1735 passed / 2 warnings / 88.69s** | **PASS（checkout-local）**：同范围显式 `--cov-branch` 复跑为 **1735 passed / 2 warnings / 110.06s**，总覆盖率 **67.27%**，高于 40% 门槛。后续完整运行均未再出现 N-19 的退出后插件线程 warning，但单次历史观察仍保留 WATCH。dirty checkout 含未跟踪测试；该证据不等于 clean-checkout、外部 CI 或发布证明 |
| okx mixins 目录 | 44 文件 / 20,369 行 | **20 文件 / 17,129 行** | 合并机械切分 + 样板收敛（API 零变更） |
| L1 五仓 mypy 错误 | 416（binance 392 / okx 17 / bybit 3 / gateio 4） | 历史收敛为 **0**；当前 handoff 候选 **3** | `type-check-l1` 当前因已持久化 OKX 候选的 `registry_registration.py:120,174` 缺注解与 `market_wss_base.py:906` 的 `int(Any | None)` 失败；root lint 与主包 mypy PASS，修复后须复核 L1 |
| 公共 API 指标 | 无固定分母 | docstring **594/691（85.96%）**；参数注解 **1016/1035（98.16%）** | `make public-api-quality`，2026-09-20 |
| 主包覆盖率（CI 标记，`-n 8`） | 历史 66.29% | **67.27%** | **1735 passed、2 warnings、110.06s**；statement + branch 口径，满足 55% 阶段目标，`pyproject` 默认门槛维持 40% |
| 子仓 format 债（Ruff 0.16.2） | 无可机读逐仓基线 | **453 文件 / 15 仓** | 快照基线为 452；已持久化 OKX handoff 候选为 **87 > 86**，`make format-ratchet` 正确失败。不得用更新基线掩盖该回退，也不是 format 已清零的证据 |
| 子仓 Bandit 静态预检（默认 profile + `--ignore-nosec`） | 无独立基线 | **26 项**（High 1 / Medium 3 / Low 22） | 仅为当前 checkout 的源码预检；CTP `B507` High 未清零前不得建立绿色 required 基线 |
| 根仓渐进 Ruff ignore 债（2 条选定规则） | 无可机读盘点 | 最近完整报告 **37 项** | Tasks37–41 仅有目标 TC 零新增证据，未取得 fresh 全范围库存；该历史数不计入 10 项子仓 lint 棘轮余量，也不等于已清零 |

> **2026-09-20 工件分类更正**：缺少 `bt_api_base` 是旧三项失败的首个可见症状，不是“只需一个 base wheel”的完整闭包证明。当前测试从本地 source 构建 base wheel，并从已安装的本地 distributions 临时 materialize 构建/运行时闭包；生产 validator/verifier 只消费调用方显式传入的绝对 wheelhouse。测试临时 `pytest-socket` 仅锁定最小 socket-plugin 契约，不替代真实第三方归档工件的发布验收。

## 1. 状态说明

| 状态 | 含义 |
|------|------|
| DONE | 已修复并有证据 |
| DOING | 本迭代进行中 |
| TODO | 已登记待办 |
| WONTFIX | 明确不修（需理由） |

## 2. lint 债（ruff，按规则）

| ID | 规则 | 初始 | 现状 | 状态 | 处理方式 |
|----|------|------|------|------|---------|
| L-01 | `F821` undefined-name | 135 | 0 | DONE | 补缺失 import / 自引用修正 / 删死代码（见 §5） |
| L-02 | `W291` trailing-whitespace | 712 | 0 | DONE | `ruff --fix` 665 处 + AST 定位 docstring 内 266 处精确清理 |
| L-03 | `F401` unused-import | 164 | 0 | DONE | `ruff --fix` 144 处 + 20 处改文件级 noqa（原指令写在 docstring 内无效） |
| L-04 | `I001` unsorted-imports | 154 | 0 | DONE | `ruff --fix` |
| L-05 | `E701` multiple-statements | 36 | 0 | DONE | 脚本拆分，**每文件 AST 等价性校验**通过 |
| L-06 | `F541` f-string 无占位符 | 21 | 0 | DONE | `ruff --fix` |
| L-07 | `PIE790` unnecessary-pass | 9 | 0 | DONE | `ruff --fix` |
| L-08 | `UP006`/`UP035`/`UP041` | 4/1/2 | 0 | DONE | `ruff --fix` |
| L-09 | `S106` hardcoded-password（测试桩） | 45 | 3 | TODO | Bitget×2、Bybit×1：按实施计划 Task 10-A 改为具名测试占位常量；Bitget 联合测试当前缺 `bt_api_bequant`，状态为 NOT_RUN，CI 须使用可追溯、锁定 sibling SHA 的 BeQuant/Coinbase 工件并核验导入来源，不能用父仓 ignore 或偶然 editable 安装掩盖 |
| L-10 | `TC002` | 4 | 4 | TODO | OKX 的 Self×2 与插件参数类型×2 已判定为候选 type-only import；实施时须保留 PluginInfo 运行时导入，并新增插件入口离线回归 |
| L-11 | `F841` unused-variable | 3 | 0 | DONE | 删除 examples 中未使用的异常绑定和仪表盘变量 |
| L-12 | `UP042` str+Enum | 3 | 0 | DONE | Python 3.11+ 的 Mock 枚举改用 `StrEnum` |
| L-13 | `B007`/`S501`/`SIM105` | 1/1/1 | 0 | DONE | 改用 `_` 循环变量、显式 TLS 配置、`contextlib.suppress` |
| L-14 | `B017`/`E402` | 2/2 | 2/1 | TODO | Binance/OKX 的 MockTransport 在内层注入 `httpx.ReadTimeout`，但公开边界断言须收窄为 base 映射后的 `RequestTimeoutError`；IB Web 须把 OrderStatus 移回动态加载前。位置、基线 SHA 与测试门见实施计划 Task 10-A |
| L-15 | `S603` | 1 | 0 | DONE | E-05：固定 CTP/SWIG 隔离子进程收敛至专用 helper；命令静态、环境键白名单、输出脱敏且有行为测试 |

> 剩余 10 项均位于子仓，已进入棘轮基线，**只降不升**；任何新增违规都会让 CI 失败。每项的 SHA、最小修复和验收门已在实施计划 Task 10-A 固化；该清单不是子仓变更已完成的声明。

### 2.1 根仓渐进 Ruff ignore 债（只读盘点）

`make tech-debt-report` 固定扫描 `bt_api_py`、`tests`、`scripts`、`examples`，并且只选择下表 2 条
根 `pyproject.toml` 渐进 ignore。它先验证规则和行内理由，再以固定 argv 调用 Ruff JSON；缺规则/理由、
异常退出、坏 JSON 或意外规则一律失败。它**不扫描子仓、不改文件、不自动修复**。最近一次完整报告的
历史证据为 37 项；Tasks37–41 未运行 fresh 全范围报告，只有目标 TC 零新增证据。该历史库存并不与完整门禁
棘轮的 10 个子仓项相加或相互替代。

| ID | 规则 | 最近完整报告 | 根配置行内理由 | 状态 | 后续边界 |
|----|------|-----:|----------------|------|----------|
| G-07 | `TC001` | 22 | `deferred annotations make many runtime imports appear type-only` | DOING（2026-09-20 完成 22/22 审计） | 全部受运行时注解解析契约保护；保持直接导入。若要变更，先完成兼容 API 设计并纳入持久回归 |
| G-08 | `TC003` | 15 | `gradual cleanup for stdlib type-only imports` | DOING（历史 15 项已审计；本轮 3 个新增测试命中安全收敛，1 个脚本运行时依赖保留） | 反射可见项受运行时注解解析契约保护；不得以别名或全限定名规避 Ruff。测试注解须证明无运行时用法；脚本公共注解若被运行时反射，直接导入继续如实计入库存 |

> 已关闭：G-02 `S112` 的两个根脚本命中已以 mock 行为契约保护后改为只记录浏览器名和异常类型的 debug 诊断；隔离扫描为 0，规则已从根全局 ignore 与报告固定规则中移除。CTP vendor 路径的范围化 per-file `S112` 例外保持不变。

> 已关闭：G-05 `PERF403` 的唯一根仓命中已在 `LogstashHandler.format_to_logstash` 以离线 payload 行为契约保护后改为字典推导式；隔离扫描为 0，规则已从全局 ignore 与报告固定规则中移除。该变更不触及外部 ELK 连接。

> 已关闭：G-03 `PERF401` 的最后 7 个根命中均先以纯内存、临时目录或 dry-run 行为契约固定后收敛；`ruff check bt_api_py tests scripts examples --isolated --select PERF401` 为 0，规则已从根全局 ignore、`tests/*.py` per-file ignore 和报告固定规则中移除。

> 已关闭：G-01 `S110` 的最后 9 个根命中（OKX request/WSS 清理、Hyperliquid 示例与 WebSocket 接收）先以 fake-only 行为契约锁定；每条诊断只记录异常类型，保留 best-effort 清理或继续接收语义。`ruff check bt_api_py tests scripts examples --isolated --select S110` 为 0，规则已从根全局 ignore、`tests/*.py` per-file ignore 和报告固定规则中移除；CTP vendor 的范围化 per-file `S110` 例外保持不变。

> 类型导入清理的附加边界：凡可能被运行时反射的注解，除目标 Ruff 检查外还必须通过 `typing.get_type_hints` 回归。F-55/F-64 已证明仅凭延迟注解、私有命名或局部测试均不足以支持移入 `TYPE_CHECKING`；为保持既有注解 API，相关直接导入已恢复，`tests/test_type_hint_introspection.py` 当前覆盖 63 个目标。2026-09-20 的逐项复核确认当时 G-07/G-08 **37/37** 均在该边界内；F-86 只清理此后出现且经独立证明不进入反射面的 3 个测试注解，脚本公开 `Iterable[str]` 经真实 `get_type_hints` 证明属于运行时依赖后保留直接导入并继续计入库存。不允许用别名或全限定名规避诊断、虚假清零。该反射回归文件在当前 checkout 仍为未跟踪状态，只有被显式纳入交付后才是持久保护；G-07/G-08 仍不得标为 DONE。

> 更新这些数值只能重跑报告；任何实际清理都必须另有最小回归，不能通过扩大 `ignore`、新增 `noqa` 或重写报告基线来降低计数。

#### 已闭环的零存量全局例外

| ID | 规则 | 清理前实测 | 状态 | 证据与后续边界 |
|----|------|------------|------|----------------|
| G-04 | `PERF402` | 0 | DONE | 已从根全局 ignore 与 `tests/*.py` per-file ignore 移除；`ruff check tests --isolated --select S113,PERF402` 和根源码/脚本/示例同命令均为 0。今后新增命中直接进入 lint |
| G-06 | `S113` | 0 | DONE | 同上；不再是报告规则，不以新增配置例外代替 requests timeout 的逐项治理 |
| G-03 | `PERF401` | 7 | DONE | F-47/F-54/F-56/F-57/F-58/F-60 的等价行为契约覆盖后，根、测试、脚本和示例的隔离扫描均为 0；已移除根及 `tests/*.py` ignore 和报告规则 |
| G-01 | `S110` | 9 | DONE | F-61 的 fake-only 契约覆盖后，根、测试、脚本和示例的隔离扫描均为 0；已移除根及 `tests/*.py` ignore 和报告规则，CTP vendor 的范围化 per-file 例外保留 |

## 3. 结构债

| ID | 项 | 现状 | 状态 | 处理方式 |
|----|----|------|------|---------|
| S-01 | `bt_api_py/bt_api.py` | 8864 行 | TODO（代码拆分） | 方案卡已完成（M5-Task14）；按方案卡结论本期不执行，实际拆分留待独立交付 |
| S-02 | `bt_api_py/_execution_session.py` | 8251 行 | TODO（代码拆分） | 同上 |
| S-03 | `bt_api_ctp/.../ctp/client.py` | 4002 行 | TODO（代码拆分） | 同上 |
| S-04 | `bt_api_py/_normalization.py` | 2511 行 | TODO（代码拆分） | 同上 |
| S-05 | `bt_api_ctp/.../live_ctp_feed.py` | 2025 行 | TODO（代码拆分） | 同上 |
| S-06 | 超长函数（>150 行） | 基线 42；当前主包 AST 复算 **16**（Task15 五项均已脱离清单；追加 `migrate_execution_journal` **361 → 128**） | DOING | Task15 **5/5** 已完成；迁移原子性切片已完成。其余 16 个候选均位于 dirty/high-risk 执行、CTP 或恢复路径，暂不强行修改；另以干净后备项完成 `coerce_funding_snapshot` **81 → 28** 的职责拆分，但不把它计入 >150 清单收缩 |
| S-07 | `scripts/analysis/` 与 `scripts/` 顶层 8 个脚本**字节级重复** | 顶层 8 个 canonical 实现 + 8 个薄兼容入口 | **DONE** | 保留旧 CLI/import 路径，不删除；wrapper 私有化自身基础设施名称，惰性转发 canonical 已有属性的读取、写入及删除/恢复，CLI 经 `runpy` 执行 canonical。顶层 8 文件 SHA256 前后不变，见 F-83 |
| S-08 | 子仓机械切分文件族（`*_partN` / `*_mixin`） | okx 25 个 part 文件 | **DONE** | 已合并回 8 个逻辑模块（见 F-13）；其余子仓同类文件待评估 |
| S-09 | okx mixins 端点样板（3 方法/端点） | 已收敛 514 个方法 | **DONE** | 见 F-13；余下 ~90 builder / ~183 包装因形态不同保留原样（见 F-13 备注） |

## 4. 例外清单（Explicit Waivers）

| ID | 范围 | 理由 | 登记位置 | 复核 |
|----|------|------|---------|------|
| E-01 | `bt_api_ctp/**/ctp_structs_*.py`、`ctp_constants.py`、`ctp_wrap.*`、`ctp/ctp.py` | SWIG/结构体字段映射，属生成/半生成代码 | 本文件 | 每迭代 |
| E-03 | `examples/network_tests/moved_ctp_feed.py` | 已迁移提示文件，模块级 `pytest.skip` 主动禁用（原 631 行死代码已精简为指针） | 本文件 | 每迭代 |
| E-04 | 4 个 `examples/network_tests/integration/test_*_integration.py` | 导入可用性验证：import 本身即被测对象 → 文件头 `# ruff: noqa: F401` | 文件头 + 本文件 | 每迭代 |
| E-05 | `bt_api_py/_ctp_probe.py` 的 `subprocess` import（B404）和唯一 `subprocess.run`（S603/B603） | CTP/SWIG 必须在子进程隔离；固定 `sys.executable -c` 命令和静态脚本、无 shell、调用方仅能写入 6 个固定环境键，返回输出会脱敏。`tests/test_isolated_trader_probe.py` 以 monkeypatch 证明这些不变量 | 源码行级 `# nosec B404` / `# noqa: S603 # nosec B603` + 本文件 + 迭代07设计/验收 | 每迭代及改动此 helper 时 |
| E-06 | `scripts/ci/check_format_ratchet.py` 的 `subprocess` import（B404）和唯一 `subprocess.run`（S603/B603） | format 棘轮必须调用固定的 Ruff argv；路径仅由仓库内模块名派生、cwd 固定为仓库根、`shell=False`，异常仅转换为本地扫描失败 | 源码行级 `# nosec B404` / `# noqa: S603 ... # nosec B603` + 本文件 + 迭代07设计/验收；`test_ruff_process_uses_fixed_root_and_never_a_shell` 证明固定 argv/cwd/无 shell | 每次改动该脚本时 |
| E-07 | `scripts/ci/render_tech_debt.py` 的 `subprocess` import（B404）和唯一 `subprocess.run`（S603/B603） | 只读盘点必须调用固定的 Ruff JSON argv；范围、cwd 和 `shell=False` 固定，配置缺项、异常退出、坏 JSON 或意外规则均 fail closed | 源码行级 `# nosec B404` / `# noqa: S603 ... # nosec B603` + 本文件 + 迭代07设计/验收；`test_invocation_uses_fixed_argv_root_cwd_and_no_shell` 与 JSON/配置行为测试 | 每次改动该脚本时 |

## 5. 本迭代已修复（含证据）

| ID | 问题 | 修复 | 证据 |
|----|------|------|------|
| F-01 | OKX 机械切分丢 import：95 处 `F821`（运行时 `NameError`） | 87 处改自引用本地类；2 处跨 Mixin 改函数内惰性导入 | `F821 → 0`；14 个模块导入成功；跨 Mixin 属性可解析 |
| F-02 | `bt_api_binance/feeds/rest_market.py` 14 处 `F821`（`Any` 未导入） | 补 `from typing import Any` | `F821 → 0` |
| F-03 | `examples/risk_management_root_demo.py` 使用 `asyncio` 未导入 | 补 `import asyncio` | `F821 → 0` |
| F-04 | `moved_ctp_feed.py` 631 行墓碑内死代码（31 处 `F821`）引用已移除的 `bt_api_py.ctp` 路径 | 保留修正后的指针，删除 625 行重复死代码（其测试已存在于 `bt_api_ctp/tests/test_ctp_feed.py`） | 三类测试类在迁移目标文件中核对一致 |
| F-05 | 真实 CTP 账号 `089763` 泄漏在 tracked 测试（9 处） | 替换为占位 `test_account` | `git grep 089763 → 0`；该测试 3 passed |
| F-06 | `pyrightconfig.json` 引用不存在的 `bt_api_base/`、`packages/` | 修正为真实路径 | 路径存在性校验通过 |
| F-07 | 根 `AGENTS.md` 是从全栈项目复制的模板（声称存在前端/后端项目），引用空的 `.joyincode/rules/*` | 重写为项目真实规范索引 + 门禁说明 + 测试并行要求 | 引用的所有文件均存在 |
| F-08 | `examples/risk_management_root_demo.py` 在**同步函数内 `await`**（CPython 无法编译；ruff 解析器宽容故未报） | 改为 `async def main()` + `asyncio.run(main())`，与同类 4 个示例一致 | `compileall` 全 scope 通过 |
| F-09 | 4 个示例集成测试的 `# ruff: noqa` 写在 **docstring 内**（无效） | 移到文件顶部成为真正的文件级指令 | `F401 → 0` |
| F-10 | `examples/.../test_mexc_integration.py`、`scripts/*/analyze_code_quality.py` 硬编码失效绝对路径 `/Users/.../source_code/bt_api_py` | 改为由 `__file__` 推导 | 路径存在性校验通过 |
| F-11 | `pyproject.toml` 中 2 条 per-file-ignores 指向**不存在的路径** | 替换为真实模式（子仓 tests / examples 的 S106） | 死配置清除 |
| F-12 | `tests/bt_api_contract/test_execution_arming.py` 参数化 ID 内嵌 `time.monotonic_ns()` → 各 xdist worker 收集结果不一致，**阻塞 `-n` 并行** | 加显式静态 `ids=[...]` | `pytest -n 8 --collect-only` 无冲突；全量 `-n 8` 88s 通过 |

| F-13 | okx `feeds/live_okx/mixins` 结构劣化：25 个按行数机械切分的 `*_partN` 文件；每个端点手写 3 个方法（builder + 同步包装 + 异步包装），约 39 行里 35 行是样板 | ① 合并 part 文件回 8 个逻辑模块；② 新增 `RestCallMixin`（`_finish`/`_rest`/`_rest_async`），514 个方法收敛为一行委托；③ 保留全部方法与签名（测试会直接调用 builder/normalizer） | 44 → **20 文件**、20,369 → **17,129 行**；**方法清单对账 916 → 916（0 丢失 / 0 新增 / 0 签名变化）**；okx 292 passed、bt_api_base 669 passed、根 1321 passed |
| 备注 | 迁移脚本留在子仓 `scripts/`：`merge_mixin_parts.py`、`collapse_endpoints.py`（严格模板匹配，形态不同即跳过） | 未收敛的 ~90 builder（尾部键序/形态不同）与 ~183 包装（内层调用为 `**extra_data` 展开等变体）保留原样，属已知余量 | — | — |

| F-14 | L1 五仓类型检查：`binance` 392 / `okx` 17 / `bybit` 3 / `gateio` 4 个 mypy 错误，长期未纳入门禁 | ① binance 384 个同源错误用 `feeds/host_mixin.py`（`TYPE_CHECKING` 声明宿主属性/方法，运行时不定义、不经 MRO 遮蔽）一次解决；② 余下逐项修正注解；③ 新增 `make type-check-l1` 并接入 CI | **416 → 0**；binance 测试对照 165 failed/432 passed **与改动前完全一致**（零回归） |
| F-15 | **真实缺陷（mypy 抓出）**：`bt_api_bybit/errors/bybit_translator.py` 用 `str(ret_code) in cls.ERROR_MAP` 查询**整型键**字典，恒为 False —— `ERROR_MAP` 是死代码，bybit 错误翻译从未生效 | 改为按整型查（容忍字符串数字） | `comparison-overlap` 错误消失；bybit 34 项测试通过（原测试只断言异常类型，修复后翻译更精确） |
| F-16 | **同类真实缺陷（mypy 抓出）**：okx `request_base.signature` / `get_header` / `translate_error`、`market_wss_base.sign`、`spot._get_index_price` 均声明 `-> None` 却返回真实值 | 修正返回类型注解（调用方一直在用返回值） | okx mypy 归零；292 项测试通过 |

| F-17 | **真实缺陷**：`bt_api_binance/environment.py` 的 `configure_environment` 对任何非 SPOT/SWAP 资产（WALLET/MARGIN/COIN-M/OPTION 等 10 类）一律抛 `ValueError`——但该函数只在 demo/testnet 下才需要改写端点，**生产环境被一并拒绝**，导致 72 项测试失败 | 资产类型检查移入非生产分支；未知资产用**自身默认 URL 的路径**做安全校验（保留 `_override` 的全部防注入校验）；未配置的端点（如钱包无私有 WS）跳过 | binance 离线套件 `165 → 93 failed`；钱包/保证金 30 项全过 |
| F-18 | `test_live_binance_margin_wss_data.py` 8 项失败：测试已 mock `wss_author`（本意离线），但 `__init__` 的凭据守卫**先于 mock 生效**，构造期即抛错 | 按测试自身意图补占位凭据（不联网） | 该文件 `8 → 8 passed`；离线过滤套件 `0 failed` |
| F-19 | examples 中 10 项低风险 Ruff 债（无用变量、旧式 `str, Enum`、取消任务处理、TLS 配置文字量） | 删除无用状态；Mock 枚举改 `StrEnum`；仅当配置关闭 TLS 校验时禁用对应警告；以 `contextlib.suppress` 等价等待已取消任务 | 定向 Ruff 仅余既有 `S603`；7 个变更文件 `compileall` 通过；未以 `noqa` 掩盖问题 |
| F-20 | AC-9 原先只能引用全树诊断，测试/示例/私有模块混入分母，无法证明公开 API 覆盖率 | 新增 `scripts/measure_public_api_quality.py`、5 个 fixture 回归测试与 `make public-api-quality`；固定源级公共 callable 规则，JSON 含 schema、逐文件与实际排除项 | 指标测试 **5 passed**；docstring **594/691（85.96%）** ≥85%，参数注解 **1016/1035（98.16%）** ≥45%；L1 mypy 五仓均通过；后续根回归 **1328 passed** |
| F-21 | 5 个 Curve/Raydium/SushiSwap 示例测试仍导入在 `77d96e90` 删除的旧容器/DEX feed 实现，收集时必然失败 | 正式退役 5 个不可执行测试模块及 3 个包标记；新增 AST 回归测试，只拒绝这 6 个已移除的模块前缀 | 删除前测试准确检出 **11** 个导入；删除后 `tests/test_legacy_dex_example_retirement.py` **1 passed**，`compileall examples/network_tests` 通过 |
| F-22 | SimNow 多环境探针的 `S603`/`B404`/`B603` 位于示例测试内，虽然命令固定却缺少边界、输入和泄漏证明 | 提取为内部 `bt_api_py/_ctp_probe.py`：命令/脚本/工作目录固定，空输入在创建子进程前拒绝，账号字段仅经固定环境键传递，stdout/stderr 脱敏；示例保留原 `(env_key, ready, details)` 契约 | `tests/test_isolated_trader_probe.py` **8 passed**（纯 monkeypatch）；定向 Ruff/format/compileall 与 `make security-scan` 通过；`make quality-ratchet` 将快照 **11 → 10**；未启动 CTP/SimNow |
| F-23 | OKX `normalize_order_readiness` 为 198 行，混合 checks、reasons 与最终判定，影响只读 preflight 的审阅边界 | 只将 checks/reasons/判定提取到私有 `_build_okx_readiness_assessment`；入口签名、输出、键序、上游调用顺序以及 `None`/`False` 语义保持不变 | AST **198 → 139**；OKX 行为契约 **41 passed**；根并行回归 **1336 passed、2 warnings、74.00s**；Ruff/format/mypy/security-scan 通过，未发起任何外部连接 |
| F-24 | Binance `normalize_order_readiness` 为 180 行，混合 checks、reasons 与最终判定；另发现 ZMQ 启动失败测试修改共享 `threading.Thread`，会让未初始化的 forwarding loop 在 `-n 8` 下死等 | Binance 先补失败/短路行为契约，再只提取私有 `_build_binance_readiness_assessment`；ZMQ 测试改为替换 `service_module.threading` 局部绑定，并追踪 command server/publisher 清理 | Binance AST **180 → 143**、契约 **43 passed**；ZMQ 单例在 `--timeout=30` 下通过且整文件 **18 passed**；新鲜根并行回归 **1338 passed、2 warnings、76.11s**，未启动外部连接 |
| F-25 | `_ctp_budget.evaluate_ctp_budget` 为 170 行，混合可达状态扫描、coverage、PnL/资金/expiry 与结果构造；虽无 I/O，却处于 fail-closed 预算准入边界 | 先以纯 fixture 固定完整 ordinary/recovery 数字、状态扫描 reason 首次出现顺序、余额不足/未知估值/过期；随后只提取 `_evaluate_reachable_states`，顶层 validation 与最终计算保留入口 | AST **170 → 128**；预算直接契约 **4 passed**，加 arming/recovery 调用链 **133 passed、5.75s**；新鲜根标记回归 **1338 passed、2 warnings、76.25s**；未启动外部 CTP/SimNow |
| F-26 | `_normalization.normalize_event` 为 181 行，混合 event kind 推断与多个 mapper；其中 orderbook fields 独立但 sequence/continuity 错误会影响消费方丢包识别 | 先固定价量列表的 strict 配对/数值化、`action=update` 与未知 continuity；随后只提取 `_orderbook_event_fields`，保持 dispatcher 的其他 kind 与 `_finish_event` 边界 | AST **181 → 134**；orderbook 契约 **7 passed**，加 dispatcher 调用链 **171 passed、7.16s**；新鲜根标记回归 **1339 passed、2 warnings、75.51s**；未启动外部连接 |
| F-27 | `BtApi.get_order_readiness` 为 157 行，混合公共 guard、账户配置、OKX 路径与 Binance 多阶段只读编排；读取顺序或 `extra_data` 复用错误会改变 preflight 证据 | 先固定完整 Binance 成功路径的六次读取顺序、参数和嵌套 `extra_data` 深拷贝隔离；随后仅提取 `_get_binance_order_readiness`，保留公开 guard、账户配置、OKX 和 `normalize_error` 外壳 | AST **157 → 104**；新增契约先在原实现 **1 passed、4.99s**，完整 readiness **44 passed、5.27s**；新鲜根标记回归 **1340 passed、2 warnings、76.11s**；未启动网络、CTP 或 SimNow |
| F-28 | 子仓 format 仅有 report-only 矩阵，452 个存量可在未被关注时继续增长；单个仓下降无法阻止另一仓回退 | 新增 `check_format_ratchet.py`、15 仓/452 文件 JSON 快照、Makefile target 与阻塞 `format-ratchet` job；`quality-gate` 显式检查结果。逐仓增长、新仓/缺失仓 fail closed，更新只接受无增长且至少一仓下降 | 专属更新/工作流契约 **23 passed**；Ruff check/format 与脚本 Bandit 通过；本机直接扫描和 `make format-ratchet` 均复算 **452**，变更后的根并行回归 **1363 passed、2 warnings、75.95s**。不运行 `ruff format`、不修改子仓、也不将 report-only 或 format 清零宣称完成 |
| F-29 | 根 `pyproject.toml` 的渐进 Ruff ignore 只能靠手工说明，无法把配置理由与实时数量一起复核 | 新增只读 `render_tech_debt.py`、13 项行为测试和 `make tech-debt-report`；脚本校验仍在全局 ignore 的规则/行内理由并固定 Ruff JSON 扫描范围。先以隔离扫描确认 `PERF402`/`S113` 均为 0，再从根与测试路径例外移除 | 定向测试 **13 passed**；Ruff check/format、单脚本 Bandit 与 `make tech-debt-report` 通过；此前 6 条实测 **86** 项，实际治理仍须逐条交付 |
| F-30 | `PERF403` 的唯一根仓命中位于 Logstash payload 的附加字段循环，若机械改写可能改变保留字段过滤或上下文字段 | 先新增纯离线 LogRecord payload 契约，再将循环替换为语义等价字典推导式；从根全局 ignore、报告规则和期望清单移除 `PERF403` | 新契约在旧实现 **1 passed**；变更后 ELK/报告定向测试 **47 passed**、根标记回归 **1377 passed、2 warnings、79.72s**，`PERF403` 隔离扫描为 0、`make lint` 通过、报告为 **5 条 / 85 项**；未连接 ELK |
| F-31 | 两个字节级一致的 IBKR Cookie 脚本以 `except Exception: continue` 吞掉浏览器读取失败，既无诊断也被根全局 `S112` ignore 掩盖 | 先用 `browser_cookie3`/`requests`/logger 的内存替身固定“Chrome 失败后继续 Firefox、成功返回 cookie、诊断不含 cookie 或异常正文”；随后仅增加固定 debug 诊断并移除根全局 ignore 与报告规则 | 旧实现新契约 **2 failed**（无 debug），修复后 Cookie/报告测试 **15 passed**、根 marker 回归 **1379 passed、2 warnings、73.73s**，`S112` 隔离扫描为 0、`make lint` 通过、报告为 **4 条 / 83 项**；未访问浏览器、Gateway 或网络；CTP vendor 的范围化 per-file 例外未改 |
| F-32 | `ctp_env_selector._load_default_fronts` 的本地 YAML 配置加载失败会静默回退硬编码前置地址，留下 1 个 `S110` 命中且无法区分“未配置”与“配置不可读” | 先用临时文件和内存 `yaml` 替身固定解析失败时的内置地址回退与日志脱敏契约；随后仅增加 debug 诊断，记录异常类型而不记录 YAML 内容、异常正文或配置路径，选择与公开 API 不变 | 新契约在旧实现 **1 failed**（无 debug）；修改后 selector/报告定向回归 **18 passed**，`S110` 隔离扫描通过，报告为 **4 条 / 82 项**；根 marker 回归 **1379 passed、2 warnings、74.57s**。只解析本地临时配置，未连接 CTP、SimNow 或网络 |
| F-33 | 两个字节级一致的 margin 文档抓取脚本在侧栏链接发现失败时静默吞掉异常，既无诊断也留下 2 个 `S110` 命中 | 先用假 `playwright.sync_api` 模块和假页面固定“发现失败仍返回空列表、warning 仅记录异常类型”的契约；随后提取同名 helper，两个副本保持字节一致，未改抓取主流程 | 新契约在旧实现 **2 failed**（无 helper）；修改后 scraper/报告定向回归 **15 passed**，`S110` 隔离扫描通过，报告为 **4 条 / 80 项**；根 marker 回归 **1381 passed、2 warnings、88.93s**。未调用 `main()`、浏览器、Playwright 或网络 |
| F-34 | 生产监控示例在 Prometheus exporter 停止失败时 `except Exception: pass`，既静默吞掉清理故障也留下 1 个 `S110` 命中 | 以 fake `bt_api_py.logging_system` / `bt_api_py.monitoring` 离线加载示例；提取 shutdown helper，成功保留原 info，失败仅 warning 异常类型并返回 | 旧实现新契约 **2 failed**（无 helper）；修复后离线测试 **2 passed**、目标 `S110`/format 通过，报告 **80 → 79**，合并后的根 marker **1383 passed、2 warnings、74.69s**。未调用 `main()`、启动 exporter、访问网络或等待 |
| F-35 | 文档契约检查器有 3 个 `PERF401` append 循环，错误文案与排列顺序缺少精确回归约束 | 先锁定同一 certification entry 的缺字段、路径、SHA 与过期错误顺序；随后用等价 `errors.extend(...)` 收敛 3 处循环 | 定向测试 **2 passed**，目标 `PERF401`/format 与文档契约命令通过，报告 **79 → 76**，合并后的根 marker **1383 passed、2 warnings、74.69s**；未访问网络或写入文档 |
| F-36 | exchange-support 文档生成器的行构建循环保留一个可机械收敛的 `PERF401`，且需要保住两个交易所条目与政策文字的顺序 | 先以自定义双条目 fixture 锁定标题、表格行、支持/不支持状态与政策文案，再将 `render()` 的行 `append` 循环改为同序列表推导式 | 生成器定向回归 **3 passed**，目标 `PERF401` 与 format 通过，`python scripts/generate_exchange_support_docs.py --check` 通过；报告 **76 → 75**，不写文档、不访问网络 |
| F-37 | GitHub 治理校验器对 required checks 逐项 `append` drift，缺少多项缺失时的精确顺序契约 | 先以本地假治理数据锁定多个 required check 缺失的完整消息顺序，再用同序 `drifts.extend(...)` 收敛循环 | 治理校验定向回归 **9 passed**，目标 `PERF401` 与 format 通过；报告 **75 → 74**，不调用 GitHub API、不改治理清单或文档 |
| F-38 | 两个核心棘轮脚本的 `Sequence` 仅出现在延迟求值的注解中，却保留为运行时标准库导入 | 在 `from __future__ import annotations` 前提下，仅将 `Sequence` 放入 `TYPE_CHECKING`；保留固定 Ruff argv、`shell=False`、快照与失败语义 | 两脚本定向回归合计 **41 passed**，目标 `TC003` 与 format 通过，`make quality-ratchet`/`make format-ratchet` 通过；报告 **74 → 72**。三项合并后的根 marker **1385 passed、2 warnings、76.91s**；不更新基线、不修改子仓 |
| F-39 | 两份字节一致的慢测试分析脚本在 JSON 记录转换中各有一处 `PERF401`，重构需保留缺失字段默认值和输入顺序 | 先以临时 JSON 对两个脚本路径锁定多条记录、缺失字段与顺序，再在两副本同步改为同序列表推导式 | 定向离线回归 **2 passed**，目标 `PERF401`/format 通过且副本 `cmp` 一致；报告 **72 → 70**，新鲜根 marker **1387 passed、2 warnings、75.63s**。不运行 `main()`、不访问网络 |
| F-40 | 两份字节一致的覆盖率分析脚本在已排序的未测试交易所报告行中各有一处 `PERF401`，重构需保留完整报告内容与调用边界 | 先 stub 四个依赖并锁定未测试交易所排序、模块行顺序、低覆盖率筛选/顺序与完整文本，再在两副本同步改为同序 `report.extend(...)` | 定向离线回归 **2 passed**，目标 `PERF401`/format 通过且副本 `cmp` 一致；报告 **70 → 68**，新鲜根 marker **1389 passed、2 warnings、74.91s**。不调用 `main()`、`pytest.main()` 或写出报告，不访问外部服务 |
| F-41 | 两份字节一致的 v2 capability 提取脚本在 Markdown/CSV 行构建中各有两处 `PERF401`，机械改写必须保住输入键序、能力字段顺序和缺失值回退 | 先以非排序字典、混合能力和缺失 capability 的纯内存 fixture 锁定完整 Markdown/CSV；两个副本同步以同序 `values.extend(...)` 替换内层 append 循环 | v2 契约 **2 passed**，目标 `PERF401`/format 通过且副本 `cmp` 一致；报告 **68 → 64**。不读写 capability 文件、不调用主入口或外部服务 |
| F-42 | 两份字节一致的 v1 capability 提取脚本在 CSV 行构建中各有一处 `PERF401`，需保住原 Markdown 边界和 CSV 字段顺序 | 先以非排序字典与混合 capability 的纯内存 fixture 锁定 Markdown/CSV，再仅将 CSV 的内层 append 循环替换为同序 `values.extend(...)` | v1 契约 **2 passed**，目标 `PERF401`/format 通过且副本 `cmp` 一致；报告 **64 → 62**。不读写 capability 文件、不调用主入口或外部服务 |
| F-43 | 四个根测试模块的 `Path`/`AsyncIterator` 只用于延迟注解，却保留运行时标准库导入 | 确认四文件均有 `from __future__ import annotations`，仅移入 `TYPE_CHECKING`，不改测试逻辑、fixture 或断言 | 目标 `TC003`/format 与 `git diff --check` 通过，报告 **62 → 58**；不触发临时 venv/pip 的确定性范围 **42 passed**。完整离线根回归为 **1390 passed、3 failed、2 warnings**：两项 artifact-first 临时环境测试与一项 wheel contract 均因 `bt_api_base` 本地不可用而失败；原断言未放宽。显式排除这 3 项工件前置条件后的根范围 **1390 passed、2 warnings**，不能替代完整 marker，故 AC-12 为 NO-GO；全程 `PIP_NO_INDEX=1`，未访问索引/外部服务 |
| F-44 | 两份质量分析脚本的 Python 文件遍历各有一处 `PERF401`，改写不可改变 `os.walk` 的目录剪枝、文件过滤或原始顺序 | 先以临时目录和确定性 `os.walk` 替身锁定两个脚本各自的返回顺序、隐藏文件过滤与排除目录不进入遍历；随后仅改为同序列表推导加 `extend`，不统一两副本其他既有差异 | 两路径离线契约 **2 passed**，目标 `PERF401`/format 与 `git diff --check` 通过；报告 **58 → 56**。不调用 `main()`、不写质量报告、不访问外部服务 |
| F-45 | 两份代码质量检查器在目录收集阶段各有一处 `PERF401`，改写不可改变默认/自定义排除、隐藏目录、文件顺序、report 更新或逐文件异常隔离 | 先用临时目录、确定性 `os.walk` 与 `check_file`/report 替身锁定默认与自定义排除、隐藏 `.py` 文件包含、verbose 输出、错误隔离和缺失路径；随后仅改为同序列表推导加 `extend` | 两路径离线契约 **6 passed**，目标 `PERF401`/format 与 `git diff --check` 通过；报告 **56 → 54**。后续完整离线根回归 **1398 passed、3 failed、2 warnings**，三项失败仍均为 `bt_api_base` 本地工件前置条件；排除后三项以外范围 **1398 passed、2 warnings**，不替代 marker。未调用 CLI、不写报告、不访问外部服务 |
| F-46 | 两个 docstring 工具的文件发现循环各有一处 `PERF401`，机械改写不可改变默认/自定义排除、动态 `*.egg-info` 剪枝、非排除隐藏目录或排序结果 | 先用临时目录和确定性 walk 覆盖默认/自定义 exclusion、动态 egg-info、隐藏目录/文件与排序输出，再仅改为同序列表推导加 `extend` | 两路径离线契约 **4 passed**，目标 `PERF401`/format 与 `git diff --check` 通过；报告 **54 → 52**。不调用 CLI、不写文件或访问外部服务；本批后完整根 marker 待重跑，既有离线工件 NO-GO 未解除 |
| F-47 | `reconcile_tick_universe.py` 的额外数据集组装留有 1 个 `PERF401` | 先以 stub Parquet 的内存夹具固定结果、顺序与空输入，再以等价 `extend` 收敛 | 定向离线回归 **1 passed**；不读取真实 parquet、不访问网络 |
| F-54 | `split_ctp_wrapper.py` 的章节与写出列表留有 2 个 `PERF401` | dry-run/临时输出目录契约固定拆分边界与输出清单后，以等价列表构造收敛 | 定向离线回归 **2 passed**；不调用 CTP wrapper |
| F-55 | 前序 `TYPE_CHECKING` 清理在运行时反射下使部分 `typing.get_type_hints` 失败 | 恢复相关直接导入，新增并持续扩展反射目标回归；不以缩小静态债务掩盖公开注解 API 改变 | `tests/test_type_hint_introspection.py` 当前本地 **63 passed**，但未跟踪，故为 **NOT_PERSISTENT**；G-07/G-08 保留 TODO，且本行纠正并 supersede F-38/F-43 的当前适用结论 |
| F-56 | `install_bt_api_submodules.py` 的两个 `PERF401` 会在未来触发性能门禁 | 用全 monkeypatch 的安装命令契约固定 argv/顺序，再以等价 `extend` 收敛 | 定向离线回归 **1 passed**；不运行 git、pip 或安装 |
| F-57 | `LimitsManager.get_limit_breaches` 留有 1 个 `PERF401` | 以现有风险行为测试覆盖 breach 列表顺序/内容后改为列表推导 | `tests/test_risk_management.py` **26 passed**；不访问外部服务 |
| F-58 | `ctp_close_plan._read` 留有 1 个 `PERF401`，且 close-plan 键序/冲突语义需要保住 | 用纯对象/映射契约固定插入顺序、对象属性、冲突错误码和默认值后改为等价构造 | `tests/bt_api_contract/test_ctp_close_plan_read.py` **8 passed**；不启动 CTP/SimNow |
| F-59 | `PERF401` 已零存量，仍由根及测试路径 ignore 和报告规则掩盖 | 先全范围隔离扫描为 0，再从 `pyproject.toml` 根 ignore、`tests/*.py` per-file ignore 与报告固定规则移除 | 报告行为测试 **14 passed**；`make tech-debt-report` 为 **3 条 / 51 项** |
| F-60 | `test_execution_recovery.py` 中最后一个受 per-file ignore 覆盖的 `PERF401` 未被主报告捕捉 | 以同序轮次列表推导收敛，保持请求分组与断言顺序 | 整文件 **48 passed**；最终全范围 `PERF401` 隔离扫描为 0 |
| F-61 | 根范围剩余 9 个 `S110` 以静默 `except Exception: pass` 隐藏 OKX 清理、Hyperliquid 示例和 WebSocket 接收失败 | 在 fake-only 行为契约先锁定取消/接收顺序、继续处理和 best-effort 返回后，仅补不含异常正文、订单、账户、密钥或消息内容的异常类型诊断 | 四个专属离线测试文件合计 **12 passed**；`S110` 隔离扫描为 0；根与 `tests/*.py` ignore 及报告规则移除，CTP vendor 例外未改 |
| F-62 | 4 个未进入运行时反射面的测试专用 `Path`/`ModuleType` 导入仍留在运行时，形成可安全消除的 `TC003` 存量 | 只在 `from __future__ import annotations` 的测试文件中将注解专用导入置于 `TYPE_CHECKING`；保留运行时 `Path` 及所有注解文本、fixture/断言语义；同时将反射守卫从 46 扩至 57 个目标 | 定向测试 **9 passed**、报告契约 **14 passed**，根范围 `TC003` **20 → 16**，报告 **42 → 38**；完整离线 marker **1475 passed / 3 failed / 2 warnings**，三项失败仍仅是 `bt_api_base` 本地工件前置条件 |
| F-63 | 订单/持仓六个限额 helper 的 `CRITICAL` 分支返回字典时读取未赋值 `warning`，关键限额结果可异常并被预交易入口吞掉 | 仅在已确认的六个分支补 `warning = ""`；状态、restriction、阈值、返回字段和其余逻辑不变 | 六个参数化 helper 契约与一个真实 `LimitsManager.check_pre_trade_limits` 端到端契约；`tests/test_risk_management.py` **34 passed**，合并风险/反射/报告 **111 passed** |
| F-64 | 首次将五个风险 mixin 的私有 helper 注解 import 移入 `TYPE_CHECKING` 后，独立审查发现 `typing.get_type_hints` 仍会对继承到 `LimitsManager` 的方法报 `NameError` | 恢复五个运行时 import，并为 `_check_compliance_limits`、`_check_margin_requirement`、`_check_max_order_size`、`_check_position_limits`、`_check_risk_limits` 新增反射 target；只保留已审计为局部变量注解的 `PolicyEngine` `Callable` 移动 | 反射契约 **58 → 63 passed**；五个 helper 的直接 `get_type_hints` 全部可解析。报告真实值为 **37**（TC001 22、TC003 15），不以私有命名掩盖运行时类型 API 回归 |
| F-65 | artifact-first 的旧三项失败表面为缺少 `bt_api_base`，但仅补一个 wheel 无法证明 PEP 517 构建、运行时依赖和包来源的完整离线闭包 | validator/verifier 只接收绝对显式 wheelhouse；隔离继承 `PIP_*`、禁用 index/cache，保留默认构建隔离与完整解析；每轮 wheel/venv 唯一，安装后 `pip check`，并核验 root/base 从 fresh `site-packages` 导入 | 定向 artifact 契约 **19 passed、82.22s**；闭包落地时完整根 marker **1499 passed、2 warnings、97.25s**。临时 `pytest-socket` 仅覆盖最小 socket-plugin 行为，不替代真实第三方归档工件验收 |
| F-66 | 将反射可见注解的 import 移入 `TYPE_CHECKING` 会使 `typing.get_type_hints` 产生 `NameError`，而别名或全限定名只会伪造 Ruff 清零 | 保留运行时直接导入；任何后续清理先定义注解 API 兼容策略、精确解析值断言和版本影响 | 本地反射观察为 **63 passed**，但 `tests/test_type_hint_introspection.py` 当前未跟踪，故为 **NOT_PERSISTENT**；当时 G-07/G-08 的 37 项不得机械降低或标为 DONE，后续非反射新增命中须另有行为证据 |
| F-67 | required 的 `quality-gate` 漏列 `quality-ratchet`，质量棘轮失败理论上可不影响绿色聚合结果 | 在已有 format-ratchet 聚合之外，补齐 quality-ratchet 的 needs、非 success `exit 1` 与 summary 行，并锁定 `if: always()`；用 YAML `BaseLoader` 结构合同锁定，而非文本匹配 | 两个棘轮合同 **45 passed**；`make quality-ratchet` 仍为 10 项存量且通过，说明修复汇总正确性而非将债务清零 |
| F-68 | `PluginCatalog` 对已安装 venue 只要同名路径存在就报 `certified`，忽略配置 tier、最低版本和入口点；目录也可伪装为证据卡 | `certified` 须同时要求配置为 `certified`、实体卡文件、安装、版本达标和入口点；其余情况保留 `missing` / `loadable` / `installed` 的降级顺序 | 审查后补齐缺卡、目录伪卡、未安装、版本/入口不足、experimental 与五条件全满足的公开契约；定向 **11 passed**，完整离线根回归 **1510 passed、2 warnings、99.49s**；不访问网络或外部交易所 |
| F-69 | 单个第三方 `bt_api.adapters` entry point 的加载、注册，甚至其诊断日志失败都会中断内置 broker 与后续健康插件的发现 | 仅隔离单插件的 `Exception`，尽力记录带 entry-point 名称和 traceback 的 warning 后继续；本轮完成才置 loaded。枚举/选择错误和 `BaseException` 仍传播且保持可重试 | 覆盖 load/register 失败、日志观察失败、后续健康插件、一次性尝试、注册表隔离与 `BaseException` 重试；`tests/test_broker_loader.py tests/test_btapibroker_import.py` **15 passed、1 existing deprecation warning**，后续完整离线根回归 **1522 passed、2 warnings、89.88s** |
| F-70 | quality-ratchet 仅检查快照路径在当前缺失，未检查当前新发现的子仓 `src/tests` 未进入快照；范围扩张可在计数相等时静默通过 | 对快照与默认发现范围做尾部斜杠归一化后的双向比较；普通门禁与 `--update` 均拒绝 scope 漂移，只有审查后的 `--force-update` 可写入新范围 | 独立范围契约 **5 passed**，实际 `make quality-ratchet` 保持 **10** 项并通过；完整离线根回归 **1522 passed、2 warnings、89.88s**。基线 JSON 的既有 21→10 变更不归因于本修复 |
| F-71 | PR governance 仅依赖 `submodules_changed` 布尔值；单独修改 `.gitmodules` 或删除式 gitlink 路径时可没有 SHA 上下文而在严格模式通过 | 精确识别 `.gitmodules` 与父仓 gitlink `bt_api/bt_api_<name>`；存在这些路径但 flag 非真即违规，`submodules_changed=true` 的路径校验复用同一识别逻辑。普通 `bt_api/readme.md` 与工具文件不误判 | **历史阶段，当前设计已由 F-74 supersede**。当时定向治理契约 **14 passed**、完整离线根回归 **1526 passed、2 warnings、99.41s**；这些历史证据不替代 schema v2 或本批最终 marker |
| F-72 | `SourceSupervisor` 同 key 并发首订阅/释放缺少临界区，可能重复 start 或在新订阅后误 stop | per-key `asyncio.Lock` 覆盖 upstream start/stop await 与 refcount 更新；不同 key 不互斥，空闲锁弱引用回收 | 确定性本地契约 **5 passed**；不构成外部 feed 生产证明 |
| F-73 | docs workflow 触发面与安装/合同顺序不足，依赖失败可能被 fallback 或宽松参数掩盖 | 扩充 paths；只允许 `python -m pip install -e .`；generator `--check` → docs contract → strict build，禁止 fallback、`--no-deps`、`--no-build-isolation` 和 `continue-on-error` | 本地结构契约 **4 passed**；GitHub/Pages **NOT_RUN** |
| F-74 | F-71 的全局 flag/SHA 仍不能表达多路径 gitlink 身份，base 也缺可信对象验证 | schema v2 逐 path `gitlink_changes` + `collection_errors`；真实 raw `-z`/40-hex 采集；可信 base 脚本与对象探针；add/delete 识别但拒绝，legacy 全局字段无授权 | 本地 collector/validator/workflow **46 passed**；三分支两阶段 rollout、fork、strict **NOT_RUN**，状态 **PARTIAL / NO-GO**。本条 supersede F-71 的当前治理设计，F-71 仅保留为历史阶段 |
| F-75 | 迭代文档把历史或中途 root marker、dirty checkout 观察写成当前最终证据，产生证据漂移 | 当前 marker 统一回填为 1615 passed/2 warnings/63.32s，并将早期 1562 passed 与 coverage 混合 exit 3 保留为非最终诊断；标明未跟踪测试/dirty checkout 边界 | checkout-local 根回归 PASS；显式 `--cov-branch` 为 **1615 passed / 2 warnings / 74.40s**、覆盖率 **66.94%** PASS。clean-checkout、外部 CI 与发布证据仍 **NOT_RUN** |
| F-76 | optional optimized security 步骤用 fail-open 方式掩盖 Bandit/pip-audit 失败 | 保留扫描器真实退出码并增加 workflow 结构合同；CTP High 不得基线化 | 本地结构合同 **5 passed**；真实 GitHub Bandit/pip-audit **NOT_RUN**，CTP `B507` 仍 NO-GO |
| F-77 | optimized wheel smoke 可被 build/install 失败遮蔽，且未明确绑定本轮 wheel | 禁止失败遮蔽；只选择本轮产物并核验安装、导入来源与最小运行合同 | 本地结构合同纳入上述 **5 passed**；真实 GitHub build/wheel **NOT_RUN**，状态 **PARTIAL** |
| F-78 | canonical runner 无法稳定表达默认排除 CTP、仅 CTP和 branch coverage，且 pipeline 可只返回 `tee` 状态 | 默认注入 `not ctp`；`--ctp` 仅解除默认排除；`make test-ctp` 显式 `--ctp -m ctp`；`--cov` 增加 `--cov-branch`；pytest/tee 任一失败即整体失败并保留摘要 | runner/Makefile 本地合同 **23 passed**；根 marker **1615 passed、2 warnings、63.32s**，branch coverage 复跑 **1615 passed、2 warnings、74.40s** / **66.94%**。network/CTP/SimNow **NOT_RUN** |
| F-79 | optional performance 在缺少 `tests/performance/`、空报告或无 benchmarks 时可 skip/绿色，schedule 因而伪造持续性能验证 | 只允许 `workflow_dispatch` opt-in；benchmark、checker 和 artifact 均 fail closed | 初始结构合同累计 **8 passed**；本地可执行能力已由 F-97 补齐。GitHub hosted 仍 **NOT_RUN**，schedule 未恢复 |
| F-80 | CI full-suite 使用裸 `pytest`、未显式并行/branch coverage，threshold 直接插入 shell，且报告/artifact 缺失可留下假绿窗口 | 固定 `python -m pytest tests -v -n 8 --cov-branch`；threshold 只经 step env、0..100 十进制数值白名单与 Bash argv 数组传递；`coverage-full.xml`/HTML 非空验证并严格归档 | tests workflow 本地结构合同 **20 passed**；真实 GitHub hosted **NOT_RUN**，状态 **PARTIAL** |
| F-81 | 正式发布原来跨运行重建、可绕过 TestPyPI smoke，manual/release gate、候选身份、精确 wheel 与依赖索引均存在假通过窗口 | 新增确定性候选 manifest/摘要记录与复验；manual build-only；三个外部 job 统一 release + enable gate；build 输出提供 wheel/sdist/manifest 独立摘要锚；TestPyPI 精确 wheel 下载后从本地安装，依赖仅走 PyPI；PyPI 复验同一 artifact | 本地候选/工作流与既有发布资源定向合同 **54 passed**；GitHub hosted、TestPyPI/PyPI、OIDC/Environment/trusted publisher/Ruleset 均 **NOT_RUN**。第三方 action/构建依赖固定、OIDC 最小执行面和 Release 元数据顺序仍开放，故 **PARTIAL / NO-GO** |
| F-82 | `migrate_execution_journal` 达 361 行，且在 COMMITTED 事务原子写成功前先把内存状态改为 `COMMITTED`；写入失败时异常处理误认为已提交并跳过 rollback | 以私有 helper 和 frozen `_MigrationPreparation` 拆分职责；先构造独立 committed payload 并原子落盘，成功后才更新内存状态。若提交写失败，PREPARED 状态触发完整 rollback；若已落盘后 receipt/freeze 收尾失败，则保留 committed transaction 供恢复 | 失败注入与迁移定向 **6 passed**；`tests/bt_api_contract/test_execution_session.py` **161 passed**；入口 AST **361 → 128**。测试证明 source、destination/staging/sealed、transaction 与所有非空 identity-registry manifest 精确恢复；完整根回归 **1671 passed、2 warnings、95.99s**，branch coverage **1671 passed、2 warnings、108.50s / 67.11%** |
| F-83 | `scripts/analysis/` 维护 8 份完整实现副本，其中 `analyze_code_quality.py` 已与顶层可移植路径修复漂移；直接删除又会破坏历史 CLI/import 路径 | 以 `scripts/*.py` 为唯一 canonical；8 个旧路径改为惰性 import/CLI wrapper。独立审查先发现首版只读 proxy 不转发 `wrapper.CORE_DIR = value`，继而发现标准 `patch.object` 删除/恢复和 wrapper 自身 `Path` 名称遮蔽；最终以私有基础设施别名、持久 canonical 属性集合和私有 `ModuleType` 子类转发读取、写入及删除/恢复 | 6 组既有双路径契约 + 两个补齐 parity + 隔离 wrapper 合同共 **41 passed**；Ruff/format/mypy PASS，顶层 8 个 SHA256 前后完全一致，独立复审 **PASS**。未执行真实扫描、coverage、网络或报告写出 |
| F-84 | `coerce_funding_snapshot` 在单一入口内混合 freshness mapping 重建、领域对象构造与最终 availability/identity/expiry 校验，虽未超过 150 行仍不利于独立验证 | 在干净源码/测试边界内抽取 3 个私有 helper，保持公共签名、错误优先级和 dataclass 对象身份；独立审查发现并修复外层 `Mapping.source` 提前读取导致的访问次数与异常归一化回归 | 入口 AST **81 → 28**；状态化 Mapping 在内的定向 **17 passed**，目标 Ruff/format/mypy 与 `git diff --check` PASS；未访问交易所、网络、订单或外部时间源 |
| F-85 | `fill_missing_docstrings.py` 的异构 `ast.AST` 容器造成 20 个 mypy 错误，且行为测试发现旧写入逻辑可能生成无效源码、丢 module docstring、把字符串注入 class bases，或使 UTF-8 文件的 PEP 263 cookie 位置失效 | 用精确 AST 联合类型与 `TypedDict` 建模；token 定位定义头冒号，复用原始缩进并稳定合并同索引动作；任何写入前重新 parse 且要求目标 docstring 全部形成，否则 fail closed、不改文件 | 目标 mypy **20 → 0**，精确 CI **77 → 57 errors（14 → 13 files）**；定向 **23 passed**，目标 Ruff/format/mypy、diff-check 与独立复审 PASS；完整根 **1725 passed**、branch coverage **67.24%**。仅承诺保持 UTF-8 cookie 位于前两行；split-line `async \\` / `def` 仅安全拒绝，未声明支持 |
| F-86 | F-85 后实时报告出现 4 个新增 `TC003` 命中：3 个测试注解专用 `Path`/`ModuleType`，以及 `fill_missing_docstrings.py` 的公开 `Iterable[str]` 注解 | 三个测试仅在 `from __future__ import annotations` 下把注解专用类型移入 `TYPE_CHECKING`；脚本的真实 `get_type_hints` 回归证明 `Iterable` 是运行时反射依赖，因此保留直接导入和报告命中，不以别名、`noqa`、ignore 或基线隐藏 | 四测试定向 **48 passed**，后续棘轮联合范围 **31 passed**；目标 mypy/Ruff/format 与独立复审 PASS。`make tech-debt-report` **40 → 37**（TC001 22、TC003 15）：只计 3 个真实清理，脚本项继续留债 |
| F-87 | `check_quality_ratchet.py --force-update` 可在缩小已记录 scope 或抬高旧范围债务时直接覆盖快照，与“仅用于受审查范围扩张”的文档合同矛盾 | 有现存 baseline 时先拒绝任何 scope shrink；仅真实扩张才额外扫描 recorded scope，旧范围任一规则回升即拒绝；same-scope 直接比较首次扫描结果。所有拒绝路径保持基线字节不变，普通 `--update` 语义不变 | 范围缩小、扩张时旧范围回升、同范围回升三条故障注入与安全扩张均纳入定向回归；目标 Ruff/format/mypy、`make quality-ratchet`、diff-check PASS。不得把 `--force-update` 解释为无条件重基线 |
| F-88 | artifact-first 路径存在 5 个 mypy 类型边界错误：调用方环境是只读 mapping、`Distribution.locate_file()` 返回抽象 `SimplePath`，而公开 wheelhouse 入口的字符串契约未在签名中表达 | `pip_source_environment` 接受 `Mapping[str, str]`，先以真实运行时 guard 拒绝非映射输入，再复制；`run_validation` 接受 `Path | str | None` 且在创建 artifacts 前单次归一化；三处 `SimplePath` 经 `Path(str(...)).resolve()` 进入本地文件系统边界后继续执行 purelib/platlib 与文件存在性 fail-closed 校验 | 目标 **12 passed**，四文件 mypy/Ruff/format、diff-check 与独立复审 PASS；可比 254 文件范围 **57 → 52 errors（13 → 11 files）**，Task30 的精确 256 文件命令未重跑。债务报告保持 **37**，未使用 `Any`、`cast`、ignore、`noqa` 或配置放宽；完整根/coverage 均为 **1731 passed、2 warnings**，覆盖率 **67.24%** |
| F-89 | 三个测试文件存在 4 个 mypy 类型错误：AST helper 调用处丢失 import 节点窄化，裸 `ModuleType` 又无法表达 PyArrow/YAML 假模块的动态属性 | 对 `ast.walk` 结果先显式窄化为 `ast.Import | ast.ImportFrom`；用精确 `ModuleType` 子类声明 `ParquetFile`、`parquet` 与 `safe_load`，其中拒绝 Parquet 读取的桩以 `Callable[..., Never]` 如实表达总是抛错的行为 | 三文件目标 mypy **4 → 0**，定向 **7 passed**，Ruff/format、diff-check、债务报告与独立复审 PASS；可比 254 文件范围 **52 → 48 errors（11 → 8 files）**，精确 256 文件命令未重跑。债务保持 **37**；本批未新增 `Any`、`cast`、ignore、`noqa`、配置放宽或动态属性逃逸；完整根 **1731 passed、2 warnings、92.95s**，branch coverage **1731 passed、2 warnings、135.56s / 67.24%** |
| F-90 | 两份离线脚本测试用裸 `ModuleType` 动态挂 Playwright、browser-cookie3 与 requests 属性，产生 8 个 mypy `attr-defined`，且请求异常分支未被真实执行 | 用精确模块子类声明 `sync_playwright`、四个浏览器函数、`get` 与 `exceptions.RequestException`；Chrome 读取失败后由 Firefox 触发离线请求异常，再由 Safari 成功，Edge 不调用，日志仍不含 cookie 或异常正文 | 两文件 mypy **8 → 0**，定向 **4 passed**，Ruff/format、目标 TC001/TC003、diff-check 与独立复审 PASS；不启动浏览器或网络。fresh `--no-incremental` 可比 254 文件为 **41 errors / 7 files**；因该运行额外暴露既有 `_execution_session.py` 注解错误，不把 Task34 的 48/8 与本值写成直接算术下降 |
| F-91 | 生产监控/Hyperliquid 两个离线测试用裸模块表达 18 个运行时属性；首版虽令 mypy 通过，但 `config_loader.load_exchange_config` 只有注解、实例没有属性，并以 `raising=False` 临时创建，仍是假接口 | 为 logging/monitoring 与 Hyperliquid 的根包、feeds、functions、live feed、log message、config loader 建立精确 `ModuleType` 子类并赋真实属性；config loader 先绑定安全失败占位函数，异常注入恢复默认 `raising=True` | 两文件 mypy **18 → 0**，定向 **7 passed**，Ruff/format、目标 TC001/TC003、diff-check 与最终复审 PASS；同一 fresh 254 文件范围 **41 → 23 errors（7 → 5 files）**。完整根 **1734 passed、2 warnings、91.45s**，branch coverage **1734 passed、2 warnings、119.06s / 67.27%**；真实服务、Hyperliquid、网络均未运行 |
| F-92 | `_load_journal` 的空 `reservation_cancel_events` 无法推断 key/value 类型，令当前主包门禁保留 1 个 `var-annotated` | 依据 `_client_key` 的三段 ledger identity + client id，声明为 `dict[tuple[str, str, str, str], list[dict[str, object]]]`；不改变局部变量运行时语义 | `make type-check` **128 source files / 0 issues**；execution-session **164 passed**，目标 Ruff/format、diff-check 与独立复审 PASS |
| F-93 | OKX WebSocket 清理离线测试的三份空事件列表不可推断，宽泛 tuple 推断又与一元/三元精确断言形成 2 个 overlap 错误 | 用 `Literal` 判别的 `_Event` tuple 联合覆盖 2 个一元、3 个二元、1 个三元和 sleep 事件，三份列表显式使用 `list[_Event]` | 目标 mypy **5 → 0**、**3 passed**，Ruff/format、目标 TC001/TC003、diff-check 与独立复审 PASS；与 F-92 合并后 fresh 254 文件 **23 → 17 errors（5 → 3 files）** |
| F-94 | Actions workflow 结构测试把 `yaml.BaseLoader` 的动态结果当作字符串/映射直接使用，产生 5 个类型错误且坏形状诊断不明确 | loader 如实返回 `object`；带路径的 mapping/list/string guard 在每层 fail closed 收窄，新增错误 mapping/字段类型负例，不用 `cast` 掩盖 YAML 边界 | 目标 mypy **5 → 0**、**21 passed**，Ruff/format、目标 TC001/TC003、diff-check 与独立复审 PASS |
| F-95 | WebSocket infrastructure 有 11 个集合、handler、queue 与异常构造类型错误；首版又绑定 15.x 专用类型路径、固定 8765 端口，并把共享 receive helper 锁死为 `AsyncMock`，分别破坏最低依赖、测试隔离和 consumer 类型合同 | 用最小 receiver/connection/server/listening-socket `Protocol` 表达 12/13 legacy 与 15 new asyncio 共有能力；顶层 `websockets.serve` 保留公共版本路由，loopback 使用 OS 分配端口；跨文件检查发现的 receiver 回归也以协议修复 | 目标 11 个错误归零，当前 15.0.1 两文件 **8 passed**；独立复审在 13.1/15.0.1 各 **7 passed** 并最终 PASS。12.0 未单独安装执行，不作实跑声明；无外网，未干预占用 8765 的 Anki |
| F-96 | CTP 隔离 probe 的 fake runner 把外层 `*args: tuple[object, ...]` 误传给 `CompletedProcess`，产生 1 个参数类型错误 | 按生产调用的真实 argv 建模为 `list[str]`，返回 `CompletedProcess[str]`，保持 `text=True` 输出与脱敏断言 | 目标 mypy **1 → 0**、**8 passed**，Ruff/format、目标 TC001/TC003、diff-check 与独立复审 PASS。Tasks37–41 联合定向 **203 passed**，最终 fresh 254 文件 **0 errors / 0 files**；精确 256 文件命令仍未重跑 |
| F-97 | F-79 只消除了 workflow false-green，仍没有可执行 benchmark、版本化阈值或严格报告比较 | 固定唯一 orderbook `normalize_event` node 与 `-n 8`；schema v1 基线强制 singleton fullname、mean ≤0.5ms、rounds ≥1000；checker 对文件/JSON/集合/统计/阈值 fail closed，Make 临时报告自动清理，workflow 仅在成功后上传 | 定向 **59 passed**；7 次本地有效 JSON mean **8.096–32.879µs**、rounds **3613–14608**，门禁通过；最新根 marker **1796 passed、2 warnings、95.29s**。该阈值只防灾难性退化；7 条空闲 xdist worker warning、多 benchmark 聚合与 GitHub hosted 均保留为显式边界 |
| F-98 | `OrderRequest.__post_init__` 将 17 项校验集中在单一入口，C901 为 **18 > 10**；既有负例又未逐字锁定 10 条拒绝分支及跨段首次错误顺序 | 保持原 1–8/9–17 顺序，机械拆为两个私有 helper；用 `dataclasses.replace` 驱动非法运行时值，精确断言异常类型、消息、边界及三组跨 helper 优先级，不新增运行时约束或配置例外 | 入口/core/intent C901 **18→1/9/10**，`models.py` **3→2**、主包 **84→83**；两文件 **50 passed**、七文件 **81 passed**、目标行/分支覆盖完整，19 字段 frozen/signature/反射保持，128 source mypy 与独立复审 PASS；完整根 **1824 passed、2 warnings、127.85s** |

## 6. 迭代期发现（新登记）

| ID | 发现 | 影响 | 状态 |
|----|------|------|------|
| N-01 | **子仓 ruff 配置分裂**：14/15 子仓自带 `[tool.ruff]`，规则集各不相同（父仓 select 14 条 / gateio、htx 7 条 / okx 继承父仓）；ruff 对每个子仓文件使用其**自己的配置根**，不继承父仓 | 门禁口径不统一：`ruff check --config pyproject.toml bt_api/bt_api_gateio` 会从 0 报出 13 项 —— 即基线中的"1301"混用了不同规则集 | DEFERRED：D1 当前父仓范围不改子仓文件；须由 owner 选择规则子集，并以独立子仓任务、测试、提交交付，父仓仅更新指针 |
| N-02 | `scripts/analysis/` 曾是 `scripts/` 顶层 8 个脚本的完整副本 | 维护双份且已经出现绝对路径修复漂移 | DONE（F-83）：顶层为唯一 canonical，旧路径仅保留受测兼容 wrapper；不删除历史入口，也不再复制实现主体 |
| N-03 | 4 个示例集成测试的 noqa 写在 docstring 内（机制失效） | 21 项 F401 长期漏网 | DONE（F-09） |
| N-04 | `risk_management_root_demo.py` 无法编译（同步函数内 await） | 示例不可运行，CI 不覆盖 examples 故长期未发现 | DONE（F-08） |
| N-05 | 参数化测试 ID 含实时时间戳 | 阻塞 `-n` 并行（团队要求的 `-n 8` 无法使用） | DONE（F-12） |
| N-06 | 原 `submodule-quality` 把 lint 与 format 绑定在同一个 report-only job；CI 同版 Ruff 0.16.2 的快照基线为 15 仓 **452** 个待格式化文件（Binance 100、OKX 86、IB Web 40、Base 33；计数含 Ruff 支持的 Markdown 代码块），另有 **10** 个 lint 项 | 直接移除 `continue-on-error` 会把已知格式存量一次性变为阻塞故障；把 lint 与 format 混合提交会失去独立回滚 | DOING：`submodule-lint` / `submodule-format` 仍各自 report-only；根 `format-ratchet` 以 15 仓逐仓快照阻止 format 债增长（新/缺失仓也失败），并由 `quality-gate` 阻塞。已持久化 OKX handoff 候选使实际总数为 **453**，因此相对 452 基线正确失败；两项仍须独立清零、稳定 CI、可回滚后再升级 blocking，交付图见实施计划 Task 10-B |
| N-07 | `scripts/analyze_docstrings.py` 全树扫描会计入生成代码、测试和示例，输出的 6018 个缺失 docstring 不能证明 AC-9 的“公开 API 覆盖率” | 69%/85% 目标没有可复算、稳定的分母 | DONE（F-20）：专用 AST 指标固定公开源级分母并有 fixture 回归测试 |
| N-08 | Curve/Raydium/SushiSwap 的 5 个示例测试仍导入已不存在的旧容器/DEX feed 路径 | 原文件在当前根仓无法收集，不能作为行为通过证据 | DONE（F-21）：经历史删除与当前公开边界确认后正式退役，并以精确前缀 AST 测试防回归 |
| N-09 | `test_zmq_forwarding_runtime_start_sync_cleans_up_after_thread_start_failure` 修改共享 `threading.Thread`；在没有既有 forwarding loop 的 xdist worker 中，`_run_awaitable_sync` 无法启动后台 loop 而死等 | 根并行回归不能完成，先前被中断运行不构成通过证据 | DONE（F-24）：mock 已局部化，清理断言保留且新鲜根并行回归通过 |
| N-10 | Bitget 的 test_bequant_bitget_coinbase_request_base.py 同时导入 BeQuant/Coinbase；当前根开发环境未安装 `bt_api_bequant`，而 `bt_api_coinbase` 的偶然外部 editable 安装不属于 Bitget 可复现依赖 | 该文件不能在本地收集，S106 修复的行为回归不可宣称通过 | OPEN：由 Bitget 子仓 CI 安装可追溯、锁定 sibling SHA 的 BeQuant/Coinbase 工件并核验导入来源，或由 owner 设计并验证等价的 bitget-only 测试重构；不得跳过、新增 ignore 或把偶然 editable 安装当作证据 |
| N-11 | 根渐进 Ruff ignore 只有配置行内说明，没有受配置校验保护的实时总数 | 文档容易把历史数字或零计数规则遗漏为当前事实 | DONE（F-29 至 F-86）：只读报告固定范围、规则、理由与 JSON 边界；`PERF402`/`S113`、`PERF403`、`S112`、`PERF401` 和根 `S110` 均已从根全局项移除。最近一次完整报告的历史证据为 **37** 项；Tasks37–41 未取得 fresh 全范围库存。类型导入的进一步清理必须先通过运行时 `typing.get_type_hints` 或等价运行时契约 |
| N-12 | 子仓没有独立的 Bandit required gate；根 `pyproject.toml` 会跳过 `B101/B104/B105`，不能复用为完整子仓安全策略 | 当前默认 Bandit profile + `--ignore-nosec` 预检在 15 个 `src` 根发现 26 项；CTP `cleaner/pull/sftp_backend.py` 的 `B507` 为 High，使用 `AutoAddPolicy` 会接受未知主机密钥 | OPEN：先在 `bt_api_ctp` 以独立提交改为加载系统 known_hosts + `RejectPolicy`，补 fake-Paramiko 离线回归和部署说明；再由根仓实现 source-only、fail-closed 的默认-profile Bandit 棘轮。快照不得豁免 High，也不等同于 pip-audit、gitleaks 或运行时安全验收 |
| N-13 | 正式 PyPI 发布路径曾可绕过同一 source candidate 的 TestPyPI publish/smoke，生产候选身份没有端到端绑定 | release 可重新构建或在未验证 TestPyPI 候选时直达 PyPI，无法证明 smoke 的就是生产 artifact | PARTIAL / P0：本地单运行链、manual build-only、候选 manifest、wheel/sdist/manifest 独立摘要锚、TestPyPI 精确 wheel 与同 artifact PyPI 复验已经实现并受合同保护；GitHub hosted、TestPyPI/PyPI、OIDC/Environment/trusted publisher/Ruleset 仍 **NOT_RUN / NO-GO** |
| N-14 | 本地 singleton 性能能力已建立，但 pytest-benchmark/xdist 的多个 worker 对同一路径 JSON 缺少可靠聚合；当前还会产生 7 条空闲 worker warning | 若直接把精确 node 扩大为目录/多 benchmark，last-writer-wins 可能丢失记录；0.5ms 绝对上限也只适合灾难性退化保护，不是跨机器敏感趋势 | PARTIAL / OPEN：schema v1 与 checker 现强制 exact singleton，7 次本地实跑通过；增加第二个 benchmark 前必须设计并验证聚合方案。GitHub hosted 首次运行、跨 runner 校准和 schedule 恢复仍 **NOT_RUN / NO-GO** |
| N-15 | 特权发布 workflow 仍使用可移动 action 标签并在线浮动安装 build/twine；OIDC job checkout 并执行候选源码 | 上游标签或构建依赖被替换时可在摘要生成前或 OIDC 上下文执行；本地 artifact 摘要不能证明构建工具链可信，候选脚本也扩大令牌暴露面 | OPEN / P0：从官方来源在线核验并固定全部 action 的完整 commit SHA；以带哈希的 release build lock 固定完整传递依赖；将候选验证移到无 OIDC job，最终 OIDC publish job 不 checkout、不执行仓库脚本，只消费已验证不可变 artifact/digest。完成前 `ENABLE_PYPI_RELEASE` 保持关闭 |
| N-16 | `release: published` 在 TestPyPI/smoke/PyPI 前先公开 GitHub Release 元数据 | smoke 失败虽不会写 PyPI，但用户可能看到 Release 与 registry 不一致的中间状态 | OPEN：优先改为受保护 tag/受控晋级触发，全部门禁后再发布 GitHub Release；若暂保留当前触发器，失败时必须撤回或明确标记 Release，AC-17 不得据此判 PASS |
| N-17 | 同时运行两个各自 `-n 8` 的完整套件时，`test_breach_during_active_order_is_persisted_and_remains_blocked_after_restart` 一次观察到 `terminal_confirmed` 未及时为 true | 16 worker 叠加的本机资源争用可能暴露时序波动；若在 canonical 单套件中复现，会破坏恢复语义证据 | WATCH：精确节点随后 **5/5 passed**；后续单独完整根套件与 branch coverage 均已推进到 **1735 passed**。不将双套件并发作为 canonical 运行模式；若单套件再现立即升级为需修复缺陷，不得通过 rerun 隐藏 |
| N-18 | dirty `bt_api_okx` 将逐仓 format 计数从基线 86 推高到 87，使根 `make format-ratchet` fail closed；同一工作树还使 L1 mypy 从历史 0 变为 3 | 子仓格式债出现真实 +1 回退，且 `registry_registration.py:120,174` 缺注解、`market_wss_base.py:906` 将 `Any | None` 传给 `int`；若更新基线或在父仓代修会破坏归属与棘轮证据 | OPEN：对比子仓 HEAD 确认 `market_wss_base.py` 由已格式化变为需格式化；`tests/feeds/test_demo_environment.py` 则是基线已有债。必须由 OKX owner 在子仓独立整理并验证，父仓不改写 452 快照、不擅自修改当前未归属代码 |
| N-19 | branch coverage 命令曾在 **1706 passed、2 warnings** 汇总和 exit 0 之后，由 `pytest-rerunfailures 14.0` 的 daemon `run_connection` 线程额外抛出一次 `ValueError: not enough values to unpack` | 该 teardown warning 未计入 pytest warnings summary；若持续出现，可能让插件后台协议故障留在绿色退出之后 | WATCH：随后本轮非 coverage 与 branch coverage 完整套件均为 **1735 passed、2 warnings** 且未再出现，但一次未复现不足以关闭。若 canonical 单套件再现，需隔离 xdist/coverage/rerunfailures 组合、增加受测退出后诊断或升级/固定兼容版本，不得仅靠重跑隐藏 |

## 7. 遗留测试失败

| ID | 测试 | 原状态（2026-08-17 基线） | 现状 | 状态 |
|----|------|--------------------------|------|------|
| T-01 | `test_plugin_loader_registers_bt_api_alpaca_for_bt_api` | 失败（alpaca 包未安装） | 通过（改用 monkeypatch 注入假插件） | DONE |
| T-02 | `test_bt_api_consumes_alpaca_plugin_balance_and_subscribe` | 失败（alpaca 包未安装） | 通过（同上） | DONE |
| T-03 | `test_all_plugin_entry_points_discoverable` | 失败（硬编码 >=61 插件数） | 通过（已改为无固定数量断言） | DONE |
| T-04 | binance 165 项失败 | 未记录 | **非网络部分已修复**（见 F-17/F-18）：离线过滤套件 `493 passed / 0 failed`；余 85 项全部为 network/integration 标记的联网测试（需真实网络与账号），属预期 | DONE（离线部分） |
| T-05 | okx 1 项失败（`test_get_history_bar`，标 `@pytest.mark.network`） | 未记录 | 离线环境网络超时；排除 network 后 **292 passed** | WONTFIX（需外网） |

## 8. 计划债（docs/plans/ 74 份）

| ID | 项 | 状态 |
|----|----|------|
| P-01 | `docs/plans/` 共 74 份计划（其中 67 份为 2026-06-17 批量生成的 bmad 计划） | DONE：`docs/plans/README.md` 已索引并逐份复核 **74/74**；最终为已落地 61、已废弃/阻塞 8、部分/未开工 5、待确认 0。三批末轮复核分别取得 **34/472/119 passed**；Task30 的精确 256 文件 mypy 为 **57 errors / 13 files**，Task33/34 的历史可比 254 文件范围依次为 **52/11**、**48/8**，Task36/41 后 fresh `--no-incremental` 可比范围依次为 **23/5**、**0/0**，精确命令未重跑；owner 文件 format 失败等外部脏树问题继续保留为部分完成 |

## 9. 配置债

| ID | 项 | 状态 |
|----|----|------|
| C-01 | `pyrightconfig.json` 失效路径 | DONE（F-06） |
| C-02 | 根 `AGENTS.md` 失效引用 + 内容错位 | DONE（F-07） |
| C-03 | `stock_data/` 忽略规则 | DONE（owner 已落地） |
| C-04 | 覆盖率门槛**重复定义**（`pyproject.toml fail_under = 40` 与 CI env `COVERAGE_THRESHOLD: "40"` 同值两写；另有一处过期计划文档称 80） | DONE（CI 已改为以 pyproject 为单一来源；dispatch 输入留空即用 pyproject） |
| C-05 | 子仓 ruff 配置分裂（见 N-01） | TODO |
| C-06 | 子仓 mypy 覆盖（L1 已接入；其余 10 个子仓未纳入） | TODO |
| C-07 | 根 2 条渐进 Ruff ignore 的实际存量与移除计划 | DOING：最近一次完整 `make tech-debt-report` 的历史证据为 **37** 项（TC001 22、TC003 15；G-07/G-08）；Tasks37–41 只有目标 TC 零新增证据，未取得 fresh 全范围库存。`PERF402`/`S113`、`PERF403`、`S112`、`PERF401` 与根 `S110` 均已移除，CTP vendor 的范围化 `S110` / `S112` 例外未改。历史余项须先证明运行时反射兼容或明确不进入反射面；不得为压债削弱 `typing.get_type_hints` 守卫或用别名隐藏命中 |

## 10. 禁止事项（本迭代血泪教训）

**禁止使用 `ruff check --select RUF100 --fix`（以及把 RUF100 与其他修复混在一次 `--fix` 里）。**

- 2026-09-19 实测：该操作在缓存/`--select` 覆盖规则集的情况下，会把**仍在起作用的** `# noqa` 判为"未使用"并删除，
  一次误删 52 条（父仓）+ 17 条（子仓），其中包含**有意义**的抑制：
  - `bt_api_okx/.../request_base.py`：`# noqa: UP017 - package supports Python 3.9`（删掉会有人误用 py39 不支持的 `datetime.UTC`）
  - `bt_api_base/.../interfaces.py`：`# noqa: B027`
  - 多个可用性检测的 `# noqa: F401`、以及 `ctp/ctp.py` 的 13 条 `# noqa: F403`
- 恢复方式（本迭代实际使用）：从 `git diff`（**父仓与每个子仓分别执行**）提取被删行并按代码行匹配还原。
- 正确做法：
  - 判定 noqa 是否冗余，必须用**配置驱动**的 `ruff check <path>`（不带 `--select`，必要时加 `--no-cache`）；
  - 需要移除时**逐条人工确认**，不要批量自动修 `RUF100`。
