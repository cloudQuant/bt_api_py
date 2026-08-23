# 分支模型与 PR 路由（Branch Model）

> 状态：生效中（迭代03，2026-08-23）。本文与 `CONTRIBUTING.md`、根 `README.md`
> 的贡献章节、`.github/pull_request_template.md` 保持同一口径；发现不一致时以
> 本文件为准并发 issue 修正。

## 1. 分支角色

| 分支 | 角色 | 允许来源 | 禁止事项 | 门禁 |
|---|---|---|---|---|
| `dev` | 默认、日常集成 | fork / `feature/*` / 文档 / bugfix / SHA bump | 直接功能 push | PR、≥1 个非作者批准、code-owner review、Governance、Quality |
| `master` | 稳定发布线（GitHub Release / PyPI 来源） | `dev → master` promotion；`hotfix/* → master` | 常规功能直推、`code-optimization` 整线 merge | PR、2 个非作者批准 + code-owner review（第二维护者就位前不启用，见决策门 D3）、Release/Quality/Submodule summaries、禁 force push/删除 |
| `code-optimization` | 性能与架构实验线 | `perf/*` 或明确优化 PR | 无基准证据的重构、直接进 `master` | PR、≥1 批准、Governance、Quality/Performance |

## 2. 工作流总览

```text
普通贡献：fork / feature/* ── PR ──> dev ── promotion PR ──> master ── Release ──> PyPI

性能优化：perf/* ── benchmark PR ──> code-optimization ── selective PR ──> dev

发布 hotfix：hotfix/<issue>-<slug> (from master) ── PR ──> master ── forward-port PR ──> dev

适配器变更：plugin repository PR ──> plugin merge ──> parent SHA-bump PR ──> dev
```

要点：

- 生产 PyPI 只能由受保护 `master` 可达的 tag 与 GitHub Release 触发。
- `code-optimization` 永不整线合并进 `master`；只允许可审查、可回滚的选择性 PR 进入 `dev`。
- 每个 `master` hotfix 必须在一个工作日内有 `dev` 前移 PR，或记录"不前移"的理由与 owner。

## 3. PR 路由表

| 变更类型 | 默认目标分支 | 必需证据 | 合并后动作 |
|---|---|---|---|
| 文档、注释、非行为性工具 | `dev` | strict docs build、受影响测试 | promotion 候选 |
| 常规功能、普通 bugfix | `dev` | 回归测试、兼容影响说明 | promotion 候选 |
| R2 核心接口/兼容性（BtApi、containers/feeds 基类、gateway/websocket/forwarding、CTP 接口） | `dev` | API 说明、目标测试、owner 审阅 | promotion 候选 |
| 性能优化 | `code-optimization` | 可复现的 benchmark 前后数据、语义不变说明 | 选择性 PR 到 `dev` |
| 发布阻断 bug / 安全修复 | `master`（hotfix） | 最小复现、回归测试、影响范围说明 | 1 个工作日内前移 `dev` |
| 交易所适配器实现 | 对应 `bt_api_*` 插件仓 | 插件仓 CI 通过、兼容说明 | 主仓独立 SHA bump PR |
| gitlink / `.gitmodules` 变更 | `dev` | 新旧 SHA、submodule 校验结果、回滚 SHA | promotion 候选 |

## 4. 风险分级

| 等级 | 典型路径 | 最低评审 |
|---|---|---|
| R0 文档/测试 | `docs/`、测试注释、非行为性工具 | 1 位维护者 |
| R1 常规模块 | 单个 feed 实现、container、examples、scripts | 1 位领域 owner |
| R2 核心/兼容性 | BtApi 门面、containers 基础类型、feeds 抽象基类、gateway、websocket、forwarding、rate_limiter、CTP SWIG 接口 | 领域 owner + 复核留痕（D3 就绪后升级为双批准） |
| R3 发布/安全/供应链 | `master` hotfix、打包配置、依赖升级、publish 路径、认证与密钥处理 | 核心维护者明确批准 |

风险标签（`risk:r0`–`risk:r3`）由 triage 维护者添加或确认；`PR Governance / Summary`
检查负责验证标签与目标分支的一致性——它们不是 Ruleset 的原生能力。

## 5. 平台能力边界（避免误设）

1. `CODEOWNERS` 解决**责任归属**与 owner review 请求；同一条规则任一 owner 批准即满足，
   它不能替代"双人审批"。`master` 的双批准由 Ruleset 审批数设置承担。
2. 标签语义（`target:*`、`release:hotfix` 等）只能由 workflow 检查，不能写进 Ruleset 期望。
3. required check 必须在草稿 PR 的适用与不适用路径都稳定产出同名 summary 后才列入 manifest。
