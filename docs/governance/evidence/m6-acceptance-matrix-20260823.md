# M6 正式验收矩阵（2026-08-23）

> 执行人角色：管理员（@cloudQuant）。判定依据：计划 §M6 验收矩阵与 §8 完成定义。
> 配套证据：`m1-bootstrap-chain-20260823.md`、`m4-draft-pr-drills-20260823.md`。

## 七维验收矩阵

| 维度 | 必须证据 | 状态 | 说明 |
|---|---|---|---|
| 分支模型 | D0 决策、bootstrap 链、默认分支 API | **通过** | `dev` 为默认入口（API 前后留证）；bootstrap 后 `master` 仅接收显式 PR；链路 SHA 可串联（见 m1） |
| 所有权 | CODEOWNERS errors API | **通过** | `GET /codeowners/errors` → `{"errors":[]}`；CODEOWNERS 覆盖核心路径并已在 `master`/`dev` 生效 |
| Ruleset | API 摘要与 manifest diff | **一致·推迟应用** | 远端 `rulesets=[]` 与全部 manifest 的 `disabled+gated` 状态一致；`verify_github_governance.py` exit 0。激活推迟理由见下节 |
| CI | 五类草稿 PR 演练 | **通过** | 所有候选 summary 在适用/不适用路径稳定出现（见 m4 清单）；无 `Waiting for status` 卡死；strict 模式已启用 |
| 安全 | SECURITY.md、gitleaks 记录 | **部分通过** | 增量 gitleaks 生效（浅克隆缺陷已修）；bandit B105 跳过附书面理由；仓库内无秘密。PVR 未启用（外部动作） |
| 发布 | TestPyPI record、Environment/tag 证据 | **阻塞·外部授权** | workflow 侧守卫已落地（manual 仅 testpypi+expected_sha；release 校验 tag/版本/可达性）；TestPyPI 演练待发布负责人单独授权（D4） |
| 子模块 | pilot bump PR 双端证据 | **机制通过** | 插件仓提交在远端 + 主仓 gitlink bump PR 全链路演练；完整校验 runner 的 0/60 环境失败已记录为后续迭代事项 |

## Ruleset 推迟应用的理由（重要）

四个 manifest 均为 `disabled` 且带门控标记，远端未创建任何 Ruleset——**这是有意的合规状态**：

1. `master.json`：pending D3（双人审批无第二维护者）。
2. `dev.json` / `code-optimization.json`：检测逻辑经演练修复后具备激活条件，但存在**单一维护者运营约束**——Ruleset 要求非作者批准时，唯一维护者无法批准自己的 PR，激活即冻结所有合并。解除需二选一：(a) 第二维护者获得 write 权限并完成 review drill；(b) owner 明确批准 bypass actor 策略并回填 manifest 的 `bypass_actors`。
3. `release-tags.json`：pending D4（bypass_actors 待填入确认的 release actor ID）。

依据 evidence/README 规则第 3 条：远端状态与 manifest `enforcement` 字段必须在同一治理提交中同步翻转——上述门控未解除前，任何单侧激活都构成 drift。

## 观察期强化（已生效）

`PR_GOVERNANCE_STRICT=true`（2026-08-23T09:42:32Z）：governance 校验违规从此硬失败。
该变量不依赖 Ruleset，即可对全部 PR 强制执行路由表与标签纪律。

## 管理员交接包（Blocked 解除清单）

| 门 | 待办动作 | 责任人 |
|---|---|---|
| D3-master / 运营约束 | 确认第二维护者（write 权限 + 在 CODEOWNERS 生效分支完成一次 review drill）；或决策 solo 期 bypass actor 并更新 manifest | 管理员 + 核心维护者 |
| D4 | 创建 `pypi`/`testpypi` Environment；在 PyPI 侧绑定 trusted publisher；回填 release-tags actor；随后按 `docs/governance/release-flow.md` 执行 TestPyPI 演练 | 管理员 + 发布负责人 |
| D5-PVR | Settings → Security 开启 Private Vulnerability Reporting，API 返回 `{"enabled":true}` 后更新决策日志 | 管理员 |
| Ruleset 激活顺序 | 门控解除后：同一治理提交内翻转 manifest `enforcement` → 合入 → 应用远端 → 重跑 verify 脚本 | 管理员 |
| M7 | 以本目录为起点开始周度量；连续 4 周满足稳定化退出条件后方可宣称流程持续运行 | triage 轮值 |

## 完成定义对照（计划 §8）

1. M0–M5 全部通过；M6 执行至当前权限与门控可及范围，所有假设均已声明（无隐式默认值）。✅
2. `dev` 默认入口、`master` 发布线、`code-optimization` 选择性 promotion 在文档、workflow、manifest 与草稿 PR 中一致。✅
3. CODEOWNERS、manifest 与远端比对（exit 0）、稳定 CI summaries、SECURITY 入口、Issue/PR 模板、子模块 PR 路径均有证据。✅
4. TestPyPI 路径已准备但**未演练**——按计划要求显式标记为下一 release 的外部验收门，未伪造发布证据。✅
5. 未将离线/模拟结果描述为实盘或生产安全保证。✅

**判定：Implementation Complete（含显式声明的推迟项与外部验收门）；Operationally Proven 待 M7 四周观察期后另行宣称。**
