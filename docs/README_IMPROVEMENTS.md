# bt_api_py 项目改进文档索引

## 📚 文档导航

本目录包含 bt_api_py 项目的完整改进建议和实施指南。所有文档按**当前计划 → 长期路线 → 活跃参考 → 历史归档**四层组织。

---

### 🟢 当前执行计划

| 文档 | 说明 |
|------|------|
| **[plans/2026-03-23-bt-api-py-next-iteration-plan.md](plans/2026-03-23-bt-api-py-next-iteration-plan.md)** | **当前主迭代计划** — 四大主题全部完成：WebSocket 收敛(✅)、请求层统一(✅)、质量门禁(✅)、文档收敛(✅) |

> **新参与者请从这里开始。** 所有后续改进均围绕此文件执行，不再新增同级平行计划。

### 🔵 长期路线图

| 文档 | 说明 |
|------|------|
| [plans/2026-03-16-stability-roadmap.md](plans/2026-03-16-stability-roadmap.md) | 中长期稳定化路线 — 基础设施、质量门禁、架构收敛阶段规划 |

### 🟡 活跃参考文档

| 文档 | 说明 |
|------|------|
| [NEXT_STEPS.md](NEXT_STEPS.md) | 快速开始指南 — 推荐下一步、3个月路径、BMAD 工具建议 |
| [test-markers-guide.md](test-markers-guide.md) | 测试标记使用指南 — 标记分类、用法、CI/CD 集成 |
| [QUICK_START_TESTING.md](QUICK_START_TESTING.md) | 测试快速上手 — 常用命令、标记速查表 |
| [development-guide.md](development-guide.md) | 开发指南 — 开发环境、工作流程 |
| [release-checklist.md](release-checklist.md) | 发布检查清单 |
| [project-overview.md](project-overview.md) | 项目架构概览 |
| [websocket-optimization.md](websocket-optimization.md) | WebSocket 优化方案（主题 A 参考） |

### 📁 历史归档（已完成 / 已被当前计划取代）

<details>
<summary>展开查看历史文档列表</summary>

#### 迭代计划（已完成）
- [plans/2026-03-08-junior-engineering-iteration-plan.md](plans/2026-03-08-junior-engineering-iteration-plan.md) — Phase 1
- [plans/2026-03-08-junior-engineering-iteration-plan-phase-2.md](plans/2026-03-08-junior-engineering-iteration-plan-phase-2.md) — Phase 2
- [plans/2026-03-08-junior-engineering-iteration-plan-phase-3.md](plans/2026-03-08-junior-engineering-iteration-plan-phase-3.md) — Phase 3
- [plans/2026-03-19-bt-api-py-code-quality-iteration-plan.md](plans/2026-03-19-bt-api-py-code-quality-iteration-plan.md) — 代码质量迭代（已并入当前主计划）
- [plans/2025-03-08-performance-monitoring-system.md](plans/2025-03-08-performance-monitoring-system.md) — 性能监控系统设计

#### 完成报告
- [phase3-completion-report.md](phase3-completion-report.md) — Phase 3 完成报告
- [test-optimization-complete.md](test-optimization-complete.md) — 测试优化完成报告
- [test-optimization-phase3-report.md](test-optimization-phase3-report.md) — Phase 3 测试优化报告
- [test-verification-complete.md](test-verification-complete.md) — 测试验证完成报告
- [code_quality_improvement_final_report.md](code_quality_improvement_final_report.md) — 代码质量改进终报

#### 背景分析材料
- [project-improvement-recommendations.md](project-improvement-recommendations.md) — 8大类改进建议（背景参考）
- [comprehensive-improvement-plan.md](comprehensive-improvement-plan.md) — 综合改进方案（背景参考）
- [CODE_QUALITY_IMPROVEMENTS.md](CODE_QUALITY_IMPROVEMENTS.md) — 代码质量改进汇总
- [code-quality-improvements.md](code-quality-improvements.md) — 代码质量改进
- [code-quality-report.md](code-quality-report.md) — 代码质量报告
- [improvement-report.md](improvement-report.md) — 改进报告 v1
- [improvement-report-v2.md](improvement-report-v2.md) — 改进报告 v2
- [IMPROVEMENT_EXECUTION_PLAN.md](IMPROVEMENT_EXECUTION_PLAN.md) — 执行计划（已并入当前主计划）
- [source-tree-analysis.md](source-tree-analysis.md) — 源码树分析
- [type-hints-improvement-plan.md](type-hints-improvement-plan.md) — 类型提示改进方案
- [test_architecture_plan.md](test_architecture_plan.md) — 测试架构规划
- [test-optimization-summary.md](test-optimization-summary.md) — 测试优化总结
- [testing_optimization_summary.md](testing_optimization_summary.md) — 测试优化总结（旧版）

