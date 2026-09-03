# 迭代 04 候选验收报告

> 代码候选：`18d787bc71cc0db03cb9f6ccd7589e25e311a16f`
>
> 基线：`origin/dev@fdeb6c1182d8a4be5c9c2b713bba7007f1c03fa7`
>
> 取证时间：2026-09-03T06:03:21Z
>
> 机器：Python 3.11.8，Conda base
> 完整机器可读收据：`docs/acceptance/iteration-04-candidate-receipt.json`

## 结论

本地实现已完成并经同一代码 SHA 验证；**严格的 T7 候选门禁为 Blocked，发布门禁也为 Blocked**。因此本报告不把任何本地绿色结果写成“已发布”“治理已完成”或“全部支持”。

| 层级 | 状态 | 含义 |
| --- | --- | --- |
| Local implementation | Validated | 运行时契约、wheel、核心子模块档、活跃文档和范围内质量验证均通过。 |
| Strict T7 candidate | Blocked | 计划中要求的全量 `scripts/` Ruff 与格式命令未通过。 |
| External Release Gate | Blocked | 远端规则、环境、真实 PR/CI/审查和发布取证尚不存在。 |

## 已通过的本地证据

| 项目 | 结果 |
| --- | --- |
| 主仓收集 | 682 项测试被收集。 |
| 离线完整套件 | 671 passed、5 skipped、6 deselected；网络、集成、性能、e2e 与 CTP 按计划排除。 |
| 传输定向覆盖率 | 105 passed；四个关键模块行覆盖率为 93.33%、100.00%、88.53%、89.62%，均不低于 85%。 |
| 范围内静态质量 | `bt_api_py`、`tests`、`scripts/ci`、本迭代生成器及子模块入口的 Ruff/format 通过；mypy 为 189 个源文件通过。 |
| wheel 契约 | 已安装 wheel 的资源、sdist 资源、源资源哈希一致；`doctor --bundle core-reference --format json` 成功。详见 `docs/acceptance/iteration-04-wheel-receipt.json`。 |
| 发布工作流回归 | `publish.yml` 中的 wheel 校验命令已被 YAML 解析和 `tests/test_package_resources.py` 覆盖，防止参数折叠或字面量 `+` 回归。 |
| 核心子模块档 | base、Binance、OKX、CTP 各自在干净虚拟环境中构建 wheel、安装、导入、收集和离线测试，4/4 通过。工件位于 `/private/tmp/bt-api-py-iteration04-core-reference-18d-LZxUie`。 |
| 支持文档 | 支持状态生成器检查、文档契约和严格 MkDocs 构建均成功。MkDocs 仅给出上游兼容性迁移提示，命令退出码为 0。 |

## 严格候选阻塞项

### 1. 全量旧脚本质量门禁

计划的以下两条命令在该 SHA 上失败：

~~~bash
/Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m ruff check bt_api_py tests scripts
/Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m ruff format --check bt_api_py tests scripts
~~~

- 第一条返回 1，报告 **2,215** 项发现，其中 229 项可用安全修复自动处理。
- 第二条返回 2：三个历史脚本无法被解析，另有 **16** 个文件需要格式化。
- 本迭代触及的 Python 范围已通过补充范围检查；但该结果不能替代计划要求的全量命令。因此不应降低为“候选通过”。

建议新开一个仅处理旧 `scripts/` 的质量迭代：先修复三个语法错误，再以固定 Ruff 配置分批清理，最后将这两条精确命令纳入绿色基线。不要通过在 Ruff 配置中排除旧脚本来掩盖此门禁。

### 2. 全量子模块是完整诊断，不是全量认证

`--profile all --diagnostic` 生成了 60 个包的日志和分类：4 个通过、56 个 `unavailable`、0 个 `failed`。其 JSON 结果为 `failed`，原因是未初始化模块不具备可验证源，而不是任何包的测试失败。

这符合“不得把 0/60 或缺日志说成支持”的设计，但也意味着 56 个包只能保持 unverified/experimental，不能升级为 fully supported 或 certified。工件位于 `/private/tmp/bt-api-py-iteration04-all-diagnostic-18d-y3ptCl`。

### 3. 子模块提交尚未可被远端解析

本地核心验证依赖四个任务拥有的子模块提交：

| 子模块 | 本地候选提交 | 作用 |
| --- | --- | --- |
| bt_api_base | `123b03a` | 声明 gateway 所需的 `pyarrow` 运行时依赖。 |
| bt_api_binance | `e08ef32` | 修正 HTTP mock 生命周期，使离线测试不泄露网络。 |
| bt_api_okx | `91027d1` | 将真实 history-bar 探测标记为 network。 |
| bt_api_ctp | `5686d3f` | 补齐 CTP bar 的 server/open/close 时间字段。 |

这些 SHA 目前仅存在于本地嵌套仓库。它们必须先在各自上游仓库独立推送、审查和落地；在此之前，父仓库的 gitlink 不能作为可克隆的社区 PR 提交。

## 外部发布门禁复核

2026-09-03T05:53:43Z 对 GitHub 执行了只读复核：默认分支是 `dev`，但 rulesets 为空、`dev`/`master` 均未保护、仅存在 `github-pages` environment、PVR 未启用、没有开放 PR。详见 `docs/governance/evidence/iteration-04-external-gate.md` 和 `docs/operations/iteration-04-handoff.md`。

没有执行 push、创建 PR、修改 ruleset、environment、PVR 或发布操作。

## 工作区边界

实现始终在隔离工作树 `/private/tmp/bt_api_py_iter04_impl_20260903` 中完成。原用户工作区的 `bt_api/bt_api_ctp` 未被暂存、提交或纳入本候选；候选中的四个 gitlink 都是此隔离工作树内任务产生的本地提交，且已在上节明确标为远端落地前的阻塞项。

## 后续决策

在修复旧脚本质量门禁、独立落地子模块提交、创建实际 PR 并由管理员完成外部门禁前，候选应保持 **Blocked**。本地实现无需回滚；如任一拆分 PR 需要撤销，应使用对应提交的 `git revert`，而不是重置用户工作区。
