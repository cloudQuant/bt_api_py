# REG01 注册政策回执与真实授权矩阵（第103轮）

裁定：正式REG01 PASS；本机真实ServiceHost/CURVE注册与RPC，合成provider。
正式总数22/68，不能推导真实交易所、其他用例或整轮完成。

四个SDK生产路径的窄增量分离access policy、实际Risk policy和service requirements回执。
legacy policy_version仍为访问政策兼容别名。Risk APPLIED绑定真实gate.policy指纹；
NOT_APPLICABLE只用于无Risk的组件，legacy缺失字段不被解释为N/A。配置ref与实际policy_id
不同必须经可信不可变TrustedRiskPolicyBinding(scope, policy对象)显式映射；账户wire alias
与provider account ID由已有可信target映射保持区分。Registry持久、续期和dispatch重查，
Runtime投影对应不可变回执。四路径只接受REG-only源码，不含订单组WIP。

Root在固定edb721源码基线加四路径候选和精确测试上执行一个新节点；实际仓库启动父为
4861237c83765682a1c77324c23670290b550100。Python3.11.5/pytest8.0.0，1passed/5.74秒、
Python退出0；外层stderr为空，pytest无warning摘要。178来源文件和六包前后未变。
未单独持久化子进程业务JSON或所有日志；行为证明来自精确测试断言及JUnit通过结果。
source-manifest中的qa_executed:false保留创建时含义，执行由独立invocation/result/JUnit证明。

正例为真实认证策略取得有效能力、访问/Risk/就绪版本回执、Gateway账户snapshot和合法
Risk订单ACKED_TRACKED。伪造策略、错误账户、未授予订单能力、Gateway内部provider_session
端口绕过均拒绝。每次拒绝后完整counter tuple不变：Risk写/订单查询/成交查询，以及
Gateway写/读/连接/关闭；随后合法订单只新增一次provider写。正常client/runtime/host关闭、
进程回收且无forced cleanup均为节点断言。独立backtrader_entry_review最终逐条裁定GO。

fixture Gateway刷新间隔3600秒/max-staleness7200秒，隔离本次有界授权矩阵与已独立测试的
定时刷新；真实worker和wall/monotonic clock未替换。最初alias配置失败、早期plugin和
WireIdentity夹具失败保留；DC7/6262候选未执行，最终3B8执行一次，未重复已通过QA。

交付保留checkout四共享文件中的其他group在制改动，仅将经过本次实际运行的四个
候选源码blob写入index。测试新增与本证据为完整提交。子包gitlink不变，无新worktree。
实际commit SHA可从本文件Git历史查询；Root postcommit单独记录精确scope/bytes和空index。
