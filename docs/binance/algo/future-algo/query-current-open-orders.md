# Query Current Algo Open Orders (USER_DATA)

> 来源: [Binance Algo Trading API](https://developers.binance.com/docs/algo/future-algo/Query-Current-Algo-Open-Orders)

## API Description

Query Current Algo Open Orders.

## HTTP Request

```
GET /sapi/v1/algo/futures/openOrders
```

## Request Weight(IP)

1

## Request Parameters

| Name | Type | Mandatory | Description |
|------|------|-----------|-------------|
| recvWindow | LONG | NO | |
| timestamp | LONG | YES | |

**Notes:**

- You need to enable **Futures Trading Permission** for the api key which requests this endpoint.
- Base URL: `https://api.binance.com`

## Response Example

```json
{
    "total": 1,
    "orders": [
        {
            "algoId": 14517,
            "symbol": "ETHUSDT",
            "side": "SELL",
            "positionSide": "SHORT",
            "totalQty": "5.000",
            "executedQty": "0.000",
            "executedAmt": "0.00000000",
            "avgPrice": "0.00",
            "clientAlgoId": "d7096549481642f8a0bb69e9e2e31f2e",
            "bookTime": 1649756817004,
            "endTime": 0,
            "algoStatus": "WORKING",
            "algoType": "VP",
            "urgency": "LOW"
        }
    ]
}
```
