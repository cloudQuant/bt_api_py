# Time-Weighted Average Price(Twap) New Order (TRADE)

> 来源: [Binance Algo Trading API](https://developers.binance.com/docs/algo/spot-algo/Time-Weighted-Average-Price-New-Order)

## API Description

Place a new spot TWAP order with Algo service.

## HTTP Request

```
POST /sapi/v1/algo/spot/newOrderTwap
```

## Request Weight(UID)

3000

## Request Parameters

| Name | Type | Mandatory | Description |
|------|------|-----------|-------------|
| symbol | STRING | YES | Trading symbol eg. BTCUSDT |
| side | ENUM | YES | Trading side ( BUY or SELL ) |
| quantity | DECIMAL | YES | Quantity of base asset; Maximum notional per order is 200k, 2mm or 10mm, depending on symbol. Please reduce your size if your order is above the maximum notional per order. |
| duration | LONG | YES | Duration for TWAP orders in seconds. [300, 86400] |
| clientAlgoId | STRING | NO | A unique id among Algo orders (length should be 32 characters)，If it is not sent, we will give default value |
| limitPrice | DECIMAL | NO | Limit price of the order; If it is not sent, will place order by market price by default |
| timestamp | LONG | YES | |

**Notes:**

- Total Algo open orders max allowed: **20 orders**.

## Response Example

```json
{
    "clientAlgoId": "65ce1630101a480b85915d7e11fd5078",
    "success": true,
    "code": 0,
    "msg": "OK"
}
```
