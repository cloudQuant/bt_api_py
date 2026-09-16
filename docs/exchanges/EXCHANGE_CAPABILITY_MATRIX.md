# 交易所能力矩阵

**最后更新：** 2026-09-16
**版本：** 0.15

本文档提供每个交易所的详细能力矩阵，帮助开发者和维护者快速了解各交易所的实现状态、测试覆盖率和文档完整性。

---

## 📊 能力矩阵格式说明

| 列名 | 说明 |
|------|------|
| **Exchange** | 交易所名称（三重下划线格式） |
| **REST** | REST API支持状态（✅完整 / ⚠️部分 / ❌未实现） |
| **WebSocket** | WebSocket支持状态（✅完整 / ⚠️部分 / ❌未实现） |
| **Tests** | 测试文件数 / 通过率 |
| **Docs** | 文档状态（✅完整 / ⚠️部分 / ❌缺失） |
| **Risk Notes** | 特殊注意事项或风险提示 |

---

## ✅ 生产就绪交易所 (Production Ready)

### 加密货币交易所 (CEX)

| Exchange | REST | WebSocket | Tests | Docs | Risk Notes |
|----------|------|-----------|-------|------|-----------|
| **BINANCE___SPOT** | ✅ | ✅ | 45+/100% | ✅ | 完整支持，代码质量高 |
| **BINANCE___SWAP** | ✅ | ✅ | 50+/95% | ✅ | 合约功能完整 |
| **BINANCE___OPTION** | ✅ | ✅ | 20+/90% | ✅ | 期权支持完整 |
| **HTX___SPOT** | ✅ | ✅ | 37+/95% | ✅ | 完整支持 |
| **HTX___SWAP** | ✅ | ✅ | 40+/90% | ✅ | 合约功能完整 |
| **BYBIT___SPOT** | ✅ | ❌ | 101/85% | ⚠️ | WebSocket 未实现（REST-only） |
| **BYBIT___SWAP** | ✅ | ❌ | 95+/80% | ⚠️ | WebSocket 未实现（REST-only） |
| **BITGET___SPOT** | ✅ | ⚠️ | 45+/85% | ⚠️ | WebSocket需完善 |
| **BITGET___SWAP** | ✅ | ⚠️ | 42+/80% | ⚠️ | WebSocket需完善 |
| **KRAKEN___SPOT** | ✅ | ⚠️ | 46+/85% | ⚠️ | WebSocket需完善 |
| **GATEIO___SPOT** | ✅ | ⚠️ | 56+/85% | ⚠️ | WebSocket需完善 |

### 传统金融

| Exchange | REST | WebSocket | Tests | Docs | Risk Notes |
|----------|------|-----------|-------|------|-----------|
| **CTP___FUTURE** | ✅ | ✅ | 15+/90% | ✅ | 完整支持，C++扩展，需要Python 3.11+ |
| **IB___STOCK** | ✅ | ✅ | 10+/90% | ✅ | 完整支持，需要ib-insync包 |

### 去中心化交易所 (DEX)

| Exchange | REST | WebSocket | Tests | Docs | Risk Notes |
|----------|------|-----------|-------|------|-----------|
| **HYPERLIQUID___PERP** | ✅ | ✅ | 40+/90% | ✅ | 唯一完整支持的DEX |

---

## 🟡 Beta 测试版 (可用但需验证)

### 加密货币交易所 (CEX)

| Exchange | REST | WebSocket | Tests | Docs | Risk Notes |
|----------|------|-----------|-------|------|-----------|
| **OKX___SPOT** | ✅ | ⚠️ | 部分/60% | ⚠️ | Mock路径问题导致测试失败 |
| **OKX___SWAP** | ✅ | ⚠️ | 部分/55% | ⚠️ | WebSocket需完善 |
| **OKX___OPTION** | ✅ | ⚠️ | 部分/50% | ⚠️ | 期权支持不完整 |
| **MEXC___SPOT** | ✅ | ⚠️ | 11/42通过 | ⚠️ | 测试稳定性问题 |
| **MEXC___SWAP** | ✅ | ⚠️ | 部分/50% | ⚠️ | WebSocket需完善 |
| **COINBASE___SPOT** | ✅ | ⚠️ | 19/45通过 | ⚠️ | Import路径问题 |
| **COINBASE___SWAP** | ✅ | ❌ | 部分/60% | ⚠️ | WebSocket未实现 |

### 去中心化交易所 (DEX)

| Exchange | REST | WebSocket | Tests | Docs | Risk Notes |
|----------|------|-----------|-------|------|-----------|
| **UNISWAP___V3** | ✅ GraphQL | ❌ | 基础/30% | ⚠️ | 无传统订单簿，链上交易，仅AMM支持 |
| **PANCAKESWAP___V3** | ✅ | ❌ | ❌ | ⚠️ | 容器丰富，但缺少测试文件 |
| **SUSHISWAP___V3** | ✅ | ❌ | ❌ | ⚠️ | 多链支持，但缺少测试文件 |

---

## 🚧 Alpha 开发中 (框架完成)