#### BMAD 产物 (`_bmad-output/`)
- `planning-artifacts/` — 规划文档（epic 定义、能力矩阵、优化执行方案等）
- `implementation-artifacts/` — 实施记录（print 移除报告、sprint 状态、类型修复报告）
- `improvement-reports/` — 改进报告
- `test-artifacts/` — 测试产物（自动化总结、CI 管道进展）

</details>

## 📊 项目状态概览

### 已完成 ✅
- Phase 3 迭代计划（6个任务）
- 测试优化工作
- 细粒度测试标记系统
- 18个文件问题修复
- 完整文档体系
- **主题 B：请求层统一与测试确定性增强**
  - 22个交易所 `request_base.py` 模块收敛到共享 `_http_client.async_request`
  - 11个模块 `extra_data=None` 防御性修复
  - 20个异步回归测试（7个补充到已有文件 + 4个新测试文件覆盖13个模块）
  - 零残留 `await self.async_http_request` 调用
- **主题 C：质量门禁统一与类型债务压缩**
  - CI 工作流去重：`optimized-tests.yml` 瘦身为 Extended CI（移除与 `tests.yml` 重叠的 pr-check / full-test）
  - 覆盖率门槛对齐确认：`pyproject.toml` / `tests.yml` / `run_optimized_tests.sh` 均为 80
  - mypy `websocket.*` glob 冲突修复：拆分为具体子模块 `ignore_errors`
  - `websocket/__init__.py` 结构 bug 修复（`get_manager`/`get_monitor` 从嵌套函数移回类方法）
  - 10个 mypy 类型错误清零（785个源文件零错误）

- **主题 D：文档与计划收敛**
  - 55+ 文档分为四层：当前计划 → 长期路线 → 活跃参考 → 历史归档
  - 当前主计划入口明确（`2026-03-23-bt-api-py-next-iteration-plan.md`）
  - 历史文档用 `<details>` 折叠，降低新参与者认知负担
- **主题 A：WebSocket / Gateway 架构收敛**
  - 架构结论：`websocket/` 包为权威实现，`websocket_manager.py` 保留为 legacy 兼容层（已添加弃用提示），`gateway/` 基于 ZMQ 独立运行无需收敛
  - `advanced_connection_manager.py` 6处 `except Exception` 替换为 `ConnectionClosed/OSError/WebSocketException` 具体分支
  - 新增 12 个连接生命周期回归测试（connect/disconnect 幂等、重连订阅恢复、压缩帧、错误分类）

### 进行中 🚧
- 2026-03-23 迭代四大主题 **全部完成** ✅ — 下一步可规划新迭代方向

## 🔗 相关资源

### BMAD 工具
- [BMAD 使用指南](../_bmad/)
- [可用工作流列表](../_bmad/_config/bmad-help.csv)

### 项目文档
- [项目根 README](../README.md)
- [AGENTS.md - AI 代理指南](../AGENTS.md)
- [贡献指南](../CONTRIBUTING.md)

### 测试脚本
- [run_market_tests.sh](../scripts/run_market_tests.sh)
- [run_auth_tests.sh](../scripts/run_auth_tests.sh)

## 💡 获取帮助

如果你不确定从哪里开始：

1. 先阅读 **[NEXT_STEPS.md](NEXT_STEPS.md)**
2. 选择一个优先级开始
3. 使用 BMAD 工具辅助实施
4. 查看具体文档了解细节

## 📝 更新日志

- **2026-03-23**: 完成主题 B（请求层统一）和主题 C（质量门禁统一与类型债务压缩）
- **2026-03-23**: CI 工作流去重，optimized-tests.yml 瘦身为 Extended CI
- **2026-03-23**: mypy 类型错误清零，websocket 模块冲突修复
- **2026-03-08**: 初始版本，包含所有改进建议文档
- **2026-03-08**: 添加测试优化相关文档
- **2026-03-08**: 添加 Phase 3 完成报告

## 🤝 贡献

如果你想对改进计划提出建议：

1. 阅读相关文档
2. 提出问题和建议
3. 参与讨论
4. 贡献代码

---

**开始你的改进之旅！** 🚀

从 **[NEXT_STEPS.md](NEXT_STEPS.md)** 开始，选择最适合你的改进方向。
