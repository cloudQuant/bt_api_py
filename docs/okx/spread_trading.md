# Spread Trading

Spread trading allows users to trade the price difference between two instruments.

## Introduction

### Basic Concepts

- **Spread**: The price difference between two legs (instruments)
- **Leg**: An individual instrument within a spread
- **Near Leg**: The leg with the nearer expiry
- **Far Leg**: The leg with the further expiry

### High Level Workflow

1. Obtain available spreads
2. Submit spread orders
3. Monitor order and trade status
4. Manage positions

## REST API

### Order Management

| Method | Endpoint | Description |

|--------|----------|-------------|

| POST | `/api/v5/sprd/order` | Place order |

| POST | `/api/v5/sprd/cancel-order` | Cancel order |

| POST | `/api/v5/sprd/mass-cancel` | Cancel all orders |

| POST | `/api/v5/sprd/amend-order` | Amend order |

### Query

| Method | Endpoint | Description |

|--------|----------|-------------|

| GET | `/api/v5/sprd/order` | Get order details |

| GET | `/api/v5/sprd/orders-pending` | Get active orders |

| GET | `/api/v5/sprd/orders-history` | Get orders (last 21 days) |

| GET | `/api/v5/sprd/orders-history-archive` | Get orders history (last 3 months) |

| GET | `/api/v5/sprd/trades` | Get trades (last 7 days) |

### Public

| Method | Endpoint | Description |

|--------|----------|-------------|

| GET | `/api/v5/sprd/spreads` | Get spreads (public) |

| GET | `/api/v5/sprd/books` | Get order book (public) |

| GET | `/api/v5/sprd/ticker` | Get ticker (public) |

| GET | `/api/v5/sprd/public-trades` | Get public trades (public) |

| GET | `/api/v5/market/sprd-candles` | Get candlesticks |

| GET | `/api/v5/market/sprd-history-candles` | Get candlesticks history |

| POST | `/api/v5/sprd/cancel-all-after` | Cancel all after |

---

## WebSocket Trade API

| Operation | Description |

|-----------|-------------|

| `sprd-order` | Place order |

| `sprd-amend-order` | Amend order |

| `sprd-cancel-order` | Cancel order |

| `sprd-mass-cancel` | Cancel all orders |

## WebSocket Private Channel

### Order channel

- **URL Path**: `/ws/v5/business` (requires login)
- **Channel**: `sprd-orders`

### Trades channel

- **Channel**: `sprd-trades`

## WebSocket Public Channel

### Order book channel

- **URL Path**: `/ws/v5/public`
- **Channel**: `sprd-books`, `sprd-books5`, `sprd-bbo-tbt`

### Public trades channel

- **Channel**: `sprd-public-trades`

### Tickers channel

- **Channel**: `sprd-tickers`

### Candlesticks channel

- **URL Path**: `/ws/v5/business`
- **Channel**: `sprd-candle1m`, etc.