### 去中心化交易所 (DEX)

| Exchange | REST | WebSocket | Tests | Docs | Risk Notes |
|----------|------|-----------|-------|------|-----------|
| **DYDX___PERP** | ✅ 基础 | ❌ | ❌ | ⚠️ | 缺少WebSocket、完整测试 |
| **CURVE___V2** | ⚠️ 基础 | ❌ | ❌ | ⚠️ | 稳定币交换，需要完整测试 |
| **BALANCER___V2** | ⚠️ 基础 | ❌ | ❌ | ⚠️ | AMM，需要完整测试 |
| **RAYDIUM___SOL** | ⚠️ 基础 | ❌ | ❌ | ⚠️ | Solana AMM，需要完整测试 |
| **COWSWAP___V1** | ⚠️ 基础 | ❌ | ❌ | ⚠️ | 聚合器，需要完整测试 |

---

## 📋 框架就绪 (已注册)

以下交易所已注册但实现非常基础，仅包含 `__init__.py`，**不可用于生产环境**。

### 加密货币交易所 (部分列表)

4E, AAX, AscendEX, Bequant, BigONE, Bitcoin.com, BTSE, Coinsbit, CoinList, CoinMetro, CoinTiger, CoinZest, Currency.com, DigiFinex, FTX (已关闭), FTX US (已关闭), Gate.io, Hcoin, Huobi Korea, IDAX, Indodax, ItBit, Koinim, Koinex, Kuna, LakeBTC, LBank, Liquid, Livecoin, Lykke, Nexchange, Nominex, NovaDAX, OceanEx, OKCoin, ProBit, QBTC, Quedex, RightBTC, SouthXchange, Stellarport, StormGain, SushiSwap, The Ocean, Tidex, TimeX, Tokocrypto, Tokenize, Tokens, TradeSatoshi, VCC, WhiteBIT, XinFin, ZB, Zecoex

---

## 📈 统计总览

> 计数基于本矩阵明细行（生产就绪 / Beta / Alpha）与「框架就绪 (已注册)」列表条目。

### 按状态分类

| 状态 | 数量 |
|------|------|
| ✅ 生产就绪 | 14 |
| 🟡 Beta | 10 |
| 🚧 Alpha | 5 |
| 📋 框架就绪 | 58 |

### 按类型分类

| 类型 | 生产就绪 | Beta | Alpha | 框架就绪 |
|------|--------|------|------|---------|
| 加密货币 (CEX) | 11 | 7 | 0 | 58 |
| 传统金融 | 2 | 0 | 0 | 0 |
| 去中心化 (DEX) | 1 | 3 | 5 | 0 |

### 能力覆盖

| 能力 | 完整支持 | 部分支持 | 未实现 | N/A |
|------|--------|--------|--------|-----|
| REST API | 24 | 5 | 0 | 58 |
| WebSocket | 8 | 10 | 11 | 58 |
| 完整测试 | 14 | 10 | 5 | 58 |
| 完整文档 | 8 | 21 | 0 | 58 |

---

## 🔍 如何使用本矩阵

### 对于新用户

1. **优先选择生产就绪交易所** - ✅ 标记的交易所已完整测试
2. **查看测试状态** - 确保测试通过率高（>85%）
3. **检查风险提示** - 注意特殊注意事项
4. **阅读文档** - 查看 `docs/exchanges/{exchange}/` 详细文档

### 对于开发者

1. **贡献测试** - 提高Beta和Alpha交易所的测试覆盖率
2. **完善WebSocket** - 为部分支持的交易所添加WebSocket支持
3. **更新文档** - 为缺失文档的交易所添加文档
4. **修复问题** - 参考风险提示修复已知问题

### 对于维护者

1. **监控测试状态** - 定期运行测试确保稳定性
2. **更新矩阵** - 添加新交易所或更新状态
3. **审查风险** - 定期审查风险提示是否仍然适用
4. **优先级管理** - 根据用户反馈调整开发优先级

---

## 📝 维护说明

### 更新频率

- **生产就绪交易所**：每季度审核
- **Beta交易所**：每月审核
- **Alpha/框架就绪**：每半年审核

### 更新触发条件

1. 新增交易所
2. 测试覆盖率变化 >10%
3. 功能完整性变更
4. 用户报告重大问题
5. 文档状态变更

### 审核检查清单

- [ ] 测试文件存在且可运行
- [ ] 测试通过率 >80%
- [ ] 文档文件存在且内容完整
- [ ] REST API功能完整
- [ ] WebSocket功能完整（如适用）
- [ ] 风险提示准确
- [ ] 统计数据准确

---

## 📞 获取帮助

- **文档**: https://cloudquant.github.io/bt_api_py/
- **GitHub**: https://github.com/cloudQuant/bt_api_py
- **Issues**: https://github.com/cloudQuant/bt_api_py/issues
- **Email**: yunjinqi@gmail.com

---

**注意：** 本文档会定期更新。如果您发现信息过时，请在GitHub上提交Issue或Pull Request。

**最后验证：** 2026-03-08 by BMAD Team
