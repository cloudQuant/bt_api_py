# Time-Weighted Average Price(Twap) New Order (TRADE)

> 来源: [Binance Algo Trading API](<https://developers.binance.com/docs/algo/future-algo/Time-Weighted-Average-Price-New-Order)>

## API Description

Send in a Twap new order. Only support on USDⓈ-M Contracts.

## HTTP Request

```
POST /sapi/v1/algo/futures/newOrderTwap

```

## Request Weight(UID)

3000

## Request Parameters

| Name | Type | Mandatory | Description |
|------|------|-----------|-------------|
| symbol | STRING | YES | Trading symbol eg. BTCUSDT |
| side | ENUM | YES | Trading side ( BUY or SELL ) |
| positionSide | ENUM | NO | Default BOTH for One-way Mode; LONG or SHORT for Hedge Mode. It must be sent in Hedge Mode. |
| quantity | DECIMAL | YES | Quantity of base asset; The notional (quantity *mark price(base asset)) must be more than the equivalent of 1,000 USDT and less than the equivalent of 1,000,000 USDT |
| duration | LONG | YES | Duration for TWAP orders in seconds. [300, 86400] |
| clientAlgoId | STRING | NO | A unique id among Algo orders (length should be 32 characters)，If it is not sent, we will give default value |
| reduceOnly | BOOLEAN | NO | "true" or "false". Default "false"; Cannot be sent in Hedge Mode; Cannot be sent when you open a position |
| limitPrice | DECIMAL | NO | Limit price of the order; If it is not sent, will place order by market price by default |
| recvWindow | LONG | NO | |
| timestamp | LONG | YES | |

**Notes:**
- Total Algo open orders max allowed: **30 orders**.
- Leverage of symbols and position mode will be the same as your futures account settings. You can set up through the trading page or fapi.
- Receiving `"success": true` does not mean that your order will be executed. Please use the query order endpoints (`GET sapi/v1/algo/futures/openOrders` or `GET sapi/v1/algo/futures/historicalOrders`) to check the order status.
- `quantity * 60 / duration` should be larger than minQty.
- `duration` cannot be less than 5 mins or more than 24 hours.
- For delivery contracts, TWAP end time should be one hour earlier than the delivery time of the symbol.
- You need to enable **Futures Trading Permission** for the api key which requests this endpoint.
- Base URL: `https://api.binance.com`

## Response Example

```json
{
    "clientAlgoId": "65ce1630101a480b85915d7e11fd5078",
    "success": true,
    "code": 0,
    "msg": "OK"
}

```
