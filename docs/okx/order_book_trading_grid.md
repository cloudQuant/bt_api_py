# Order Book Trading - Grid Trading

## REST API

### POST / Place grid algo order

- **Rate Limit**: 20 requests per 2 seconds
- **Rate limit rule**: User ID + Instrument ID
- **Permission**: Trade

```bash
POST /api/v5/tradingBot/grid/order-algo

```bash

### POST / Amend grid algo order basic param

```bash
POST /api/v5/tradingBot/grid/amend-order-algo

```bash

### POST / Amend grid algo order

```bash
POST /api/v5/tradingBot/grid/adjust-order-algo

```bash

### POST / Stop grid algo order

```bash
POST /api/v5/tradingBot/grid/stop-order-algo

```bash

### POST / Close position for contract grid

```bash
POST /api/v5/tradingBot/grid/close-position

```bash

### POST / Cancel close position order for contract grid

```bash
POST /api/v5/tradingBot/grid/cancel-close-order

```bash

### POST / Instant trigger grid algo order

```bash
POST /api/v5/tradingBot/grid/order-instant-trigger

```bash

### GET / Grid algo order list

```bash
GET /api/v5/tradingBot/grid/orders-algo-pending

```bash

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

```bash
GET /api/v5/tradingBot/grid/orders-algo-history

```bash

### GET / Grid algo order details

```bash
GET /api/v5/tradingBot/grid/orders-algo-details

```bash

### GET / Grid algo sub orders

```bash
GET /api/v5/tradingBot/grid/sub-orders

```bash

### GET / Grid algo order positions

```bash
GET /api/v5/tradingBot/grid/positions

```bash

### POST / Spot grid withdraw income

```bash
POST /api/v5/tradingBot/grid/withdraw-income

```bash

### POST / Compute margin balance

```bash
POST /api/v5/tradingBot/grid/compute-margin-balance

```bash

### POST / Adjust margin balance

```bash
POST /api/v5/tradingBot/grid/margin-balance

```bash

### POST / Add investment

```bash
POST /api/v5/tradingBot/grid/add-investment

```bash

### GET / Grid AI parameter (public)

```bash
GET /api/v5/tradingBot/grid/ai-param

```bash

### POST / Compute min investment (public)

```bash
POST /api/v5/tradingBot/grid/min-investment

```bash

### GET / RSI back testing (public)

```bash
GET /api/v5/tradingBot/public/rsi-back-testing

```bash

### GET / Max grid quantity (public)

```bash
GET /api/v5/tradingBot/grid/max-grid-quantity

```bash

- --

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
