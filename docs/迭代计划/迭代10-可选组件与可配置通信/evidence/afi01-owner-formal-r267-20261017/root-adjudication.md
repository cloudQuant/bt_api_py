# AFI01 正式验收：持久用户归属与服务授权链

Root 对照正式计划第10节 AFI01，核对已提交源代码、作者原始结果及独立条款审计，裁定 **AFI01 正式 PASS**。当前累计由40/68增至41/68。未重复已通过测试，没有将真实交易所或双机验收计入本项。

## 条款与证据

- 操作者与受限委托：R93 的实际 FastAPI/SDK/ServiceHost 读取授权链及 R124/R129/R151、正式 AFI07 的写入/批准链，保留服务端 principal、actor 和 delegate。R2 新节点进一步检查两个持久 User 的独立 SDK worker 身份、线程及服务调用。
- 跨主体、账户、策略及组合：R255 将签名 JWT subject 绑定到 active User.id 和已有资源归属；R2 在生产 JWT/owner 依赖、真实本机 SDK/ServiceHost、SQLite 组合记录上，验证合法自有读取、跨 owner 资源拒绝及拒绝时调用计数不增加。个人 User.id 是本产品的归属边界；不声明新增组织租户平台。
- 伪造身份、scope、审批与重放：R2 的自有反例不能通过 tenant/workspace/actor/delegate/owner 字段改变授权；已接受 R124/R129/R151 与 AFI07 覆盖同键重放、异内容冲突、并发/重启、审批摘要/版本/过期/撤销与受限派发。复用这些证据，没有用重复 GET 代替写入幂等证明。

R2 是作者执行的 pytest：1 passed / 3.76s，原日志 SHA-256 为 `5319c05797ac2614c781f4c52cf7ef3c5ec0125440e01e639a9bff2461e3fc48`。HTTP 使用 ASGITransport，服务段为实际 SDK、spawn ServiceHost、本地传输及合成 provider；不是 Root 重跑，不是外部账户测试。独立只读条款审计为 resume_load01_prepare；Root 阅读了原输出和关键授权/清理断言。

## 源代码交付与限制

持久用户 guard 已在 AFI dev `860bd7ba034f1a13db46e48f0fd5d4115274dd3f` 提交。新集成测试在 `1955309201c1ac812ab8c5904aa0a105f860679e` 单文件提交，parent `056d40f5df7702a1d6d8a14cf0238d3303ce2f6d`。作者运行工作区20,440字节与提交20,439字节仅相差一个CRLF到LF转换，Root重新核实归一化相等。

R2 运行时 service_read 的旧字节不含已提交R238 Paper additions；已逐hunk核实这些差异只涉及本节点未使用的Paper能力，并将工作区恢复到已提交Paper内容。R93 helper模块中的额外伪造header测试hunk不在R2执行函数内，未作为必要依赖纳入本轮。没有重跑无关Paper或既有正式用例。

R2 metadata 后来只更正 author-pass/root-review-pending 状态，原日志和12个源pin保留。更早4,359字节metadata副本未找到，不声明对其做过字节对比。当前公开记录明确原运行主体、证据层级及这些来源差异。此限制不改变原日志、已提交测试或授权行为的裁定。

本轮SDK只归档证据与状态文件；实际SDK提交及精确scope见Git历史和 `.codex-tmp/iteration10-afi01-formal-r267-postcommit.json`。不修改其他未完成用例的状态。
