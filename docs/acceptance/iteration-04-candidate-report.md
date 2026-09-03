# 迭代 04 候选验收报告

> 代码候选（本地）：`9a0bb16049228da1278fe02957e9c0ff9911a0c4`
>
> 基线：`origin/dev@fdeb6c1182d8a4be5c9c2b713bba7007f1c03fa7`
>
> 取证完成时间：2026-09-03T06:39:11Z
>
> 机器：Python 3.11.8，Conda base
> 完整机器可读收据：`docs/acceptance/iteration-04-candidate-receipt.json`

## 结论

**本地代码候选和严格 T7 候选门禁均已通过；External Release Gate 仍为 Blocked。** 这只证明同一候选 SHA 的本地运行时、wheel、核心子模块、文档和严格质量门禁，不表示已发布、远端治理已完成，或 60 个子模块均已认证。

| 层级 | 状态 | 含义 |
| --- | --- | --- |
| Local implementation | Validated | 运行时契约、wheel、核心子模块档、活跃文档和范围内质量验证均通过。 |
| Strict T7 candidate | Validated | 计划指定的全量 `bt_api_py`、`tests`、`scripts` Ruff/format、mypy、测试、wheel、core-reference 和文档命令均返回 0。 |
| Community PR landing | Blocked | 四个子模块 gitlink 所引用的提交仍只存在于本地嵌套仓库，不能作为远端可克隆的 parent PR。 |
| External Release Gate | Blocked | 远端规则、环境、真实 PR/CI/审查和发布取证尚不存在。 |

## 已通过的本地证据

| 项目 | 结果 |
| --- | --- |
| 主仓收集 | 688 项测试被收集。 |
| 完整离线套件 | `SKIP_LIVE_TESTS=true pytest tests -q` 为 683 passed、5 skipped。 |
| T7 标记离线套件 | 677 passed、5 skipped、6 deselected；网络、集成、性能、e2e 与 CTP 按计划排除。 |
| 传输定向覆盖率 | 105 passed；四个关键模块的行覆盖率为 93.33%、100.00%、88.53%、89.62%，均不低于 85%。pytest-cov 的含分支展示覆盖率为 86.15%。 |
| 严格静态质量 | `ruff check bt_api_py tests scripts`、`ruff format --check bt_api_py tests scripts` 均通过；mypy 为 190 个源文件通过。 |
| 旧脚本修复 | 六个无法解析的历史生成器保留原入口，但改为明确、可测试的退役迁移提示；版本批处理要求显式 `--dry-run` 或 `--apply`，IBKR cookie 工具不再关闭 TLS 验证。 |
| wheel 契约 | 已安装 wheel 的资源、sdist 资源、源资源哈希一致；`doctor --bundle core-reference --format json` 成功。详见 `docs/acceptance/iteration-04-wheel-receipt.json`。 |
| 发布工作流回归 | `publish.yml` 中的 wheel 校验命令已被 YAML 解析和 `tests/test_package_resources.py` 覆盖，防止参数折叠或字面量 `+` 回归。 |
| 核心子模块档 | base、Binance、OKX、CTP 各自在干净虚拟环境中构建 wheel、安装、导入、收集和离线测试，4/4 通过；每阶段 stdout/stderr 均存在。工件位于 `/private/tmp/bt-api-py-iteration04-core-reference-9a0bb160-v3`。 |
| 支持文档 | 支持状态生成器检查、文档契约和严格 MkDocs 构建均成功。MkDocs 仅给出上游兼容性迁移提示，命令退出码为 0。 |

## 严格 T7 候选门禁（已通过）

以下命令均在上述本地候选 SHA 上返回 0：

~~~bash
/Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m pytest tests --collect-only -q
SKIP_LIVE_TESTS=true /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m pytest tests -m "not network and not integration and not performance and not e2e and not ctp" -q --maxfail=0
SKIP_LIVE_TESTS=true /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m pytest tests/bt_api_contract tests/forwarding --cov=bt_api_py.bt_api --cov=bt_api_py._direct_backend --cov=bt_api_py._operation_backend --cov=bt_api_py.forwarding.btapi_backend --cov-report=term-missing -q
/Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m ruff check bt_api_py tests scripts
/Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m ruff format --check bt_api_py tests scripts
/Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m mypy bt_api_py tests --ignore-missing-imports
~~~

为避免通过配置排除来掩盖旧脚本问题，本次没有修改 Ruff 排除规则。历史入口的退役行为已加入 `tests/scripts/test_legacy_generator_retirement.py`，全量 `scripts/` 目录可以被解析、检查和格式化。

## 全量子模块是完整诊断，不是全量认证

`--profile all --diagnostic` 生成了 60 个包的日志和分类：4 个通过、56 个 `unavailable`、0 个 `failed`。其 JSON 结果为 `failed`，原因是未初始化模块不具备可验证源，而不是任何包的测试失败。

这符合“不得把 0/60 或缺日志说成支持”的设计，但也意味着 56 个包只能保持 unverified/experimental，不能升级为 fully supported 或 certified。工件位于 `/private/tmp/bt-api-py-iteration04-all-diagnostic-9a0bb160-v3`，并已验证每个记录的日志路径均存在。

## 子模块提交尚未可被远端解析

本地核心验证依赖四个任务拥有的子模块提交：

| 子模块 | 本地候选提交 | 作用 |
| --- | --- | --- |
| bt_api_base | `123b03a` | 声明 gateway 所需的 `pyarrow` 运行时依赖。 |
| bt_api_binance | `e08ef32` | 修正 HTTP mock 生命周期，使离线测试不泄露网络。 |
| bt_api_okx | `91027d1` | 将真实 history-bar 探测标记为 network。 |
| bt_api_ctp | `5686d3f` | 补齐 CTP bar 的 server/open/close 时间字段。 |

这些 SHA 目前仅存在于本地嵌套仓库。它们必须先在各自上游仓库独立推送、审查和落地；在此之前，父仓库的 gitlink 不能作为可克隆的社区 PR 提交。

## 外部发布门禁复核

2026-09-03T06:39:11Z 对 GitHub 执行了只读复核：默认分支是 `dev`，但 rulesets 为空、`dev`/`master` 均未保护、仅存在 `github-pages` environment、PVR 未启用、没有开放 PR。详见 `docs/governance/evidence/iteration-04-external-gate.md` 和 `docs/operations/iteration-04-handoff.md`。

没有执行 push、创建 PR、修改 ruleset、environment、PVR 或发布操作。

## 工作区边界

实现始终在隔离工作树 `/private/tmp/bt_api_py_iter04_impl_20260903` 中完成。原用户工作区的 `bt_api/bt_api_ctp` 未被暂存、提交或纳入本候选；候选中的四个 gitlink 都是此隔离工作树内任务产生的本地提交，且已在上节明确标为远端落地前的阻塞项。

## 后续决策

本地候选无需回滚。获得授权后，应先独立落地四个子模块提交、为 56 个 unavailable 模块建立可复现源与问题跟踪，再创建拆分 PR 并完成管理员治理和发布环境门禁。此前状态应保持 **Local Candidate**，不得标记为 Released 或 Governance Complete。
