# 第201轮：LOAD01 owner relay 续约修复及 r5 失败证据

仅接受测试运行器的四行租约维护修复：borrowed owner relay 每 0.5 秒执行既有 runtime.maintain 的到期维护，不受 relay poll 阻塞影响。原 30 秒租约、5 秒 RPC、100 策略/10 owner/20 stream/200 Hz/2 Hz 与资源阈值不变。SDK/dev 父 ba2eddbc；提交哈希见此文件 Git 历史。

Root 实际 r5 使用先前冻结 e08da3c5 与六精确包 gitlink 的 export，加该四行以及明确的 source/runner 归档绑定 overlay。37 输入前后不变，112 子进程全部通过 ready barrier 并进入负载；失败时 required lease 仍有至少 22.047 秒。该证据支持修复此前 relay 启动等待导致的租约过期，不接受整体负载性能。

r5 为 FAILED/CHILD_FAILED：strategy-046 orders.cancel 已发出但在 5 秒内未收到匹配应答，RequestOutcomeUnknownError(response_timeout)。120 秒 pilot 未完成，1800 秒正式运行未启动，LOAD01 不计 PASS。业务数据库中的 ACKED 记录无 transport message-id 桥，不能据此断言对应超时请求已确认。同步 handler/维护造成队头阻塞及 NOBLOCK reply drop 是下一步需分辨的候选原因，不作因果定论。

清理为异常关闭：109 次强制终止、一个 strategy exit 1、hub/monitor exit 0；112 个记录 PID 在只读复核时均无存活，PID81428和历史保留Host未被查询或处理。此前只读 SQLite WAL 访问可能更新 -shm sidecar，未删除/回写，不能声称目录字节不变。有限诊断和原始结果完整保留在 raw-evidence.zip。

仅此四行和证据纳入提交。之后的计时遥测及性能修复另轮验收；正式计数仍 33/68。
