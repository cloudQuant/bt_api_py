# 子模块升级协议（Submodule Bump）

> 状态：生效中（迭代03 M5，2026-08-23）。pilot 仓见决策门 D6：
> `bt_api_base`、`bt_api_binance`、`bt_api_okx`。扩大范围需新决策。

## 原则

1. **插件实现改插件仓**：交易所适配器（feed 行为、签名、WebSocket 解析）的 PR
   一律提到对应 `bt_api/bt_api_<exchange>` 插件仓，走该仓自身的 CI 与评审。
2. **主仓只收 SHA bump**：主仓不直接修改子模块内容；gitlink 变更必须以独立
   bump PR 进入 `dev`，由 `.github/workflows/submodule-tests.yml` 的
   `Submodule Gate / Summary` 机械校验。
3. **双端证据**：每个 bump 必须同时留下"插件仓 PR 已合并"与"主仓 bump PR"两条
   可追溯记录；缺任何一端即不完整。
4. **hotfix 例外**：发布阻断场景允许 bump PR 直接进 `master`，但仍需全套证据，
   且 1 个工作日内前移 `dev`。

## Bump PR 模板字段（必填）

| 字段 | 说明 |
|---|---|
| 插件仓 PR 链接 | 已合并的插件侧 PR URL |
| old_sha → new_sha | gitlink 新旧 40 位 SHA；`Submodule Gate` 从 diff 中机械提取并复核 |
| 兼容性说明 | 对公共接口/容器字段/行为语义的影响 |
| 回滚方式 | `git update-index --cacheinfo 160000,<old_sha>,<path>` 或 revert bump commit |

## 校验链路

```text
plugin repo PR merged
        │
        ▼
main repo bump PR (dev)          Submodule Gate / Summary
  ├─ .gitmodules/gitlink diff ──► 检测 gitlink 变更数量
  ├─ 无变更 ────────────────────► not-applicable，成功通过
  └─ 有变更 ────────────────────► recursive checkout +
                                  bt_api/install_and_test_all.py 全量校验
                                  + report artifact 上传
```

## Pilot 协议（D6 三仓）

| 责任方 | 义务 |
|---|---|
| 插件维护者 | 保持插件仓 CI 绿色；合并后主动开主仓 issue 申请 bump（贴新旧 SHA） |
| 主仓 triage | 确认标签 `sha-bump-required`；核对插件仓 PR 合并状态后才接受 bump PR |
| 发布负责人 | promotion 进 master 前，确认 pilot 三仓无未处理 bump 积压（周指标 `submodule_sha_lag_count`） |

其余 57 个子模块暂不套用本协议；其 gitlink 升级仍按普通依赖变更处理，
但同样受 `Submodule Gate` 机械校验约束。
