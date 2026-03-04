On this page

# Get User Commission Rate for UM(USER_DATA)

## API Description

Get User Commission Rate for UM

## HTTP Request

GET `/papi/v1/um/commissionRate`

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


  - [API Description](</docs/derivatives/portfolio-margin/account/Get-User-Commission-Rate-for-UM#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/account/Get-User-Commission-Rate-for-UM#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/account/Get-User-Commission-Rate-for-UM#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/account/Get-User-Commission-Rate-for-UM#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/account/Get-User-Commission-Rate-for-UM#response-example>)
