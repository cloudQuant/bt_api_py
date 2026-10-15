# 第121轮：受管组订单三Host正常服务集成

组协调器、账户组执行和Risk预留服务已接通正常ServiceHost构建、受信角色/政策、Host自有upstream、注册生命周期及组/两腿RPC。provider-v2合同绑定revision、fill watermark、费用与币种，正常归一化事实进入GroupProofJournal；普通Risk入口保留GTC限制，组流程只借用已提交RiskOrderJournal的writer fence。

Root r6原始验收7passed/11.12秒/exit0：一个真实本机三Host/CURVE/SQLite节点及六个wire回复回归。第一组两腿FILLED/PAIRED且残差0；第二组B端事实不完整，保持UNKNOWN和持久hold，query不重发。两账户provider各两次提交/订单查询/成交查询，三Host正常关闭、无强制回收。367个来源pin和7个仓库HEAD前后相同，一条既有pytest配置warning、stderr空。实际使用SDK91e36628及固定Execution743a0b9原始源码导出；本轮parent74228cdd，不据此宣称当前全包发行验证。

此前已接受options5项、runtime5项、proof2项、authority7项各自原始结果和pin一并保留，没有重跑通过的QA。19个明确source/test路径中，components、registry、Risk entry只提交group相关部分；Monitor格式、无关export、普通Risk查询/终态全部未验收改动仍在checkout。完整路径均匹配至少一份原已接受QA pin；normalization的候选LF与checkout CRLF经Git path filter得到同一blob。未绑定QA pin的bundle测试保留untracked，已提交role_config/authority测试不重加。

Root由19个候选的真实Git-filter blob生成有效diff（256b270d…f3d67ecaf），git apply --check --cached通过，独立静态审查GO。R1/R2草案的CRLF比较/坏diff产物未作为应用依据；原件保留。没有宣称隔离候选树QA，交付依据为原实际Host结果和所选hunk/依赖核对。提交不更新gitlink、不改checkout源码字节。证据为RESERVATIONS_ONLY的本机合成provider服务链；LIVE_GROSS共享cursor、各ARB完整否定/部分成交/补偿/恢复矩阵、BT/Sizer/Monitor、双机，以及CFG04独立安装组代理和CFG06完整角色/政策目录仍未完成。正式22/68不变。
