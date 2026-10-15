# 第116轮：AFI持久资金成本账响应兼容

第115轮SDK正常账户簿响应新增financial子对象后，AFI的旧11字段精确合同会拒绝新响应。本轮修复该消费者兼容缺口：继续接受原11字段，仅增加明确的financial扩展；校验已知schema、账户、执行generation/cursor、完整ledger cut和owner形状。资金/成本是持久owner账投影，保持原账户总额、估值与legacy费用口径，不把这些数值加入provider总值。financial的BALANCED/RESIDUAL与实际残差一致；legacy PARTIAL及其独立cut保留。

Root首次执行最终新增15项合同检查：15 passed、exit0、1.11秒，4条插件禁用后的既有pytest配置warning、stderr空；2094个AFI/固定SDK及依赖源码和原9项无关文件pin、AFI HEAD前后不变。固定SDK b58918bb与Base b4aabd1e/Execution1373的提交源码导出，测试断言7个实际模块来源/hash。检查包含真实SDK Service.handle回包经AFI读client解析，以及canonical financial DTO/JSON形状的可用数据正反例、owner/cut/状态矛盾拒绝。独立静态审查最终GO；早期作者失败和未执行Root r1 spec保留，Root只运行最终r2一次。不重复旧UI或四Host验收。

AFI/dev仅两路径提交dd1e14a287cebc1137999045d1332d86bdab3806（父db32c3ce）；原README/Windows脚本变更保留，index空。SDK本轮仅证据和两份进度文档，父b58918bb，实际提交由Git给出。证据是合成服务响应兼容合同，不是本轮HTTP/Host/真实账户或完整AFI02/03验收；当前正式22/68不变。Backtrader真实资金成本消费、Risk终态Host和组服务链路继续独立验收。
