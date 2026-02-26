# Query Sub Orders (USER_DATA)

> 来源: [Binance Algo Trading API](https://developers.binance.com/docs/algo/spot-algo/Query-Sub-Orders)

## API Description

Get respective sub orders for a specified algoId.

## HTTP Request

```
GET /sapi/v1/algo/spot/subOrders
```

## Request Weight(IP)

1

## Request Parameters

| Name | Type | Mandatory | Description |
|------|------|-----------|-------------|
| algoId | LONG | YES | |
| page | INT | NO | Default is 1 |
| pageSize | INT | NO | MIN 1, MAX 100; Default 100 |
| recvWindow | LONG | NO | |
| timestamp | LONG | YES | |

## Response Example

```json
{
    "total": 1,
    "executedQty": "1.000",
    "executedAmt": "3229.44000000",
    "subOrders": [
        {
            "algoId": 13723,
            "orderId": 8389765519993908929,
            "orderStatus": "FILLED",
            "executedQty": "1.000",
            "executedAmt": "3229.44000000",
            "feeAmt": "-1.61471999",
            "feeAsset": "USDT",
            "bookTime": 1649319001964,
            "avgPrice": "3229.44",
            "side": "SELL",
            "symbol": "ETHUSDT",
            "subId": 1,
            "timeInForce": "IMMEDIATE_OR_CANCEL",
            "origQty": "1.000"
        }
    ]
}
```
