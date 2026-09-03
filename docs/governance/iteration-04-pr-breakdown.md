# 迭代 04 社区 PR 拆分与落地顺序

> 状态：草案。当前没有创建远端 PR，也没有 push 本地提交。

以下拆分将运行时、交付物、CI、文档和候选证据隔离，便于各自审查与回滚。每个 PR 的目标分支应为 `dev`；在远端治理门禁配置完成前，它们只能作为普通候选，不能被称为受保护合并。

| PR | 建议提交 | 目的 | 非目标 | 关键验证 | 回滚 |
| --- | --- | --- | --- | --- | --- |
| A | `cb51824b` | 记录新鲜基线、外部治理事实与不越权原则。 | 不修改 GitHub 设置，也不宣称治理已完成。 | 基线命令、GitHub 只读复核。 | `git revert cb51824b`。 |
| B | `5511fad7`、`e1e40bad`、`18d787bc` | 让 wheel/sdist 携带 bundle 配置，并在已安装 wheel 与发布工作流中验证 doctor。 | 不发布 PyPI/TestPyPI。 | build、`verify_wheel_contract.py`、`tests/test_package_resources.py`、YAML 解析。 | 逐个 revert；没有 wheel 验证不得发布。 |
| C | `aed717c0`、`568b7f37`、`6f29081a`、`fb1a3aca` | 统一 direct/ZMQ 操作边界，补齐缓存新鲜度、typed 命令和命令对账，并将门面回归归入契约套件。 | 不把 ZMQ 故障静默回退为 direct，也不删除 legacy API。 | 离线全套件、`tests/bt_api_contract tests/forwarding` 覆盖率、能力/错误路径测试。 | revert 本 PR；运行时保持 fail-closed。 |
| D | `f8c799d0`、`d88d0342`、`20c8ba6b` | 用每包临时环境、wheel 构建、依赖安装、socket 禁用和阶段化日志取代共享环境子模块验证。 | 不把 all-profile diagnostic 升格为 required check。 | `tests/scripts/test_submodule_validation.py`、core-reference 工件。 | revert 本 PR；保留可读诊断，不恢复旧共享环境脚本。 |
| E | `b9824e18`、`a93510f2` | 将 README、活跃文档和支持矩阵降到当前可复现事实。 | 不以历史统计宣称“73+ fully supported”。 | 生成器检查、文档契约、`mkdocs build --strict`。 | revert 本 PR；证据缺失时应降级为 experimental/unverified。 |
| F | 候选收据、报告和交接文档提交 | 固化本地验证、严格阻塞项、远端待办和拆分顺序。 | 不创建 PR、不修改远端治理或发布设置。 | 收据 SHA 对照、`git diff --check`、文档构建。 | `git revert <F commit>`；不得篡改先前收据。 |

## 子模块落地前置条件

父仓库当前本地验证引用 `123b03a`、`e08ef32`、`91027d1`、`5686d3f` 四个嵌套仓库提交。为避免 PR 混入不可达 gitlink：

1. 分别在 bt_api_base、bt_api_binance、bt_api_okx、bt_api_ctp 上游仓库创建并审查对应的小 PR。
2. 先 push 并确认每个 SHA 可由公共远端 clone/fetch 得到；需要发布的包先完成版本规范化与新版本发布。
3. 再单独创建“submodule gitlink bump”PR，并以 `Submodule Gate / Core Reference` 为 required check。
4. 父仓库运行时/CI PR 不应同时夹带未审查的子模块 gitlink 或 generated 文件。

## 通用 PR 模板

每个 PR 描述必须包含：目的、非目标、精确验证命令、回滚提交/策略、兼容性和对外风险、子模块或发布前置条件。除 F 外，候选收据不应作为对功能 PR 的替代测试证据；F 只能在 A–E 已汇入同一远端基线后创建。

迭代 03 的 `7d5db275` 是一个独立的治理修正候选，应作为普通 `dev` PR 处理并重新取证，不能与上述 PR 混合。
