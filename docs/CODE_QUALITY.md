# bt_api_py Code Quality Standards

本文档记录项目的代码质量标准、工具链及近期优化，遵循行业最佳实践。

## 质量检查命令

| 命令 | 说明 |
|------|------|
| `make lint` | Ruff 语法/风格检查 |
| `make format` | Ruff 自动格式化 |
| `make format-ratchet` | 检查各子仓的 Ruff format 债务不增长（CI 阻塞） |
| `make format-ratchet-update` | 仅在至少一个子仓 format 债务下降且无任何增长时刷新快照 |
| `make type-check` | Mypy 类型检查 |
| `make security-scan` | Bandit 安全扫描 |
| `make check` | lint + type-check |
| `make pre-commit-run` | 运行全部 pre-commit 钩子 |
| `make test` | 通过 canonical runner 并行运行测试套件，默认 marker 为 `not ctp` |
| `make test-cov` | 与 `make test` 同范围，生成 statement + branch coverage |
| `make test-ctp` | 显式使用 `--ctp -m ctp` 仅运行 CTP marker（需对应环境） |
| `make test-performance` | 运行唯一离线性能 node、生成临时 pytest-benchmark 报告并按版本化绝对上限 fail closed |
| `make public-api-quality` | 统计主包公共 callable 的 docstring 与参数注解覆盖 |
| `make tech-debt-report` | 只读盘点根仓 2 条渐进 Ruff ignore 规则的当前债务 |

## 迭代07：全量门禁与棘轮

