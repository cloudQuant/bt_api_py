On this page

# Get Position Margin Change History(TRADE)

## API Description

Get position margin change history

## HTTP Request

GET `/dapi/v1/positionMargin/history`

## Request Weight

- *1**

## Request Parameters

Name| Type| Mandatory| Description

- --|---|---|---

symbol| STRING| YES|

type| INT| NO| 1: Add position margin,2: Reduce position margin

startTime| LONG| NO|

endTime| LONG| NO|

limit| INT| NO| Default: 50

recvWindow| LONG| NO|

timestamp| LONG| YES|

## Response Example


    [
        {
            "amount": "23.36332311",
              "asset": "BTC",
              "symbol": "BTCUSD_200925",
              "time": 1578047897183,
              "type": 1,
              "positionSide": "BOTH"
        },
        {
            "amount": "100",
              "asset": "BTC",
              "symbol": "BTCUSD_200925",
              "time": 1578047900425,
              "type": 1,
              "positionSide": "LONG"
        }
    ]


  - [API Description](</docs/derivatives/coin-margined-futures/trade/Get-Position-Margin-Change-History#api-description>)
  - [HTTP Request](</docs/derivatives/coin-margined-futures/trade/Get-Position-Margin-Change-History#http-request>)
  - [Request Weight](</docs/derivatives/coin-margined-futures/trade/Get-Position-Margin-Change-History#request-weight>)
  - [Request Parameters](</docs/derivatives/coin-margined-futures/trade/Get-Position-Margin-Change-History#request-parameters>)
  - [Response Example](</docs/derivatives/coin-margined-futures/trade/Get-Position-Margin-Change-History#response-example>)
