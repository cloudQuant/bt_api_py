<!--
  提交前请确认：普通贡献目标分支为 dev；master 仅接受 promotion 与 hotfix。
  完整路由表见 docs/governance/branch-model.md
-->
## 目标分支与理由

<!-- 必填。dev（默认）/ master（仅 promotion 或 hotfix/*）/ code-optimization（性能优化） -->

## 变更类型与风险级别

<!-- 必填。类型：feature / fix / docs / test / refactor / perf / chore
     风险（按 docs/governance/branch-model.md §4）：
     [ ] risk:r0 文档/测试
     [ ] risk:r1 常规模块
     [ ] risk:r2 核心/兼容性（BtApi、containers/feeds 基类、gateway、websocket、forwarding、CTP 接口）
     [ ] risk:r3 发布/安全/供应链 -->

## 兼容性 / 交易所影响

<!-- 必填。涉及哪些交易所标识（如 BINANCE___SPOT）、Python 版本影响（3.11–3.13 阻塞矩阵；3.14 canary）、平台影响、公共 API 是否变化 -->

## 已执行的测试与结果

<!-- 必填。粘贴实际执行的命令与结果摘要，例如：
     make test-fast
     pytest tests/test_xxx.py -q
     mkdocs build --strict   （文档变更时） -->

## 子模块 SHA（如适用）

<!-- 若本 PR 变更 gitlink 或 .gitmodules：旧 SHA → 新 SHA，插件仓 PR 链接，回滚 SHA。
     协议详见 docs/governance/submodule-bump.md。不涉及则写 N/A -->

## 安全 / 发布影响

<!-- 是否触及凭据处理、订单路由、forwarding 网关、打包或发布路径？是→请说明并考虑加 risk:r3 标签 -->

## 关联 Issue

<!-- 关闭 XXX 时写 "Closes #XXX"；无关联 issue 请说明原因 -->
