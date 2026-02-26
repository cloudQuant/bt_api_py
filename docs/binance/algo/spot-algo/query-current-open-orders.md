# Query Current Algo Open Orders (USER_DATA)

> 来源: [Binance Algo Trading API](https://developers.binance.com/docs/algo/spot-algo/Query-Current-Algo-Open-Orders)

## API Description

Get all open SPOT TWAP orders.

## HTTP Request

```
GET /sapi/v1/algo/spot/openOrders
```

## Request Weight(IP)

1

## Request Parameters

| Name | Type | Mandatory | Description |
|------|------|-----------|-------------|
| recvWindow | LONG | NO | |
| timestamp | LONG | YES | |

## Response Example

```json
{
    "total": 1,
    "orders": [
        {
            "algoId": 14517,
            "symbol": "ETHUSDT",
            "side": "SELL",
            "totalQty": "5.000",
            "executedQty": "0.000",
            "executedAmt": "0.00000000",
            "avgPrice": "0.00",
            "clientAlgoId": "d7096549481642f8a0bb69e9e2e31f2e",
            "bookTime": 1649756817004,
            "endTime": 0,
            "algoStatus": "WORKING",
            "algoType": "TWAP",
            "urgency": "LOW"
        }
    ]
}
```
