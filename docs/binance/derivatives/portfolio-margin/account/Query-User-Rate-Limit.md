On this page

# Query User Rate Limit (USER_DATA)

## API Description

Query User Rate Limit

## HTTP Request

GET `/papi/v1/rateLimit/order`

## Request Weight

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    [
      {
            "rateLimitType": "ORDERS",
            "interval": "MINUTE",
            "intervalNum": 1,
            "limit": 1200
        }
    ]


  - [API Description](</docs/derivatives/portfolio-margin/account/Query-User-Rate-Limit#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/account/Query-User-Rate-Limit#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/account/Query-User-Rate-Limit#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/account/Query-User-Rate-Limit#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/account/Query-User-Rate-Limit#response-example>)
