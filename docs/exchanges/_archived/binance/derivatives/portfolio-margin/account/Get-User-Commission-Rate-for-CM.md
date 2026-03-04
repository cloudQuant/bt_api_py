On this page

# Get User Commission Rate for CM(USER_DATA)

## API Description

Get User Commission Rate for CM

## HTTP Request

GET `/papi/v1/cm/commissionRate`

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
        "symbol": "BTCUSD_PERP",
        "makerCommissionRate": "0.00015",  // 0.015%
        "takerCommissionRate": "0.00040"   // 0.040%
    }


  - [API Description](</docs/derivatives/portfolio-margin/account/Get-User-Commission-Rate-for-CM#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/account/Get-User-Commission-Rate-for-CM#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/account/Get-User-Commission-Rate-for-CM#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/account/Get-User-Commission-Rate-for-CM#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/account/Get-User-Commission-Rate-for-CM#response-example>)
