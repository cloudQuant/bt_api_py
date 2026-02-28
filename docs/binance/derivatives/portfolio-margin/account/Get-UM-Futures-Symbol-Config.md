On this page

# UM Futures Symbol Configuration(USER_DATA)

## API Description

Get current UM account symbol configuration.

## HTTP Request

GET `/papi/v1/um/symbolConfig`

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


  - [API Description](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Symbol-Config#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Symbol-Config#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Symbol-Config#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Symbol-Config#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/account/Get-UM-Futures-Symbol-Config#response-example>)
