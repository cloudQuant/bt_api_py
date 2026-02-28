On this page

# Symbol Order Book Ticker

## API Description

Best price/qty on the order book for a symbol or symbols.

## Request


    {
        "id": "9d32157c-a556-4d27-9866-66760a174b57",
        "method": "ticker.book",
        "params": {
            "symbol": "BTCUSDT"
        }
    }


## Request Weight

- *2** for a single symbol;
- *5** when the symbol parameter is omitted

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| NO|

>   *If the symbol is not sent, bookTickers for all symbols will be returned in an array.
>  * The field `X-MBX-USED-WEIGHT-1M` in response header is not accurate from this endpoint, please ignore.
>

## Response Example


    {
      "id": "9d32157c-a556-4d27-9866-66760a174b57",
      "status": 200,
      "result": {
        "lastUpdateId": 1027024,
        "symbol": "BTCUSDT",
        "bidPrice": "4.00000000",
        "bidQty": "431.00000000",
        "askPrice": "4.00000200",
        "askQty": "9.00000000",
        "time": 1589437530011   // Transaction time
      },
      "rateLimits": [
        {
          "rateLimitType": "REQUEST_WEIGHT",
          "interval": "MINUTE",
          "intervalNum": 1,
          "limit": 2400,
          "count": 2
        }
      ]
    }


> OR


    {
      "id": "9d32157c-a556-4d27-9866-66760a174b57",
      "status": 200,
      "result": [
        {
          "lastUpdateId": 1027024,
          "symbol": "BTCUSDT",
          "bidPrice": "4.00000000",
          "bidQty": "431.00000000",
          "askPrice": "4.00000200",
          "askQty": "9.00000000",
          "time": 1589437530011
        }
      ],
      "rateLimits": [
        {
          "rateLimitType": "REQUEST_WEIGHT",
          "interval": "MINUTE",
          "intervalNum": 1,
          "limit": 2400,
          "count": 2
        }
      ]
    }


  - [API Description](</docs/derivatives/usds-margined-futures/market-data/websocket-api/Symbol-Order-Book-Ticker#api-description>)
  - [Request](</docs/derivatives/usds-margined-futures/market-data/websocket-api/Symbol-Order-Book-Ticker#request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/market-data/websocket-api/Symbol-Order-Book-Ticker#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/websocket-api/Symbol-Order-Book-Ticker#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/market-data/websocket-api/Symbol-Order-Book-Ticker#response-example>)
