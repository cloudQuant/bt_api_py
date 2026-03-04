# OKX API V5 Documentation

Official documentation reference for OKX V5 API, organized from [<https://www.okx.com/docs-v5/en/](<https://www.okx.com/docs-v5/en/)>.>

## Base URLs

### Production

| Service | URL |

|---------|-----|

| REST | `<https://www.okx.com`> |

| Public WebSocket | `wss://ws.okx.com:8443/ws/v5/public` |

| Private WebSocket | `wss://ws.okx.com:8443/ws/v5/private` |

| Business WebSocket | `wss://ws.okx.com:8443/ws/v5/business` |

### Demo Trading

| Service | URL |

|---------|-----|

| REST | `<https://www.okx.com`> (with header `x-simulated-trading: 1`) |

| Public WebSocket | `wss://wspap.okx.com:8443/ws/v5/public` |

| Private WebSocket | `wss://wspap.okx.com:8443/ws/v5/private` |

| Business WebSocket | `wss://wspap.okx.com:8443/ws/v5/business` |

## Documentation Index

| File | Description |

|------|-------------|

| [overview.md](overview.md) | API overview, authentication, WebSocket, rate limits, account modes |

| [trading_account.md](trading_account.md) | Trading account management: balance, positions, leverage, fees, configuration |

| [order_book_trading_trade.md](order_book_trading_trade.md) | Order placement, cancellation, amendment, order queries, fills |

| [order_book_trading_algo.md](order_book_trading_algo.md) | Algo trading: trigger, TP/SL, trailing stop, iceberg, TWAP orders |

| [order_book_trading_grid.md](order_book_trading_grid.md) | Grid trading bot: spot grid, contract grid |

| [order_book_trading_others.md](order_book_trading_others.md) | Signal bot, recurring buy, copy trading |

| [market_data.md](market_data.md) | Market data: tickers, order book, candlesticks, trades, SBE data |

| [public_data.md](public_data.md) | Public data: instruments, funding rate, open interest, mark price, index |

| [block_trading.md](block_trading.md) | Block trading: RFQ workflow, quotes, multi-leg trades |

| [spread_trading.md](spread_trading.md) | Spread trading: spread orders, public spreads |

| [trading_statistics.md](trading_statistics.md) | Trading statistics: open interest, taker volume, long/short ratio |

| [funding_account.md](funding_account.md) | Funding: deposit, withdrawal, transfer, convert |

| [sub_account.md](sub_account.md) | Sub-account management: create, API keys, balances, transfers |

| [financial_product.md](financial_product.md) | Financial products: on-chain earn, ETH/SOL staking, simple earn, flexible loan |

| [affiliate.md](affiliate.md) | Affiliate program API |

| [status_announcement_error.md](status_announcement_error.md) | System status, announcements, error codes |

## Python SDK

Install the official Python SDK:

```bash
pip install python-okx

```bash
Quick start:

```python
import okx.Trade as Trade
import okx.Account as Account
import okx.MarketData as MarketData
import okx.PublicData as PublicData

# API initialization

apikey = "YOUR_API_KEY"
secretkey = "YOUR_SECRET_KEY"
passphrase = "YOUR_PASSPHRASE"
flag = "0"  # Production: 0, Demo: 1

# Account API

accountAPI = Account.AccountAPI(apikey, secretkey, passphrase, False, flag)

# Trade API

tradeAPI = Trade.TradeAPI(apikey, secretkey, passphrase, False, flag)

# Market Data (no auth needed)

marketAPI = MarketData.MarketAPI(flag=flag)

# Public Data (no auth needed)

publicAPI = PublicData.PublicAPI(flag=flag)

```bash

## WebSocket Example

```python
import asyncio
from okx.websocket.WsPublicAsync import WsPublicAsync
from okx.websocket.WsPrivateAsync import WsPrivateAsync

# Public WebSocket

async def public_ws():
    ws = WsPublicAsync(url="wss://ws.okx.com:8443/ws/v5/public")
    await ws.start()
    args = [{"channel": "tickers", "instId": "BTC-USDT"}]
    await ws.subscribe(args, callback=lambda msg: print(msg))
    await asyncio.sleep(30)

# Private WebSocket

async def private_ws():
    ws = WsPrivateAsync(
        apiKey="YOUR_API_KEY",
        passphrase="YOUR_PASSPHRASE",
        secretKey="YOUR_SECRET_KEY",
        url="wss://ws.okx.com:8443/ws/v5/private",
        useServerTime=False
    )
    await ws.start()
    args = [{"channel": "account"}]
    await ws.subscribe(args, callback=lambda msg: print(msg))
    await asyncio.sleep(30)

```bash

## Key Concepts

- **instType**: Instrument type — `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION`
- **tdMode**: Trade mode — `cash` (spot), `cross` (cross margin), `isolated` (isolated margin)
- **ordType**: Order type — `market`, `limit`, `post_only`, `fok`, `ioc`, `optimal_limit_ioc`
- **posSide**: Position side — `long`, `short`, `net`
- **mgnMode**: Margin mode — `cross`, `isolated`
- **instId**: Instrument ID, e.g. `BTC-USDT`, `BTC-USDT-SWAP`, `BTC-USD-231229`
- **instFamily**: Instrument family, e.g. `BTC-USD`, `BTC-USDT`
- **uly**: Underlying index, e.g. `BTC-USD`
- **settleCcy**: Settlement currency

## Account Modes

| Mode | acctLv | Description |

|------|--------|-------------|

| Spot mode | 1 | Simple spot trading |

| Futures mode | 2 | Single-currency margin for futures |

| Multi-currency margin | 3 | Multiple currencies as margin |

| Portfolio margin | 4 | Portfolio-based risk management |
