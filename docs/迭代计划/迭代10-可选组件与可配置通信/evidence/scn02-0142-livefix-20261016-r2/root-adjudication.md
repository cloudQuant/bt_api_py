# 第173轮根裁定：SCN02 014_2 空交易所修复验证与本机 dry/replay/live 观察 r2

- 裁定：GO（修复验证级）。`backtrader/stores/btapistore.py` 单文件修复（+43/−1，4 hunks；sha `9670cca7…` → `451b488f…`）经根复核（工作区 diff 与 `btapistore-fix.diff` 逐字节一致、文件 sha 与验证值一致、该文件不在 SCN02 基线 porcelain 内）后已提交 Backtrader dev `eb03f91e0`（`git show --stat HEAD` = 1 file changed, 43 insertions(+), 1 deletion(-)；HEAD `b002afa2d` → `eb03f91e0`，仅此一文件）。014_2 正式 PASS 未取得（第二阻塞项在案），不新增正式 PASS（28/68）。
- 修复机制：订阅时保留每合约解析出的交易所；venue tick `ExchangeID` 为空时回退框架 CZCE 推断（镜像 `_normalize_ctp_instrument` CZCE 分支，含窄 CZCE 期权形态 `^([A-Z]{1,2})(\d{3})([CP])(\d{3,5})$`，DCE/SHFE 4 位月份显式排除）；venue 自带 exchange 永不覆盖。离线验证 negatives：UR2505C1900 / rb2510C3300 / m2501C2900 不匹配。
- 运行验收（固定 HEAD `b002afa2…`，全程零写、`--allow-write` 未用，`order_write=0`）：
  - dry：exit 0，`status=DRY`，network 0，provider_sessions 0；
  - replay：exit 0，`LOCAL_REPLAY_PASS`，run_id `replay-20261016T032158Z-062101ef`；
  - live（240 s）：exit 1，`FAIL / no_complete_minute_window`，但 `run_error=null`（385.078 s，run_id `live-20261016T032210Z-ae1ea79c`）——7 根封口分钟 bar 送达策略（`BarEvidence` 构造即校验非空 exchange；pre-fix 每轮在首根 bar 前必现 `ValueError: exchange must be an exact non-empty string`，exit 3）。**主修复判定 SUCCESS**。
- 对照验证：stores 单测失败表与 pristine HEAD 程序化比对逐项一致（两侧 38F/2E，40==40，无 unique、顺序相同，`failures-identical-check.py` exit 0）→ 零新增失败；行为检查 30/30 PASS。
- 第二阻塞项（在案，未修复）：feed 封口身份为裸 contract code（`btapifeed.py:1484` `symbol=self._dataname`），而策略证据表按全称符号键控（`ctp_options_midfreq_strategy.py:674-681`，来自 `run.py:924 adddata(name=full_symbol)`）→ 7× `FEED_CLOSED_BAR_PROVENANCE_REQUIRED` → 预期 FAIL、feature_minutes 0。修复需改 btapifeed.py 封口身份或 run.py 接线，超出单文件范围；feed 侧改身份会改变所有既有 live feed 的封口身份，留待 owner 决策的独立轮次，修复后需重跑 live。
- 归档裁剪：`head-checkout/`（439 MiB pristine HEAD 检出，纯 scratch，含 `.pytest_cache`/`__pycache__`）未入档；其比对产物 `head-pristine-failing-subset.txt`（136 KiB）已入档。其余 35 个文件字节原样。
- 归并/卫生：Backtrader 侧 `backtrader/strategy.py`（+1 行 `from decimal import Decimal`，sha `6c085eb5…`）、`btapibroker.py` 等脏文件属 BT V2 线，未触碰未提交；本轮仅提交 `btapistore.py`。凭据扫描 PASS（`secret-scan.txt` 严格范围 + 碰撞分析：命中仅为仓库内公开 SimNow 占位常量与合成 fixture 数值，无真实凭据进入任何工件）。
- 关键工件 sha256：root-report `312b08fa931964427771804f108859482ba971ba5505400c1ff1d2eee3e11b50`；btapistore-fix.diff `1dbe2aebf25f1d60254b175b0e42e621331b009c701e96968cfbcec1b97ba809`；failures-identical-check.txt `1fa943602de5bc44ee1f09321f47cbb3ec0e86eb5cbfdc1a94e5d6e6b3df65e3`；0142-live-evidence/run.json `a64743729bf108989b1dfa090caa0fe704b893060b5da01436e11fb7a3eb883e`；0142-live-evidence/strategy_report.json `83dee63ff089cd9aaa905486410d51381b69664c81a1be27f30703d9d3735cc4`；0142-replay-evidence/report.json `4590582d97ccf1869db0439a177e771296a2d20dc62bcd3280b9c0ec5cf23876`。
- 证据等级 `BACKTRADER_SINGLE_FILE_EMPTY_EXCHANGE_FIX_WITH_IDENTICAL_UNIT_FAILURE_LISTS_AND_ZERO_WRITE_LIVE_OBSERVATION`。
- 剩余缺口：014_2 live 观察仍无 PASS（第二阻塞项修复后重跑）；SCN02 其余子项状态以交接文档为准，不因本轮变化。
