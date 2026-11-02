# bt_api_py 本轮许可调整审阅包

状态：本地修改，未暂存、未提交、未推送、未发布、未开 PR。

前一阶段交付：新增三份未生效审阅文件；所有现行许可、元数据与业务文件保持。本文件包含完整待审条件和写入前的来源核查快照；下文核查快照中的“只读/未应用/干净”只描述准备阶段，不代表最终工作树状态。最终差异仅包含本文所列新增文件与 backtrader 一行纠错，用户既有改动不在交付补丁中。

完整条款见 [独立许可草案](LICENSE-SOURCE-AVAILABLE-DRAFT.md)，逐文件边界见 [范围草案](LICENSE-SCOPE-DRAFT.md)。已批准适用白名单为空，根 LICENSE 不替换。

本次增量：基础商业政策已获用户确认；其他例外、正式文本和生效范围仍待审。新增 `DISCLAIMER.md` 及 README 入口，更新三仓许可正文和各仓范围说明。下文历史快照中尚未确认基础政策的表述仅记录前一阶段状态，不覆盖当前确认。免责声明未生效且不是许可条件；根许可和原有免责不变。

# 本地许可调整审阅说明

日期：2026-10-10。状态：本地审阅文件；未提交、未推送、未发布、未创建 PR。

本轮独立许可正文为 `LICENSE-SOURCE-AVAILABLE-DRAFT.md`，逐仓库范围为 `LICENSE-SCOPE-DRAFT.md`。草案均未生效；仓库现行根 LICENSE 保留。源码可见与禁止未经授权商业使用的方向明确；基础商业政策现已确认，具体正式文本和适用范围尚未获批准。个人使用本人自有资金回测和实盘明确拟免费，不以本人投资收益本身认定收费商用。无自动转开源期限。

## 提交前最小决策

1. **剩余例外**：用户已确认个人本人自有资金免费，企业内部使用、为他人管理资金、收费服务或商业产品集成需另行书面授权，无须重复确认这一基础政策。个人免费用途中的常规集成不属于商业产品集成。家庭共同账户、借款/全资公司账户、无偿亲友代管、公益学术机构、培训咨询/捐赠/报销成本等例外应逐项选定，或明确继续暂缓。不得因收益金额、交易频率等让个人自有资金使用变成收费商用。
2. **许可人及范围**：确认有权签发许可的主体，并以来源/授权证据确定首次生效的新版本、完整提交 SHA 和逐文件/版权部分白名单。当前三仓非商用许可白名单为空；所有来源不明、未获足够授权或不可分离的已有内容继续保留原许可。Git 作者名与根版权声明不足以证明完整权属。
3. **采用文本与贡献机制**：审阅完整独立草案、再分发规则与长期源码存档安排；决定下游原创新增部分是否必须采用同一许可、是否须公开，以及未来外部贡献能否另行取得商业授权。贡献者须主动确认具体许可，不能通过修改 CONTRIBUTING 追溯取得版权或宽泛重许可权。正式法律条款建议律师审阅。

仓库特有的硬阻塞另见各自适用清单：Fincept 签署与 GPL 组合分发；opendata 现行 BSL 冲突、既有转换与第三方资源；bt_api_py 的 19 子仓及厂商 SDK；backtrader 的 GPL 来源与版权声明保全。

## README、贡献说明及元数据的未来修改草案

本轮不把 ai-for-investor、opendata、bt_api_py 的现行许可元数据改成新许可，也不覆盖现有 README/CONTRIBUTING 中仍然正确描述当前版本的段落。权属与版本边界通过后，才按经批准清单同步修改：

- README：仅对批准的新版本及许可作品称“源码可见”，说明个人本人自有资金使用免费、商用另行授权、无自动转换；链接许可全文、版本适用清单及第三方例外。
- Python/前端包元数据：核实发行包的实际文件范围；只为已覆盖的包采用自定义许可标记及许可文本，不能冒用 MIT、BUSL-1.1、OSI classifier 或已注册标准许可标识。混合发行物须准确记录各部分许可；不能只改一个包标识就覆盖依赖、子模块或旧版代码。
- 贡献说明：新贡献者确认对明确文件及许可版本有权授权，保留来源和例外；既有 MIT 入站条款不改写成历史贡献者已同意版权转让或限制商业用途的证据。商业授权外部贡献须额外明确获得。
- 第三方声明：记录实际文件路径、上游版本/提交、版权、原许可证及待核实项；无需删除或降低现有 MIT/GPL/厂商声明。数据接口条款与数据再分发权不由代码许可代替。

