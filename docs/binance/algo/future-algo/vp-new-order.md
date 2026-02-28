# Volume Participation(VP) New Order (TRADE)

> 来源: [Binance Algo Trading API](<https://developers.binance.com/docs/algo/future-algo)>

## API Description

Send in a VP new order. Only support on USDⓈ-M Contracts.

## HTTP Request

```bash
POST /sapi/v1/algo/futures/newOrderVp

```bash

## Request Weight(UID)

300

## Request Parameters

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | YES | Trading symbol eg. BTCUSDT |

| side | ENUM | YES | Trading side ( BUY or SELL ) |

| positionSide | ENUM | NO | Default BOTH for One-way Mode; LONG or SHORT for Hedge Mode. It must be sent in Hedge Mode. |

| quantity | DECIMAL | YES | Quantity of base asset; The notional (quantity *mark price(base asset)) must be more than the equivalent of 10,000 USDT and less than the equivalent of 1,000,000 USDT |

| urgency | ENUM | YES | Represent the relative speed of the current execution; ENUM: LOW, MEDIUM, HIGH |

| clientAlgoId | STRING | NO | A unique id among Algo orders (length should be 32 characters)，If it is not sent, we will give default value |

| reduceOnly | BOOLEAN | NO | "true" or "false". Default "false"; Cannot be sent in Hedge Mode; Cannot be sent when you open a position |

| limitPrice | DECIMAL | NO | Limit price of the order; If it is not sent, will place order by market price by default |

| recvWindow | LONG | NO | |

| timestamp | LONG | YES | |

- *Notes:**

- Total Algo open orders max allowed: **10 orders**.
- Leverage of symbols and position mode will be the same as your futures account settings. You can set up through the trading page or fapi.
- Receiving `"success": true` does not mean that your order will be executed. Please use the query order endpoints (`GET sapi/v1/algo/futures/openOrders` or `GET sapi/v1/algo/futures/historicalOrders`) to check the order status. For example: Your futures balance is insufficient, or open position with reduce only or position side is inconsistent with your own setting. In these cases you will receive `"success": true`, but the order status will be expired after we check it.
- You need to enable **Futures Trading Permission** for the api key which requests this endpoint.
- Base URL: `<https://api.binance.com`>

## Response Example

```json
{
    "clientAlgoId": "00358ce6a268403398bd34eaa36dffe7",
    "success": true,
    "code": 0,
    "msg": "OK"
}

```bash
