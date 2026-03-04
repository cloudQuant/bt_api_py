On this page

# Symbol Configuration(USER_DATA)

## API Description

Get current account symbol configuration.

## HTTP Request

GET `/fapi/v1/symbolConfig`

## Request Weight

- *5**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| NO|

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    [
      {
      "symbol": "BTCUSDT",
      "marginType": "CROSSED",
      "isAutoAddMargin": "false",
      "leverage": 21,
      "maxNotionalValue": "1000000",
      }
    ]


  - [API Description](</docs/derivatives/usds-margined-futures/account/rest-api/Symbol-Config#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/account/rest-api/Symbol-Config#http-request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/account/rest-api/Symbol-Config#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/account/rest-api/Symbol-Config#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/account/rest-api/Symbol-Config#response-example>)
