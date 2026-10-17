# R284：MIG01 正式验收裁定

裁定：MIG01 PASS，正式进度从 57/68 增至 58/68。依据正式计划第10节 MIG01 的源码和构建产物扫描、消费者迁移、隔离安装与正常调用条款。此裁定不新增真实 provider、账户、双机或负载验收。

SDK/Base 旧 forwarding/gateway 实现已退役；五个相关 provider 的旧 adapter 实现已删除，Binance 的直接 client/account-provider re-export 和 HTX 空命名空间按已明确范围保留。Backtrader 的最后一处不可达 GatewayClient wrapper、环境加载器和路由分支在 dev `6b25ee94ffec14cfad4b511eb6370eba38521c10` 删除；早期迁移拒绝、Direct、只读 Service 与直接 CTP 路径保留。该源码轮25项定向测试通过，独立静态审查接受。AFI 消费者退役与安装后普通入口复用 [AFI10 R283](../afi10-formal-r283-20261017/root-adjudication.md)；旧配置转换复用 [18项固定来源验收](../mig01-offline-config-migration-r1-20261017/round-record.md)。这些已有测试未为本次归档重跑。

本轮安装组合精确限定为 SDK `35e3879a7c3c43e38fce0bfdacd0668c1f69d6a2`、Base `fb47f3dd733613494aa1d86cfd94b2d6fe70314d`、Backtrader `6b25ee94ffec14cfad4b511eb6370eba38521c10`，版本分别0.15.5、0.15.8、1.4.0。SDK/Base wheel复用已接受M6产物；不能把它们写成当前SDK6ff的发行验证。新BT wheel由精确Git archive构建，532个包文件全部与归档一致，没有退休Gateway实现。新Python3.12.10 venv关闭系统包继承，--no-index安装且移除PYTHONPATH，三个发行包都来自该venv的site-packages；Gateway/Execution/Risk/Monitor/transport-zmq和zmq未安装。

三项隔离进程调用均exit0、stderr空：DirectBackend调用本地合成feed一次；Cerebro处理三根内存bar；Store拒绝8个旧构造入口、3个环境入口及3个后改旧backend入口，旧路由API构造数为0，同时保留Direct Store和注入的只读Service connect/balance/disconnect，写调用数为0。operator --help成功，无配置输入且未启动服务。只读Service是本地fake API，不是实际RPC；Direct记录feed_closed=0，不用作feed资源关闭证据。M6旧BT构建日志缺失的历史边界保留，本轮新BT构建有完整命令和输出。

构建exit0并保留setuptools-scm/Git文件列表及setup.py弃用警告。R1 archive命令重复git导致的预构建失败保留在ZIP，未作为通过证据。新交付草稿曾错误地把SDK tar哈希片段拼入提交号；Root通过git rev-parse/cat-file和原M6 provenance核验，修正了两个尚未提交的描述文件。旧草稿逐字节保存在D:/t/mig01-btapistore-retirement-proposal-history-r1；ZIP、构建、wheel和执行结果均未变更。

Root核对原始smoke/来源/安装记录及31个ZIP成员；独立源码条款审计没有发现额外退役缺口，独立发行审查接受该固定组合与上述范围。当前工作区后续变化不自动继承本PASS。精确命令、脚本、原始输出、环境与外部tar/wheel身份见[manifest.json](manifest.json)和raw-evidence.zip。
