# `scripts/` 使用手册

`scripts/` 保存项目维护、测试、发版和受控运维的辅助工具。它不是应用运行时的一部分：使用前应先确认当前分支、工作区状态和所需凭据；任何会改写文件、暂存 Gitlink、推送远端或访问真实交易账户的工具，都必须先阅读本文件中对应的边界说明。

本目录现在只保留一个 canonical 入口。历史上存在的 `scripts/testing/`、`scripts/tools/` 和顶层重复副本已移除；不要重新复制脚本到多个目录。兼容入口仅在下文明确标注的地方保留。

## 快速选择

| 目标 | 首选命令 | 会修改什么 |
| --- | --- | --- |
| 运行默认离线测试 | `./scripts/run_tests.sh --fast` | 生成本地 `logs/`，可选 coverage/HTML 报告 |
| 切换主仓库和子仓库分支 | `scripts\switch_all_branches.bat dev` 或 `./scripts/switch_all_branches.sh dev` | 本地分支与远端跟踪引用 |
| 发版前同步子仓库指针 | `scripts\update_gitlinks.bat --check`，确认后去掉 `--check` | 仅暂存根仓库 Gitlink |
| 更新支持状态文档 | `python scripts/generate_exchange_support_docs.py --check` | 无 `--check` 时改 README 与文档标记块 |
| 检查 CI 质量基线 | `make quality-ratchet`、`make format-ratchet` | 只读；带 `--update` 时更新已批准基线 |
| 安装根包与全部子包 | `scripts\install_all.bat` 或 `./scripts/install_all.sh` | 当前 Python 环境 |

从 Windows PowerShell 调用 `.bat`；macOS/Linux 或 Git Bash 调用 `.sh`。Python 工具统一从仓库根目录执行：`python scripts/<name>.py ...`。

## Git 与发版工具

### 分支切换：`switch_all_branches`

```bat
scripts\switch_all_branches.bat dev
scripts\switch_all_branches.bat master
```

```sh
./scripts/switch_all_branches.sh dev
./scripts/switch_all_branches.sh master
```

该工具将根仓库与所有已初始化的递归子仓库统一到 `dev` 或 `master`。它先确认每个工作树干净、每个 `origin/<branch>` 存在，随后执行 fetch、switch 和仅 fast-forward 的同步；不会执行 reset、强推或创建 merge commit。预检失败时不开始切换。子模块分支头不同于根仓库记录的 Gitlink 时，根仓库会显示子模块指针变更，这是正常的待发布状态。

### Gitlink 同步：`update_gitlinks`

```bat
scripts\update_gitlinks.bat --check
scripts\update_gitlinks.bat
```

```sh
./scripts/update_gitlinks.sh --check
./scripts/update_gitlinks.sh
```

用于根仓库发版提交前。`--check` 只显示当前一级子仓库 `HEAD` 与根仓库记录之间的差异。无参数模式只执行 `git add -- <submodule>` 来暂存 Gitlink，绝不提交、推送或暂存根仓库其他文件。任一递归子仓库未初始化、有未提交修改或存在 Gitlink 冲突时，脚本会停止。

建议顺序：子仓库提交并推送 → `switch_all_branches` → `update_gitlinks --check` → `update_gitlinks` → `git diff --cached --submodule=log` → 根仓库提交、CI、tag/Release。不要用 `git submodule update` 代替分支切换：它通常会按根仓库记录的 SHA 检出并使子仓库处于 detached HEAD。

### 批量同步与安装

| 脚本 | 用法 | 说明与边界 |
| --- | --- | --- |
| `git_pull_all.bat` / `.sh` | `./scripts/git_pull_all.sh --init -j 4` | 对当前分支执行并行 `git pull --ff-only`；`--init` 会初始化缺失子仓库但按 Gitlink SHA 检出。|
| `git_push_all.bat` / `.sh` | `./scripts/git_push_all.sh -j 4` | 并行推送根仓库和子仓库的当前分支；会改变远端，先确认每个待推送提交。|
| `bump_all_submodules.py` | `python scripts/bump_all_submodules.py --help` | 生态版本升级编排器；先查看帮助、在隔离分支运行并审阅各子仓库 diff。|
| `audit_submodule_changes.py` | `python scripts/audit_submodule_changes.py` | 只读汇总子仓库未提交修改，适合切分支和发版前排查。|
| `install_all.bat` / `.sh` | `./scripts/install_all.sh` | 一次安装根包和全部子包。|
| `install_bt_api_submodules.py` | `python scripts/install_bt_api_submodules.py --help` | Python 安装实现；当需要定制安装参数时使用。|

