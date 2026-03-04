On this page

# Change Position Mode(TRADE)

## API Description

Change user's position mode (Hedge Mode or One-way Mode ) on _**EVERY symbol**_

## HTTP Request

POST `/fapi/v1/positionSide/dual`

## Request Weight

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

dualSidePosition| STRING| YES| "true": Hedge Mode; "false": One-way Mode

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    {
        "code": 200,
        "msg": "success"
    }


  - [API Description](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Position-Mode#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Position-Mode#http-request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Position-Mode#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Position-Mode#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/trade/rest-api/Change-Position-Mode#response-example>)
