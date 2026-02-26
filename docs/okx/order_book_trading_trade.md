# Order Book Trading - Trade

All Trade API endpoints require authentication.

## REST API

### POST / Place order

Place an order only if you have sufficient funds.

- **Rate Limit**: 60 requests per 2 seconds
- **Rate limit rule (except Options)**: User ID + Instrument ID
- **Rate limit rule (Options only)**: User ID + Instrument Family
- **Permission**: Trade

```
POST /api/v5/trade/order
```

Request Parameters:

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| instId | String | Yes | Instrument ID, e.g. `BTC-USDT` |
| tdMode | String | Yes | Trade mode: `cross`, `isolated`, `cash`, `spot_isolated` |
| ccy | String | Conditional | Margin currency. Required for `cross` MARGIN orders in Futures mode |
| clOrdId | String | No | Client Order ID (max 32 chars) |
| tag | String | No | Order tag (max 16 chars) |
| side | String | Yes | `buy` or `sell` |
| posSide | String | Conditional | Position side: `long`, `short`, `net`. Required for long/short mode FUTURES/SWAP |
| ordType | String | Yes | Order type: `market`, `limit`, `post_only`, `fok`, `ioc`, `optimal_limit_ioc`, `mmp`, `mmp_and_post_only`, `elp` |
| sz | String | Yes | Quantity to buy or sell |
| px | String | Conditional | Order price. Required for `limit` orders |
| reduceOnly | Boolean | No | Whether reduce-only. Default `false` |
| tgtCcy | String | No | Target currency for SPOT: `base_ccy` or `quote_ccy` |
| banAmend | Boolean | No | Whether to ban amend. Default `false` |
| stpId | String | No | Self trade prevention ID |
| stpMode | String | No | Self trade prevention mode: `cancel_maker`, `cancel_taker`, `cancel_both` |
| tpTriggerPx | String | No | Take-profit trigger price |
| tpOrdPx | String | No | Take-profit order price (-1 for market) |
| slTriggerPx | String | No | Stop-loss trigger price |
| slOrdPx | String | No | Stop-loss order price (-1 for market) |
| tpTriggerPxType | String | No | TP trigger price type: `last`, `index`, `mark` |
| slTriggerPxType | String | No | SL trigger price type: `last`, `index`, `mark` |

Python Example:
```python
import okx.Trade as Trade

apikey = "YOUR_API_KEY"
secretkey = "YOUR_SECRET_KEY"
passphrase = "YOUR_PASSPHRASE"
flag = "1"  # Production: 0, Demo: 1

tradeAPI = Trade.TradeAPI(apikey, secretkey, passphrase, False, flag)

# Spot limit order
result = tradeAPI.place_order(
    instId="BTC-USDT",
    tdMode="cash",
    clOrdId="b15",
    side="buy",
    ordType="limit",
    px="2.15",
    sz="2"
)
print(result)
```

Response Example:
```json
{
  "code": "0",
  "msg": "",
  "data": [{
    "clOrdId": "oktswap6",
    "ordId": "312269865356374016",
    "tag": "",
    "ts": "1695190491421",
    "sCode": "0",
    "sMsg": ""
  }],
  "inTime": "1695190491421339",
  "outTime": "1695190491423240"
}
```

### POST / Place multiple orders

Place orders in batches. Maximum 20 orders per request.

- **Rate Limit**: 300 orders per 2 seconds
- **Rate limit rule**: User ID + Instrument ID (Options: User ID + Instrument Family)
- **Permission**: Trade

```
POST /api/v5/trade/batch-orders
```

### POST / Cancel order

Cancel an incomplete order.

- **Rate Limit**: 60 requests per 2 seconds
- **Permission**: Trade

```
POST /api/v5/trade/cancel-order
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| instId | String | Yes | Instrument ID |
| ordId | String | Conditional | Order ID. Either ordId or clOrdId required |
| clOrdId | String | Conditional | Client Order ID |

Python Example:
```python
result = tradeAPI.cancel_order(instId="BTC-USDT", ordId="590908157585625111")
print(result)
```

### POST / Cancel multiple orders

Cancel incomplete orders in batches. Maximum 20 orders per request.

```
POST /api/v5/trade/cancel-batch-orders
```

### POST / Amend order

Amend an incomplete order.

- **Rate Limit**: 60 requests per 2 seconds
- **Permission**: Trade

```
POST /api/v5/trade/amend-order
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| instId | String | Yes | Instrument ID |
| cxlOnFail | Boolean | No | Cancel on fail. Default `false` |
| ordId | String | Conditional | Order ID |
| clOrdId | String | Conditional | Client Order ID |
| reqId | String | No | Request ID for idempotency |
| newSz | String | Conditional | New size |
| newPx | String | Conditional | New price |

### POST / Amend multiple orders

```
POST /api/v5/trade/amend-batch-orders
```

### POST / Close positions

```
POST /api/v5/trade/close-position
```

### GET / Order details

Retrieve order details.

- **Rate Limit**: 60 requests per 2 seconds
- **Permission**: Read

