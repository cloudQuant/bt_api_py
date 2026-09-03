# 迭代 04 交接清单

## 当前可交接状态

- 本地代码候选：`18d787bc71cc0db03cb9f6ccd7589e25e311a16f`。
- 本地实现证据：`docs/acceptance/iteration-04-candidate-receipt.json`、`docs/acceptance/iteration-04-candidate-report.md`、`docs/acceptance/iteration-04-wheel-receipt.json`。
- 结论：运行时/交付物/核心子模块/文档本地验证通过；严格 T7 候选和 External Release Gate 均为 **Blocked**。
- 未执行任何远端写操作：没有 push、PR、ruleset、branch protection、environment、PVR、TestPyPI 或 PyPI 发布。

## 必须先完成的本地/上游工程工作

1. 建立一个只处理 `scripts/` 的质量迭代，使下列精确命令均返回 0：

   ~~~bash
   /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m ruff check bt_api_py tests scripts
   /Users/yunjinqi/opt/anaconda3/bin/conda run -n base python -m ruff format --check bt_api_py tests scripts
   ~~~

   当前基线为 2,215 个 Ruff 发现、3 个解析错误和 16 个待格式化文件。先修语法错误，再按可审查的小批次修复；不要通过排除规则隐藏。

2. 在四个子模块上游仓库独立落地本地提交，然后读回远端 SHA：

   | 仓库 | 本地提交 | 发布/版本注意事项 |
   | --- | --- | --- |
   | bt_api_base | `123b03a` | 新增 `pyarrow` 运行时依赖，发布前必须选择新的可发布版本。 |
   | bt_api_binance | `e08ef32` | 离线测试修复；推送后重新运行 package CI。 |
   | bt_api_okx | `91027d1` | 真实网络探测已标记；发布前核对 pyproject、模块和发布版本的一致性。 |
   | bt_api_ctp | `5686d3f` | CTP bar 时间字段修复；发布前核对源码/标签版本一致性。 |

3. 仅在四个 SHA 都可远端 fetch 后，创建单独的 parent gitlink bump PR；不要先 push 一个引用不可达子模块提交的父仓库分支。

4. 对 56 个 unavailable 子模块分批初始化并运行 `--profile all --diagnostic`。每个真实失败都应有 issue/PR、阶段日志和明确的 unverified/experimental 状态；在连续完整绿色基线前，all-profile 保持 scheduled diagnostic。

## 仓库管理员待办

以下项目需要明确管理员授权与执行，不能由本地候选收据替代：

1. 为 `dev`、`master` 和发布 tag 创建并读回 rulesets/branch protection；禁止未经审查的直接推送、force push 和删除。
2. 配置至少两名可审查维护者以及 CODEOWNERS 适用的审批策略。
3. 将以下 checks 设为与 PR 类型相匹配的 required checks：
   - `Tests / Quality Gate`
   - 涉及 gitlink 时的 `Submodule Gate / Core Reference`
   - 任何批准后的安全/发布检查
4. 创建受保护的 `testpypi`、`pypi` environments，配置审批、最小权限和可信发布；不要把现有 `github-pages` 当作发布环境。
5. 评估并启用 Private Vulnerability Reporting，或记录正式豁免及替代安全披露流程。
6. 创建真实的 PR，保存 checks、两位审查者、合并 SHA、Actions URL 与 artifact URL；本地 `/private/tmp` 工件不能代替托管 CI 证据。

## 发布前复核顺序

1. A–E 功能 PR 已在同一远端 `dev` 基线审查合并。
2. required checks 与真实 core-reference artifact 成功。
3. 从受保护的 `master` 可达 SHA 运行 TestPyPI；验证 `expected_sha`、新环境安装和 `doctor --bundle core-reference --format json`。
4. 版本、tag、wheel/sdist digest 与 TestPyPI smoke 全部一致后，才创建发布和生产 PyPI 操作。
5. 最后将 F 的候选收据更新为远端 SHA 与 Actions 证据；此前不得标记 Released。

## 原用户工作区保护

本迭代只在 `/private/tmp/bt_api_py_iter04_impl_20260903` 中实施。不要用 `git reset`、`git checkout --` 或工作树清理操作处理用户主工作区已有的 `bt_api/bt_api_ctp` 变更；它不属于本迭代，也不应被暂存或提交。
