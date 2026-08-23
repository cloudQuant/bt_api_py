# M1 Bootstrap 链路证据（2026-08-23）

> 执行人角色：管理员（@cloudQuant）。所有 SHA 均可经 `git log` / GitHub API 复核。
> 对应计划：`docs/迭代计划/迭代03-开源项目治理与社区PR协作/正式迭代计划.md` M1 步骤 1–6。

## 分支创建与合并链

| 步骤 | 对象 | SHA / URL | 说明 |
|---|---|---|---|
| BOOTSTRAP_SHA 记录 | 远端 `master` | `1436ec0adaf4b283a54bfe69f5be163df3e3e3b9` | 决策日志 D0 已登记 |
| `dev` 创建 | `refs/heads/dev` | `1436ec0a`（= BOOTSTRAP_SHA） | M1 步骤 1；此时未切默认分支 |
| bootstrap 分支 | `governance/bootstrap` | `198f88a6` → `9bfe5f88` | 含 M0–M5 六个里程碑提交及演练修复 |
| Bootstrap PR | [#1](https://github.com/cloudQuant/bt_api_py/pull/1) | merge 于 2026-08-23T07:47:07Z | 合入后 `master` = `bcf6b77f` |
| 同步 PR master→dev | [#3](https://github.com/cloudQuant/bt_api_py/pull/3) | merge 于 2026-08-23T08:51:17Z | 合入后 `dev` = `f338199b` |
| 默认分支切换 | repo API PATCH | 切换前 `master` → 切换后 `dev` | 仅在同步 PR 通过后执行 |

## 后续治理提交（dev 线）

- PR #9 `fix(ci): correct gitlink detection…` → `dev` = `fbea4515`
- PR #11 `fix(ci): echo markdown fences…` → `dev` = `7f0b63a5`

## 默认分支切换前后 API 摘要（脱敏）

```text
GET /repos/cloudQuant/bt_api_py → default_branch
  切换前: "master"
  切换后: "dev"        （PATCH /repos/cloudQuant/bt_api_py, field default_branch=dev）
```

## 结论

- fork 新建 PR 的默认目标分支为 `dev`（GitHub 行为由默认分支决定）。
- `master` 自 bootstrap 起仅接收显式 PR 合并，无直接 push。
- 本链路满足计划 §8 Implementation Complete 第 2 条的分支模型一致性要求。
