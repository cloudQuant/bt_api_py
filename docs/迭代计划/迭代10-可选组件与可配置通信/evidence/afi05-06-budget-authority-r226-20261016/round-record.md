# R226：Risk 静态组合预算 authority 与 SDK Host 接线

日期：2026-10-16。Risk `dev` 子包提交 `ede55dec5e8e7ad1c5376969eb54e9933ebf9e85`（父提交 `647840a75b2071fcb5b4395f554190afe2fae082`），范围严格为两条路径：`src/bt_api_risk/service_component.py` 与 `tests/test_policy_catalog_budget_authority.py`。Risk postcommit receipt 已逐字节归档在 raw ZIP 的 `delivery/risk-postcommit.json`。SDK parent 当前 R226 前 HEAD 为 `0ffd260dd782143e02ffde7332f9bc59a67ad948`；Root 将以一个父仓 scoped commit 收入两处 SDK 接线、Risk gitlink、本目录三件证据和 Root 进度记录。父提交号由 Root 在提交后记录；本文件创建时不虚构尚不存在的 commit SHA。

## Root 验收结果

最终 R7 固定源组合的唯一 Host 节点通过：1 passed / 0 failed / 0 error / 0 skipped，pytest exit 0，source count 569，四个仓库 HEAD guard 前后稳定。输出目录为 `D:\temp\iteration10-afi-budget-authority-host-focused-root-20261016-r7`，stdout SHA-256 `649a12e0965dfad135be2898144608f65342deef7632d774ee8a373e7bda38a1`，stderr 为空；stdout 保留一次 Windows Proactor/ZMQ RuntimeWarning。R7 spec 是 `.codex-tmp/iteration10-afi05-06-budget-authority-host-root-spec-r7-r223-policy-20261016.json`，其固定 export/完整 root 输出均在 raw ZIP。节点经真实注册的 SDK `service.risk_policy` wire/Host path，测试认证 principal、按 scope 的最小 grants、maker/checker/无权 principal、逐成员确认只能由 Risk owner 从注入的 `ConfirmedPolicyStore` 回读精确 receipt，以及 `authority_ready`；始终断言 `consumer_effective` 为 false。

R7 runtime 输入分层记录为：SDK checkout mutation guard `ac12f7c3ba3d642ee9e105c442c6987c78bae5d9`，Risk guard `647840a75b2071fcb5b4395f554190afe2fae082`，Base guard `b4aabd1e17b9c7252fa7b4d266932087d99b5517`，ZMQ guard `a397fdb460d2deb22e67ceb4963992ef63dbbb0e`；实际固定 runtime 由 R7 `export-manifest.json` 的 719a SDK、71be Risk、b4 Base、a397 ZMQ accepted blobs，加 R221/R223 overlays，再加本轮两条仅限 policy catalog 的 SDK patch 组成。该组合通过，不等于普通 AFI 包启动或生产 endpoint 接入。

R5 是较早固定源码组合上的单 Host 通过，1/1、exit 0、557 pins，保留在 ZIP；它不替代针对 R223+R221+R226 最终组合的 R7。R3 Root run 中五个 authority unit 节点通过，唯一 Host 节点失败（1 failed / 5 passed，exit 1，543 pins）：Host 只返回 `REQUIRED_REGISTRATION_FAILED`，没有实际 phase/traceback；源码检查发现 member scope resolver 的 key 与所给 scope ref 不一致，但不能把该来源发现当作已恢复的运行时首因。R4 单 Host 节点在成功拿到 draft RPC 回复后失败，因为测试只接收 `dict`，而 Runtime 的冻结 reply 是 `mappingproxy`。R3、R4 原结果保留为失败历史，R5/R7 只运行各自范围，没有重跑 R3 五个 unit 节点。

继承的 R219 compiler/Catalog Root 离线验收为16 passed、0 skip；R226 没有重跑。R219 已提交 AFI compiler/test 和原始固定源记录，必要的源/验收摘要也随 raw ZIP 保存。

## 本轮代码范围与持久化边界

Risk `PolicyCatalogStore` 继续是单一 SQLite 权威：pool/generation/member 状态及 receipt 由 owner 持久保存；预算单位精确、总额/未分配/成员守恒，写入采用 `BEGIN IMMEDIATE`，同一 pool 的容量与重叠成员 claims 串行检查。maker/checker、受信审批 principal、成员 owner scope、逐账户发布和 exact confirmed receipt 均在 owner 内验证。已发布的部分拒绝保留整个 claim 为 hold；UNKNOWN、确认缺失/不匹配不释放预算；只有未发布提案可以安全撤回。AFI 仍只是编译草案和之后的 HTTP/UI 适配层，不创建第二份额度账本。

本轮仅接受到本地合成 SDK Host 与 SQLite 目录边界。没有普通 AFI 应用启动、AFI UI/HTTP 对此 Host 的接入，也没有 Risk admission resolver 消费 `authority_ready`；因此 `consumer_effective=false`、没有任何 AFI05/06 formal PASS，迭代计数仍为39/68。没有账户/provider、凭据或真实部署操作。

下一最小产品接入需在 AFI 当前共享服务文件锁释放后，把普通认证 HTTP/用户界面接到已注册 SDK `service.risk_policy`，由 session identity 构造真实 principal/scope，展示草稿、审批和逐账户 `published/confirmed/partial_hold/unknown_hold`。随后单独接入 Risk admission consumer，只有它实际读取精确已确认版本时才可投影 `consumer_effective=true`；在那之前不得宣称预算已对订单生效。完整 AFI05/06 正式验收仍待完成。

Root 的 R7 spec、所有 R3/R4/R5/R7 原始输出及其固定 R7 源码闭包逐字节保存在 `raw-evidence.zip`。外层 manifest 列明每个归档成员的字节数/SHA-256、源码 pins、各次 Root run 与限制。
