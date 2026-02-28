On this page

# Fund Collection by Asset(USER_DATA)

## API Description

Transfers specific asset from Futures Account to Margin account

## HTTP Request

POST `/sapi/v1/portfolio/asset-collection`

## Request Weight(IP)")

- *60**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

asset| STRING| YES|

recvWindow| LONG| NO|

timestamp| LONG| YES|

>   * The BNB transfer is not be supported
>

## Response Example


    {
        "msg": "success"
    }


  - [API Description](</docs/derivatives/portfolio-margin-pro/account/Fund-Collection-by-Asset#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin-pro/account/Fund-Collection-by-Asset#http-request>)
  - [Request Weight(IP)](</docs/derivatives/portfolio-margin-pro/account/Fund-Collection-by-Asset#request-weightip>)
  - [Request Parameters](</docs/derivatives/portfolio-margin-pro/account/Fund-Collection-by-Asset#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin-pro/account/Fund-Collection-by-Asset#response-example>)
