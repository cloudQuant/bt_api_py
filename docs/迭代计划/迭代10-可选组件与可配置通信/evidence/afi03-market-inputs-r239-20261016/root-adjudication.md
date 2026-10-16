# AFI03 正式验收裁定（2026-10-16）

结论：正式 PASS。Root 对照正式计划第 10 节 AFI03 及 §5.1/§5.2，核对 R110/R222 已接受账户来源和本轮固定导出、原始作者 HTTP/UI 日志、此前独立 typecheck 成功节点，并接受独立逐条静态复核。AFI dev `4d11cffaee710e18ea48ee64430c80861aa7d3c2` 已精确提交八路径，parent `e4a007a244f8d408bbc098ae0956e19ff07f6088`；提交 blob 与八个候选 SHA-256 全匹配，index 为空，当前 analytics/Paper 工作区改动保留。源码及原始证据清单见本目录 manifest.json；提交核对为 `.codex-tmp/iteration10-r239-afi-postcommit.json`。

| 条款 | 支持证据 |
|---|---|
| 多币种、合约乘数与单位/来源 | R12 HTTP1 的 EUR 持仓与 USD 账户总额分离，乘数 10/contracts、来源和版本；UI 显示逐行币种/乘数，不合并成未经估值的余额 |
| FX/价格时间、独立 cut 与偏差 | HTTP1 验证 1.08 USD_PER_EUR、价格及 FX event/cut/as-of/source；R110 已接受两账户 2 秒非原子快照偏差，页面呈现 provider_snapshot_consistency.skew_ns |
| 缺失、零、错误、陈旧、未对账、覆盖不全 | HTTP1 与 UI 分别区分 zero/missing/error/stale/coverage-incomplete；缺失或陈旧乘数、过期 binding、FX 零和反向单位拒绝；显示 not_established、creditable=false，不将缺失补零或认作低风险 |
| 完整键、游标与来源可追溯 | HTTP 查询构造和投影保留完整行情身份、query fingerprint、source/revision/cut/coverage；R222 保留 account cursor/session generation；UI 暴露查询身份、来源和覆盖，不要求公开签名分页 token |
| 正常访问链及授权拒绝 | 真实 FastAPI lifespan、JWT、ServiceRead 与 MarketData v2 query/auth；HTTP2 的 GUEST 请求拒绝且无 execute，授权 USER 可读取 |

新增运行证据按原归属记录：作者 R15 HTTP1 1 passed（4.47s）、R16 HTTP2 1 passed（5.00s），各一条既有 Quandl 弃用 warning；作者 R17 UI 目标节点 1 passed，其他 38 节点仅被过滤，不计通过。此前 Root R5 的 `vue-tsc --noEmit` 单节点 exit 0、stdout/stderr 空，所针对产品 Vue/API 源码与 R12 相同；同一 R5 的 Vitest 失败仍保留，不将整个 R5 记为通过。Root 没有重复上述通过节点。

本条使用本机合成行情叶子：仅 `MarketDataQueryService.execute` 返回合成观察事实，不证明外部行情源、真实账户或投资组合估值。界面保留 market_value=not_calculated，账户总额保持 950 USD；估值、收益及风险统计属于 AFI04，不由本裁定接受。当前 UI fixture 中跨账户 skew 为 null 的限制由已接受 R110 非零 API 场景及直接渲染代码共同覆盖。没有新增条款外的真实 provider 门槛。

本裁定只新增 AFI03，正式总数从 39/68 更新为 40/68。其他部分用例和用户已暂缓的双机场景状态不变。
