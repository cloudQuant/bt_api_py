# 第172轮裁定：遗留件归并（journal 十进制匹配测试 + r20 归档文档）

- 裁定：GO（登记归并）；无正式条款变化。
- 背景：工作区遗留两件未提交产物：
  1. `tests/unit/test_service_risk_order_journal_decimal_match.py`（50 行，sha `10f24243…`）——第160轮（DIM01 正式通过轮）复核明确披露"（未 pin）保留未提交"；所测 `_decimal_text_matches` 辅助函数由 `b3b2c6d6`（risk 敞口观察接入轮）提交，本测试为该冻结行为的严格化 pin：尺度等价（`"1"`/`"1.0"`）不重写、不改变 payload sha；精确数值等值且 16 位尾差不匹配；畸形文本、`_` 分隔、NaN/Infinity、布尔与非字符串输入全部拒否。
  2. `docs/迭代计划/迭代10-可选组件与可配置通信/risk-order-exposure-sdk-round-r20.md`（39 行，sha `578cc5b2…`）——与已提交 `evidence/risk-order-exposure-service-20261016/author-delivery-note.md` 逐字节相同，为顶层散落的重复副本（r20 证据目录此前已提交）。
- 验证：固定树 root `6bafd206`、execution `dc8bd7a3`、risk `e2bedb3cc7aefc084e9353af3fbef743eea60c8`；命令与原始输出见本目录 `cmd.txt`/`stdout.log`/`exit.txt`/`stderr.log`；结果 12 passed、exit 0、0.88s、stderr 空、1 条既有未知配置 option warning（`--noconftest` 隔离运行固有）。log sha：stdout `fdc31070…`、stderr 空文件、exit `9a271f2a…`、cmd `f736e44a…`。
- 范围：仅上述两文件并入 + 本证据目录 + 进度文档登记；不触碰 risk gitlink、`.codex-tmp/`、`%SystemDrive%/`、`0` 与其它工作线文件。多轮复核（reg32/ARB04/CFG06b-combo3/DIM03/DIM01-p2）均将两件登记为 foreign 且"不得 staged"，本轮为其正式归属收口。
- 证据等级 `TEST_ONLY_LEFTOVER_ADOPTION_WITH_FIXED_TREE_RUN`。
