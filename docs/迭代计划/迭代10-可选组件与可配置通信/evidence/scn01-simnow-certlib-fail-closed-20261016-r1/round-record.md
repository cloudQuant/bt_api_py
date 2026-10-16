# 第234轮 SCN01 正常工厂拒绝路径 — SDK delivery packet

本包供 Root 在 Backtrader 两路径提交完成后应用。Root 原始输出以 raw-evidence.zip 封存11个文件，manifest.json 固定每个成员及四个source pins。SDK 交付证据范围严格为 raw-evidence.zip、manifest.json、round-record.md 三路径；bt-scoped-commit-spec.json 是Root helper配置，不属于SDK提交范围。原先准备的独立Root spec与三份散装副本保留为未提交辅助材料，不纳入该三路径范围。

Root固定QA：4 passed / 0 failed，pytest exit 0，用时0.40s；一条既有配置warning（禁用无关asyncio插件时未知配置项）。BT HEAD在调用前后均为 438b127fcff2c0619b13835248ee3ef210cc0728，四个source pins均不变。候选BT提交精确两路径：examples/live/000_live_certification/simnow_penetration/certlib/__init__.py 与 tests/unit/test_scn01_simnow_certlib_fail_closed.py；详见 bt-scoped-commit-spec.json。该JSON按 .codex-tmp/iteration10-scoped-source-delivery.py config字段组织，但 independent_static_review=PENDING_ROOT_REVIEW，暂不可执行通用提交helper。

证据级别 SOURCE_FAKE_SIMNOW_NORMAL_FACTORY_FAIL_CLOSED_ONLY。SCN01正式case未通过/计数未增加；正式总数保持39/68。没有provider、账户、CTP SDK session/login/query/order或双机验收。未改SDK进度、SDK index或任一repo HEAD。当前开发与验收进度 opening 仍写“截止第232轮”，尾部已有完整第233轮；Root 应在应用本段后将概况 cutoff 校正到234，正式计数保持39/68。

## 拟追加至 开发与验收进度.md 的第234轮小节

## 第234轮：SCN01 SimNow normal factory fail-closed 源码验收（2026-10-16）

Backtrader dev 在 438b127fcff2c0619b13835248ee3ef210cc0728 上的候选仅改动 certlib/__init__.py 与 test_scn01_simnow_certlib_fail_closed.py 两路径：不支持的 gateway transport 及缺少 execution/risk/monitor backend 会在 CTP client 导入和 SDK root 解析前明确拒绝，supported direct factory 路径保持不变。Root 固定来源 QA 为 4 passed / 0 failed、exit 0、0.40s；四个来源 pin 与 BT HEAD 前后不变。仅有禁用无关 asyncio 插件后出现的既有 pytest 配置 warning。

本轮证据级别为 SOURCE_FAKE_SIMNOW_NORMAL_FACTORY_FAIL_CLOSED_ONLY，不新增 SCN01 正式 PASS，正式计数保持39/68；没有 provider、账户、CTP SDK session/login/query/order 或双机场景验收。原始 Root 11 项输出、manifest 与本记录见 evidence/scn01-simnow-certlib-fail-closed-20261016-r1/。SDK 交付候选为三份证据和本进度文件；SDK parent 与提交号在 Root 完成 BT scoped commit 后另行核实。