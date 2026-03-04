On this page

# Get Funding Rate Info

## API Description

Query funding rate info for symbols that had FundingRateCap/ FundingRateFloor / fundingIntervalHours adjustment

## HTTP Request

GET `/dapi/v1/fundingInfo`

## Response Example


    [
        {
            "symbol": "BLZUSDT",
            "adjustedFundingRateCap": "0.02500000",
            "adjustedFundingRateFloor": "-0.02500000",
            "fundingIntervalHours": 8,
            "disclaimer": false
        }
    ]


  - [API Description](</docs/derivatives/coin-margined-futures/market-data/Get-Funding-Info#api-description>)
  - [HTTP Request](</docs/derivatives/coin-margined-futures/market-data/Get-Funding-Info#http-request>)
  - [Response Example](</docs/derivatives/coin-margined-futures/market-data/Get-Funding-Info#response-example>)
