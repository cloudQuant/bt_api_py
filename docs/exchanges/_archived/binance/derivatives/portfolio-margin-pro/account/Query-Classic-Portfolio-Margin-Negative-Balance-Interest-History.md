On this page

# Query Portfolio Margin Pro Negative Balance Interest History(USER_DATA)

## API Description

Query interest history of negative balance for portfolio margin.

## HTTP Request

GET `/sapi/v1/portfolio/interest-history`

## Request Weight(IP)")

- *50**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

asset| STRING| NO|

startTime| LONG| NO|

endTime| LONG| NO|

size| LONG| NO| Default:10 Max:100

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    [
        {
            "asset": "USDT",
            "interest": "24.4440",               //interest amount
            "interestAccruedTime": 1670227200000,
            "interestRate": "0.0001164",         //daily interest rate
            "principal": "210000"
        }
    ]


  - [API Description](</docs/derivatives/portfolio-margin-pro/account/Query-Classic-Portfolio-Margin-Negative-Balance-Interest-History#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin-pro/account/Query-Classic-Portfolio-Margin-Negative-Balance-Interest-History#http-request>)
  - [Request Weight(IP)](</docs/derivatives/portfolio-margin-pro/account/Query-Classic-Portfolio-Margin-Negative-Balance-Interest-History#request-weightip>)
  - [Request Parameters](</docs/derivatives/portfolio-margin-pro/account/Query-Classic-Portfolio-Margin-Negative-Balance-Interest-History#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin-pro/account/Query-Classic-Portfolio-Margin-Negative-Balance-Interest-History#response-example>)