- `make lint` 是主包和根测试的快速检查；完整范围请使用 `make lint-all`。
- `make test` 与 `scripts/run_tests.sh` 默认注入 `not ctp`；`--ctp` 只解除该默认排除，不代表“仅 CTP”。仅 CTP 必须用 `./scripts/run_tests.sh --ctp -m ctp` 或 `make test-ctp`。runner 必须同时传播 pytest 和 `tee` 的失败状态，不能用摘要文本覆盖真实退出码。
- `make test-performance` 固定运行 `normalize_event` 的唯一 orderbook benchmark node，并保持 `-n 8`。基线 [`2026-09-21-performance-baseline.json`](acceptance/2026-09-21-performance-baseline.json) 要求 mean ≤0.5ms、rounds ≥1000；`check_performance_baseline.py` 对缺/空/坏 JSON、名称集合漂移和非法统计 fail closed。Make 使用临时目录且退出后清理；workflow 只在 checker 成功后上传。当前 0.5ms 只代表灾难性退化安全上限（约 ≥2000 events/s），不是敏感趋势检测。
- 当前 pytest-benchmark 5.1.0 + xdist 3.8.0 的 singleton `-n 8` 运行由一个 worker 产出有效 JSON，其余 7 个空闲 worker会记录无 benchmark warning。基线 schema v1 因而强制 exact singleton；增加第二个 benchmark 前必须先解决多 worker 同路径报告聚合/覆盖风险。7 次本地有效报告已通过，GitHub hosted 与 schedule 恢复仍 **NOT_RUN**。
- `make quality-ratchet` 是 CI 阻塞门禁，覆盖主包、根测试、scripts、examples 和全部已检出的子仓 `src/tests`；快照记录的范围缺失、或当前发现的范围未写入快照，均必须失败，不能静默缩小或扩大。
- 常规 `--update` 只可在范围完全一致且债务下降时固化新低；审查过的范围扩张才可显式使用 `--force-update`，且仍不得删除既有 scope 或抬高旧范围债务。它不是无条件重基线命令。
- CI 的 `quality-gate` 现在显式汇总并逐项阻断 `quality`、`quality-ratchet`、`compatibility`、`full-suite`、`wheel-contract` 与 `format-ratchet`；不能因漏列 `quality-ratchet` 而让棘轮失败留下绿色聚合结果。对应 YAML 结构合同在 `tests/scripts/test_check_quality_ratchet.py`。
- CI `full-suite` 固定使用 `python -m pytest tests -v -n 8 --cov-branch`。手工 `coverage-threshold` 只能经 step `env` 进入 shell，通过 0..100 十进制数值白名单后以 Bash 数组逐 argv 传递；`coverage-full.xml` 与 `htmlcov/index.html` 必须非空并严格归档。Codecov 仅为可选遥测。本地结构合同 **20 passed**，GitHub hosted **NOT_RUN**。
- 发布候选必须由 `scripts/ci/release_candidate.py` 记录并复验 source SHA、版本、wheel/sdist 文件名/大小/SHA256、wheel-contract receipt、manifest 与 `SHA256SUMS.txt`；跨 job 还要使用 build 输出的 wheel/sdist/manifest 独立摘要锚。手动 dispatch 只能构建，TestPyPI/smoke/PyPI 必须在同一 release 运行、同一 artifact 上且同时受显式 enable gate 保护。TestPyPI 仅用于精确 wheel 下载，fresh venv 从已验证本地 wheel 安装，依赖只从 PyPI 解析。该本地合同不替代 hosted/OIDC/Environment/trusted publisher/Ruleset/action SHA/构建依赖锁定，生产发布仍 **NO-GO**。
- 2026-09-20 的入库棘轮快照为 **10** 项。`make lint-all` 在存量尚未清零前会非零；这不是通过证据，只有棘轮不回退才表示“未恶化”。
- 子仓 `ruff lint` 与 `ruff format` 已拆为各自的 report-only CI 矩阵任务；当前 10 项 lint 与 453 个待格式化文件（快照基线 452，dirty OKX 87 > 86）清零、稳定 CI 且可独立回滚前，两者均不得升级为 blocking；详见 [`TECH_DEBT.md`](TECH_DEBT.md) N-06/N-18。
- `make format-ratchet` 与 CI 的 `format-ratchet` job 使用 [`docs/acceptance/2026-09-20-format-ratchet.json`](acceptance/2026-09-20-format-ratchet.json) 对 15 个已检出子仓逐仓复算。任一计数上升、新子仓或缺失子仓都会失败；`quality-gate` 也依赖该结果。它**不会运行** `ruff format`、不会改写子仓，也不会把 report-only 的 `submodule-format` 转为 blocking；当前 dirty checkout 为 453，故相对 452 快照正确失败。
- 当且仅当所有子仓均未增长且至少一个子仓计数下降时，才可运行 `make format-ratchet-update` 固化新低；刻意改变模块范围或基线版本必须经审查后显式使用脚本的 `--force-update`。
- `scripts/ci/check_format_ratchet.py` 的 `B404`、`S603` 与 `B603` 是 E-06 的精确行级例外：它只能调用固定的 Ruff argv，工作目录固定为仓库根且 `shell=False`；`tests/scripts/test_check_format_ratchet.py` 锁定这些不变量。该豁免不扩大到其他子进程调用，并已由该脚本的 Bandit 扫描复核。
- `make tech-debt-report` 只读调用固定的 `ruff check --output-format json --select`，范围仅为根仓 `bt_api_py`、`tests`、`scripts`、`examples`，并从根 `pyproject.toml` 校验 2 条渐进 ignore 及其行内理由。最近一次完整报告的历史证据为 **37** 项（TC001 22、TC003 15）；Tasks37–41 仅有目标 TC 零新增证据，未取得 fresh 全范围库存。它不扫描子仓、不改配置或源码，也不代表这些债务已清零。
- `PERF402`、`S113`、`PERF401` 与 `S110` 在全范围隔离扫描中均为 0；`PERF403` 的唯一命中已在 `LogstashHandler.format_to_logstash` 以离线行为契约保护后清理；`S112` 的两个 IBKR Cookie 脚本命中已在 mock 行为契约保护后改为安全 debug 诊断。最后 9 个根 `S110` 命中仅补充不含异常正文、订单、账户或消息内容的异常类型诊断，并由 fake-only 回归锁定其 best-effort 语义。六条规则均已从根全局 ignore 移除，`S110`、`PERF401`、`PERF402` 与 `S113` 也已从 `tests/*.py` 例外移除；CTP vendor 路径的范围化 `S110` / `S112` per-file 例外不属于本次清理。此后根范围的新命中会直接进入 lint。
- 对 `TC001` / `TC003` 的清理，`from __future__ import annotations` 不是充分证据：可能被反射的注解必须新增或扩展 `typing.get_type_hints` 回归。当前 63 目标契约证明先前部分 `TYPE_CHECKING` 移动会破坏运行时解析，故五个风险 mixin helper 的运行时导入已恢复；本轮仅 `PolicyEngine.__init__` 的局部 `Callable` 注解通过实际运行时用法审计后最小移动。2026-09-20 对当时 **37/37** 项的逐项审计仍是历史兼容性边界；此后只允许在明确证明不进入反射面时减少库存。`fill_missing_docstrings.py` 的真实 `get_type_hints` 回归反而证明 `Iterable` 是运行时依赖，因此保留直接导入和报告命中。别名或全限定名规避均不可用于压债。`tests/test_type_hint_introspection.py` 必须随最终交付纳入版本控制，才可作为持久反射保护。
- artifact-first validator 与 wheel contract 的离线依赖证据必须通过显式、存在且绝对的 wheelhouse 提供：运行会清除继承的 `PIP_*` 来源、禁用 index/cache，并保留默认 PEP 517 构建隔离、fresh wheel/venv、完整依赖解析和安装后的 `pip check`。环境边界接受只读 `Mapping[str, str]` 后复制，调用方对象不得被修改；复制后仅重建 `PIP_CONFIG_FILE`、`PIP_NO_INDEX`、`PIP_FIND_LINKS`、`PIP_NO_CACHE_DIR`。公开 validator 的 wheelhouse 接受 `Path | str | None`，但必须在创建 artifacts 前单次归一化，内部只流转 `Path | None`；`Distribution.locate_file()` 的抽象 `SimplePath` 必须显式转为本地 `Path` 后继续 purelib/platlib 与存在性校验。历史 artifacts 只能留作证据，不能被新运行选择；根/base 均须从 fresh venv `site-packages` 导入。测试生成的最小 `pytest-socket` 仅覆盖 socket 隔离选项，真实发布验收仍需要真实归档工件。
- Task33 将 artifact-first 四文件的 5 个边界错误降为 0，定向 **12 passed**；`Mapping` 同时用于真实运行时校验，非映射环境在复制前 fail closed，因此不是 TC003 规避。Task30 的精确 256 文件 mypy 证据仍为 **57 errors / 13 files**；因两份测试禁止直接读取，Task33 只取得排除它们后的可比 254 文件结果 **52 errors / 11 files**，不得将其表述为新的精确 CI 全量结果。该收敛未使用 `Any`、`cast`、ignore、`noqa` 或配置放宽。
- Task34 将三个测试文件的 4 个 mypy 错误降为 0：`ast.walk` 结果先显式窄化为 import 节点，PyArrow/YAML 假模块以精确 `ModuleType` 子类声明真实运行时属性；定向 **7 passed**，行为、断言和依赖隔离边界不变。可比 254 文件结果由 **52 → 48 errors（11 → 8 files）**；Task30 的精确 256 文件证据仍为 **57 errors / 13 files**，精确命令未重跑。
- Task35 将两份离线脚本测试中的 8 个 `ModuleType` 动态属性错误降为 0：Playwright、browser-cookie3 与 requests/requests.exceptions 均使用真实模块子类，`RequestException` 后继续尝试 Safari 的分支由两个脚本副本共同覆盖；定向 **4 passed**，不启动浏览器或网络。fresh `--no-incremental` 的可比 254 文件结果为 **41 errors / 7 files**；它额外暴露一个既有 `_execution_session.py` 注解错误，故不把 Task34 的 48/8 与本值写成直接算术下降。
- Task36 将生产监控/Hyperliquid 两个离线测试的 18 个假模块属性错误降为 0，定向 **7 passed**。独立审查发现并修复 `config_loader` 只有注解、运行时没有属性却以 `raising=False` 注入的假接口：fixture 现先绑定安全失败占位函数，异常注入恢复默认 `raising=True`。同一 fresh 254 文件范围由 **41 → 23 errors（7 → 5 files）**；精确 256 文件命令仍未重跑。
- Task37/38 先消除主包 journal 重放与 OKX WebSocket 清理测试的 6 个类型错误：`reservation_cancel_events` 以四段字符串 client key 和异构 JSON record 精确建模，测试事件则用 `Literal` tuple 联合覆盖全部七种形状。`make type-check` 恢复为 **128 source files / 0 issues**，对应 **164 + 3 passed**，独立复审 PASS；同一 fresh 254 文件范围由 **23 → 17 errors（5 → 3 files）**。
- Task39 将 Actions YAML 的 `BaseLoader` 返回值保持为 `object`，再以带路径的 mapping/list/string 运行时 guard fail closed 收窄，而不是 `cast`；坏形状负例和既有 workflow 合同共 **21 passed**，目标 5 个错误归零。
- Task40 将 WebSocket infrastructure 的 11 个错误收敛为最小 receiver/connection/server/socket `Protocol`，保持声明的 `websockets>=12.0` 公共接口边界；独立复审还抓出并修复 `_receive_messages` 具体绑定 `AsyncMock` 导致 consumer 新增 1 个错误。loopback server 改用 `127.0.0.1:0`，不再与本机 Anki 的 8765 端口冲突。当前 15.0.1 两文件 **8 passed**，复审在 13.1/15.0.1 各 **7 passed**；12.0 未单独安装执行，不作实跑声明。
- Task41 让隔离 CTP probe 的 fake runner 接收真实 `list[str]` 命令并返回 `CompletedProcess[str]`，目标 1 个错误归零、**8 passed**。Tasks37–41 联合定向 **203 passed**；最终同一 fresh 254 文件范围为 **0 errors / 0 files**，但精确 256 文件命令仍未重跑，不能冒充精确 CI 全量类型证明。本批只取得目标文件 TC001/TC003 零新增证据，没有重新运行会触及两份禁读测试的全范围债务报告。
- `scripts/ci/render_tech_debt.py` 的 `B404`、`S603` 与 `B603` 是 E-07 的精确行级例外：argv、cwd 和 `shell=False` 均固定，配置缺项、异常退出、坏 JSON 或意外规则均 fail closed；`tests/scripts/test_render_tech_debt.py` 锁定这些边界。该豁免不扩大到其他子进程调用。
- L1 子仓的类型检查使用 `make type-check-l1`；`make security-scan` 当前只覆盖主包。2026-09-20 对 15 个子仓 `src` 以 Bandit 默认规则、`--ignore-nosec` 所作的只读预检为 26 项（High 1 / Medium 3 / Low 22），其中 CTP SFTP connector 的 `B507` High 必须先由子仓修复；在 High 为 0 之前，不得以快照把它写成绿色 required gate。后续子仓静态棘轮还须与依赖漏洞审计和 secret/history 审计分开表述。
- `make public-api-quality` 以固定的 AST 路径规则统计主包源码，而非解析运行时 `__all__`；它排除私有模块、测试支持、生成/构建目录，并输出逐文件明细和实际排除项。2026-09-20 的可复算结果为 docstring **594/691（85.96%）**、参数注解 **1016/1035（98.16%）**。
- `bt_api_py/_ctp_probe.py` 的 `S603`（Ruff）及 `B404`/`B603`（Bandit）是经批准的**行级**安全例外，不是配置级忽略：命令、解释器和 `-c` 子脚本均为模块常量，不使用 shell，调用方数据只经固定环境键传递，子进程输出会脱敏。对应行为测试在 `tests/test_isolated_trader_probe.py`；详见 [`TECH_DEBT.md`](TECH_DEBT.md) E-05。该离线测试不证明 SimNow/CTP 连通性。
- `docs/plans/README.md` 已在 2026-09-21 完成 **74/74** 逐份复核：已落地 61、已废弃/阻塞 8、部分/未开工 5、待确认 0。状态依据当前工作树的源码、测试和配置，不自动等同于 Git HEAD、外部 CI 或生产能力证明。
- `coerce_funding_snapshot` 的职责拆分遵循干净文件与行为保持边界：入口 AST **81 → 28**。独立审查曾发现自定义 `Mapping.source` 被提前读取的回归，修复后用状态化 Mapping 锁定读取次数、异常归一化和既有错误优先级；定向 **17 passed**，目标 Ruff/format/mypy 与 diff-check 均通过。
- `fill_missing_docstrings.py` 不再用宽泛 `ast.AST` 容器掩盖节点差异：精确 AST 联合类型与 `TypedDict` 使目标 mypy **20 → 0**、精确 CI 范围 **77 → 57 errors**。行为合同覆盖 multiline/inline/async、tab、同索引 action 与 UTF-8 文件前两行的 PEP 263 cookie 位置；生成源码只有在重新解析且目标 docstring 全部形成时才落盘，否则 fail closed。定向 **23 passed**，独立复审 **PASS**；split-line `async \\` / `def` 当前安全拒绝。

