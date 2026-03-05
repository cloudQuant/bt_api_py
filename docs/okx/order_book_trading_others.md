# Order Book Trading - Signal Bot, Recurring Buy, Copy Trading

## Signal Bot Trading

### REST API

| Method | Endpoint | Description |

|--------|----------|-------------|

| POST | `/api/v5/tradingBot/signal/create-signal` | Create signal |

| GET | `/api/v5/tradingBot/signal/signals` | Get signals |

| POST | `/api/v5/tradingBot/signal/order-algo` | Create signal bot |

| POST | `/api/v5/tradingBot/signal/stop-order-algo` | Cancel signal bots |

| POST | `/api/v5/tradingBot/signal/margin-balance` | Adjust margin balance |

| POST | `/api/v5/tradingBot/signal/amendTPSL` | Amend TPSL |

| POST | `/api/v5/tradingBot/signal/set-instruments` | Set instruments |

| GET | `/api/v5/tradingBot/signal/orders-algo-details` | Signal bot order details |

| GET | `/api/v5/tradingBot/signal/orders-algo-pending` | Active signal bot |

| GET | `/api/v5/tradingBot/signal/orders-algo-history` | Signal bot history |

| GET | `/api/v5/tradingBot/signal/positions` | Signal bot order positions |

| GET | `/api/v5/tradingBot/signal/positions-history` | Position history |

| POST | `/api/v5/tradingBot/signal/close-position` | Close position |

| POST | `/api/v5/tradingBot/signal/sub-order` | Place sub order |

| POST | `/api/v5/tradingBot/signal/cancel-sub-order` | Cancel sub order |

| GET | `/api/v5/tradingBot/signal/sub-orders` | Signal bot sub orders |

| GET | `/api/v5/tradingBot/signal/event-history` | Signal bot event history |

---

## Recurring Buy

### REST API

| Method | Endpoint | Description |

|--------|----------|-------------|

| POST | `/api/v5/tradingBot/recurring/order-algo` | Place recurring buy order |

| POST | `/api/v5/tradingBot/recurring/amend-order-algo` | Amend recurring buy order |

| POST | `/api/v5/tradingBot/recurring/stop-order-algo` | Stop recurring buy order |

| GET | `/api/v5/tradingBot/recurring/orders-algo-pending` | Recurring buy order list |

| GET | `/api/v5/tradingBot/recurring/orders-algo-history` | Recurring buy order history |

| GET | `/api/v5/tradingBot/recurring/orders-algo-details` | Recurring buy order details |

| GET | `/api/v5/tradingBot/recurring/sub-orders` | Recurring buy sub orders |

### WebSocket

- **Channel**: `recurring-buy-orders` (URL: `/ws/v5/business`)

---

## Copy Trading

### REST API

| Method | Endpoint | Description |

|--------|----------|-------------|

| GET | `/api/v5/copytrading/current-subpositions` | Get existing lead positions |

| GET | `/api/v5/copytrading/subpositions-history` | Lead position history |

| POST | `/api/v5/copytrading/algo-order` | Place lead stop order |

| POST | `/api/v5/copytrading/close-subposition` | Close lead position |

| GET | `/api/v5/copytrading/instruments` | Leading instruments |

| POST | `/api/v5/copytrading/set-instruments` | Amend leading instruments |

| GET | `/api/v5/copytrading/profit-sharing-details` | Profit sharing details |

| GET | `/api/v5/copytrading/total-profit-sharing` | Total profit sharing |

| GET | `/api/v5/copytrading/unrealized-profit-sharing-details` | Unrealized profit sharing details |

| GET | `/api/v5/copytrading/total-unrealized-profit-sharing` | Total unrealized profit sharing |

| POST | `/api/v5/copytrading/set-profit-sharing-ratio` | Amend profit sharing ratio |

| GET | `/api/v5/copytrading/config` | Account configuration |

| POST | `/api/v5/copytrading/first-copy-settings` | First copy settings |

| POST | `/api/v5/copytrading/amend-copy-settings` | Amend copy settings |

| POST | `/api/v5/copytrading/stop-copy-trading` | Stop copying |

| GET | `/api/v5/copytrading/copy-settings` | Get copy settings |

| GET | `/api/v5/copytrading/batch-leverage-info` | My lead traders |

| GET | `/api/v5/copytrading/copy-trading-configuration` | Copy trading configuration |

| GET | `/api/v5/copytrading/public-lead-traders` | Lead trader ranks (public) |

| GET | `/api/v5/copytrading/public-weekly-pnl` | Lead trader weekly PnL (public) |

| GET | `/api/v5/copytrading/public-pnl` | Lead trader daily PnL (public) |

| GET | `/api/v5/copytrading/public-stats` | Lead trader stats (public) |

| GET | `/api/v5/copytrading/public-preference-currency` | Lead trader currency preferences (public) |

| GET | `/api/v5/copytrading/public-current-subpositions` | Lead trader current positions (public) |

| GET | `/api/v5/copytrading/public-subpositions-history` | Lead trader position history (public) |

| GET | `/api/v5/copytrading/public-copy-traders` | Copy traders (public) |

### WebSocket

- **Channel**: `lead-trading-notification` (URL: `/ws/v5/business`)

For lead trading notifications including new copier events and position events.
