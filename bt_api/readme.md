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
| bt_api_bitfinex | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bitflyer | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bitget | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bithumb | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bitinka | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bitmart | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bitrue | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bitso | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bitstamp | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bitunix | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_bitvavo | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_btbns | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| bt_api_btc_markets | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

### ⏳ 待完成
| bt_api_btcturk | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_buda | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_bybit | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_bydfi | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_coinbase | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_coincheck | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_coindcx | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_coinex | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_coinone | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_coinspot | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_coinswitch | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_cryptocom | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_ctp | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_dydx | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_exmo | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_foxbit | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_gateio | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_gemini | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_giottus | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_gmx | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_hitbtc | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_htx | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_hyperliquid | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_ib_web | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_independent_reserve | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_korbit | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_kraken | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_kucoin | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_latoken | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_localbitcoins | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_luno | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_mercado_bitcoin | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_mexc | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_mt5 | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_phemex | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_poloniex | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_ripio | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_satoshitango | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_swyftx | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_upbit | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_valr | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_wazirx | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_yobit | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_zaif | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |
| bt_api_zebpay | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ | ⏳ |

---

## 总计

- **已完成**: 18 / 65
- **待完成**: 47 / 65

---

## 使用说明

1. 选择一个待完成的包，将其状态从 ⏳ 改为 🔄（进行中）
2. 按顺序完成 6 个任务
3. 每个任务完成后，将 ⏳ 改为 ✅
4. 所有任务完成后，将该包移到"已完成"表格