## 测试、质量与分析

### 常用测试入口

| 脚本 | 典型用法 | 适用场景 |
| --- | --- | --- |
| `run_tests.sh` | `./scripts/run_tests.sh --fast` | 项目标准 runner；支持 `--ctp`、`--parallel N`、`--cov`、`--html`、`-m <marker>`。默认排除 CTP。|
| `run_optimized_tests.sh` | `./scripts/run_optimized_tests.sh` | CI 风格的优化测试组合；Makefile 的优化测试目标使用它。|
| `run_base_tests.sh` | `./scripts/run_base_tests.sh -v` | 基础/非网络测试集合。|
| `run_exchange_tests.sh` | `./scripts/run_exchange_tests.sh okx --network -v` | 单交易所测试；仅在明确允许网络访问时加 `--network`。|
| `run_market_tests.sh` | `./scripts/run_market_tests.sh ticker -v` | 公开行情相关测试。|
| `run_auth_tests.sh` | `./scripts/run_auth_tests.sh account -v` | 需要账户或认证配置的测试；不要在无隔离凭据的环境执行。|
| `check_file_sizes.py` | `python scripts/check_file_sizes.py` | 扫描超过治理阈值的源文件。|

`scripts/analysis/` 的 8 个文件是历史 import/CLI 兼容入口；实现唯一来源仍是顶层同名脚本。不要删除或修改这些 wrapper 的代理语义。

| Canonical 脚本 | 用法 | 输出/作用 |
| --- | --- | --- |
| `analyze_code_lines.py` | `python scripts/analyze_code_lines.py` | 统计代码行数和目录规模。|
| `analyze_code_quality.py` | `python scripts/analyze_code_quality.py` | 汇总代码质量问题。|
| `analyze_coverage.py` | `python scripts/analyze_coverage.py` | 分析测试覆盖率报告。|
| `analyze_slow_tests.py` | `python scripts/analyze_slow_tests.py` | 识别慢测试。|
| `check_code_quality.py` | `python scripts/check_code_quality.py` | 检查类型标注和文档注释质量。|
| `check_core_isolation.py` | `python scripts/check_core_isolation.py` | 检查 core/插件隔离约束。|
| `extract_capabilities.py` | `python scripts/extract_capabilities.py` | 提取交易所能力矩阵。|
| `extract_capabilities_v2.py` | `python scripts/extract_capabilities_v2.py` | 改进版能力矩阵提取。|

附加质量工具：

- `analyze_docstrings.py`：只读盘点缺失 docstring 和中文注释；先运行它再决定是否修复。
- `fill_missing_docstrings.py`：AST 自动写入 docstring；只在专用分支运行，并逐项审阅 diff。
- `measure_public_api_quality.py`：统计公开 API 的 docstring 与参数注解覆盖率。
- `optimize_code.sh`：调用格式化/升级类工具；会修改工作区，运行前确保工作区干净。

### CI 专用工具：`scripts/ci/`

这些脚本由工作流、Makefile 或对应测试保护。除非正在修改关联工作流/测试，不要把它们当作通用代码格式化工具；尤其不要在未经批准时使用任何 `--update` 或 `--force-update`。

