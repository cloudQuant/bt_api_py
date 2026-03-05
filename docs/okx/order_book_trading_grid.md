# Order Book Trading - Grid Trading

## REST API

### POST / Place grid algo order

- **Rate Limit**: 20 requests per 2 seconds
- **Rate limit rule**: User ID + Instrument ID
- **Permission**: Trade

```
POST /api/v5/tradingBot/grid/order-algo

```

### POST / Amend grid algo order basic param

```
POST /api/v5/tradingBot/grid/amend-order-algo

```

### POST / Amend grid algo order

```
POST /api/v5/tradingBot/grid/adjust-order-algo

```

### POST / Stop grid algo order

```
POST /api/v5/tradingBot/grid/stop-order-algo

```

### POST / Close position for contract grid

```
POST /api/v5/tradingBot/grid/close-position

```

### POST / Cancel close position order for contract grid

```
POST /api/v5/tradingBot/grid/cancel-close-order

```

### POST / Instant trigger grid algo order

```
POST /api/v5/tradingBot/grid/order-instant-trigger

```

### GET / Grid algo order list

```
GET /api/v5/tradingBot/grid/orders-algo-pending

```

| Parameter | Type | Required | Description |

|-----------|------|----------|-------------|

| algoOrdType | String | Yes | `grid` (Spot grid), `contract_grid` (Contract grid) |

| algoId | String | No | Algo ID |

| instId | String | No | Instrument ID |

| instType | String | No | Instrument type |

| after | String | No | Pagination |

| before | String | No | Pagination |

| limit | String | No | Default 100, max 100 |

### GET / Grid algo order history

```
GET /api/v5/tradingBot/grid/orders-algo-history

```

### GET / Grid algo order details

```
GET /api/v5/tradingBot/grid/orders-algo-details

```

### GET / Grid algo sub orders

```
GET /api/v5/tradingBot/grid/sub-orders

```

### GET / Grid algo order positions

```
GET /api/v5/tradingBot/grid/positions

```

### POST / Spot grid withdraw income

```
POST /api/v5/tradingBot/grid/withdraw-income

```

### POST / Compute margin balance

```
POST /api/v5/tradingBot/grid/compute-margin-balance

```

### POST / Adjust margin balance

```
POST /api/v5/tradingBot/grid/margin-balance

```

### POST / Add investment

```
POST /api/v5/tradingBot/grid/add-investment

```

### GET / Grid AI parameter (public)

```
GET /api/v5/tradingBot/grid/ai-param

```

### POST / Compute min investment (public)

```
POST /api/v5/tradingBot/grid/min-investment

```

### GET / RSI back testing (public)

```
GET /api/v5/tradingBot/public/rsi-back-testing

```

### GET / Max grid quantity (public)

```
GET /api/v5/tradingBot/grid/max-grid-quantity

```

---

## WebSocket

### WS / Spot grid algo orders channel

- **URL Path**: `/ws/v5/business` (requires login)
- **Channel**: `grid-orders-spot`

### WS / Contract grid algo orders channel

- **Channel**: `grid-orders-contract`

### WS / Grid positions channel

- **Channel**: `grid-positions`

### WS / Grid sub orders channel

- **Channel**: `grid-sub-orders`
