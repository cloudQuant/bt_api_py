On this page

# Margin Account Repay(MARGIN)

## API Description

Repay for a margin loan.

## HTTP Request

POST `/papi/v1/repayLoan`

## Request Weight

- *100**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

asset| STRING| YES|

amount| DECIMAL| YES|

recvWindow| LONG| NO| The value cannot be greater than 60000

timestamp| LONG| YES|

## Response Example


    {
        //transaction id
        "tranId": 100000001
    }


  - [API Description](</docs/derivatives/portfolio-margin/trade/Margin-Account-Repay#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/trade/Margin-Account-Repay#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/trade/Margin-Account-Repay#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/trade/Margin-Account-Repay#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/trade/Margin-Account-Repay#response-example>)