## 2025-03 优化（已执行）

### 1. 异常类型注解

- **exceptions.py**：为所有异常类的 `__init__` 参数添加完整类型注解
- 提升 IDE 补全与静态类型检查准确性

### 2. API 返回类型

- **bt_api.py**：`init_logger()` 添加 `-> object` 返回类型及 docstring

### 3. 异常处理改进

- **log_message.py**：`_get_project_logs_dir()` 中的 `except Exception` 增加 `as e` 与 debug 日志
- 便于排查包导入失败时的 fallback 路径

### 4. 安全扫描

- 新增 `bandit[toml]` 为 dev 依赖
- 新增 `make security-scan` 目标
- `pyproject.toml` 配置 `[tool.bandit]` 排除 CTP/tests

### 5. S113 requests timeout（2025-03 第二批）

- **functions/update_data/**：`download_swap_history_bar_from_binance.py`、`download_funding_rate_from_binance.py`、`download_bars_from_okex.py`、`download_spot_history_bar_from_okx.py`、`download_spot_history_bar_from_binance.py`、`update_exchange_symbol_info.py`：所有 `requests.get/post` 添加 `timeout=30`
- **functions/utils.py**：`get_public_ip()` 中 `requests.get` 添加 `timeout=10`
- 消除网络请求无限等待风险，符合安全最佳实践

### 6. pathlib 迁移（2025-03 第二批）

- **config_loader.py**：新增 `get_exchange_config_path(filename)` 辅助函数
- **containers/exchanges/**：`binance_exchange_data.py`、`okx_exchange_data.py`、`kraken_exchange_data.py` 配置路径迁移至 pathlib
- **functions/log_message.py**：`_get_project_logs_dir()`、`SpdLogManager` 中 `os.path` 迁移至 `pathlib.Path`
- 提升可读性与跨平台兼容性

### 7. 代码质量优化（2025-03 第三批）

- **S113 Kraken**：`live_kraken/request_base.py` 中 `req_lib.post()` 显式传入 `timeout=` 参数，消除 Ruff S113 静态检测误报
- **pathlib 扩展迁移**：`ib_web` 等 exchange_data 统一使用 `get_exchange_config_path()`
- **PERF 性能优化**：`pancakeswap_pool.py` 使用列表推导替代 `filter_by_tvl`/`filter_by_volume` 循环；`anomaly_detector.py`、`ensemble_model.py` 中 `_dict_to_features` 使用列表推导；`exchange_health.py`、`advanced_websocket_manager.py` 使用列表推导
- **logging_system**：`extra.update(kwargs)` 替代循环赋值
- **S110/S112 异常日志**：`live_ib_web_feed.py` portfolio 端点失败时记录 debug 日志；`monitoring/metrics.py` 中 metric.collect 失败时记录 debug 日志

### 8. 代码质量优化（2025-03 第四批）

- **S110/S112 异常日志**：`my_websocket_app.py` 代理解析、WebSocket 重启失败时增加 `logger.debug`；`monitoring/config.py` 清理资源失败时记录 debug 日志；`monitoring/elk.py` Logstash 发送失败时记录 debug；`monitoring/prometheus.py` 服务循环异常时记录 debug；`audit_logger.py` 解析/读取失败时记录 debug
- **PERF401 性能优化**：`pancakeswap_exchange_data.py` 稳定币与交易对使用 `list.extend` 替代循环 append；`live_dydx/spot.py` K 线归一化使用列表推导
- **S113 requests timeout**：`tests/containers/symbols/test_binance_symbol.py`、`tests/containers/bars/test_ok_request_bar.py` 中 `requests.get` 添加 `timeout=30`

## 编码规范（AGENTS.md 摘要）

- **行宽**：100 字符
- **类型**：公共 API 使用类型注解，优先 3.11+ 语法
- **异常**：使用 `bt_api_py.exceptions` 中的自定义异常
- **文档**：Google 风格 docstring
- **命名**：类 PascalCase，函数 snake_case，常量 UPPER_SNAKE_CASE

## 渐进式改进项

| 项目 | 说明 | 参考 |
|------|------|------|
| S110 | try-except 中增加安全诊断 | ✅ 根仓隔离扫描为 0；根与 `tests/*.py` ignore 已移除。诊断只记录异常类型，CTP vendor 的范围化 per-file 例外保留 |
| S112 | try-except-continue 增加安全诊断 | ✅ 根全局 ignore 已移除；IBKR Cookie 脚本仅记录浏览器名和异常类型，CTP vendor 的范围化 per-file 例外保留 |
| S113 | `requests` 调用添加 `timeout=` | ✅ 已修复（含 Kraken、tests） |
| PERF401 | 用列表推导替代循环 | ✅ 根、测试、脚本和示例隔离扫描为 0；已移除根和 `tests/*.py` ignore 与报告规则，未来命中直接进入 lint |
| TC003 | 标准库仅类型导入置于 `TYPE_CHECKING` | TODO：根仓渐进债仍有 15 项；先证明 `typing.get_type_hints` 与原注解字符串兼容，才能移动反射可见的导入；运行时反射确需的直接导入继续如实计入库存 |
| PERF402/403 | 用列表复制/字典推导替代循环 | ✅ 根仓当前均无命中；`PERF403` 已由 Logstash payload 行为契约保护后移除 ignore |
| pathlib 迁移 | `os.path` → `pathlib.Path` | ✅ exchange_data 已全部迁移 |

## 渐进式 Mypy 加强（2025-03）

- **已启用 `disallow_untyped_defs`**：`bt_api_py.exceptions`、`bt_api_base.event_bus`
- **后续扩展计划**：按模块逐步启用，建议顺序 `registry` → `bt_api` → `logging_*` → `containers` 子模块

## 测试覆盖率门槛（当前）

- 默认门槛来源是 `pyproject.toml` 的 `[tool.coverage.report] fail_under = 40`；CI 不另行硬编码阈值。仅手工 `workflow_dispatch` 可用经过校验的 `coverage-threshold` 临时覆盖该次运行。
- `make test-cov` / `./scripts/run_tests.sh --cov` 显式传入 `--cov-branch`；本仓的当前覆盖率证据是 statement + branch 口径，不得与未开 branch coverage 的运行混用。
- 2025-03 文档中出现的 60/70/80 是当时的历史目标，不是当前门禁，不得据此宣称 CI 要求 80%。
- 迭代07 的阶段目标为 55%；2026-09-21 Task41 后以 CI 同标记、8 workers 显式 `--cov-branch` 新鲜复算为 **67.27%**（1735 passed、2 warnings、110.06s）。若提高配置门槛，必须再次取得新的完整覆盖率证据。此前 1706 项 coverage 运行结束后曾出现一次 `pytest-rerunfailures 14.0` daemon thread teardown warning；本轮 marker 与 coverage 均未复现，但 TECH_DEBT N-19 继续保持 WATCH，不能仅凭一次未复现静默关闭。

## 参考文档

- [AGENTS.md](https://github.com/cloudQuant/bt_api_py/blob/master/AGENTS.md) - AI 代理开发指南
- [安全实践](./guides/security_best_practices.md) - 安全实践
