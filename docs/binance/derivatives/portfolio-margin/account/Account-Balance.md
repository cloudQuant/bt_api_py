On this page

# Account Balance(USER_DATA)

## API Description

Query account balance

## HTTP Request

GET `/papi/v1/balance`

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
            "asset": "USDT",    // asset name
            "totalWalletBalance": "122607.35137903", // wallet balance =  cross margin free + cross margin locked + UM wallet balance + CM wallet balance
            "crossMarginAsset": "92.27530794", // crossMarginAsset = crossMarginFree + crossMarginLocked
            "crossMarginBorrowed": "10.00000000", // principal of cross margin
            "crossMarginFree": "100.00000000", // free asset of cross margin
            "crossMarginInterest": "0.72469206", // interest of cross margin
            "crossMarginLocked": "3.00000000", //lock asset of cross margin
            "umWalletBalance": "0.00000000",  // wallet balance of um
            "umUnrealizedPNL": "23.72469206",     // unrealized profit of um
            "cmWalletBalance": "23.72469206",       // wallet balance of cm
            "cmUnrealizedPNL": "",    // unrealized profit of cm
            "updateTime": 1617939110373,
            "negativeBalance": "0"
        }
    ]

    - *OR (when asset sent)**

    {
        "asset": "USDT",    // asset name
        "totalWalletBalance": "122607.35137903", // wallet balance =  cross margin free + cross margin locked + UM wallet balance + CM wallet balance
        "crossMarginBorrowed": "10.00000000", // principal of cross margin
        "crossMarginFree": "100.00000000", // free asset of cross margin
        "crossMarginInterest": "0.72469206", // interest of cross margin
        "crossMarginLocked": "3.00000000", //lock asset of cross margin
        "umWalletBalance": "0.00000000",  // wallet balance of um
        "umUnrealizedPNL": "23.72469206",     // unrealized profit of um
        "cmWalletBalance": "23.72469206",       // wallet balance of cm
        "cmUnrealizedPNL": "",    // unrealized profit of cm
        "updateTime": 1617939110373,
        "negativeBalance": "0"
    }

  - [API Description](</docs/derivatives/portfolio-margin/account#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/account#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/account#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/account#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/account#response-example>)