建议未来贡献确认语句（尚未采用）：

> 我确认对本次贡献列明的原创部分有权授权，并同意这些具体部分按项目指定的正式许可版本接收和发布。我保留版权；第三方材料及其原许可已单列说明。本确认不适用于过去贡献，不是版权转让，也不自动授权项目所有者商业许可或任意重许可。

## 一手参考与设计来源

本轮只比较原则，不复制、翻译改写或换名使用标准 BSL 正文。MariaDB BSL 1.1 的自动转换与正文使用限制，不能满足无自动转换目标；opendata 既有冲突须律师审阅：[MariaDB BSL 1.1](https://mariadb.com/bsl11/)。

禁止商业使用的许可应描述为源码可见；开源定义允许商业领域使用：[OSI 开源定义](https://opensource.org/osd)。MIT 原授权包括相应商业利用权，不能以新根文件追溯取消：[OSI MIT 正文](https://opensource.org/license/mit)。

GPLv3 对适用作品再分发的限制不能由非商用条款覆盖：[GPLv3 官方文本](https://www.gnu.org/licenses/gpl-3.0.en.html)、[SPDX GPL-3.0-or-later 文本](https://spdx.org/licenses/GPL-3.0-or-later.html)。组合分发需要检查实际通信和结合关系：[GNU GPL FAQ](https://www.gnu.org/licenses/gpl-faq.en.html#MereAggregation)。GNU 页直接抓取超时，已以一手搜索摘要、SPDX 转载正文和本地 GPL 文件交叉核对；未据此作出具体产品的法律豁免结论。

## 本轮已确认政策与免责声明审阅

用户已确认：个人本人自有资金免费；企业内部使用、为他人管理资金、收费服务或商业产品集成需另行书面授权。政策确认不代表正式许可、生效版本或任何权属白名单已经批准；前三仓白名单仍为空，Backtrader 仍保留 GPL，不适用上述新增商业限制。

新增 `DISCLAIMER.md` 是独立免责及风险提示草案，未生效，不构成许可证或商业限制。保证和责任限制须按适用法律、现行许可证、实际功能、展示和纳入方式审阅；正式发布用语及适用主体/版本仍须确认。通用声明不能消除实际个性化投资建议的义务，商业软件授权不等于金融监管许可或数据再分发权。

MIT 原有免责保留；GPL 第 15–17 条及第 7/10 条的既有保证、责任和权利边界保留。参考 [MIT 正式文本](https://opensource.org/license/mit) 与 [GPLv3 正式文本](https://opensource.org/license/gpl-3.0)。本轮不新增用户赔偿义务、任意责任上限、仲裁/法院选择或点击即接受约定。

正式发布前建议律师审阅免责声明与许可文本的配合，并核对适用主体、实际服务承诺及法律不得排除的责任；本轮不保证免责效力。完整业务测试和版权调查不在本轮增量范围。

---

## 写入前的逐仓来源核查快照

# bt_api_py 本地许可审查记录

审查日期：2026-10-10。状态：只读核实完成；以下计划与范围文件均为未生效草案。

## 1. 仓库与现有工作树

| 项目 | 本地核实结果 |
| --- | --- |
| 路径 | `/Users/yunjinqi/Documents/new_projects/bt_api_py` |
| origin | `https://github.com/cloudQuant/bt_api_py.git`（没有凭证信息） |
| 分支 | `dev` |
| HEAD | `d3674e19a11b9f35f19ae756899bcf18854c8c46` |
| 项目版本 | `0.15.4`，`pyproject.toml:7` |
| 父仓用户改动 | 以下 5 个已跟踪文件修改；本次没有触碰 |
| 本次目标仓改动 | 无；未暂存、未提交、未推送、未发布、未开 PR |

父仓既有改动（`git status --porcelain=v1 --ignore-submodules=all`）：

- `docs/迭代计划/迭代09-历史数据获取/初始需求.md`
- `docs/迭代计划/迭代09-历史数据获取/实施计划.md`
- `docs/迭代计划/迭代09-历史数据获取/研发设计文档.md`
- `docs/迭代计划/迭代09-历史数据获取/需求文档.md`
- `docs/迭代计划/迭代09-历史数据获取/验收文档.md`

本次只记录父仓状态和父仓索引中的 gitlink；没有读取子仓内部的许可、源码或工作树差异，没有调用递归子模块初始化或更新。

## 2. 已读取的仓库规范

完整读取了 `AGENTS.md`、`CONTRIBUTING.md`、`docs/CODE_QUALITY.md`、`docs/code-quality-baseline.md`、`docs/AGENTS.md`、`docs/development-guide.md`、`docs/explanation/architecture.md`；补充读取了与独立插件边界相关的 `docs/governance/branch-model.md`、`docs/governance/submodule-bump.md`。

目标仓没有 `.agents` / `.agents/skills` 目录。`AGENTS.md:3–4` 明确主仓是 Python 库，`bt_api/bt_api_*/` 是子模块。`CONTRIBUTING.md:172–186` 的路由表和 `docs/governance/submodule-bump.md:7–13` 要求插件变更走插件仓，主仓只收独立 SHA bump。本阶段没有更改任何 gitlink。

`docs/AGENTS.md:11–17` 要求修改活动文档前生成支持文档、运行文档合同与严格构建。本阶段仅在另一个工作区写审阅草案，未修改目标仓活动文档；因此没有在用户工作树运行会写文件的生成器或构建命令。正式采用时仍应遵循这项约定，使用隔离审阅副本保护用户既有改动。

## 3. 许可、入站贡献与元数据证据

| 文件 / 行号 | 本地内容与含义 | 本阶段处理 |
| --- | --- | --- |
| `LICENSE:1–21` | 标准 MIT 结构，版权行 `Copyright (c) 2026 cloudQuant`；第 5–13 行包括使用、修改、分发、再许可及销售复制品的许可，并要求保留声明 | 原文件完整保留 |
| `CONTRIBUTING.md:279–281` | 贡献者同意按 MIT 许可贡献 | 原条款保留；它未明确转让版权或授予独立的新许可权 |
| `pyproject.toml:5–10` | 名称 `bt_api_py`，版本 `0.15.4`，`license = {text = "MIT"}` | 与当前根 MIT 一致，无需纠错 |
| `pyproject.toml:34–43` | 分类器没有额外的 MIT/Open Source 分类器 | 无需清除不存在的分类器 |
| `setup.py:1,22–33` | 包装脚本声明元数据在 pyproject，且未另外声明 MIT | 保留；没有发现 setup.py 与根许可冲突 |
| `README.md:6` | 主仓是交易所插件、直接 Feed、可选 ZeroMQ 的门面库 | 不推断插件或 SDK 被根许可覆盖 |
| `README.md` | 现有内容没有根许可证章节或 MIT 徽章 | 可另拟未来许可说明，不能现阶段声称限制商用已生效 |
| `_templates/README.md:7,82–84` | 模板硬编码 MIT 徽章与说明 | 保留；若未来明确用于新许可覆盖的文件，需要同步模板策略 |
| `_templates/docs/index.md:61–63` | 文档模板硬编码 MIT | 保留；不能自动改变独立插件仓的许可模板 |
| `MANIFEST.in:1–10` | 清单主要列运行配置与 README，没有独立第三方声明条目 | 若将来正式采用新条款，需验证 LICENSE / 范围清单 / 例外声明进入 sdist 与 wheel；此清单本身不能证明漏包 |

只读父仓提交作者记录中存在多个作者身份（包括 cloudQuant、yunjinqi 和通用 Ubuntu 身份），无法仅凭名称将全部身份认定为同一版权主体，也无法据根版权行证明全部贡献都已获得改许可所需的权利。未在本次已检查的父仓许可 / 贡献 / 治理入口中找到版权转让或重新许可确认记录。MIT 允许的使用与再许可不能被误写成对已经收到 MIT 版本的人撤回既有权利。

## 4. 19 个独立子仓：全部排除

以下来自父仓 `.gitmodules:1–77` 和索引 mode `160000`。远程均是 `https://github.com/cloudQuant/<目录名>.git`，但相同组织或维护者名字不证明父仓有权修改其许可。表中的 SHA 是父仓索引 gitlink，不是对各子仓内部当前 HEAD 或许可的核实。

| 子仓路径 | 父仓索引 gitlink SHA |
| --- | --- |
| `bt_api/bt_api_base` | `3de0fa4f6cfe8d1973e9f4b9b47b01254259524f` |
| `bt_api/bt_api_binance` | `5444c30160b16f19a1e3d145de3f8ee8d499c71d` |
| `bt_api/bt_api_bitget` | `36510361214d7ae800ba65e10c3aab2515af9533` |
| `bt_api/bt_api_bybit` | `c9a8e64c1396083c0000d7f9a4986ab15483450b` |
| `bt_api/bt_api_coinbase` | `2eac6e8f155aca4f006a9e7d02779fc6306eea9b` |
| `bt_api/bt_api_ctp` | `ce1edd60785eb4c66fefa16a994a66946a1e068f` |
| `bt_api/bt_api_dydx` | `d2a0808b6f92ed4c1d611a3dd997c0029ce3859a` |
| `bt_api/bt_api_execution` | `2700cb5454ef4c3d1780eda28b6f33307a860998` |
| `bt_api/bt_api_gateio` | `518fd0affa24cd1b4c14cc65927f4ea13649fa3b` |
| `bt_api/bt_api_gateway` | `44fd2fe26b51f1d8b573c84415a6cff660f33dea` |
| `bt_api/bt_api_htx` | `dfebac2350d684ce786159ddd171d9b1af1df230` |
| `bt_api/bt_api_hyperliquid` | `d474167fdce8f6a30b167ffec0bf83d55cf8cf96` |
| `bt_api/bt_api_ib_web` | `6bcbd07317e21ed950805353f8f43a32fe775893` |
| `bt_api/bt_api_kraken` | `3cc8efc3def9d391777418b1172254e766da47c1` |
| `bt_api/bt_api_mexc` | `98aa839103780c2f44c24bc05890f81128f7d3ac` |
| `bt_api/bt_api_monitor` | `d515a8209324d56742c095d70976593bb3ba3eff` |
| `bt_api/bt_api_mt5` | `8221d58f2129bd10348743ae54da88e3635de41d` |
| `bt_api/bt_api_okx` | `cbe671f5d74cba6b3ee9cd68eb1319e271eb820a` |
| `bt_api/bt_api_risk` | `d0c18a9d503a6792b582d0927244ceb55395032b` |

`bt_api_execution` 的 `.gitmodules:64` 标为 `installable = false`，也不影响其独立仓库边界。治理文件提到的历史子模块数量不得代替本次索引得到的 19 个。

## 5. SDK、依赖与其他例外

- 本次父仓跟踪清单中，没有发现父仓直接跟踪的 `bt_api_py/ctp/` 原生 SDK 或 `.so/.dll/.dylib/.a/.lib/.h/.hpp/.cpp/.c` 文件。这只是跟踪清单结论，不证明子仓、忽略文件或安装环境没有厂商 SDK。
- `pyproject.toml:172` 仍写有 CTP vendor wrapper 路径豁免；`bt_api_py/_ctp_probe.py:1,10` 与 `docs/reference/installation.md:69` 提到 CTP/SWIG。旧路径、生成绑定和相应厂商 SDK 权利必须独立核实。任何 CTP / IB / MT5 SDK、头文件、二进制、生成包装器及第三方移植代码均不纳入新许可白名单。
- `pyproject.toml:15–33` 的 runtime 依赖（含独立 `bt_api_base`）及 `:238–307` 的可选依赖 / 插件包拥有各自的许可证；不能因打包或安装在同一环境中改用父仓的新许可。IB 可选项 `ib-insync`（`:287–289`）、MT5 可选项 `pymt5`（`:295–297`）及 `core-reference` 中 `bt_api_binance` / `bt_api_ctp`（`:304–307`）明确保留独立许可边界。
- 本次已检查的父仓入口没有 `THIRD_PARTY_NOTICES` 文件；尚不应凭没有声明文件就宣称没有第三方代码、数据、交易所文档或资源。未来声明只应记录已核实的来源、版本、路径和原许可，不应给未知 SDK 臆测 MIT/GPL 等许可。
- 示例数据、市场数据、交易所接口文档、商标和非代码资源的权属没有逐项核实，默认排除。目录、进程或 ZeroMQ 边界本身不能证明与 GPL 软件组合分发时免责，组合分发需单独审阅。

## 6. 可审阅修改与未来候选范围

本阶段实际新增文件仅在任务工作区：`licensing-review/bt_api_py/AUDIT.md` 和 `licensing-review/bt_api_py/LICENSE-SCOPE-DRAFT.md`。目标仓的 `LICENSE`、`CONTRIBUTING.md`、README、元数据、子模块、SDK 和业务代码均未改变。

将来得到明确权属和条款确认后，可审阅的候选修改如下；它们不等于已核准的许可白名单：

| 候选文件 | 计划 | 前提 |
| --- | --- | --- |
| 新 `LICENSE-SCOPE.md` | 记载生效版本 / tag / commit、逐文件白名单、权利人及全部例外 | 已逐项证明许可权和生效边界 |
| 新独立许可文本 | 源码可见；个人自有资金回测和实盘免费；无自动转开源期限 | 用户审阅条款和律师审阅 |
| `LICENSE` | 仅在确定主仓覆盖范围与例外表达后再决定采用何种根文件结构，保留原 MIT 文本与适用记录 | 不撤回旧版 MIT；未知 / 第三方部分不改许可 |
| `README.md` | 明确新版本覆盖范围、免费个人用法、商用另行许可及旧版 / 第三方例外 | 新许可确已采用，避免草案被误认生效 |
| `CONTRIBUTING.md` | 只针对采用后的新增贡献说明准确的入站授权与适用路径 | 必须独立确认，不把历史 MIT 贡献追溯变为版权转让 |
| `pyproject.toml` | 采用后才将 MIT 字段改为准确表达新许可和例外的形式 | 依实际打包工具 / 元数据规范验证，不编造 SPDX 标准许可标识 |
| `MANIFEST.in` / 打包验证 | 检查新许可文本、范围清单与第三方声明确实进入分发包 | 根据实际 sdist / wheel 结果决定是否需要显式 include |
| `_templates/README.md`, `_templates/docs/index.md` | 明确模板适用范围；必要时去除对新范围不准确的 MIT 固定文字 | 不自动改变独立插件模板 / 子仓许可 |
| 新第三方声明 | 记录逐项核实的第三方来源与原许可；未知项单列待核实 | 独立审核 SDK / 移植代码 / 数据 / 文档，不能将未知项包进新许可 |

## 7. 阻塞与最小确认项

1. 确认合法许可主体、贡献者 / 雇佣 / 外包 / 移植来源的权利，拿到需要的明确重新许可授权；根 MIT 版权行和 GitHub 作者名不足以替代该证据。
2. 确认首次适用的新版本 / tag / commit，以及精确文件白名单。当前已获核准的白名单为空，不可按 `bt_api_py/**` 或仓库根递归套用。
3. 确认商用边界草案：为他人管理资金、收费服务、企业内部使用是否均需另行许可；家庭成员 / 共同账户、借入资金、赞助、教学 / 研究、开源分发方式和免费公共服务的边界保持待审。个人仅使用自有资金获得交易收益本身不得被划为收费商用。
4. 确认未来贡献入站规则。新规则不对既有 MIT 贡献产生版权转让，不自动适用于独立子仓。

法律文本建议律师审阅；本记录不保证条款法律效力。本阶段没有增加仲裁、管辖地、专利许可 / 专利报复、赔偿义务或宽泛版权转让。

## 8. 验证

- 根 `LICENSE`、`pyproject.toml` 和贡献说明的当前 MIT 文字一致；`setup.py` 没有独立的冲突声明。
- `.gitmodules` 路径清单与父仓索引 19 个 mode `160000` 路径一致；子仓全部排除。
- `git diff --check -- LICENSE README.md CONTRIBUTING.md pyproject.toml setup.py MANIFEST.in .gitmodules`：通过（这些文件本阶段未修改）。
- 未运行业务单元 / 实盘 / 网络测试：没有业务代码修改，也未启动 SDK、读取运行凭证或连接交易所。
- 未运行文档生成 / strict MkDocs：目标仓活动文档未修改，生成器可能写用户工作树；正式采用阶段应在隔离副本遵循仓库文档门禁。
- 父仓完整 `git diff --check`：通过；它也检查了用户既有 5 个文档改动，不意味着这些改动是本次写入。
- `tomllib` 解析 `pyproject.toml`：通过，名称 / 版本 / MIT 字段符合记录；`ast.parse` 解析 `setup.py`：通过，没有执行安装脚本、构建或加载 SDK。
- 两份审阅草案逐一以 `git diff --no-index --check /dev/null <file>` 检查：没有空白错误诊断；该命令的退出码 1 表示相对空文件存在新增内容，不作为失败或已提交的证据。
- 最终父仓状态仍仅有上述 5 个用户文档改动；本次没有在父仓产生新文件或变更。
