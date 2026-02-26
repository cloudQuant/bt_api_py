# Cancel Algo Order (TRADE)

> 来源: [Binance Algo Trading API](https://developers.binance.com/docs/algo/spot-algo/Cancel-Algo-Order)

## API Description

Cancel an open TWAP order.

## HTTP Request

```
DELETE /sapi/v1/algo/spot/order
```

## Request Weight(IP)

1

## Request Parameters

| Name | Type | Mandatory | Description |
|------|------|-----------|-------------|
| algoId | LONG | YES | eg. 14511 |
| recvWindow | LONG | NO | |
| timestamp | LONG | YES | |

## Response Example

```json
{
    "algoId": 14511,
    "success": true,
    "code": 0,
    "msg": "OK"
}
```
