# BT API 包发布清单

本文档跟踪所有交易所插件包的发布准备状态。

## 任务说明

每个包需要完成以下任务才能发布：

1. **代码审查** - 仔细阅读项目代码，确保质量
2. **README 更新** - 更新中英文版本的 readme.md
3. **在线文档更新** - 更新中英文版本的在线文档（用于 GitHub Pages 和 ReadTheDocs）
4. **CI/CD 完善** - 完善持续集成和部署配置
5. **标签和发布** - 打 tags 和创建 GitHub release
6. **PyPI 发布** - 发布到 PyPI

---

## 包状态清单

### 🚧 迭代 41：执行、风控与监控基础设施

以下三个仓库是 `bt_api_py` 的直接 Git 子模块，位于 `bt_api/` 下；它们不是交易所
插件，也不计入下方的交易所插件发布统计。父仓固定其 Gitlink，使用者应通过
`git submodule update --init --recursive` 获取相同版本。

| 包名 | 子模块路径 | 当前状态 | 接入约束 |
|------|------------|----------|----------|
| bt_api_execution | `bt_api/bt_api_execution` | 🚧 已登记；当前为初始仓 | 在实现公开执行契约、持久化恢复和 capability 协商前，不得作为可交易执行引擎宣传或启用。 |
| bt_api_risk | `bt_api/bt_api_risk` | 🚧 已登记；待完成硬风控门与 durable reservation 接入 | 只能由 SDK 最终写入边界调用；策略内存计数不能替代账户级风控。 |
| bt_api_monitor | `bt_api/bt_api_monitor` | 🚧 已登记；待完成 durable control-plane 接入 | 只消费事件并发出 freeze/drain/manual-intervention 控制信号，不能生成普通开仓。 |

这三个模块的实现、跨仓依赖和验收由 Backtrader 的
`docs/_internal/opts/requirements/迭代41-实盘执行风控监控与示例架构重构/` 管理。
子模块登记本身不改变任何实盘准入状态，也不自动把它们加入 `bt_api_py` 的运行时依赖。

### ✅ 已完成

| 包名 | 代码审查 | README | 在线文档 | CI/CD | 标签/Release | PyPI发布 |
|------|:--------:|:------:|:--------:|:-----:|:------------:|:--------:|
| bt_api_base | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_binance | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_okx | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bequant | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bigone | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bingx | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bitbank | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bitflyer | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bitget | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_coinbase | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bybit | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_ctp | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_dydx | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_gateio | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_htx | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_hyperliquid | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_ib_web | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_kraken | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_mexc | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_mt5 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 总计

- **已完成**: 20 / 20
- **待完成**: 0 / 20

---

## 使用说明

1. 选择一个待完成的包，将其状态从 ⏳ 改为 🔄（进行中）
2. 按顺序完成 6 个任务
3. 每个任务完成后，将 ⏳ 改为 ✅
4. 所有任务完成后，将该包移到"已完成"表格
