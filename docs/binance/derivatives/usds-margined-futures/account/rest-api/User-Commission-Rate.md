On this page

# User Commission Rate (USER_DATA)

## API Description

Get User Commission Rate

## HTTP Request

GET `/fapi/v1/commissionRate`

## Request Weight

- *20**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    {
        "symbol": "BTCUSDT",
          "makerCommissionRate": "0.0002",  // 0.02%
          "takerCommissionRate": "0.0004"   // 0.04%
    }


  - [API Description](</docs/derivatives/usds-margined-futures/account/rest-api/User-Commission-Rate#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/account/rest-api/User-Commission-Rate#http-request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/account/rest-api/User-Commission-Rate#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/account/rest-api/User-Commission-Rate#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/account/rest-api/User-Commission-Rate#response-example>)
