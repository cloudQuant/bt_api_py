# 第195轮：迁移标记跨进程读写原子性

仓库/分支：bt_api_py/dev，验收父提交 ed3555fc32fdf51213d0e34ba13afa73a2283e2b。提交哈希可从本文件 Git 历史取得。

范围：marker 的 arm、seal、rollback、reverse acceptance 及公开读取共用持久 sidecar OS 锁；锁内重新读取，防止旧 rollback 覆盖新 seal。无 marker 路径保持无副作用；schema_version 拒绝 bool/float。原子替换后的 POSIX 目录同步失败明确报告 durability uncertain，不恢复旧状态。

Root 首次新文件验收为6通过/1失败，Windows并发读句柄导致原子replace被拒；原始输出保存在initial/。修复后公开读同锁，内部helper避免重入；测试改为真实arm/rollback/seal API。独立静态复审GO，Root对受锁变化影响的新文件7节点完成一次必要回归：7 passed，exit0、stderr空，284来源pins及HEAD不变（accepted/）。原有已接受12节点未重复。

该轮只接受本机marker组件，不是MIG03全部通过；正常业务首次写接线、旧客户端停写证明和迁移/回退整链仍待验收。历史失败、无关WIP、未跟踪Monitor阈值文件均保留。
