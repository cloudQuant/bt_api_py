# 治理决策日志（Decision Log）

> 计划来源：`docs/迭代计划/迭代03-开源项目治理与社区PR协作/正式迭代计划.md`（v2）
> 记录日期：2026-08-23
> 维护规则：每个决策门只能是 `approved` / `rejected` / `blocked` 三态之一，禁止隐式默认值。
> 状态变更必须附决策人、日期与证据链接。

## 决策门状态总览

| ID | 议题 | 推荐值 | 决策人 | 状态 | 证据 / 阻塞原因 |
|---|---|---|---|---|---|
| D0 | 默认分支模型 | 新增 `dev` 为日常集成与默认分支；`master` 为发布线。远端切换晚于 M1 bootstrap | 管理员 + 核心维护者 | **approved** | v2 计划获实施授权（2026-08-23 用户指示按最新迭代计划开发）；BOOTSTRAP_SHA=`1436ec0adaf4b283a54bfe69f5be163df3e3e3b9`；默认分支切换为管理员动作，见 M1/M6 |
| D1 | Python 兼容口径 | 3.11–3.13 为支持且阻塞发布的矩阵；3.14 为 non-blocking canary；3.9/3.10 不再宣称支持 | 维护者 + CI owner | **approved** | `pyproject.toml requires-python = ">=3.11"`（B6）；README/CI 中 3.9–3.14 表述在 M1 统一移除 |
| D2 | Owner 团队真实身份 | 使用真实 GitHub 用户并确认 write 权限；禁止占位 owner | 核心维护者 | **approved（单一维护者）** | GitHub 账号 `@cloudQuant`（repo admin，gh API 核验）；当前无第二位已确认维护者，CODEOWNERS 仅登记 `@cloudQuant` |
| D3 | 分支审批门槛 | `dev` ≥1 个非作者批准 + code-owner review；`master` ≥2 个非作者批准 + code-owner review | 核心维护者 | **部分 blocked** | `dev` 门槛可行；`master` 双人审批因无第二位维护者而 **blocked**——在第二维护者确认前，不得启用 master 完整 Ruleset，也不得对外宣称 master 已完整治理 |
| D4 | 发布权限与环境 | release manager、`pypi`/`testpypi` Environment、PyPI trusted publisher、`v*` tag 规则；manual dispatch 不得发布 PyPI | 发布负责人 + 管理员 | **blocked** | Environments API 仅返回 `github-pages`（2026-08-23 核验）；`pypi`/`testpypi` Environment 未创建、trusted publisher 绑定未确认、tag rule 未建。全部为管理员动作，M5 只交付 workflow 侧守卫 |
| D5 | 安全通道与社区入口 | 启用 GitHub Private Vulnerability Reporting；否则私密邮箱 + SLA；Discussions 未启用前用 Question Form | 安全 + 社区负责人 | **blocked（PVR）/ approved（表单）** | PVR API 返回 `{"enabled":false}`（2026-08-23）；备用邮箱 yunjinqi@gmail.com 可用但 SLA 待 owner 书面确认；`hasDiscussionsEnabled=false` → Issue Forms 提供 Bug/Feature/Question |
| D6 | 插件治理 pilot | pilot 仓：`bt_api_base`、`bt_api_binance`、`bt_api_okx`；扩大到 10 个需新决策 | 插件协调人 | **approved** | v2 计划推荐值获实施授权；仅文档协议层落地（M5），不批量改 60 个插件仓 |
| D7 | 镜像与 Merge Queue | 当前不设 Gitee 镜像；连续 4 周日均待合并 PR ≥3 或频繁基线冲突才另立 Merge Queue 项目 | 管理员 + triage owner | **approved** | 单一 origin（GitHub）现状一致（B1）；无 merge_group 需求信号 |
| D8 | Coverage 口径 | 当前强制线 40%（pyproject `fail_under=40` + CI `COVERAGE_THRESHOLD=40`）；60% 为独立质量提升目标，提高阈值须带测试增量与基线证据 | 质量负责人 | **approved** | `pyproject.toml:103 fail_under=40`、`.github/workflows/tests.yml:20 COVERAGE_THRESHOLD="40"`（2026-08-23 核验）；release checklist 的 60% 表述已修正 |

## 变更记录

| 日期 | 门 | 变更 | 决策人 |
|---|---|---|---|
| 2026-08-23 | D0–D8 | 初次记录；D3(master 部分)、D4、D5(PVR) 为 blocked | cloudQuant（依据 v2 计划实施授权） |
| 2026-08-23 | D3/M4/M6 | M4 观察期完成：五类草稿演练归档（`evidence/m4-draft-pr-drills-20260823.md`），期间发现并修复 5 项缺陷（PR #1/#2/#9/#11）；`PR_GOVERNANCE_STRICT=true` 启用；M6 验收矩阵判定 Implementation Complete（`evidence/m6-acceptance-matrix-20260823.md`）。**D3 追记：单一维护者运营约束**——dev/code-opt Ruleset 要求非作者批准，唯一维护者无法自批准，激活将冻结全部合并；推迟至第二维护者确认或 owner 批准 bypass actor 策略。四个 Ruleset 均保持未应用（与 manifest 一致，verify exit 0） | cloudQuant |

## Blocked 解除条件

- **D3-master**：第二位维护者获得 write 权限并在 CODEOWNERS 生效分支完成一次 review drill。
- **D3-dev/code-opt（运营约束）**：满足其一即可申请激活——(a) 上述第二维护者确认；(b) owner 书面批准 solo 期 bypass actor 并回填对应 manifest 的 `bypass_actors`。
- **D4**：管理员创建 `pypi`/`testpypi` Environment、绑定 trusted publisher、建立 `v*` tag rule，并提供变更前后 API 摘要。
- **D5-PVR**：管理员在 Settings → Security 开启 Private Vulnerability Reporting，`GET /private-vulnerability-reporting` 返回 `{"enabled":true}`。
