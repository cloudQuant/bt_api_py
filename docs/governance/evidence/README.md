# 治理演练证据（Governance Evidence）

> 本目录存放迭代03（M6）端到端演练与后续运营期的**脱敏证据摘要**。
> 状态：待填充——M6 演练由管理员与发布负责人执行后归档至此。

## 归档规则

1. 只提交脱敏摘要：不含 token、API key、私钥、PyPI token、原始私有 API
   payload 或下载的发布包。原始响应仅保留在管理员受控位置。
2. 每份证据必须可回溯：记录产生时间、执行人角色、对应决策门（D0–D8）和
   关联 PR / workflow run URL。
3. 文件命名：`<里程碑>-<主题>-<YYYYMMDD>.md`，例如
   `m6-draft-pr-drills-20260901.md`。

## 各里程碑应产生的证据

| 里程碑 | 证据 | 通过标准 |
|---|---|---|
| M1 | `dev` 创建 SHA、bootstrap merge SHA、`master → dev` 同步 PR、默认分支切换前后 API 摘要 | 链路 SHA 可串联；fork 新 PR 默认目标为 `dev` |
| M4 | 五类草稿 PR 演练（文档/R0、R2 核心、性能、hotfix、SHA bump）：PR URL、head SHA、base branch、check 名称与结果 | `PR Governance / Summary`、`Tests / Quality Gate`、`Submodule Gate / Summary` 在适用与不适用路径均稳定出现 |
| M5 | TestPyPI 演练记录：candidate SHA、`expected_sha` 校验结果、新鲜环境安装命令与 smoke 结果、版本号 | TestPyPI 失败时不创建 Release；SHA 与版本可追溯 |
| M6 | 验收矩阵七维证据：分支模型、所有权（CODEOWNERS errors API）、Ruleset 与 manifest diff、CI、安全（gitleaks 记录）、发布、子模块 pilot | 全部维度与 manifest 一致；无 `Waiting for status` 卡死 |
| M7 | 每周治理指标摘要（schema 见 `docs/governance/metrics-schema.json`） | 连续 4 周满足稳定化退出条件后方可宣称流程持续运行 |

## Ruleset 启用前置条件（再次强调）

`.github/governance/rulesets/*.json` 中任何 `disabled` 的 Ruleset，只有在：

1. 对应草稿 PR 演练证据归档至本目录；
2. 决策门阻塞解除（D3 双维护者、D4 发布环境等，见 decision-log.md）；
3. 管理员在同一治理提交中同步翻转远端状态与 manifest 的 `enforcement` 字段；

三者齐备后才允许置为 `active`。`scripts/ci/verify_github_governance.py`
会在 CI 中对漂移报非零退出。
