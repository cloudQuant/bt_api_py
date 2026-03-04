On this page

# Mark Price

## API Description

Mark Price and Funding Rate

## HTTP Request

GET `/fapi/v1/premiumIndex`

## Request Weight

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| NO|

## Response Example

> **Response:**


    {
        "symbol": "BTCUSDT",
        "markPrice": "11793.63104562",    // mark price
        "indexPrice": "11781.80495970",    // index price
        "estimatedSettlePrice": "11781.16138815", // Estimated Settle Price, only useful in the last hour before the settlement starts.
        "lastFundingRate": "0.00038246",  // This is the Latest funding rate
        "interestRate": "0.00010000",
        "nextFundingTime": 1597392000000,
        "time": 1597370495002
    }


> **OR (when symbol not sent)**


    [
        {
            "symbol": "BTCUSDT",
            "markPrice": "11793.63104562",    // mark price
            "indexPrice": "11781.80495970",    // index price
            "estimatedSettlePrice": "11781.16138815", // Estimated Settle Price, only useful in the last hour before the settlement starts.
            "lastFundingRate": "0.00038246",  // This is the Latest funding rate
            "interestRate": "0.00010000",
            "nextFundingTime": 1597392000000,
            "time": 1597370495002
        }
    ]


  - [API Description](</docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price#http-request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/market-data/rest-api/Mark-Price#response-example>)
