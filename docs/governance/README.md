# 项目治理（Project Governance）

本目录是 bt_api_py 社区协作与发布治理的唯一权威文档集。

## 文档索引

| 文档 | 内容 |
|---|---|
| [分支模型](branch-model.md) | `dev` / `master` / `code-optimization` 角色、PR 路由表、风险分级 |
| [决策日志](decision-log.md) | 决策门 D0–D8 的状态、决策人与解除阻塞条件 |
| [基线快照](baseline-2026-08-23.md) | 迭代03实施前的脱敏事实记录（含历史凭据核查结论） |
| [指标 Schema](metrics-schema.json) | M7 每周治理摘要的数据结构定义 |

## 快速入口

- 我要提常规贡献 → 目标分支 **`dev`**，先读
  [CONTRIBUTING](https://github.com/cloudQuant/bt_api_py/blob/dev/CONTRIBUTING.md)
- 我要改交易所适配器 → 去 `bt_api/bt_api_*` 对应插件仓提 PR，主仓随后收 SHA bump
- 我要报告安全漏洞 → 读根目录
  [SECURITY](https://github.com/cloudQuant/bt_api_py/blob/dev/SECURITY.md)，不要开公开 issue
- 我要了解为什么这样设计 → 读迭代计划
  `docs/迭代计划/迭代03-开源项目治理与社区PR协作/正式迭代计划.md`

> 发布流程（release-flow）与子模块升级协议（submodule-bump）文档随迭代03 M5 里程碑交付。

## 边界声明

本目录中的文档描述的是仓库内可审计的政策与自动化。GitHub 远端设置
（默认分支、Rulesets、Environments、tag 规则）只能由管理员按决策门结论应用，
应用前后必须留存脱敏 API 摘要。CI 与验证脚本只做只读比对，永不持有管理权限。
