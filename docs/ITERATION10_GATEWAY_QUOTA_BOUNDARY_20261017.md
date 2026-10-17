# 迭代10：Gateway 配额边界修复

2026-10-17，第289轮。父仓库 bt_api_py / dev；本轮父提交为 06465137fcdbd82296779f6f2abc1efbf52900c0。

Gateway 子包已先提交 12e8de6f9552f7974a450f3217d497fb9c89e33d（父 29b09555ae6429c2b40d6bfb69dc923a8a71993d）。本轮父仓库更新该 gitlink、此记录和迭代进度，保留其他已有修改。

修复慢 COMMIT 后可能返回过期 grant、COMMIT 失败时提前标记 granted 而漏清 waiter，以及同 subject 普通队头挡住 critical 请求使用 reserved capacity 的问题。同时交付已回归的 BEGIN-only bounded retry 与提交后 advisory wake 计算。已成功提交的迟到 quota 保守保留，不退款、不重放事务。

Root R1 整文件 43 passed / 2 failed（45项，5.40秒）；仅修改两处测试时钟/新公平顺序断言，Root R2 补跑 2 passed / exit0。生产源不变，其他43项源段未变；两轮118输入与三个HEAD均前后相同。独立只读审查GO。45唯一用例为增量覆盖，不冒充一次全套45/45。

[子包验收记录及原始结果](../bt_api/bt_api_gateway/docs/ITERATION10_QUOTA_DEADLINE_RESERVED_CAPACITY_20261017.md)包含源码pin、原始R1/R2结果、失败前测试副本和证据边界。仅本机SQLite/合成时钟组件，不证明100进程性能或真实账户。

正式进度仍61/68。未验收仍为ARB05、DIM07、DIST01、DIST02、LOAD01、VENUE01、VENUE02。DIM07/LOAD01的旧未执行R5/R12包保留，下一实际运行须刷新为本次提交来源；需用户内存释放通知及实际可用内存至少10,603,200,512 B。未启动负载或账户，未改变保留会话。
