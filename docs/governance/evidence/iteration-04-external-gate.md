# 迭代 04 外部治理门禁

> 取证时间：2026-09-03T03:31:18Z
> 仓库：cloudQuant/bt_api_py
> 取证性质：只读。本文不表示远端设置已被修改。

## 当前状态

| 门禁 | 当前证据 | 状态 | 需要的后续动作 |
| --- | --- | --- | --- |
| 默认开发分支 | dev | Verified | 迭代 PR 以 dev 为目标分支。 |
| GitHub Rulesets | API 返回空数组 | Blocked | 仓库管理员创建并读回 dev、master、release-tag 规则。 |
| dev 分支保护 | protected=false | Blocked | 在规则生效前，不得把本地 PR 测试视为强制审查。 |
| master 分支保护 | protected=false | Blocked | 同上。 |
| 发布 environments | 仅 github-pages；无 TestPyPI/PyPI | Blocked | 管理员创建受保护 TestPyPI/PyPI environment 与审批策略。 |
| Private vulnerability reporting | enabled=false | Blocked | 管理员评估并启用或记录正式豁免。 |
| 当前开放 PR | 无 | Pending | 分拆的迭代 PR 创建后重新核验 checks、审查和 merge 状态。 |

## 候选阶段只读复核

> 复核时间：2026-09-03T05:53:43Z

再次执行本文列出的只读 GitHub 命令，结果未发生改善：默认分支仍为 `dev`；rulesets 仍为空；`dev` 与 `master` 均为 `protected=false`；environment 仍只有 `github-pages`；PVR 仍为 `enabled=false`；开放 PR 仍为空。没有进行任何远端写操作。

## 严格 T7 后最终只读复核

> 复核时间：2026-09-03T06:39:11Z

在本地严格 T7 候选通过后，重新执行同一组只读 GitHub 命令。远端状态仍未改变：默认分支为 `dev`；rulesets 为空；`dev` 与 `master` 均为 `protected=false`；environment 仍只有 `github-pages`；PVR 为 `enabled=false`；开放 PR 为 0。没有进行任何远端写操作。

该结果使本地候选保持 `Validated`，但使 Community PR landing 和 External Release Gate 保持 `Blocked`；它绝不构成发布、远端治理或托管 CI 成功证据。

## 迭代 03 修正提交

本地提交 7d5db275（fix(governance): correct iteration 03 acceptance gates）尚未位于 origin/dev。它应作为普通 dev PR 的候选，独立审查并在合入后重新执行治理验收；在此之前不得把其本地测试结果写成远端治理已完成。

## 只读复验命令

~~~bash
gh repo view cloudQuant/bt_api_py --json nameWithOwner,url,defaultBranchRef,isPrivate,hasIssuesEnabled
gh api repos/cloudQuant/bt_api_py/rulesets
gh api repos/cloudQuant/bt_api_py/branches/dev --jq '{name,protected,protection_url}'
gh api repos/cloudQuant/bt_api_py/branches/master --jq '{name,protected,protection_url}'
gh api repos/cloudQuant/bt_api_py/environments
gh api repos/cloudQuant/bt_api_py/private-vulnerability-reporting
gh pr list --repo cloudQuant/bt_api_py --state open --json number,title,headRefName,baseRefName,isDraft,url
~~~

## 释放条件

本地候选通过后，仍必须由有权限的仓库管理员完成并提供新鲜 API/PR/Actions 收据。除非 Rulesets、分支保护、发布 environment、所需 checks 与真实 PR 审查均有实时证据，迭代只能标记为 Local Candidate，不能标记为 Released 或 Governance Complete。
