On this page

# Recent Trades List

## API Description

Get recent market trades

## HTTP Request

GET `/fapi/v1/trades`

## Request Weight

- *5**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

limit| INT| NO| Default 500; max 1000.

>   * Market trades means trades filled in the order book. Only market trades will be returned, which means the insurance fund trades and ADL trades won't be returned.
>

## Response Example


    [
      {
        "id": 28457,
        "price": "4.00000100",
        "qty": "12.00000000",
        "quoteQty": "48.00",
        "time": 1499865549590,
        "isBuyerMaker": true,
      }
    ]


  - [API Description](</docs/derivatives/usds-margined-futures/market-data/rest-api/Recent-Trades-List#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/market-data/rest-api/Recent-Trades-List#http-request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/market-data/rest-api/Recent-Trades-List#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/rest-api/Recent-Trades-List#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/market-data/rest-api/Recent-Trades-List#response-example>)
