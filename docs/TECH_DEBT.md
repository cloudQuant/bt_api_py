# 技术债与例外清单

> 迭代：`docs/迭代计划/迭代07-代码质量提升/`
> 基线快照：`docs/acceptance/2026-09-19-quality-ratchet.json`（**21 项**，初始 1301）
> 最后更新：2026-09-19

本文件是**唯一台账**：lint 债、结构债、配置债、例外豁免、遗留测试都登记在此。
新增任何 `ignore` / `noqa` / 例外路径，必须同时在 `pyproject.toml`（如有）与本文件出现。

## 0. 总览

| 指标 | 迭代07 开始 | 现在 | 变化 |
|------|-----------|------|------|
| ruff 违规（完整门禁范围，34 路径） | **1301** | **21** | **-1280（-98.4%）** |
| 其中 `F821`（真实潜在缺陷） | 135 | **0** | 清零 |
| 其中 `W291` / `F401` / `I001` / `E701` / `F541` / `PIE790` | 712/164/154/36/21/9 | 全部 **0** | 清零 |
| 真实账号泄漏 | 9 处 | **0** | 清零 |
| 根测试（`-n 8`） | 1321 passed / 882s | 1321 passed / **88s** | 提速 10 倍 |
| okx mixins 目录 | 44 文件 / 20,369 行 | **20 文件 / 17,129 行** | 合并机械切分 + 样板收敛（API 零变更） |
| L1 五仓 mypy 错误 | 416（binance 392 / okx 17 / bybit 3 / gateio 4） | **0** | 纳入 `make type-check-l1` 并接入 CI |

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
| L-09 | `S106` hardcoded-password（测试桩） | 45 | 3 | DONE（余量见 E-02） | 扩展 per-file-ignores 覆盖子仓 tests 与 examples |
| L-10 | `TC002` | 4 | 4 | TODO | 需人工判定是否为纯类型导入（子仓 okx） |
| L-11 | `F841` unused-variable | 3 | 3 | TODO | 人工确认（examples/production_monitoring_demo.py 等） |
| L-12 | `UP042` str+Enum | 3 | 3 | TODO | examples 中 Mock 枚举，人工评估 |
| L-13 | `B017`/`E402`/`B007`/`S501`/`S603`/`SIM105` | 2/2/1/1/1/1 | 同左 | TODO | 逐项人工判定（S501 = 示例中 `verify=False`，S603 = 联网测试 subprocess） |

> 剩余 21 项均已进入棘轮基线，**只降不升**；任何新增违规都会让 CI 失败。

## 3. 结构债

| ID | 项 | 现状 | 状态 | 处理方式 |
|----|----|------|------|---------|
| S-01 | `bt_api_py/bt_api.py` | 8864 行 | TODO | 拆分方案卡（M5-Task14） |
| S-02 | `bt_api_py/_execution_session.py` | 8251 行 | TODO | 同上 |
| S-03 | `bt_api_ctp/.../ctp/client.py` | 4002 行 | TODO | 同上 |
| S-04 | `bt_api_py/_normalization.py` | 2511 行 | TODO | 同上 |
| S-05 | `bt_api_ctp/.../live_ctp_feed.py` | 2025 行 | TODO | 同上 |
| S-06 | 超长函数（>150 行） | 42 个 | TODO | 主包内 5 个优先（M5-Task15） |
| S-07 | `scripts/analysis/` 与 `scripts/` 顶层 8 个脚本**字节级重复** | 16 文件 / 8 对 | TODO | 建议删除 `scripts/analysis/`（`make analyze-coverage` 用的是顶层副本），待 owner 确认 |
| S-08 | 子仓机械切分文件族（`*_partN` / `*_mixin`） | okx 25 个 part 文件 | **DONE** | 已合并回 8 个逻辑模块（见 F-13）；其余子仓同类文件待评估 |
| S-09 | okx mixins 端点样板（3 方法/端点） | 已收敛 514 个方法 | **DONE** | 见 F-13；余下 ~90 builder / ~183 包装因形态不同保留原样（见 F-13 备注） |

## 4. 例外清单（Explicit Waivers）

| ID | 范围 | 理由 | 登记位置 | 复核 |
|----|------|------|---------|------|
| E-01 | `bt_api_ctp/**/ctp_structs_*.py`、`ctp_constants.py`、`ctp_wrap.*`、`ctp/ctp.py` | SWIG/结构体字段映射，属生成/半生成代码 | 本文件 | 每迭代 |
| E-02 | `S106` 余量 3 处（子仓 tests） | 夹具占位凭据，非真实密钥 | 本文件 + `pyproject.toml` per-file-ignores | 迭代07 M3 |
| E-03 | `examples/network_tests/moved_ctp_feed.py` | 已迁移提示文件，模块级 `pytest.skip` 主动禁用（原 631 行死代码已精简为指针） | 本文件 | 每迭代 |
| E-04 | 4 个 `examples/network_tests/integration/test_*_integration.py` | 导入可用性验证：import 本身即被测对象 → 文件头 `# ruff: noqa: F401` | 文件头 + 本文件 | 每迭代 |

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

## 6. 迭代期发现（新登记）

| ID | 发现 | 影响 | 状态 |
|----|------|------|------|
| N-01 | **子仓 ruff 配置分裂**：14/15 子仓自带 `[tool.ruff]`，规则集各不相同（父仓 select 14 条 / gateio、htx 7 条 / okx 继承父仓）；ruff 对每个子仓文件使用其**自己的配置根**，不继承父仓 | 门禁口径不统一：`ruff check --config pyproject.toml bt_api/bt_api_gateio` 会从 0 报出 13 项 —— 即基线中的"1301"混用了不同规则集 | TODO（M2/M4-Task11 配置统一） |
| N-02 | `scripts/analysis/` 是 `scripts/` 顶层 8 个脚本的**字节级副本** | 维护双份、易漂移 | TODO（见 S-07） |
| N-03 | 4 个示例集成测试的 noqa 写在 docstring 内（机制失效） | 21 项 F401 长期漏网 | DONE（F-09） |
| N-04 | `risk_management_root_demo.py` 无法编译（同步函数内 await） | 示例不可运行，CI 不覆盖 examples 故长期未发现 | DONE（F-08） |
| N-05 | 参数化测试 ID 含实时时间戳 | 阻塞 `-n` 并行（团队要求的 `-n 8` 无法使用） | DONE（F-12） |

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
| P-01 | `docs/plans/` 共 74 份计划（其中 67 份为 2026-06-17 批量生成的 bmad 计划），无索引/状态 | TODO（M6-Task17 逐份标注） |

## 9. 配置债

| ID | 项 | 状态 |
|----|----|------|
| C-01 | `pyrightconfig.json` 失效路径 | DONE（F-06） |
| C-02 | 根 `AGENTS.md` 失效引用 + 内容错位 | DONE（F-07） |
| C-03 | `stock_data/` 忽略规则 | DONE（owner 已落地） |
| C-04 | 覆盖率门槛**重复定义**（`pyproject.toml fail_under = 40` 与 CI env `COVERAGE_THRESHOLD: "40"` 同值两写；另有一处过期计划文档称 80） | DONE（CI 已改为以 pyproject 为单一来源；dispatch 输入留空即用 pyproject） |
| C-05 | 子仓 ruff 配置分裂（见 N-01） | TODO |
| C-06 | 子仓 mypy 覆盖（L1 已接入；其余 10 个子仓未纳入） | TODO |

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
