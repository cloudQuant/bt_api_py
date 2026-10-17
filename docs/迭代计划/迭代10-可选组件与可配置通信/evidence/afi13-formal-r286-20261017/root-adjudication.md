# AFI13 第286轮正式裁定（2026-10-17）

Root按正式计划第10节AFI13条款，结合下面互补证据，裁定 **AFI13正式PASS，累计60/68**。范围是本机合成报价、真实HTTP/SDK/服务进程与SQLite的受管Paper链路。外部demo账户、真实交易所和负载验收仍单独待验。

## 本轮原始失败与实际业务事实

Root R5只运行一次，原始结果保持 **1 failed / pytest exit 1 / 117.92秒 / 13 warnings**。失败在最终只读账本断言 `fill.fee`：实际 `PaperMatchReceiptV2` 的字段为 `fee_amount`。这不是一轮绿色pytest，不把后续核对改写成原测试通过。3,646个固定来源文件前后不变，stderr为空；spec的repositories为空，不能将它称为当前Git HEAD组合验收。

失败前已实际完成：非空旧账导入ACTIVE、显式确认cutover、两个独立AFI HTTP worker并发同操作键返回同一FILLED、异内容409、第三个worker重启后同成交重放/查询、Monitor前后响应相同、报价仅消费一次、真实旧writer拒绝及来源digest不变。Importer、Execution/Risk owner、Monitor和三个HTTP worker均完成正常关闭断言；五份进程退出收据记录正常ACK、exit 0、无fallback/terminate。Importer未单独写process-exit JSON，由已通过的 `_close_normally` ACK/join/exit-zero及tuple断言证明，不能补造收据。

Root随后以SQLite `mode=ro` / `query_only`核对同一轮保存的三份合成数据库，**22项检查通过，exit 0**，数据库及存在性、WAL/SHM/journal、原来源和result文件的前后pins相同。当前旧账户/order/position/trade完整重建digest为 `33de3bf86e46ed2d5eb0d0c2cb6c6e097b3b56cd2ba90f58012415d2302a5781`，与导入快照完全相同，数量1/1/1。AFI import/cutover与Execution导入仍ACTIVE并保持来源、目标及domain绑定。

Execution只有一次dispatch和一份V2 fill：0.25 BTC-USDT，价格10.02，费用0.0025 USDT，现金变化-2.5075。持久导入基线加该fill重建现金87.3925、累计费用0.1025、持仓1.25、平均成本10.004。四条唯一Execution事件与四条Monitor持久事件按顺序对应，同intent/generation，无重复。这里只读重建最终余额；失败前的真实 `read_paper_book` 已通过receipt count、cursor、generation及持仓断言。内存中的Monitor响应相等由失败前断言证明，不声称后验恢复了该tuple或对象身份。

## 正式条款映射

| 条款 | 接受证据 |
|---|---|
| E-only正常入口与受管模拟端口 | `evidence/afi13-paper-owner-r238-20261017/raw-evidence.zip`原始manifest/JUnit/stdout：E-only实际HTTP/lifespan/cutover/submit/query/restart节点及typed owner-policy节点共2 passed，19.31秒，exit 0；2,821来源pins。另有第123轮E-only Host兼容证据。R238不是只有政策源码测试。 |
| 手工/AI/策略经过已启用Risk，Monitor消费同源事件 | `docs/ITERATION10_AFI13_MANAGED_PAPER_MULTIWORKER_R9.md`：Root固定来源1 passed，38.63秒，3,589pins；本轮R5把非空迁移接到该入口。`docs/ITERATION10_AFI13_STRATEGY_PAPER_RISK_MONITOR_R12_20261017.md`：签名研究策略启动→真实Backtrader子进程→E/R/M，1 passed，27.29秒，2,739pins及9HEAD稳定。显式AI确认R4另有1 passed/68.53秒的实际HTTP/Host证据，但来自混合checkout，缺逐生产模块hash；仅保留为该行为的补充，不称固定发行组合。 |
| 旧状态守恒、拒绝反例与快照单owner | AFI12 R273正式裁定及AFI提交 `44ad881b729bfd1fa489b73d522c82debecc744d`覆盖非空账、未决订单/残差/P&L拒绝、上下文保留和受控回退。MIG03 R276、SDK `002d8d45be73be25d6622f5028bab1120685c9fc`覆盖真实snapshot scheduler、持久快照、正常stop/join、ACTIVE导入与旧writer拒绝。本轮R5补充导入后实际新成交且来源不变。R273两Host节点只有作者控制台证据，无持久JUnit，沿用其既有披露。 |
| 多worker/重启无双撮合 | R9与本轮R5：同键并发、异内容拒绝、worker重启、同一fill/intent、一次quote/dispatch及Monitor不重记；本轮额外核对迁移后的现金、费用和原仓。 |
| Paper/Live/Research分域、零live写、离线研究保持 | E-only及selected E/R/M固定Paper配置、wrong-domain/live启动拒绝与typed授权边界；R9/R12/R5使用Paper端口和合成quote，无live provider写路径。AFI10 R283正式裁定保留普通无组件离线BacktestService与隔离安装正常策略入口。不是从这些本机结果推定真实venue写入通过。 |

## 交付与复现边界

现有SDK `dev`父提交 `3751e31cb572c47936fe60189a50d44e71dbeeb8`；本轮仅新增组合测试、原始证据/裁定、状态/进度和该测试的精确字节规则。未改生产实现或其他WIP。原canonical草稿备份在 `.codex-tmp/afi13-imported-paper-canonical-before-r286-20261017.py`。

交付测试是实际R5源码加唯一 `fee`→`fee_amount`修正，SHA-256 `fa993c3f251608d76512827f243db287f8c0e55e55e4d6e779febba13b425743`；修正后的完整Host节点没有重跑。原执行源码SHA-256为 `0f8e63311e8f4018e2b48eeaa32664a400524cdc8a6ea2ddd1fb901c13cceaa9`。八个辅助文件与交付HEAD逐字相同；另一个runtime-origin helper只是本测试专用的模块名/callable清单变体，精确原件随ZIP归档。复现记录中的固定来源调用时，应按manifest将该变体覆盖到导出中的 `tests/unit/_runtime_origin_receipts.py`，不替换共享canonical R9 helper。

`raw-evidence.zip`保存36个原始成员，包括R5源码、命令/结果/日志/JUnit、完整来源map、进程收据、三份合成数据库及Root只读核对脚本/结果；R3 scope错配及R4关闭状态oracle失败也原样保留。逐件hash及来源见 `manifest.json`。Root没有重跑已接受的R9/R12/R238/AFI12/MIG03/AFI10节点。包含本记录的scoped commit标识最终交付。

ARB06仍未正式通过：完整R4为UNKNOWN/RISK_QUERY_UNAVAILABLE；后续SDK `3751e31c`仅接受PREPARED查询协议修复的6项本机定向测试，尚未重验完整Cerebro链。DIM07/LOAD01、双机三项及VENUE01/02继续按状态表暂缓。
