<!-- BEGIN GENERATED:EXCHANGE_SUPPORT_STATUS -->
# 交易所实现状态

**最后更新：** 2026-04-06  
**版本：** 0.15

本文档由 `docs/data/exchange_support_matrix.json` 生成，用于统一 README、文档首页和状态页的交易所支持口径。

## 状态口径

- **验证命令**：`bash scripts/run_exchange_tests.sh <name>`
- **状态策略**：Conservative: only mark an exchange as fully supported when its REST and WebSocket support are both documented and the current repository keeps corresponding tests in place.

## ✅ 已完整支持

| 交易所 | 类型 | REST | WebSocket | 测试状态 | 备注 |
| -------- | -------- | -------- | -------- | -------- | -------- |
| **Binance** | CEX | ✅ | ✅ | ✅ 通过 | 现货、合约、杠杆、期权、算法交易、网格、挖矿、质押、钱包、子账户、VIP借币 |
| **HTX (Huobi)** | CEX | ✅ | ✅ | ✅ 通过 | 现货、杠杆、U本位永续、币本位永续、期权 |
| **CTP (中国期货)** | 传统金融 | ✅ | ✅ | ✅ 通过 | 中国期货市场（上期所、大商所、郑商所、中金所） |
| **Interactive Brokers** | 传统金融 | ✅ | ✅ | ✅ 通过 | 美股、期货（通过 Web API） |

## 🔧 已实现 API（仍需继续验证或补齐能力）

| 交易所 | 类型 | 当前状态 | 测试状态 | 备注 |
| -------- | -------- | -------- | -------- | -------- |
| **OKX** | CEX | REST 已实现，WebSocket 部分实现 | ⚠️ 部分失败 | mock 目标路径问题（httpx），主体逻辑正确 |
| **Bybit** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 37 通过 | 建议补 WebSocket 覆盖后再提升状态 |
| **Bitget** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 45 通过 | 建议补 WebSocket 覆盖后再提升状态 |
| **Kraken** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 46 通过 | 建议补 WebSocket 覆盖后再提升状态 |
| **Gate.io** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 56 通过 | 建议补 WebSocket 覆盖后再提升状态 |
| **Upbit** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 101 通过 (4 skip) | 建议补 WebSocket 覆盖后再提升状态 |
| **Crypto.com** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 97 通过 | 建议补 WebSocket 覆盖后再提升状态 |
| **HitBTC** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 103 通过 (5 skip) | 建议补 WebSocket 覆盖后再提升状态 |
| **Phemex** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 65 通过 (5 skip) | 建议补 WebSocket 覆盖后再提升状态 |
| **Gemini** | CEX | REST 已实现，WebSocket 待继续补齐 | ✅ 20 通过 | 建议补 WebSocket 覆盖后再提升状态 |
| **KuCoin** | CEX | REST 已实现，仍需补稳定性验证 | ⚠️ 16 失败 / 47 通过 | mock 目标路径问题 |
| **MEXC** | CEX | REST 已实现，仍需补稳定性验证 | ⚠️ 11 失败 / 42 通过 | mock 目标路径问题 |
| **Bitfinex** | CEX | REST 已实现，仍需补稳定性验证 | ⚠️ 13 失败 / 43 通过 | mock 目标路径问题 |
| **Coinbase** | CEX | REST 已实现，仍需补稳定性验证 | ⚠️ 19 失败 / 45 通过 | 部分 import 路径变更 |
| **Hyperliquid** | DEX | 实现存在，但当前仓库测试资产不足以提升到完整支持 | ⚠️ 待补验证 | 当前仓库缺少可执行的 Hyperliquid 测试文件 |
| **dYdX** | DEX | 实现存在，但当前仓库测试资产不足以提升到完整支持 | ⚠️ 待补验证 | 当前仓库缺少可执行的 dYdX 测试文件 |
| **BYDFi** | CEX | REST 已实现，仍需补稳定性验证 | ⚠️ 1 失败 / 17 通过 | JSON 解析 bug |

## 📋 已注册（基础框架就绪）

- 数量：40+
- 说明：其余交易所已完成注册或基础框架接入，但还需要继续补实现、测试或文档后，再提升对外状态。

## 📈 统计总览

| 状态 | 数量 | 说明 |
| -------- | -------- | -------- |
| ✅ 完整支持 | 4 | REST、WebSocket 和测试资产均已具备 |
| 🔧 已实现 API | 17 | 已有实现，但仍需继续验证、补测试或补 WebSocket 能力 |
| 📋 已注册 | 40+ | 已注册或基础框架接入，等待继续完善 |
| 总计 | 73+ | 当前仓库对外声明支持的交易所总量 |

## 维护说明

1. 修改 `docs/data/exchange_support_matrix.json`。
2. 运行 `python scripts/generate_exchange_support_docs.py`。
3. 提交生成后的 README 和文档页。
<!-- END GENERATED:EXCHANGE_SUPPORT_STATUS -->
