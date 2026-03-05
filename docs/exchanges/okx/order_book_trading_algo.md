# Order Book Trading - Algo Trading

## REST API

### POST / Place algo order

Place algo orders including trigger order, oco order, conditional order, iceberg order, twap order, trailing order.

- ***Rate Limit**: 20 requests per 2 seconds
- ***Rate limit rule**: User ID + Instrument ID
- ***Permission**: Trade

```
POST /api/v5/trade/order-algo

```
Key Request Parameters:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| instId | String | Yes | Instrument ID |
| tdMode | String | Yes | Trade mode: `cross`, `isolated`, `cash` |
| side | String | Yes | `buy` or `sell` |
| ordType | String | Yes | `conditional`, `oco`, `trigger`, `move_order_stop`, `iceberg`, `twap` |
| sz | String | Yes | Quantity |
| tgtCcy | String | No | Target currency for SPOT |
| posSide | String | Conditional | Position side |
| clOrdId | String | No | Client-supplied algo ID |

**For trigger orders:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| triggerPx | String | Yes | Trigger price |
| orderPx | String | Yes | Order price (-1 for market) |
| triggerPxType | String | No | `last`, `index`, `mark` (default `last`) |

**For conditional orders (TP/SL):**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| tpTriggerPx | String | Conditional | Take-profit trigger price |
| tpOrdPx | String | Conditional | Take-profit order price (-1 for market) |
| slTriggerPx | String | Conditional | Stop-loss trigger price |
| slOrdPx | String | Conditional | Stop-loss order price (-1 for market) |
| tpTriggerPxType | String | No | `last`, `index`, `mark` |
| slTriggerPxType | String | No | `last`, `index`, `mark` |

### POST / Cancel algo order

- ***Rate Limit**: 20 requests per 2 seconds
- ***Permission**: Trade

```
POST /api/v5/trade/cancel-algos

```

### POST / Amend algo order

```
POST /api/v5/trade/amend-algos

```

### GET / Algo order details

```
GET /api/v5/trade/order-algo

```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| algoId | String | Conditional | Algo ID |
| algoClOrdId | String | Conditional | Client-supplied algo ID |

### GET / Algo order list

Retrieve incomplete algo order list.

- ***Rate Limit**: 20 requests per 2 seconds
- ***Permission**: Read

```
GET /api/v5/trade/orders-algo-pending

```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| ordType | String | Yes | `conditional`, `oco`, `trigger`, `move_order_stop`, `iceberg`, `twap` |
| algoId | String | No | Algo ID |
| instType | String | No | Instrument type |
| instId | String | No | Instrument ID |

### GET / Algo order history

```
GET /api/v5/trade/orders-algo-history

```

---

## WebSocket

### WS / Algo orders channel

- ***URL Path**: `/ws/v5/business` (requires login)
- ***Channel**: `orders-algo`

Subscribe Example:

```json
{
  "id": "1512",
  "op": "subscribe",
  "args": [{
    "channel": "orders-algo",
    "instType": "FUTURES",
    "instId": "BTC-USD-200329"
  }]
}

```

### WS / Advance algo orders channel

- ***URL Path**: `/ws/v5/business`
- ***Channel**: `algo-advance`

For iceberg, twap, and trailing orders.
