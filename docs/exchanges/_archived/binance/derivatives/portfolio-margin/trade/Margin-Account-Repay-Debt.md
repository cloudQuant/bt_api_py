On this page

# Margin Account Repay Debt(TRADE)

## API Description

Repay debt for a margin loan.

## HTTP Request

POST `/papi/v1/margin/repay-debt`

## Request Weight(Order)")

- *3000**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

asset| STRING| YES|

amount| STRING| NO|

specifyRepayAssets| STRING| NO| Specific asset list to repay debt; Can be added in batch, separated by commas

recvWindow| LONG| NO| The value cannot be greater than 60000

timestamp| LONG| YES|

>   *The repay asset amount cannot exceed 50000 USD equivalent value for a single request.
>  *If `amount` is not sent, all the asset loan will be repaid if having enough specific repay assets.
>  *If `amount` is sent, only the certain amount of the asset loan will be repaid if having enough specific repay assets.
>  * The system will use the same asset to repay the loan first (if have) no matter whether put the asset in `specifyRepayAssets`
>

## Response Example


    {
        "amount": "0.10000000",
        "asset": "BNB",
        "specifyRepayAssets": [
        "USDT",
        "BTC"
        ],
        "updateTime": 1636371437000
        "success": true
    }


  - [API Description](</docs/derivatives/portfolio-margin/trade/Margin-Account-Repay-Debt#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/trade/Margin-Account-Repay-Debt#http-request>)
  - [Request Weight(Order)](</docs/derivatives/portfolio-margin/trade/Margin-Account-Repay-Debt#request-weightorder>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/trade/Margin-Account-Repay-Debt#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/trade/Margin-Account-Repay-Debt#response-example>)
