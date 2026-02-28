# Cancel Algo Order (TRADE)

> 来源: [Binance Algo Trading API](<https://developers.binance.com/docs/algo/future-algo/Cancel-Algo-Order)>

## API Description

Cancel an active order.

## HTTP Request

```bash
DELETE /sapi/v1/algo/futures/order

```bash

## Request Weight(IP)

1

## Request Parameters

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| algoId | LONG | YES | eg. 14511 |

| recvWindow | LONG | NO | |

| timestamp | LONG | YES | |

- *Notes:**

- You need to enable **Futures Trading Permission** for the api key which requests this endpoint.
- Base URL: `<https://api.binance.com`>

## Response Example

```json
{
    "algoId": 14511,
    "success": true,
    "code": 0,
    "msg": "OK"
}

```bash
