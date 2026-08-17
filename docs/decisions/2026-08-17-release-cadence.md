# 决策：bt_api 生态版本节奏统一（B-24）

**日期**：2026-08-17
**状态**：已采纳

## 背景

61 个 `bt_api_*` 适配器仓库的版本号无统一节奏：`bt_api_base` 0.15.3、`bt_api_binance` 2.0.1、其余 45 仓停留在 0.1.x。发版、依赖升级（`core_requires`）和母仓库 pin 更新各自为政。

## 决策

**发布列车：跟随 `bt_api_base` 发版。**

理由：

1. 所有插件仓都依赖 `bt_api_base`（`core_requires=">=0.15,<1.0"`），以 base 为锚点最自然。
2. 每个发版周期：base 先发版 → 各插件仓 bump **patch** 版本并打 tag → 母仓库更新 pin。
3. 避免"每个季度一次"的固定节奏（太僵化，base 的 bugfix 需要及时传播）；也避免"各仓自由发版"（太散乱）。

## 流程

1. `bt_api_base` 发版（bump patch/minor，打 tag `vX.Y.Z`）。
2. 运行 `scripts/bump_all_submodules.py --dry-run` 生成 bump 清单。
3. 逐仓 bump patch 版本 + commit + tag（脚本 `--apply` 模式）。
4. 母仓库更新全部 pin，提交。

## 版本号约定

- 插件仓 patch 版本跟随 base 发版节奏 +1。
- `core_requires` 保持 `>=0.15,<1.0`，base 大版本变更时统一升级。

## bump 脚本

`scripts/bump_all_submodules.py`：读 `.gitmodules` → 逐仓 bump patch 版本并 commit/tag；`--dry-run` 只打印清单（验收用）。
