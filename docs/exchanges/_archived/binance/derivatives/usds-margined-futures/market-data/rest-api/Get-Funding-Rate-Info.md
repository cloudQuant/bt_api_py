On this page

# Get Funding Rate Info

## API Description

Query funding rate info for symbols that had FundingRateCap/ FundingRateFloor / fundingIntervalHours adjustment

## HTTP Request

GET `/fapi/v1/fundingInfo`

## Request Weight

- *0** share 500/5min/IP rate limit with `GET /fapi/v1/fundingInfo`

## Request Parameters

## Response Example


    [
        {
            "symbol": "BLZUSDT",
            "adjustedFundingRateCap": "0.02500000",
            "adjustedFundingRateFloor": "-0.02500000",
            "fundingIntervalHours": 8,
            "disclaimer": false   // ingore
        }
    ]


  - [API Description](</docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info#api-description>)
  - [HTTP Request](</docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info#http-request>)
  - [Request Weight](</docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info#request-weight>)
  - [Request Parameters](</docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info#request-parameters>)
  - [Response Example](</docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-Info#response-example>)
