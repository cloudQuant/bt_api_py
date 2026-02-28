On this page

# Accept the offered quote (USER_DATA)

## API Description

Accept the offered quote by quote ID.

## HTTP Request

POST `/fapi/v1/convert/acceptQuote`

## Request Weight

- *200(IP)**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

quoteId| STRING| YES|

recvWindow| LONG| NO| The value cannot be greater than 60000

timestamp| LONG| YES|

## Response Example


    {
      "orderId":"933256278426274426",
      "createTime":1623381330472,
      "orderStatus":"PROCESS" //PROCESS/ACCEPT_SUCCESS/SUCCESS/FAIL
    }


  - [API Description](</docs/derivatives/usds-margined-futures/convert/Accept-Quote#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/convert/Accept-Quote#http-request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/convert/Accept-Quote#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/convert/Accept-Quote#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/convert/Accept-Quote#response-example>)