```
GET /api/v5/trade/order
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| instId | String | Yes | Instrument ID |
| ordId | String | Conditional | Order ID |
| clOrdId | String | Conditional | Client Order ID |

Python Example:
```python
result = tradeAPI.get_order(instId="BTC-USDT", ordId="680800019749904384")
print(result)
```

Key Response Fields:

| Field | Description |
|-------|-------------|
| instId | Instrument ID |
| ordId | Order ID |
| clOrdId | Client Order ID |
| px | Order price |
| sz | Order quantity |
| ordType | Order type |
| side | `buy` or `sell` |
| posSide | Position side |
| tdMode | Trade mode |
| fillPx | Last filled price |
| fillSz | Last filled quantity |
| accFillSz | Accumulated fill quantity |
| avgPx | Average filled price |
| state | Order state: `canceled`, `live`, `partially_filled`, `filled`, `mmp_canceled` |
| fee | Fee (negative = fee, positive = rebate) |
| feeCcy | Fee currency |
| pnl | Profit and loss |
| lever | Leverage |
| tpTriggerPx | Take profit trigger price |
| slTriggerPx | Stop loss trigger price |

### GET / Order List (Pending)

Retrieve all incomplete orders under the current account.

- **Rate Limit**: 60 requests per 2 seconds
- **Permission**: Read

```
GET /api/v5/trade/orders-pending
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| instType | String | No | `SPOT`, `MARGIN`, `SWAP`, `FUTURES`, `OPTION` |
| uly | String | No | Underlying |
| instFamily | String | No | Instrument family |
| instId | String | No | Instrument ID |
| ordType | String | No | Order type filter, comma-separated |
| state | String | No | `live`, `partially_filled` |
| after | String | No | Pagination |
| before | String | No | Pagination |
| limit | String | No | Default 100, max 100 |

### GET / Order history (last 7 days)

Get completed orders placed in the last 7 days.

- **Rate Limit**: 40 requests per 2 seconds
- **Permission**: Read

```
GET /api/v5/trade/orders-history
```

### GET / Order history (last 3 months)

```
GET /api/v5/trade/orders-history-archive
```

### GET / Transaction details (last 3 days)

Retrieve recently-filled transaction details.

- **Rate Limit**: 60 requests per 2 seconds
- **Permission**: Read

```
GET /api/v5/trade/fills
```

### GET / Transaction details (last 3 months)

```
GET /api/v5/trade/fills-history
```

### GET / Easy convert currency list

```
GET /api/v5/trade/easy-convert-currency-list
```

### POST / Place easy convert

```
POST /api/v5/trade/easy-convert
```

### GET / Easy convert history

```
GET /api/v5/trade/easy-convert-history
```

### GET / One-click repay currency list

```
GET /api/v5/trade/one-click-repay-currency-list
```

### POST / Trade one-click repay

```
POST /api/v5/trade/one-click-repay
```

### GET / One-click repay history

```
GET /api/v5/trade/one-click-repay-history
```

### POST / Mass cancel order

```
POST /api/v5/trade/mass-cancel
```

### POST / Cancel All After

```
POST /api/v5/trade/cancel-all-after
```

### GET / Account rate limit

```
GET /api/v5/trade/account-rate-limit
```

### POST / Order precheck

```
POST /api/v5/trade/order-precheck
```

---

## WebSocket

### WS / Order channel

Retrieve order information. Data pushed when there are new orders or order updates.

- **URL Path**: `/ws/v5/private` (requires login)
- **Channel**: `orders`

Subscribe Example:
```json
{
  "id": "1512",
  "op": "subscribe",
  "args": [{
    "channel": "orders",
    "instType": "FUTURES",
    "instId": "BTC-USD-200329"
  }]
}
```

Python Example:
```python
import asyncio
from okx.websocket.WsPrivateAsync import WsPrivateAsync

def callbackFunc(message):
    print(message)

async def main():
    ws = WsPrivateAsync(
        apiKey="YOUR_API_KEY",
        passphrase="YOUR_PASSPHRASE",
        secretKey="YOUR_SECRET_KEY",
        url="wss://ws.okx.com:8443/ws/v5/private",
        useServerTime=False
    )
    await ws.start()
    args = [{"channel": "orders", "instType": "FUTURES", "instId": "BTC-USD-200329"}]
    await ws.subscribe(args, callback=callbackFunc)
    await asyncio.sleep(10)
    await ws.unsubscribe(args, callback=callbackFunc)

asyncio.run(main())
```

### WS / Fills channel

- **URL Path**: `/ws/v5/private`
- **Channel**: `fills`

### WS / Place order

- **URL Path**: `/ws/v5/private` (requires login)
- **Rate Limit**: 60 requests per 2 seconds
- **op**: `order`

Request Example:
```json
{
  "id": "1512",
  "op": "order",
  "args": [{
    "side": "buy",
    "instId": "BTC-USDT",
    "tdMode": "isolated",
    "ordType": "market",
    "sz": "100"
  }]
}
```

### WS / Place multiple orders

- **op**: `batch-orders`

### WS / Cancel order

- **op**: `cancel-order`

### WS / Cancel multiple orders

- **op**: `batch-cancel-orders`

### WS / Amend order

- **op**: `amend-order`

### WS / Amend multiple orders

- **op**: `batch-amend-orders`

### WS / Mass cancel order

- **op**: `mass-cancel`
