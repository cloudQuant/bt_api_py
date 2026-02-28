On this page

# Query Portfolio Margin Pro Bankruptcy Loan Amount(USER_DATA)

## API Description

Query Portfolio Margin Pro Bankruptcy Loan Amount

## HTTP Request

GET `/sapi/v1/portfolio/pmLoan`

## Request Weight(UID)")

- *500**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

recvWindow| LONG| NO|

timestamp| LONG| YES|

>   * If there’s no classic portfolio margin bankruptcy loan, the amount would be 0
>

## Response Example


    {
       "asset": "BUSD",
       "amount":  "579.45", // portfolio margin bankruptcy loan amount in BUSD
    }


  - [API Description](</docs/derivatives/portfolio-margin-pro/account/Query-Classic-Portfolio-Margin-Bankruptcy-Loan-Amount#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin-pro/account/Query-Classic-Portfolio-Margin-Bankruptcy-Loan-Amount#http-request>)
  - [Request Weight(UID)](</docs/derivatives/portfolio-margin-pro/account/Query-Classic-Portfolio-Margin-Bankruptcy-Loan-Amount#request-weightuid>)
  - [Request Parameters](</docs/derivatives/portfolio-margin-pro/account/Query-Classic-Portfolio-Margin-Bankruptcy-Loan-Amount#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin-pro/account/Query-Classic-Portfolio-Margin-Bankruptcy-Loan-Amount#response-example>)
