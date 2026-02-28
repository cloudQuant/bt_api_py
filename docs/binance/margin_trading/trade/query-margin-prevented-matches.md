
# Query Prevented Matches(USER_DATA)


## Description​


Displays the list of orders that were expired due to STP. (Self-Trade Prevention).


## HTTP Request​


GET `/sapi/v1/margin/myPreventedMatches`


## Request Weight​


- *10(IP)**


## Request Parameters​


| Name | Type | Mandatory | Description |

| --- | --- | --- | --- |

| symbol | STRING | YES |  |

| preventedMatchId | LONG | NO |  |

| orderId | LONG | NO |  |

| fromPreventedMatchId | LONG | NO |  |

| recvWindow | LONG | NO | The value cannot be greater than 60000. Supports up to three decimal places of precision (e.g., 6000.346) so that microseconds may be specified. |

| timestamp | LONG | YES |  |

| isIsolated | STRING | NO | For isolated margin or not, "TRUE", "FALSE", default "FALSE" |

- Supported parameter combinations:
  - `symbol` + `preventedMatchId`
  - `symbol` + `orderId`
  - `symbol` + `orderId` + `fromPreventedMatchId`
- If `orderId` is provided, all prevented matches for that order will be returned.
- If `preventedMatchId` is provided, the specific prevented match will be returned.
- A single request returns a maximum of 500 records. If there are more than 500 records, use `symbol` + `orderId` + `fromPreventedMatchId` combination for pagination.

## Data Source​


Database


## Response Example​


```bash
[    {        "symbol": "BTCUSDT",        "preventedMatchId": 1,        "takerOrderId": 5,        "makerSymbol": "BTCUSDT",        "makerOrderId": 3,        "tradeGroupId": 1,        "selfTradePreventionMode": "EXPIRE_MAKER",        "price": "1.100000",        "makerPreventedQuantity": "1.300000",        "transactTime": 1669101687094    }]

```bash

## Response Parameters​


| Name | Type | Description |

| --- | --- | --- |

| symbol | STRING | The trading pair symbol |

| preventedMatchId | LONG | Unique identifier for the prevented match event |

| takerOrderId | LONG | The order ID of the taker order that triggered STP |

| makerSymbol | STRING | The symbol of the maker order |

| makerOrderId | LONG | The order ID of the maker order involved in STP |

| tradeGroupId | LONG | Identifier grouping related prevented matches |

| selfTradePreventionMode | STRING | The STP mode applied. Possible values: EXPIRE_TAKER, EXPIRE_MAKER, EXPIRE_BOTH |

| price | STRING | The price at which the match would have occurred |

| makerPreventedQuantity | STRING | The quantity that was prevented from being filled on the maker side |

| transactTime | LONG | Unix timestamp (milliseconds) when the prevention occurred |
