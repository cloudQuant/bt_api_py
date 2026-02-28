On this page

# Get CM Current Position Mode(USER_DATA)

## API Description

Get user's position mode (Hedge Mode or One-way Mode ) on EVERY symbol in CM

## HTTP Request

GET `/papi/v1/cm/positionSide/dual`

## Request Weight

- *30**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    {
      "dualSidePosition": true // "true": Hedge Mode; "false": One-way Mode
    }


  - [API Description](</docs/derivatives/portfolio-margin/account/Get-CM-Current-Position-Mode#api-description>)
  - [HTTP Request](</docs/derivatives/portfolio-margin/account/Get-CM-Current-Position-Mode#http-request>)
  - [Request Weight](</docs/derivatives/portfolio-margin/account/Get-CM-Current-Position-Mode#request-weight>)
  - [Request Parameters](</docs/derivatives/portfolio-margin/account/Get-CM-Current-Position-Mode#request-parameters>)
  - [Response Example](</docs/derivatives/portfolio-margin/account/Get-CM-Current-Position-Mode#response-example>)
