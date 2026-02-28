On this page

# Change Margin Type (TRADE)

## API Description

Change user's margin type in the specific symbol market.For Hedge Mode, LONG and SHORT positions of one symbol use the same margin type.
With ISOLATED margin type, margins of the LONG and SHORT positions are isolated from each other.

## HTTP Request

POST `/dapi/v1/marginType`

## Request Weight

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

marginType| ENUM| YES| ISOLATED, CROSSED

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    {
        "code": 200,
        "msg": "success"
    }


  - [API Description](</docs/derivatives/coin-margined-futures/trade/Change-Margin-Type#api-description>)
  - [HTTP Request](</docs/derivatives/coin-margined-futures/trade/Change-Margin-Type#http-request>)
  - [Request Weight](</docs/derivatives/coin-margined-futures/trade/Change-Margin-Type#request-weight>)
  - [Request Parameters](</docs/derivatives/coin-margined-futures/trade/Change-Margin-Type#request-parameters>)
  - [Response Example](</docs/derivatives/coin-margined-futures/trade/Change-Margin-Type#response-example>)