| 脚本 | 功能 |
| --- | --- |
| `check_docs_contract.py` | 验证文档生成标记与公开文档合同。|
| `check_format_ratchet.py` | 逐子仓库检查 Ruff format 债务只降不升。|
| `check_performance_baseline.py` | 验证 benchmark 基线。|
| `check_quality_ratchet.py` | 验证 Ruff/MyPy 质量债务只降不升。|
| `collect_pr_governance_context.py` | 收集 PR 治理上下文。|
| `offline_pip.py` | CI 离线 wheel 安装辅助。|
| `release_candidate.py` | 生成并验证发布候选的版本、摘要和清单。|
| `render_tech_debt.py` | 只读生成技术债报告。|
| `submodule_validation.py` | 按 profile 验证子仓库安装与合同。|
| `validate_pr_governance.py` | 验证 PR 治理输入。|
| `verify_github_governance.py` | 验证仓库治理文件。|
| `verify_wheel_contract.py` | 验证构建 wheel 的资源合同。|

常用只读命令为 `make quality-ratchet`、`make format-ratchet` 和 `python scripts/ci/check_docs_contract.py`。更新基线是版本化的治理动作，必须伴随审阅、测试和明确授权。

## 文档、插件与运维工具

| 脚本 | 用法 | 风险/说明 |
| --- | --- | --- |
| `generate_exchange_support_docs.py` | `python scripts/generate_exchange_support_docs.py --check` | 支持状态文档生成器；去掉 `--check` 才写 README、首页和项目概览中的标记块。|
| `docs/clean_markdown.py` / `.sh` | `python scripts/docs/clean_markdown.py --help` | Markdown 清理工具；先在副本或分支上预览结果。|
| `fix_plugin_entries.py` | `python scripts/fix_plugin_entries.py --help` | 为子仓库补齐插件 entry point；会写文件，应在新增适配器时使用。|
| `verify_exchange_bundle.py` | `python scripts/verify_exchange_bundle.py --help` | 只读检查交易所 bundle 安装状态。|
| `verify_repository_baseline.py` | `python scripts/verify_repository_baseline.py --help` | 只读输出仓库与插件基线清单。|
| `reconcile_tick_universe.py` | `python scripts/reconcile_tick_universe.py --help` | 对比订阅 tick universe 和落盘结果。|
| `split_ctp_wrapper.py` | `python scripts/split_ctp_wrapper.py --help` | 拆分 CTP/SWIG wrapper；会重写代码，只在专用变更中运行。|
| `start_monitoring.py` | `python scripts/start_monitoring.py --env production` | 启动监控；会启动服务，应按部署流程运行。|
| `clear_root_logs.sh` | `./scripts/clear_root_logs.sh <target-dir>` | 删除指定日志目录内容；务必传入精确目标路径。|
| `diagnose_okx.py` | `python scripts/diagnose_okx.py --help` | 查询 OKX 诊断信息；可能读取账户配置或访问网络。|
| `get_ibkr_cookie.py` | `python scripts/get_ibkr_cookie.py` | 读取本机浏览器 IBKR cookie；严禁打印或提交 cookie。|
| `login_ibkr_gateway.py` | `python scripts/login_ibkr_gateway.py --help` | IBKR Gateway 会话登录辅助；需要交互/凭据。|
| `scrape_margin_docs.py` | `python scripts/scrape_margin_docs.py --help` | 通过 Playwright 抓取 Binance 保证金文档；会访问网络并写本地文档。|

以下 6 个兼容入口明确 **retired**，仅为让旧命令给出迁移提示而保留，不能用于生成内容：

- `generate_enhanced_docs.py`、`docs/generate_enhanced_docs.py`
- `generate_modern_docs.py`、`docs/generate_modern_docs.py`
- `generate_plugins/generate_exchange_plugin.py`、`docs/generate_plugins/generate_exchange_plugin.py`

## 本次清理

已删除三类脚本：已完成的批量迁移/自动改写工具、无引用的专项验证器与过时文档生成器，以及所有和 canonical 入口完全重复的 `testing/`、`tools/` 或顶层副本。删除后的功能映射已在上表给出；历史计划和归档文档中保留的旧路径仅描述当时的事实，不应再作为运行命令。

新增脚本时，先确认现有 canonical 工具不能覆盖需求；按功能放入 `ci/`、`docs/` 或顶层，提供 `--help`/安全预检、为写入和网络操作声明边界，并同步更新本文件与 `scripts/SKILLS.md`。
