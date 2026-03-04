On this page

# BNB transfer (TRADE)

## API Description

Transfer BNB in and out of UM

## HTTP Request

POST `/papi/v1/bnb-transfer`

## Request Weight(IP)")

- *750**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

amount| DECIMAL| YES|

transferSide| STRING| YES| "TO_UM","FROM_UM"

recvWindow| LONG| NO| The value cannot be greater than 60000

timestamp| LONG| YES|

>   * The endpoint can only be called 10 times per 10 minutes in a rolling manner
>

## Response Example


    {
        "tranId": 100000001       //transaction id
    }  


  - [API Description](</docs/derivatives/portfolio-margin/account/BNB-transfer#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/account/BNB-transfer#http-request>)
  - [Request Weight(IP)](</docs/derivatives/portfolio-margin/account/BNB-transfer#request-weightip>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/account/BNB-transfer#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/account/BNB-transfer#response-example>)
