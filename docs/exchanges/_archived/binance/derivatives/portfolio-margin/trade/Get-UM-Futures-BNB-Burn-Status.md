On this page

# Get UM Futures BNB Burn Status (USER_DATA)

## API Description

Get user's BNB Fee Discount for UM Futures (Fee Discount On or Fee Discount Off )

## HTTP Request

GET `/papi/v1/um/feeBurn`

## Request Weight

- *30**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    {
        "feeBurn": true // "true": Fee Discount On; "false": Fee Discount Off
    }


  - [API Description](</docs/derivatives/portfolio-margin/trade/Get-UM-Futures-BNB-Burn-Status#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/trade/Get-UM-Futures-BNB-Burn-Status#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/trade/Get-UM-Futures-BNB-Burn-Status#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/trade/Get-UM-Futures-BNB-Burn-Status#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/trade/Get-UM-Futures-BNB-Burn-Status#response-example>)
