# M4 五类草稿 PR 演练证据（2026-08-23）

> 执行人角色：管理员/triage（@cloudQuant）。观察期模式：`PR Governance` report-only。
> 通过标准（计划 M4/M6）：所有 required 候选 summary 在适用与不适用路径均稳定出现，无 `Waiting for status` 卡死。

## 演练矩阵

| 场景 | PR | base | 结果 | 关键观测 |
|---|---|---|---|---|
| D1 文档/R0 | [#5](https://github.com/cloudQuant/bt_api_py/pull/5) | `dev` | 关闭（已归档） | governance 输出 `OK: PR metadata satisfies the governance routing table.`；`deploy`/`submodule-matrix` 在不适用路径稳定 skipped |
| D2 R2 核心 | [#6](https://github.com/cloudQuant/bt_api_py/pull/6) | `dev` | 关闭（已归档） | 触及 `bt_api_py/bt_api.py`（仅 docstring）；全检查通过 |
| D3 性能线 | [#7](https://github.com/cloudQuant/bt_api_py/pull/7) | `code-optimization` | 关闭（已归档） | check 名称齐全（build/deploy/Quality Gates 等）；build 失败为该分线**预存问题**（numpy 构建元数据 + 22 个 strict 文档警告），与演练变更无关 |
| D4 hotfix→master | [#4](https://github.com/cloudQuant/bt_api_py/pull/4) | `master` | 关闭（已归档） | 两阶段验证见下 |
| D5 SHA bump | [#8](https://github.com/cloudQuant/bt_api_py/pull/8) / [#10](https://github.com/cloudQuant/bt_api_py/pull/10) | `dev` | 关闭（已归档） | 真实 gitlink 变更（bt_api_ctp a8a3792→8849421）；两轮验证见下 |

## D4 hotfix 双路径

- 第一阶段（无标签）：governance 摘要正确列出违规 —— `exactly one risk: label is required, found none`、`missing labels: ['release:hotfix', 'risk:r3']`、`master PR lacks reproduction/regression/test evidence`。
- 第二阶段（补 `risk:r3`+`release:hotfix`+证据正文后重触发）：输出转为 `OK: PR metadata satisfies the governance routing table.`。

## D5 SHA bump 双路径

- 第一轮：Gate 报告 **not-applicable**——与事实不符，暴露检测缺陷（见下节缺陷 #4）。
- 第二轮（PR #9 修复后）：Gate 正确进入 **validated (1 gitlink change(s))** 路径并执行完整递归校验。

## 稳定出现的 check 名称清单

`PR Governance / Summary`、`Quality Gates`、`Tests / Quality Gate`、
`Full Suite (Python 3.11, Ubuntu)`、`Compatibility / Compatibility (Python <ver>, <os>)`、
`Submodule Gate / Summary`、`build`；不适用路径：`deploy`、`submodule-matrix` 稳定 skipped。
与 `.github/governance/required-checks.json` 登记一致（`Tests / Quality Gate` 为聚合 job 实名，无 drift）。

## 观察期发现并修复的缺陷（均经独立 PR 落地）

| # | 缺陷 | 修复 |
|---|---|---|
| 1 | `pr-governance.yml` summary 步骤 Markdown 围栏被当裸命令执行（exit 127） | commit `96468674` |
| 2 | alpaca 插件集成测试路径错误且依赖本地检出 | commit `9423f6b2`（含缺失时 skip） |
| 3 | Quality Gate 历史债务首次暴露：ruff 627、格式化 44 文件、mypy 73；内含 F821 真实 bug（`_update_weights_based_on_performance` 的 `f1_score` 遮蔽，运行时必崩 TypeError）；mixin stub 遮蔽回归 | PR [#2](https://github.com/cloudQuant/bt_api_py/pull/2) |
| 4 | gitlink 检测在两处同时失效：`awk '$4 ~ /160000/'` 测的是新 SHA 字段；Python 解析 `cols[2]=="160000"` 检查旧 SHA 且新旧索引错位 | PR [#9](https://github.com/cloudQuant/bt_api_py/pull/9) |
| 5 | `submodule-tests.yml` validated 分支第三个同类围栏 bug（exit 127） | PR [#11](https://github.com/cloudQuant/bt_api_py/pull/11) |

## 记录在案、超出本迭代范围的发现

1. `code-optimization` 分线 docs build 预存失败（见 D3）。
2. `install_and_test_all.py` 在 hosted runner 上 0/60 通过——缺原生构建依赖（如 CTP 所需 swig）。子模块完整校验的 CI 可用性需后续迭代处理。
3. 本地全套件运行期间曾有并发会话改写 fixture 导致一次性误报（瞬态，复跑即消）。

## 严格模式启用

演练完成后：repo variable `PR_GOVERNANCE_STRICT=true`（2026-08-23T09:42:32Z 设置）。
此后 governance 校验违规将硬失败；分支 Ruleset 仍未应用（见 m6 验收矩阵的 Ruleset 行）。
