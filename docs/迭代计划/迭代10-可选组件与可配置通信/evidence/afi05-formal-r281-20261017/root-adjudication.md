# R281：AFI05 正式验收裁定

结论：AFI05 正式 PASS；范围为正式计划第10节要求的本机政策治理和产品入口。AFI后端已提交 `ad2171b5a97f522cfb55c983a8e5039dc823f92a`，前端已提交 `090552b062f81b76e20e203e584dbe009e56c9e1`；后台共享文件仅提交被验收的普通账户政策改动，其他预算/Paper/控制链 WIP 保留。正式计数从54/68增至55/68。

| 正式要求 | 可核验依据 |
|---|---|
| 草稿、审批、发布、认证审批审计 | R13 固定源码的真实认证 HTTP→ServiceRead→SDK/Risk Host 节点覆盖 maker draft、独立 checker approve、maker publish，拒绝 maker 自批、checker 起草/发布、service principal 审批；主体/principal/account/manual-book scope由服务端绑定，输入不接受伪造 actor/scope。R226持久目录及真实wire角色证据补充。 |
| 首版受控激活、逐账户生效、拒绝和部分生效 | R13对A1、A2、B1、B2逐一读取公开版本和账户确认回执；A1发布但未确认，A2首次绑定成功，B2发布却因旧账本拒绝绑定；未确认不冒充生效。R228精确账户/版本/回执resolver与持久store重开提供互补证据。 |
| 旧预留与政策回退 | R13在B1已接受订单与预留后尝试B2，拒绝后B1完整Risk snapshot和原确认回执不变，离线重启B1仍按旧版本接单。R226部分/UNKNOWN/缺确认保持hold，R250不兼容旧账本及缺确认拒绝。该证据是保留旧已确认版本的回退，不承诺任意已发布政策撤销。 |
| AI/普通用户不能自批增额或解除冻结 | R13直接拒绝service principal、maker自批、普通Role.USER政策读取；POST与GET共用persisted-human、generic permission和部署角色/operation gates。没有单独Role.USER POST-approve断言，明确为共享门禁源码与已有认证反例组合证明。政策API只提供draft/approve/publish/read，无unfreeze；Monitor控制只允许FREEZE/STOP_NEW/CANCEL。未发布提案retract不是解冻。 |
| 页面不把发布冒充全部生效 | `/portfolio/policies`显式区分等待确认/精确版本已确认/部分确认/不可读取。只有published且owner_confirmed、版本完全匹配、确认文件可用才显示确认；汇总限于已跟踪账户，并说明不证明运行中消费者已加载。作者55项前端检查、独立静态审查及Root真实Chrome/Vue显示检查提供证据。 |

R13原输出位于 `D:/temp/iteration10-afi05-partial-effective-r13-sdk-feb91480-metadata-binding-out-20261017`。本次Root读取并核对 exit=0、唯一JUnit节点通过（6.079秒）、原stdout/stderr摘要、source-before/source-after/spec的一致性，并重新核对2940个原输入字节；没有重新运行已通过节点。保留3条warning（2条ZMQ/Proactor、1条Starlette状态码弃用）。精确摘要见 `verified-result.json`。

原spec/result的 `evidence_level=AUTHOR_LOCAL_SYNTHETIC_FIXED_SOURCE_HOST_CANDIDATE` 和candidate文字保持原样。较晚scope记录称Root执行，但这些运行产物本身不独立证明启动者身份；本裁定不以该文字作为身份证据，也不改写原记录。这里的正式裁定来自Root对实际产物、源码和条款的复核，不能将原候选标签与本次裁定混为一体。

源码范围复核：R13的SDK 164个、Risk 27个、Monitor 23个、Transport 7个生产Python文件与当前提交逐项规范化相同；AFI选择性提交排除的预算/Paper改动不在R13普通政策调用路径。Base的新增CTP跨venue快照校验、Execution新增持仓维度/精确Paper-risk组合、Gateway批量bootstrap/不确定启动处理与R13的synthetic普通Risk路径无交集，均仅做静态兼容审查，不能宣称R13执行过这四项当前差异。

UI浏览器使用合成API读响应，禁止后端变更，并非服务端授权验收。Root检查desktop/390px手机截图、等待/精确确认/403详情清除/部分及待核实汇总，通过后正常关闭浏览器；11个输入前后不变。之后仅改两条通用403文案并同步测试断言：三个组件用例首次2通过/1旧文案断言失败，修正后仅该失败项通过。服务端事实来自独立的R13真实HTTP/SDK/Risk链；不把UI mock当该链证据。

本条不要求全消费者加载完成，也不宣称真实交易所、全AFI08/AFI10/AFI13、负载或双机验收。R226/R228/R250原始记录及失败历史原样保留，未重复既有成功QA。
