# DIM05 跨期组单节点作者验收记录

本轮只补 DIM05 的“跨期组”子条款，不宣称 DIM05 正式 PASS。作者固定源验收使用 R2 已接受的 group Host/coordinator/Risk reservation candidate，并仅叠加一个新节点；没有导入当前工作区中其他作者的未提交服务代码。

新增节点 [test_dim05_cross_period_shared_reservation.py](../../../../../tests/integration/test_dim05_cross_period_shared_reservation.py)，SHA-256 `371e876075268371281d15f28ef6e74347168c881c8fd84c60665c764b656c54`。它在一个账户下提交两个不同到期月份的合约：旧腿请求 2、部分成交 1.5 后撤单，新腿只按 1.5 对冲；断言唯一账户 reservation 覆盖两腿、两个 instrument ID 未合并、组状态为 `PAIRED`、持久 journal 只有一个 `GROUP_PAIRED`、两条 Risk leg 均已 claim/resolve，且合成 provider 恰有两次写入。

QA 运行时固定使用 `.codex-tmp/iteration10-arb03-dim05-fixed-source-r1/candidate` 的 R2 production source snapshot，并将准确 helper/fixture 复制到独立 overlay。覆盖了 2,338 个冻结 source pin，全部不变。关键来源为 `service_execution_group.py` 5606785c…、`service_execution_group_proofs.py` 8a508aa9…、`service_execution_group_authority.py` 49fd1fbf…、复用 helper a37f42a9… 和 provider fixture 9bcf0216…。新 spec 位于 `.codex-tmp/dim05-cross-period-fixed-source-r1/root-qa-spec.json`，SHA-256 `08f7d5afa6d3c7e0394a2d823bad2755171c18187a27f4e195f3179041745589`；source resolution 明确只从冻结 candidate 与 overlay 导入。

唯一新增 pytest 节点通过：1 passed，case 7.311 秒，pytest 9.110 秒，exit 0，stderr 为空。原始结果目录为 `D:\temp\iteration10-dim05-cross-period-author-20261017-r1`；`result.json` SHA-256 `58cd6232374316e194ee75cb13ad0146ff882cd5f93dfda1bb54416b772ad653`，`junit.xml` SHA-256 `a004fdc2c9b6f224ad3e2a3706b1536b02995268e4a6841a8c8a9aff4132393b`。第一次 runner 预检只因沿用了 R2 spec 中四个旧 live-path pin 而停止，未建输出目录、未启动 pytest；删除这些不参与本轮导入的 live-path pin 后，新的唯一节点才运行并通过。

该测试使用合成 provider，证明的是固定 R2 source 上经正常 Host/coordinator/Risk reservation 入口的跨期部分成交配对。合约 ID 是不透明的合成 ID；本节点没有证明交易所到期规则、旧合约平仓/恢复，也没有覆盖生产中当前其他未提交改动。旧合约退出/恢复仍是 DIM05 的独立未完成子条款。
