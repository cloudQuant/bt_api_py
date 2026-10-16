# 第176轮根裁定：DIM02 三增量（多键共享上游 / 历史→实时接缝 / 腿时间偏差就绪）

- 裁定：GO（增量级）。本批 = 3 个增量：incr-1 多键共享上游与稀疏期权链路（2 节点，真实 4 进程 spawn）、incr-2 历史→实时接缝与 bar 修正政策（4 节点）、incr-3 腿时间偏差就绪模块（新生产模块 `bt_api_py/service_market_legs.py`，240 行）+ 4 个测试文件；全部经独立复核 ADOPT。**不新增正式 PASS**（迭代10 仍 28/68；DIM 组仍部分）。
- 文件与 sha256（提交时复算全对）：
  - `bt_api_py/service_market_legs.py` `1d6456c25406dd034cb098a71a3e4e412202b784b93e3f1083295bec6571edf2`（240 行）
  - `tests/unit/test_service_market_legs.py` `d99776ab16a36b2adff8ca9ec992a3cc9f69ce90180a49a56a0f37c967a2c53b`（339 行，9 单测）
  - `tests/unit/test_service_market_dim02_integration.py` `938125294243acaeee6bb168d0c7f12c661c34ae91032ba599510327839e5e46`（1349 行，2 节点）
  - `tests/unit/test_service_market_dim02_seam.py` `7c86904384a8423d42b2cccdc92984ef185a8e2e9e9df19c705b5612531a0bc6`（1129 行，4 节点）
  - `tests/unit/test_service_market_dim02_legs.py` `a9de557c1321217304e5606e3b3b3622026801a15bf1f479c3d55cb07718a4b4`（703 行，1 spawn 节点）
- 独立复核：
  - incr-1 ADOPT（`incr1-review-status.md`；reviewer 双跑 runA/runB 各 exit 0 "2 passed"；保留 run2b/run2c 语义一致——修正：作者早前"逐字节相同"表述过度，stdout 仅 duration token 不同（10.91s vs 10.76s），语义一致成立；run2a 为更早修订的过期期望失败，mtime 链证明在 run2b 前已重写；披露：incr-1 证据目录无 sha256 快照，修订以 mtime 链推断）。
  - incr-2 ADOPT（`incr2-review-status.md`；三跑 4 passed）。
  - incr-3 ADOPT（`incr3-review-status.md`；含 mutation 敏感性验证——静默计数、陈旧不阻塞两处突变均使 spawn 节点失败；ruff/格式检查洁净）。
- 失败输出保留：run2a（过期修订）、run1p/run3-gw01-regression（GW01 外国缺陷复现）原样归档，未覆盖、未重试。
- 外国缺陷披露与闭合：`tests/unit/test_service_market_integration.py::test_market_epoch_change_requires_explicit_incomplete_resync` 因 `4d5cebf5` 在 client ready 元组 index 2 插入 account_ref 后，epoch 节点残留过期索引 `ready[2:5]`（:903）与 `ready[5]`（:915）而确定性失败；与 DIM02 无涉（--noconftest、不同目标文件、失败复现早于 DIM02 文件 mtime）。已由第 175 轮修复（`ready[3:6]`/`ready[6]`），修复后全文件 exit 0 "2 passed"（本目录 `gw01-epoch-node-fix/`）。
- 提交：父仓本提交 = 5 源文件 + 本证据目录；无子仓提交，各 gitlink 不动。
- 证据等级：LOCAL_SPAWNED_PROCESS_AND_UNIT（dev 工作区固定来源；非真实账户、非隔离安装）。
- 剩余缺口：DIM02 正式条款判定另行轮次；全部 run 均在固定文件集 + 钉住的 PYTHONPATH（5 个 src 根）下执行。
