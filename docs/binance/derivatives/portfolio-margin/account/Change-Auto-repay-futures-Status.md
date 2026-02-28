On this page

# Change Auto-repay-futures Status(TRADE)

## API Description

Change Auto-repay-futures Status

## HTTP Request

POST `/papi/v1/repay-futures-switch`

## Request Weight(IP)")

- *750**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

autoRepay| STRING| YES| Default: `true`; `false` for turn off the auto-repay futures negative balance function

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    {
        "msg": "success"
    }


  - [API Description](</docs/derivatives/portfolio-margin/account/Change-Auto-repay-futures-Status#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/account/Change-Auto-repay-futures-Status#http-request>)
  - [Request Weight(IP)](</docs/derivatives/portfolio-margin/account/Change-Auto-repay-futures-Status#request-weightip>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/account/Change-Auto-repay-futures-Status#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/account/Change-Auto-repay-futures-Status#response-example>)
