# 第128轮 Root 正式裁定：CFG04 PASS

正式计划第10节 CFG04 要求：策略端未安装四领域包时仍能校验运行所有代理，拒绝领域components、embedded及服务端槽；以隔离安装、实际代理交互和导入/资源观察证明，服务端置于不同进程。

通过证据由三个唯一节点组合：qa-r1五代理节点已通过，覆盖 store.account、broker.orders、broker.order_query、risk.read_model、runtime.telemetry 和三类错误配置拒绝；qa-r3新增通过 store.market 与 broker.order_groups。后者经CURVE登记/请求、真实本机ServiceHost与SQLite，验证PAIRED/FILLED、RECOVERY_REQUIRED/UNKNOWN hold和正常关闭。provider数据合成，CFG04本身不要求真实账户。客户端四包缺席及不可导入、非editable安装来源、独立服务进程与正常关闭均有测试断言。

qa-r1为1pass/2fail；首次market缺环境变量，group完成服务链后错比intent字符串后缀。只修正到真实稳定ID；Root已核对与r1仅此断言差异。qa-r2 cwd错误收集了工作区，exit4/零测试，不是候选业务失败。统一runner以外部冻结支持目录为cwd并绑定两个server解释器后，qa-r3为2pass/exit0，1004输入未变，stderr空，无warnings。没有重跑已通过的五代理。

固定SDK wheel来源9d1dc457，Execution ae1dffd4，其他提交/产物hash见manifest；test-support基于213f1114。Root已将全部372个项目wheel文件与指定Git提交逐项比较；其中368仅CRLF/LF，不冒充raw Git字节相等；源码archive、wheel、三venv安装文件字节相等。此来源检查不泛化为所有第三方依赖审计。

独立acceptance_review静态复审逐项GO，Root采纳并核对原始JUnit、结果、manifest、源映射和实际测试范围。正式CFG04由部分变为PASS，总数23/68。不会外推为当前最新版本、真实账户、其他用例或整个迭代通过。创建时PREPARED_NOT_EXECUTED文案的旧manifest原样保留，运行结果由后续记录独立证明。

