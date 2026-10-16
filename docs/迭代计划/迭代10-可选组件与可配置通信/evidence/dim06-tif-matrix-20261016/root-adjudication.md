# DIM06 venue×订单类型/TIF 不兼容矩阵合同 — 根裁定（第191轮，2026-10-16）

- 交付物：`tests/bt_api_contract/test_venue_order_type_tif_matrix.py`（新文件，sha256 `6f39ed54248021b5b249b93eb34a88875dd4d2690427ea2dded28381741248e5`，43 节点，生产零改动）。
- 生产方：固定来源 r1（`.codex-tmp/iteration10-focused-qa.py` + spec `.codex-tmp/it10dim06-tif-20261016-r1-qa/focused-qa.json`）一次性 exit0、`43 passed`（17.4s）、263 pins（source-before.json == source-after.json 字节等同，40261 B）、heads 不变（父 e137193d、base b4aabd1e、ctp a497bab6、okx 8c0019db、binance b438a277）；stderr 空（`e3b0c442…`）。
- 独立复核（`review/dim06-tif-review.md`，GO-with-disclosures，零证伪）：
  - 逐条断言映射生产闸门 file:line：CTP mapper `_venue_mappers/ctp.py:20-23`（`CapabilityNotSupportedError`+`definite_reject`）、CTP feed `live_ctp_feed.py:1137-1143`（TIF 词表）与 `:1104-1107`（kind 守卫先于 `_next_request_id()`@:1146）、`TimeCondition="3"`@:1135、OKX `trade_mixin.py:66-78`（词表+post_only 规则）、Binance `rest_trade.py:38`（无校验透传）；
  - R3-自由闭合双法证明（插件探针 261 源 + in-test 守卫断言 `bt_api_py.bt_api`/`_direct_backend`/`_gateway_wire` 不在 `sys.modules`）；
  - MIG01-R3 提交（ee3b05c2）后按 spec 263 pins 从磁盘复算 **263/263 零失配**（HEAD 位移已记录为可接受）；
  - 独立重跑复现 `43 passed`/exit0（17.40s，junit tests=43）。
- 采纳裁定：**GO** — 作为 DIM06 增量提交（第191轮）；**不翻转 DIM06**（保证金池归属裁定与会话路径节点另计），正式 PASS 计数不变（30/68）。
- 披露（随账本）：
  1. 合成 in-process 夹具（无真实账户/网络）；provider 侧行为留 VENUE02（正式迭代计划.md:778）。
  2. 环境偏离：`BT_API_PY_LIGHT_IMPORT=1`（避开 R3-脏默认导入链；LIGHT_IMPORT 模式由 SDK 自身测试合同）；repo 版 `bt_api_ctp` 经 PYTHONPATH 注入（site-packages 旧副本与 pin 版有 263 行差异）。
  3. Binance 无本地 TIF 闸（任意 token 透传）与 OKX 市价+post_only 无本地拒绝——按 NOT-asserted 诚实记录，未伪造拒绝。
  4. 容器断言 nit：`get_order_time_in_force()=="GFD"` 恒真（`ctp_order.py:152-153` 硬编码），载荷断言为 wire `TimeCondition=="3"`。
  5. smoke#1（35p/8f）原件被覆盖不可复现；smoke#2 与正式轮均可复现。
  6. `tests/conftest.py` autouse fixture monkeypatch `_ledger_registry_root` 属全局测试基建，与本合同无冲突（已披露）。
- 提交范围：`tests/bt_api_contract/test_venue_order_type_tif_matrix.py` + 本证据目录 + 账本 DIM 行注记 + 进度 第191轮。
