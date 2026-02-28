# Query Historical Algo Orders (USER_DATA)

> 来源: [Binance Algo Trading API](<https://developers.binance.com/docs/algo/future-algo/Query-Historical-Algo-Orders)>

## API Description

Query Historical Algo Order.

## HTTP Request

```bash
GET /sapi/v1/algo/futures/historicalOrders

```bash

## Request Weight(IP)

1

## Request Parameters

| Name | Type | Mandatory | Description |

|------|------|-----------|-------------|

| symbol | STRING | NO | Trading symbol eg. BTCUSDT |

| side | ENUM | NO | BUY or SELL |

| startTime | LONG | NO | in milliseconds eg.1641522717552 |

| endTime | LONG | NO | in milliseconds eg.1641522526562 |

| page | INT | NO | Default is 1 |

| pageSize | INT | NO | MIN 1, MAX 100; Default 100 |

| recvWindow | LONG | NO | |

| timestamp | LONG | YES | |

- *Notes:**

- You need to enable **Futures Trading Permission** for the api key which requests this endpoint.
- Base URL: `<https://api.binance.com`>

## Response Example

```json
{
    "total": 1,
    "orders": [
        {
            "algoId": 14518,
            "symbol": "BNBUSDT",
            "side": "BUY",
            "positionSide": "BOTH",
            "totalQty": "100.00",
            "executedQty": "0.00",
            "executedAmt": "0.00000000",
            "avgPrice": "0.000",
            "clientAlgoId": "acacab56b3c44bef9f6a8f8ebd2a8408",
            "bookTime": 1649757019503,
            "endTime": 1649757088101,
            "algoStatus": "CANCELLED",
            "algoType": "VP",
            "urgency": "LOW"
        }
    ]
}

```bash
