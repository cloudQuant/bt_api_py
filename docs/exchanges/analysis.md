# CoinMarketCap 衍生品交易所分析

> 数据来源: https://coinmarketcap.com/rankings/exchanges/derivatives/
> 分析日期: 2026-03-04
> 总计交易所: 113 家

## 已实现的交易所 (23 家)

以下交易所已在 bt_api_py 中实现，并出现在 CMC 衍生品排行榜中：

| CMC 排名 | 交易所 | 目录名 |
|----------|--------|--------|
| 1 | Binance | `live_binance` |
| 2 | OKX | `live_okx` |
| 3 | Gate.io | `live_gateio` |
| 4 | Bybit | `live_bybit` |
| 5 | KuCoin | `live_kucoin` |
| 6 | Bitget | `live_bitget` |
| 7 | BingX | `live_bingx` |
| 9 | HTX | `live_htx` |
| 10 | Kraken | `live_kraken` |
| 12 | MEXC | `live_mexc` |
| 13 | BitMart | `live_bitmart` |
| 14 | Bitfinex | `live_bitfinex` |
| 15 | Gemini | `live_gemini` |
| 16 | Crypto.com | `live_cryptocom` |
| 28 | CoinEx | `live_coinex` |
| 30 | Bitunix | `live_bitunix` |
| 32 | Phemex | `live_phemex` |
| 41 | Poloniex | `live_poloniex` |
| 45 | Bitrue | `live_bitrue` |
| 57 | HitBTC | `live_hitbtc` |
| 67 | BYDFi | `live_bydfi` |
| 68 | Coinbase Intl | `live_coinbase` |
| 94 | bitFlyer | `live_bitflyer` |

## 未实现的交易所 (90 家)

### Tier-1: 高优先级 (排名靠前，API 文档完善，有独立 api.md)

| CMC 排名 | 交易所 | 官网 | API 文档 | 说明 |
|----------|--------|------|---------|------|
| 8 | XT.COM | https://www.xt.com | https://doc.xt.com | 全品种交易所，支持现货+合约 |
| 11 | Deribit | https://www.deribit.com | https://docs.deribit.com | 专业期权+期货交易所 |
| 17 | LBank | https://www.lbank.com | https://www.lbank.com/docs/ | 现货+合约，V2 API |
| 18 | BTCC | https://www.btcc.com | 无公开 REST API 文档 | 老牌合约交易所，最高500x杠杆 |
| 19 | Toobit | https://www.toobit.com | https://toobit-docs.github.io/apidocs/ | 现货+USDT永续 |
| 20 | Pionex | https://www.pionex.com | https://pionex-doc.gitbook.io/apidocs/ | 量化交易机器人平台 |
| 21 | CoinW | https://www.coinw.com | https://www.coinw.com/api-doc/ | 现货+合约 |
| 23 | DigiFinex | https://www.digifinex.com | https://docs.digifinex.com | 现货+合约 V3 API |
| 25 | Deepcoin | https://www.deepcoin.com | https://www.deepcoin.com/docs | 合约交易所 |
| 27 | Zoomex | https://www.zoomex.com | Bybit V5 兼容 API | 衍生品交易所(类Bybit) |
| 37 | WhiteBIT | https://whitebit.com | https://docs.whitebit.com | 欧洲交易所，V4 API |
| 42 | HashKey Global | https://global.hashkey.com | https://hashkeyglobal-apidoc.readme.io | 持牌合规交易所 |
| 43 | AscendEX | https://ascendex.com | https://ascendex.github.io/ascendex-pro-api/ | 现货+期货 |
| 46 | Backpack Exchange | https://backpack.exchange | https://docs.backpack.exchange | Solana 生态交易所 |
| 47 | BitMEX | https://www.bitmex.com | https://www.bitmex.com/app/restAPI | 老牌衍生品交易所 |
| 48 | BTSE | https://www.btse.com | https://btsecom.github.io/docs/futures/en/ | 多资产交易所 |
| 50 | BloFin | https://blofin.com | https://docs.blofin.com | 合约交易所 |
| 53 | WOO X | https://woo.org | https://docs.woox.io | 低手续费交易所 |
| 58 | Delta Exchange | https://www.delta.exchange | https://docs.delta.exchange | 期权+期货 |
| 61 | Flipster | https://flipster.io | https://api-docs.flipster.io | 永续合约交易所 |
| 110 | PrimeXBT | https://primexbt.com | 有限 API 支持 | 多资产交易平台 |
| 111 | Coincall | https://www.coincall.com | https://docs.coincall.com | 期权+期货 |

### Tier-2: 中优先级 (有一定规模，有独立 api.md)

