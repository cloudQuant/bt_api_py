On this page

# Get Portfolio Margin Pro Account Balance(USER_DATA)

## API Description

Query Portfolio Margin Pro account balance

## HTTP Request

GET `/sapi/v1/portfolio/balance`

## Request Weight

- *20**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

asset| STRING| NO|

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example

    [
        {
            "asset": "BTC",    // asset name
            "totalWalletBalance": "100",    // wallet balance =  cross margin free + cross margin locked + UM wallet balance + CM wallet balance
            "crossMarginAsset": "100",    // crossMarginAsset = crossMarginFree + crossMarginLocked
            "crossMarginBorrowed": "0",    // principal of cross margin
            "crossMarginFree": "100",    // free asset of cross margin
            "crossMarginInterest": "0",    // interest of cross margin
            "crossMarginLocked": "0",  //lock asset of cross margin
            "umWalletBalance": "0",    // wallet balance of um
            "umUnrealizedPNL": "0",     // unrealized profit of um
            "cmWalletBalance": "0",    // wallet balance of cm
            "cmUnrealizedPNL": "0",    // unrealized profit of cm
            "updateTime": 0,
            "negativeBalance": "0",
            "optionWalletBalance": "0",  //only for PM PRO SPAN
            "optionEquity": "0"  //only for PM PRO SPAN
        }
    ]

  - [API Description](</docs/derivatives/portfolio-margin-pro/account/Get-Classic-Portfolio-Margin-Balance-Info#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin-pro/account/Get-Classic-Portfolio-Margin-Balance-Info#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin-pro/account/Get-Classic-Portfolio-Margin-Balance-Info#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin-pro/account/Get-Classic-Portfolio-Margin-Balance-Info#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin-pro/account/Get-Classic-Portfolio-Margin-Balance-Info#response-example>)
