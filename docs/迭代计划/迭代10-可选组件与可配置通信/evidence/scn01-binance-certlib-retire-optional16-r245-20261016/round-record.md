# 第245轮 SCN01 Binance 退役桥 fail-closed — 作者合成 QA 通过，Root 源码/原始证据审查待完成

候选在 Backtrader `dev` 的 `57c9fc613f263877b08da11eacadcb5a8eaccaad` 上只改两条源码路径：Binance 正常 `certlib.make_client` 不再通过已退役 Optional16 Gateway/组件桥创建客户端；任何非 direct transport 或 execution/risk/monitor 请求都在导入 Binance provider client 前明确拒绝。默认 direct 路径仍用原 `BinanceDemoClient`，原案例源码、动作、身份、批准和证据字段均不改。

作者本机新增聚焦测试为 6 passed / 0 failed，exit 0，0.63 秒；含一条既有 pytest 配置 warning。固定合成凭据只传入 fake client，未访问网络、provider 或账户。精确命令、pytest 结果和合并输出保存在 `raw-evidence.zip`，压缩包成员均有 SHA-256。Root 将审查候选源码与原始证据；本轮不重复运行已通过的六项 QA，也不声称存在 Root QA 结果。历史固定 Root QA 命令保留在 `root-qa-spec.json`，供追溯而非本轮执行。

本轮只证明正常工厂对退役后端的本机合成 fail-closed 与 direct 参数映射，不完成 SCN01 正常 SDK `ServiceClientRuntime` 接入，也不产生正式 case PASS。作者执行时原始结果记录 39/68；项目当前正式计数为 40/68（在本轮之外另有一项通过），R245 前后均为 40/68，本轮增量为 0。下轮在 R245 scoped commit 后实现 SDK `ServiceClientRuntime`/标准 `ServiceHost` 的正常合成后端，承接 case client 动作并保留请求/结果身份和原 case 证据语义；合成 Host 仍不算 provider PASS。当前 SDK 账户快照合同尚未表达 Binance C01 要求的 `canTrade` 与 `dualSidePosition`，服务端账户快照时间也不能替代客户端测得的交易所时间偏移/RTT；这些缺口必须通过有类型、可授权的正常服务合同补齐，否则 C01 保持 BLOCKED，不合成事实。