| CMC 排名 | 交易所 | 官网 | 说明 |
|----------|--------|------|------|
| 22 | WEEX | https://www.weex.com | 合约交易所 |
| 24 | KCEX | https://www.kcex.com | 合约交易所 |
| 26 | Biconomy.com | https://www.biconomy.com | 合约交易所(非DeFi Biconomy) |
| 29 | OrangeX | https://www.orangex.com | 合约交易所 |
| 31 | Tapbit | https://www.tapbit.com | 合约交易所 |
| 33 | Ourbit | https://www.ourbit.com | 合约交易所 |
| 34 | FameEX | https://www.fameex.com | 现货+合约 |
| 44 | Hotcoin | https://www.hotcoin.com | 现货+合约 |
| 49 | VOOX Exchange | https://www.voox.com | 合约交易所 |
| 59 | BITmarkets | https://www.bitmarkets.com | 合约交易所 |

### Tier-3: 低优先级 (规模较小或缺乏公开 API 文档)

| CMC 排名 | 交易所 | 说明 |
|----------|--------|------|
| 35 | CoinUp.io | 小型合约交易所 |
| 36 | BVOX | 小型合约交易所 |
| 38 | Hibt | 小型交易所 |
| 39 | YUBIT | 小型交易所 |
| 40 | UZX | 小型交易所 |
| 51 | BitradeX | 小型交易所 |
| 52 | Koinbay | 小型交易所 |
| 54 | Batonex | 小型交易所 |
| 55 | TruBit Pro | 拉美交易所 |
| 56 | AllinX | 小型交易所 |
| 60 | AstralX | 小型交易所 |
| 62 | Websea | 小型交易所 |
| 63 | MGBX | 小型交易所 |
| 64 | CoinChief | 小型交易所 |
| 65 | Coinflare | 小型交易所 |
| 66 | ONUS Pro | 越南交易所 |
| 69 | BitxEX | 小型交易所 |
| 70 | Bitcastle | 小型交易所 |
| 71 | Bitop | 小型交易所 |
| 72 | Ekbit | 小型交易所 |
| 73 | 4E | 小型交易所 |
| 74 | Globe Derivative | 小型交易所 |
| 75 | Mandala Exchange | 小型交易所 |
| 76 | Gleec BTC | 小型交易所 |
| 77 | Echobit | 小型交易所 |
| 78 | BiKing | 小型交易所 |
| 79 | Ju.com | 小型交易所 |
| 80 | NovaEx | 小型交易所 |
| 81 | BitTap | 小型交易所 |
| 82 | Cofinex | 小型交易所 |
| 83 | BitbabyExchange | 小型交易所 |
| 84 | SunX | 小型交易所 |
| 85 | IBIT Global | 小型交易所 |
| 86 | KTX | 小型交易所 |
| 87 | BlockFin | 小型交易所 |
| 88 | Tebbit | 小型交易所 |
| 89 | Coinlocally | 小型交易所 |
| 90 | CRMClick | 小型交易所 |
| 91 | YEX | 小型交易所 |
| 92 | EasiCoin | 小型交易所 |
| 93 | Aivora Exchange | 小型交易所 |
| 95 | Millionero | 小型交易所 |
| 96 | CZR Exchange | 小型交易所 |
| 97 | B2Z Exchange | 小型交易所 |
| 98 | LeveX | 小型交易所 |
| 99 | WHXEX | 小型交易所 |
| 100 | EagleX | 小型交易所 |
| 101 | OneBullEx | 小型交易所 |
| 102 | CoinP | 小型交易所 |
| 103 | Ulink | 小型交易所 |
| 104 | CrypFine | 小型交易所 |
| 105 | TGEX | 小型交易所 |
| 106 | OneEx | 小型交易所 |
| 107 | CEEX exchange | 小型交易所 |
| 108 | Nivex | 小型交易所 |
| 109 | ASTX | 小型交易所 |
| 112 | HyperPay Futures | 小型交易所 |
| 113 | FIXT | 小型交易所 |

## 实现建议

### 优先级排序
1. **Tier-1 (22家)**: API 文档完善，排名靠前，建议优先实现
2. **Tier-2 (10家)**: 有一定规模，可在 Tier-1 完成后实现
3. **Tier-3 (58家)**: 规模较小，API 文档不完善，按需实现

### 已有但未在衍生品榜单的实现 (45家)
以下交易所已在 bt_api_py 中实现，但未出现在 CMC 衍生品排行榜中（主要是现货交易所/DEX）：
- 现货: bequant, bigone, bitbank, bitbns, bithumb, bitinka, bitso, bitstamp, bitvavo, btc_markets, btcturk, buda, coincheck, coindcx, coinone, coinspot, coinswitch, exmo, foxbit, giottus, independent_reserve, korbit, latoken, localbitcoins, luno, mercado_bitcoin, ripio, satoshitango, swyftx, upbit, valr, wazirx, yobit, zaif, zebpay
- DEX: balancer, cow_swap, curve, dydx, gmx, hyperliquid, pancakeswap, raydium, sushiswap, uniswap
