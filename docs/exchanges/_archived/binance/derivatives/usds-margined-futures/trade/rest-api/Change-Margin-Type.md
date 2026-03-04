On this page

# Change Margin Type(TRADE)

## API Description

Change symbol level margin type

## HTTP Request

POST `/fapi/v1/marginType`

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


  - [API Description](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Margin-Type#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Margin-Type#http-request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Margin-Type#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Margin-Type#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Margin-Type#response-example>)
